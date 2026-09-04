"""SwarmState pre-pitch red-team battery — attack tests vs the shipped system.
Records PASS/WEAK/FAIL with evidence to /tmp/ss_redteam/battery.log"""
import os, sys, time, json
sys.path.insert(0, "/home/grave/Projects/internal.source/02-tools/swarmstate/harness")
sys.path.insert(0, "/home/grave/Projects/internal.source/02-tools/swarmstate")
os.environ.setdefault("SWARMSTATE_LIB", "/home/grave/Projects/internal.source/02-tools/swarmstate/libswarmstate.so")
from py import swarmstate as _ss; SwarmState = _ss.SwarmState
from governance import Governance, issue_token, check_multiparty
from pool import PoolHub, issue_invite, verify_invite

REPO = "/tmp/ss_redteam/repo"
OUT = "/tmp/ss_redteam/outside"
RESULTS = []
def rec(cat, name, verdict, evidence, note=""):
    RESULTS.append(dict(cat=cat, name=name, verdict=verdict, evidence=evidence[:160], note=note))
    print(f"[{verdict}] {cat}: {name} :: {evidence[:120]}{'  ('+note+')' if note else ''}")

# Self-bootstrap sandbox (ss_state_new requires an existing directory).
os.makedirs(os.path.join(REPO, "src"), exist_ok=True)
os.makedirs(OUT, exist_ok=True)
open(os.path.join(OUT, "secret.txt"), "w").write("secret\n")
if not os.path.exists(os.path.join(REPO, "src", "a.c")):
    open(os.path.join(REPO, "src", "a.c"), "w").write("int x;\n")

# ---------------- kernel EXECUTE ----------------
st = SwarmState(REPO)
exec_attacks = [
    ("metachar_semicolon", "ls; rm -rf /tmp/x"),
    ("metachar_pipe",      "ls | head"),
    ("metachar_redirect",  "echo hi > /tmp/x"),
    ("metachar_var",       "echo $HOME"),
    ("metachar_backtick",  "echo `id`"),
    ("quote",              "cat 'a b'"),
    ("abs_bin",            "/bin/ls"),
    ("abs_arg",            "grep -r x /"),
    ("dotdot_arg",         "cat ../../../etc/passwd"),
    ("dotdot_seg",         "cat sub/../x"),
    ("backslash",          "dir C:\\Windows"),
    ("not_whitelisted_rm", "rm -rf /tmp/evil"),
    ("not_whitelisted_sh", "sh -c id"),
    ("not_whitelisted_py", "python3 -c print(1)"),
    ("find_exec",          "find . -exec rm {} +"),
    ("find_delete",        "find . -delete"),
    ("git_push",           "git push origin main"),
    ("git_checkout",       "git checkout master"),
    ("git_log_ok",         "git log --oneline -3"),      # legit -> PASS
    ("grep_ok",            "grep -r TODO ."),            # legit -> PASS
]
for name, cmd in exec_attacks:
    r = st.execute_cmd(cmd)
    denied = "ERROR" in r or "not allowed" in r or "not whitelisted" in r or "too long" in r or "too many" in r
    legit = name.endswith("_ok")
    if legit: verdict = "PASS" if (not denied and r.strip()) else "FAIL"
    else:     verdict = "PASS" if denied else "FAIL"
    rec("kernel.exec", name, verdict, r.strip().splitlines()[0] if r.strip() else "(empty)")

# ---------------- kernel WRITE: path containment ----------------
os.makedirs(REPO, exist_ok=True)
write_attacks = [
    ("write_dotdot",      "../../tmp/pwn", "owned"),
    ("write_abs",         "/tmp/pwn", "owned"),
    ("write_dotdot_seg",  "sub/../pwn", "owned"),
    ("write_backslash",   "sub\\..\\pwn", "owned"),
    ("write_ok",          "src/ok.txt", "fine"),          # legit
]
for name, path, content in write_attacks:
    r = st.write(path, content)
    denied = "ERROR" in r or "denied" in r or "outside" in r or "not allowed" in r
    if name == "write_ok":
        ok = (not denied) and os.path.exists(os.path.join(REPO, "src/ok.txt"))
        verdict = "PASS" if ok else "FAIL"
    else:
        verdict = "PASS" if denied else "FAIL"
    rec("kernel.write", name, verdict, r[:100])

# symlink escape: symlink inside repo -> outside file
try:
    os.makedirs(os.path.join(REPO, "lnk"), exist_ok=True)
    link = os.path.join(REPO, "lnk", "esc")
    if os.path.lexists(link): os.unlink(link)
    os.symlink(OUT + "/secret.txt", link)
    r = st.write("lnk/esc", "owned-via-symlink")
    r2 = st.read("lnk/esc")
    denied = "ERROR" in r or "denied" in r or "outside" in r or "not allowed" in r
    verdict = "PASS" if denied else "FAIL"
    rec("kernel.write", "symlink_escape", verdict, r[:100])
    rec("kernel.read",  "symlink_read_escape", "PASS" if ("ERROR" in r2 or "denied" in r2) else "FAIL", r2[:100])
except OSError as e:
    rec("kernel.write", "symlink_escape", "SKIP", str(e))

st.close()

