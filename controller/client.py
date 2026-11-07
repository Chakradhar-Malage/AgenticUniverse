"""Thin HTTP client for the simulator. The ONLY way the controller learns about the world.

Rules it enforces:
  * 5xx / network errors are retried with backoff (the sim may be busy, never assume it is dead).
  * 4xx are NOT retried: they are answers ("stale_base_version", "402 out of credits") that the
    caller must handle. They surface as SimError with the status code and body.
"""
import time

import httpx


class SimError(Exception):
    def __init__(self, status, body):
        super().__init__(f"{status}: {body}")
        self.status, self.body = status, body

    @property
    def code(self):
        b = self.body
        if isinstance(b, dict):
            d = b.get("detail", b)
            if isinstance(d, dict):
                return d.get("code") or d.get("error")
            return b.get("code") or b.get("error")
        return None


class Sim:
    def __init__(self, url, timeout=300, retries=6):
        self.c = httpx.Client(base_url=url, timeout=timeout)
        self.retries = retries

    def _req(self, method, path, **kw):
        delay = 0.5
        for attempt in range(self.retries + 1):
            try:
                r = self.c.request(method, path, **kw)
                if r.status_code < 500:
                    return r
                err = f"HTTP {r.status_code}"
            except httpx.TransportError as e:
                err = repr(e)
            if attempt == self.retries:
                raise SimError(599, f"gave up after {self.retries} retries: {err}")
            time.sleep(delay)
            delay = min(delay * 2, 8)

    def _json(self, method, path, **kw):
        r = self._req(method, path, **kw)
        try:
            assert r is not None
            body = r.json()
        except Exception:
            assert r is not None
            body = r.text
        assert r is not None
        if r.status_code >= 400:
            raise SimError(r.status_code, body)
        return body

    # --- reads -------------------------------------------------------------------------------
    def state(self):
        return self._json("GET", "/sim/state")

    def workflows(self):
        return self._json("GET", "/workflows")

    def list_chunks(self, kind, after=-1):
        params = {"kind": kind}
        if after >= 0:
            params["after"] = after
        return self._json("GET", "/events/chunks", params=params)

    def get_chunk(self, n):
        r = self._req("GET", f"/events/chunks/{n}")
        assert r is not None
        if r.status_code >= 400:
            assert r is not None
            raise SimError(r.status_code, r.text)
        return r.text

    # --- time --------------------------------------------------------------------------------
    def advance(self, minutes):
        """Advance simulated time. NOT idempotent, so we never auto-retry it blindly: on a
        transport failure we re-read /sim/state to find out whether it actually happened."""
        before = self.state().get("ts") # type: ignore
        try:
            r = self.c.post("/sim/advance", json={"minutes": minutes})
        except httpx.TransportError:
            after = self.state().get("ts") # type: ignore
            return {"ts": after, "advanced": after != before, "finished": self.state().get("finished", False)} # type: ignore
        if r.status_code == 409:
            return {"ts": before, "advanced": False, "finished": True}
        if r.status_code >= 400:
            raise SimError(r.status_code, r.text)
        out = r.json()
        out["advanced"] = True
        return out