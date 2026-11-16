#!/usr/bin/env python3
"""Two-session simulation (stdlib only): hooks register peers, MCP server resolves identity by pid
binding, a message is delivered and surfaced by the UserPromptSubmit hook."""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "mesh_hook.py")
SERVER = os.path.join(ROOT, "server", "mesh_mcp.py")
CTL = os.path.join(ROOT, "bin", "meshctl")


def run(argv, env, stdin=""):
    r = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, timeout=30)
    return r.stdout, r.returncode


def hook(event, sid, env):
    return run([sys.executable, HOOK, event], env, json.dumps({"session_id": sid, "cwd": "/tmp"}))[0]


def call(env, name, args=None):
    reqs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}]
    out, _ = run([sys.executable, SERVER], env, "\n".join(json.dumps(r) for r in reqs) + "\n")
    last = json.loads(out.strip().splitlines()[-1])
    return last["result"]["content"][0]["text"]


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main():
    tmp = tempfile.mkdtemp(prefix="mesh-sim-")
    base = dict(os.environ, CLAUDE_MESH_DIR=tmp)
    a = dict(base, CLAUDE_MESH_CLAUDE_PID=str(os.getpid()))        # pid override stands in for "claude" ancestor
    b = dict(base, CLAUDE_MESH_CLAUDE_PID=str(os.getppid()))
    sa, sb = "aaaa1111-session-a", "bbbb2222-session-b"
    check("session-mesh is active" in hook("SessionStart", sa, a), "SessionStart A emits context")
    check("session-mesh is active" in hook("SessionStart", sb, b), "SessionStart B emits context")
    who = json.loads(call(a, "mesh_whoami"))
    check(who["session_id"] == sa and "pid-binding" in who["resolved_via"], "A whoami resolves by pid binding")
    peers = call(a, "mesh_list_peers")
    check("bbbb2222" in peers and "<- you" in peers, "A lists B and marks itself")
    check("delivered" in call(a, "mesh_send", {"to": sb[:8], "message": "hello from A"}), "A sends to B")
    out = hook("UserPromptSubmit", sb, b)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    check("hello from A" in ctx and "peer-provided data" in ctx, "B's hook surfaces framed message")
    check(hook("UserPromptSubmit", sb, b) == "", "message not surfaced twice")
    out, rc = run([sys.executable, CTL, "send", sb[:8], "from meshctl"], base)
    check(rc == 0 and "delivered" in out, "meshctl send works")
    check("from meshctl" in call(b, "mesh_read_inbox"), "B reads meshctl message via mesh_read_inbox")
    hook("SessionEnd", sa, a)
    check(not os.path.exists(os.path.join(tmp, "peers", sa + ".json")), "SessionEnd unregisters A")
    print("ALL PASS")


main()
