"""Deterministic crash injection for the crash-recovery demonstration.

INERT unless the environment variable CHAOS_POINT is set. When it names a hook point and the marker file
does not exist yet, the process dies instantly with exit code 137 (os._exit: no cleanup, no flush, the same
as `kill -9`), exactly once. Points sit at the nastiest moments of the governance protocol:
   after_intent  proposal written to our DB, API not yet called
   after_post    server accepted the approval request, we never recorded the reply
   after_apply   server applied the approved change, we never recorded the reply
   after_put     server auto-applied the change, we never recorded the reply
"""
import os


def _die():
    os._exit(137)


def maybe_crash(point):
    if os.environ.get("CHAOS_POINT") != point:
        return
    marker = os.environ.get("CHAOS_MARKER")
    if marker:
        if os.path.exists(marker):
            return                                   # already crashed once: the restart must run clean
        with open(marker, "w") as f:
            f.write(point)
    _die()