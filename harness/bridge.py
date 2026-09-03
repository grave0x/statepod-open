"""Scoped bridges between pools (spec v1 section 5).

Pools stay specialized; a BRIDGE carries only what both sides accept.
The bridge is a node with TWO mesh links (one per pool); every op it
sees from one side is checked against the bridge policy before it is
re-published into the other side.

Bridge policy scopes (spec v1 5):
  - allowed message types   registry learning only by default (no
                            inference req/resp passthrough, no raw
                            file content)
  - allowed task signatures target globs, e.g. "reg/STRAT/WRITE/*"
  - time window             the bridge expires after ttl seconds
  - data minimization       MODEL routing keys stay local by default

Both sides must accept: each bridge side joins its pool with a signed
invite (or is added to the pool allowlist) -- no pool may reach
another without both sides accepting.
"""
from __future__ import annotations

import fnmatch
import json
import sys
import time


def _chain_on_op(d, handler):
    """Attach a relay handler to a daemon WITHOUT clobbering existing
    handlers.  One daemon can be the shared side of several bridges
    (multi-homing): each bridge adds its own policy-checked relay."""
    prev = getattr(d, "on_op", None)
    if prev is None:
        d.on_op = handler
        return

    def chained(target, value):
        try:
            prev(target, value)
        except Exception:
            pass
        handler(target, value)

    d.on_op = chained


class BridgePolicy:
    """What may cross the bridge, and for how long."""

    def __init__(self, allow_targets: tuple[str, ...] = ("reg/STRAT/*",),
                 ttl: float = 300.0, minimize: bool = True,
                 name: str = "bridge"):
        self.allow_targets = allow_targets
        self.started = time.monotonic()
        self.ttl = ttl
        self.minimize = minimize
        self.name = name
        self.log_lines: list[dict] = []

    def expired(self) -> bool:
        return time.monotonic() - self.started > self.ttl

    def allows(self, target: str) -> bool:
        """Target-glob check.  minimize=True additionally drops anything
        outside the registry namespace (data minimization)."""
        if self.minimize and not target.startswith("reg/"):
            return False
        return any(fnmatch.fnmatch(target, pat) for pat in self.allow_targets)

    def _log(self, target: str, verdict: str, why: str):
        self.log_lines.append({"ts": time.time(), "target": target,
                               "verdict": verdict, "why": why})

    def check(self, target: str) -> bool:
        if self.expired():
            self._log(target, "DENY", "bridge expired")
            return False
        if not self.allows(target):
            self._log(target, "DENY", "not in allow_targets"
                      if not self.minimize or target.startswith("reg/")
                      else "data minimization (outside reg/)")
            return False
        self._log(target, "RELAY", "policy ok")
        return True


class PoolBridge:
    """One node, two pool links, one policy between them.

    tag_source=True (default): learning that crosses the bridge is
    re-published under the SOURCE pool's namespace
    (reg/POOL/<src-pool>/STRAT/... -> POOL:<src>:STRAT:... on the
    receiving side).  That is the federation multi-homing isolation
    model: foreign learning never lands in the local partition -- it
    arrives tagged, and local routers read only untagged keys.
    """

    def __init__(self, name: str, side_a, side_b, policy: BridgePolicy,
                 tag_source: bool = True):
        self.name = name
        self.side_a = side_a
        self.side_b = side_b
        self.policy = policy
        self.tag_source = tag_source
        self.relayed = 0
        self.denied = 0
        # wire both directions: learning from A may cross to B, and
        # learning from B may cross to A (scoped both ways).  CHAINED:
        # a side shared by several bridges keeps every relay active.
        _chain_on_op(side_a, self._make_relay(side_a, side_b, "A->B"))
        _chain_on_op(side_b, self._make_relay(side_b, side_a, "B->A"))

    @staticmethod
    def _tag_target(src, target: str) -> str:
        """Namespace a registry target under the source pool.  Already
        namespaced targets pass through unchanged (no double tags)."""
        if target.startswith("reg/POOL/"):
            return target
        if not target.startswith("reg/"):
            return target
        src_pool = getattr(src, "pool_name", None) or "unknown"
        return f"reg/POOL/{src_pool}/" + target[len("reg/"):]

    def _make_relay(self, src, dst, direction: str):
        def relay(target: str, value: str):
            try:
                if self.policy.check(target):
                    out = target
                    if self.tag_source:
                        out = self._tag_target(src, target)
                    dst.publish(str(out), str(value))
                    self.relayed += 1
                    print(f"[bridge:{self.name}] {direction} RELAY {target}",
                          file=sys.stderr, flush=True)
                else:
                    self.denied += 1
                    why = self.policy.log_lines[-1]["why"] if \
                        self.policy.log_lines else "policy"
                    print(f"[bridge:{self.name}] {direction} DENY  "
                          f"{target} ({why})", file=sys.stderr, flush=True)
            except Exception as exc:
                # never let a bridge error kill the mesh link, but never
                # hide it either -- report to stderr (meshd swallows
                # on_op exceptions by design)
                print(f"[bridge:{self.name}] {direction} ERROR {target}: "
                      f"{exc}", file=sys.stderr, flush=True)
        return relay

    def summary(self) -> dict:
        return {"name": self.name,
                "relayed": self.relayed, "denied": self.denied,
                "expired": self.policy.expired(),
                "decisions": self.policy.log_lines[-20:]}
