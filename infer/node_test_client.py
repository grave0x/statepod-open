#!/usr/bin/env python3
"""SwarmState node round-trip test: send a plan req, wait for the resp line.

Usage: node_test_client.py [query] [timeout_s]
Connects to 127.0.0.1:7799, sends {"type":"req","id":"t1","query":...},
prints the resp object (or NO RESPONSE). Bounded; exits cleanly on EOF.
"""
import socket, time, json, sys

def drain(buf: bytearray):
    """Return the resp obj if a complete line matches; else None."""
    while b"\n" in buf:
        line, rest = buf.split(b"\n", 1)
        buf[:] = rest
        line = line.decode(errors="replace").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") == "resp" and obj.get("id") == "t1":
            return obj
    return None

def send_req(query, port=7799, timeout=600):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.settimeout(timeout)
    msg = json.dumps({"type": "req", "id": "t1", "query": query}) + "\n"
    s.sendall(msg.encode())
    buf = bytearray()
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        done = drain(buf)
        if done:
            s.close()
            return done
    done = drain(buf)  # process anything buffered at EOF/timeout
    s.close()
    return done

if __name__ == "__main__":
    t0 = time.time()
    q = sys.argv[1] if len(sys.argv) > 1 else \
        "Generate a plan to find where the WRITE op is handled in kernel.c, strategy DELTA"
    to = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    resp = send_req(q, 7799, to)
    print("ELAPSED %.1fs" % (time.time() - t0))
    if resp is None:
        print("NO RESPONSE (timeout %ds)" % to)
    else:
        print("OK", resp.get("ok"))
        plan = resp.get("plan")
        print("PLAN", json.dumps(plan)[:800] if plan is not None else None)
