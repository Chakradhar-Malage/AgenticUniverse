"""A tiny in-memory stand-in for the sim's governance endpoints (contract: SIMULATOR.md section 4).
Lets us test crash behaviour deterministically. `crash_after` simulates 'the server did the work but
we never saw the reply' (the nastiest case for idempotency)."""
from controller.client import SimError


class FakeSim:
    def __init__(self):
        self.version = 1
        self.approvals = {}
        self.history = [{"version": 1, "actor": "business_default", "changed_dims": [], "proposal_id": None, "approval_id": None}]
        self.created = self.applied = 0
        self.crash_after = None          # "post" | "apply" | "put"
        self.config = None

    def _boom(self, what):
        if self.crash_after == what:
            self.crash_after = None
            raise SimError(599, "connection lost")

    def decide(self, aid, status):
        self.approvals[aid]["status"] = status

    def _json(self, method, path, json=None, params=None):
        if (method, path) == ("POST", "/approvals"):
            if any(a["proposal_id"] == json["proposal_id"] for a in self.approvals.values()): # type: ignore
                raise SimError(409, {"detail": {"code": "duplicate_proposal_id"}})
            if json["base_version"] != self.version: # type: ignore
                raise SimError(409, {"detail": {"code": "stale_base_version"}})
            aid = f"apr_{len(self.approvals) + 1:05d}"
            self.approvals[aid] = {"approval_id": aid, "proposal_id": json["proposal_id"], "workflow_id": json["workflow_id"], # type: ignore
                                   "status": "pending", "base_version": json["base_version"]} # type: ignore
            self.created += 1
            self._boom("post")
            return {"approval_id": aid, "status": "pending"}
        if (method, path) == ("GET", "/approvals"):
            return [a for a in self.approvals.values() if a["workflow_id"] == (params or {}).get("workflow_id", a["workflow_id"])]
        if method == "GET" and path.startswith("/approvals/"):
            return self.approvals[path.split("/")[-1]]
        if method == "POST" and path.endswith("/apply"):
            a = self.approvals[json["approval_id"]] # type: ignore
            if a["status"] == "applied":
                raise SimError(409, {"detail": {"code": "already_applied"}})
            if a["base_version"] != self.version:
                raise SimError(409, {"detail": {"code": "stale_base_version"}})
            self.version += 1
            self.history.append({"version": self.version, "actor": "controller_approved", "changed_dims": ["voice"],
                                 "proposal_id": a["proposal_id"], "approval_id": a["approval_id"]})
            a["status"] = "applied"
            self.applied += 1
            self._boom("apply")
            return {"version": self.version, "changed_dims": ["voice"]}
        if method == "PUT" and path.endswith("/config"):
            if json["base_version"] != self.version: # type: ignore
                raise SimError(409, {"detail": {"code": "stale_base_version"}})
            self.version += 1
            self.config = json["proposed_config"] # type: ignore
            self.history.append({"version": self.version, "actor": "controller_auto", "changed_dims": ["caller_id"],
                                 "proposal_id": json["proposal_id"], "approval_id": None}) # type: ignore
            self.applied += 1
            self._boom("put")
            return {"version": self.version, "changed_dims": ["caller_id"]}
        if method == "GET" and path.endswith("/config") and self.config is not None:
            return {"workflow_id": "w", "version": self.version, "config": self.config}
        if method == "GET" and path.endswith("/config/history"):
            return self.history
        raise AssertionError(f"unexpected call {method} {path}")