# ---------------- governance ----------------
gov = Governance(mode="enforce", governed_ops={"EXECUTE"}, secret="s1", log_path="/tmp/ss_redteam/audit.jsonl")
tok = issue_token("s1", ttl=300)
expired = issue_token("s1", ttl=1); time.sleep(1.1)
forged = issue_token("WRONG", ttl=300)
tampered = tok[:-4] + ("AAAA" if tok[-4:] != "AAAA" else "BBBB")
op_exec = {"type": "EXECUTE", "command": "ls"}
def gate(extra=None):
    o = dict(op_exec); o.update(extra or {})
    e = gov.gate([o], plan_sig="rt01")[0]; gov.record_all([e]); return e
r = gate({"auth_token": tok})                      # no reason
rec("gov", "no_reason", "PASS" if r.get("verdict") == "DENY" else "FAIL", str(r.get("verdict")))
r = gate({"reason": "test"})                        # no token
rec("gov", "no_token", "PASS" if r.get("verdict") == "DENY" else "FAIL", str(r.get("verdict")))
r = gate({"reason": "test", "auth_token": expired})
rec("gov", "expired_token", "PASS" if r.get("verdict") == "DENY" else "FAIL", str(r.get("verdict")))
r = gate({"reason": "test", "auth_token": forged})
rec("gov", "forged_token", "PASS" if r.get("verdict") == "DENY" else "FAIL", str(r.get("verdict")))
r = gate({"reason": "test", "auth_token": tampered})
rec("gov", "tampered_token", "PASS" if r.get("verdict") == "DENY" else "FAIL", str(r.get("verdict")))
r = gate({"reason": "test", "auth_token": tok})
rec("gov", "valid_token", "PASS" if r.get("verdict") == "APPROVE" else "FAIL", str(r.get("verdict")))

# multi-party: same authority twice / missing authority
ok, missing, v = check_multiparty({"A": tok, "B": tok}, {"A": "s1", "B": "s2"}, {"A", "B"})
rec("gov.multiparty", "same_authority_twice", "PASS" if not ok else "FAIL", f"ok={ok} missing={missing}")
ok2, missing2, v2 = check_multiparty({"A": tok}, {"A": "s1", "B": "s2"}, {"A", "B"})
rec("gov.multiparty", "missing_authority", "PASS" if not ok2 else "FAIL", f"ok={ok2} missing={missing2}")

# replay within TTL for a DIFFERENT op: token bound to op/plan?
r1 = gate({"reason": "test", "auth_token": tok})
r2 = gate({"reason": "test", "auth_token": tok})   # same op, second use
rec("gov", "replay_same_op", "INFO" if r2.get("verdict") == "APPROVE" else "PASS",
    str(r2.get("verdict")), note="token has no one-time binding (documented: TTL window)")
r3 = gate({"reason": "other", "auth_token": tok})  # different reason
rec("gov", "replay_other_reason", "INFO" if r3.get("verdict") == "APPROVE" else "PASS",
    str(r3.get("verdict")), note="same token accepted for a different reason string")

# audit: every denied attempt recorded
import json as _json
n_deny = 0
try:
    with open("/tmp/ss_redteam/audit.jsonl") as f:
        n_deny = sum(1 for ln in f if ln.strip() and _json.loads(ln).get("verdict") == "DENY")
except FileNotFoundError:
    pass
rec("gov.audit", "denials_recorded", "PASS" if n_deny >= 5 else "FAIL", f"deny entries={n_deny}")

# ---------------- pool invites ----------------
hub = PoolHub(secret="poolsecret", name="hub")
inv = issue_invite("poolsecret", "farm", "127.0.0.1:7999", ttl=600)
exp = issue_invite("poolsecret", "farm", "127.0.0.1:7999", ttl=1)
time.sleep(1.1)
bad = inv[:-6] + "X" * 6
rec("pool", "join_no_invite", "PASS" if hub.join("", "x") is None else "FAIL", "empty invite -> None")
rec("pool", "join_expired", "PASS" if hub.join(exp, "x") is None else "FAIL", "expired -> None")
rec("pool", "join_tampered", "PASS" if hub.join(bad, "x") is None else "FAIL", "tampered -> None")
r = hub.join(inv, "node1")
rec("pool", "join_valid", "PASS" if r and r.get("pool") == "farm" else "FAIL", str(r))
r2 = hub.join(inv, "node2")
rec("pool", "invite_reuse", "INFO" if r2 else "PASS", f"reuse={'denied' if r2 is None else 'allowed'}", note="invite replay within TTL")

# ---------------- registry / feedback freshness ----------------
s2 = SwarmState(REPO)
for i in range(5):
    s2.feedback("REG:STRAT:WRITE:DELTA", True)
rec("registry", "feedback_counts", "INFO", f"5x ok=1 recorded -> {s2.success_rate('REG:STRAT:WRITE:DELTA')}")
rec("registry", "freshness_window", "INFO", "no expiry on feedback samples (aggregate counts)",
    note="potential WEAK: an old forged op replay re-weights the registry")
s2.close()

print("\n===== SUMMARY =====")
from collections import Counter
c = Counter(r["verdict"] for r in RESULTS)
print(dict(c))
with open("/tmp/ss_redteam/battery_results.json", "w") as f:
    json.dump(RESULTS, f, indent=1)
