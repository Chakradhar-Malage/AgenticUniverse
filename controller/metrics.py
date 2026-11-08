"""The loop's own health (capability C14): ingest throughput, decision lag, memory, credit burn.
Stdlib only. `python -m controller.status --loop` prints the summary an on-call engineer or SRE needs."""
import time


def rss_mb():
    """Resident memory of this process in MB, or None if the platform doesn't allow it. Linux: /proc. Windows: ctypes."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]
        k32, ps = ctypes.windll.kernel32, ctypes.windll.psapi
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(pmc)
        if ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / 1048576
    except Exception:
        pass
    return None


def record_tick(store, sim_ts, events, ingest_s, decide_s, advance_s, lag_s, rss, credits):
    store.db.execute("INSERT INTO loop_metrics(wall_ts,sim_ts,events,ingest_s,decide_s,advance_s,lag_s,rss_mb,credits) "
                     "VALUES(?,?,?,?,?,?,?,?,?)", (time.time(), sim_ts, events, ingest_s, decide_s, advance_s, lag_s, rss, credits))


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else 0.0


def summary(db):
    rows = db.execute("SELECT wall_ts, events, ingest_s, decide_s, advance_s, lag_s, rss_mb, credits FROM loop_metrics ORDER BY id").fetchall()
    if not rows:
        return {}
    ev = sum(r[1] for r in rows)
    ing = sum(r[2] for r in rows)
    hist = db.execute("SELECT COALESCE(SUM(n_events),0) FROM chunks_done WHERE kind='history'").fetchone()[0]
    lags = [r[5] for r in rows]
    rss = [r[6] for r in rows if r[6]]
    cr = [r[7] for r in rows if r[7] is not None]
    n_changes = db.execute("SELECT COUNT(*) FROM proposals WHERE status='applied'").fetchone()[0]
    sim_days = db.execute("SELECT COALESCE(MAX(day)-MIN(day)+1, 0) FROM agg WHERE day>=0").fetchone()[0]
    wall = rows[-1][0] - rows[0][0]
    return {
        "ticks": len(rows), "events_live": ev, "events_history": hist,
        "ingest_events_per_sec": round(ev / ing) if ing else None,
        "controller_overhead_s_total": round(sum(r[2] + r[3] for r in rows), 1),
        "sim_advance_s_total": round(sum(r[4] for r in rows), 1),
        "wall_clock_s_loop": round(wall, 1), "sim_days": sim_days,
        "decision_lag_s_p50": round(_pct(lags, .5), 3), "decision_lag_s_p95": round(_pct(lags, .95), 3), "decision_lag_s_max": round(max(lags), 3),
        "peak_rss_mb": round(max(rss)) if rss else None,
        "changes_applied": n_changes, "decisions_per_sim_hour": round(n_changes / (len(rows) / 4), 2) if rows else 0,
        "credits_used": round(cr[0] - cr[-1], 1) if cr else None,
    }