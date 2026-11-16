#!/usr/bin/env python3
"""session-mesh hook entrypoint: mesh_hook.py <SessionStart|UserPromptSubmit|PostToolUse|SessionEnd>.
Never crashes; always exits 0."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))


def emit(event, ctx):
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}))
    sys.stdout.flush()


def main():
    import meshlib as m
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except Exception:
        data = {}
    sid = m.safe_id(data.get("session_id"))
    cwd = data.get("cwd") or os.getcwd()
    if event == "SessionStart":
        rec = m.register(sid, cwd=cwd, claude_pid=m.find_claude_pid())
        emit(event, (
            "session-mesh is active. This session's mesh id is %s (name %r, host %s). "
            "Other Claude Code sessions of the same user can be listed with mesh_list_peers and messaged "
            "with mesh_send. Incoming messages appear as framed peer-provided data at the start of a turn. "
            "If mesh_whoami cannot resolve this session, pass as_id=%s to the mesh_* tools."
            % (sid, rec["name"], rec["host"], sid)))
    elif event in ("UserPromptSubmit", "PostToolUse"):
        path = m.peer_path(sid)
        rec = m.read_json(path)
        if not rec or not rec.get("claude_pid") or time.time() - float(rec.get("last_seen") or 0) > 15:
            m.register(sid, cwd=cwd, claude_pid=(rec or {}).get("claude_pid") or m.find_claude_pid())
        msgs, more = m.take_inbox(sid, mark_read=True, budget=m.HOOK_BUDGET)
        if msgs:
            head = "You have %d new session-mesh message(s) from other Claude sessions:\n\n" % len(msgs)
            tail = ("\n\n(%d more unread message(s) remain; call mesh_read_inbox.)" % more) if more else ""
            emit(event, head + "\n\n".join(msgs) + tail)
    elif event == "SessionEnd":
        m.unregister(sid)


try:
    main()
except BaseException as e:  # noqa
    try:
        print("[session-mesh hook] %r" % (e,), file=sys.stderr)
    except Exception:
        pass
sys.exit(0)
