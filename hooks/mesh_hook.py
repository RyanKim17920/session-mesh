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


def watch(sid):
    """Optional asyncRewake fallback for Claude Code versions without native cross-session messaging.
    Off unless CLAUDE_MESH_WAKE=1. Polls the inbox every 1s (max lifetime CLAUDE_MESH_WAKE_MAX, default 6h);
    exits 2 with the framed mail on stderr, which wakes the idle session. One watcher per session (flock)."""
    import fcntl
    import meshlib as m
    if os.environ.get("CLAUDE_MESH_WAKE") != "1":
        return
    os.makedirs(m.p("watch"), mode=0o700, exist_ok=True)
    lock = open(m.p("watch", sid + ".lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    end = time.time() + float(os.environ.get("CLAUDE_MESH_WAKE_MAX") or 6 * 3600)
    while time.time() < end:
        rec = m.read_json(m.peer_path(sid))
        if rec is None or (rec.get("claude_pid") and not m.pid_alive(rec["claude_pid"])):
            return
        if m.inbox_files(sid):
            msgs, _ = m.take_inbox(sid, mark_read=True, budget=m.HOOK_BUDGET)
            if msgs:
                sys.stderr.write("You have %d new session-mesh message(s) from other Claude sessions:\n\n%s\n"
                                 % (len(msgs), "\n\n".join(msgs)))
                sys.stderr.flush()
                os._exit(2)
        time.sleep(1)


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
        sock = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET")
        if sock:                                      # Claude Code's own inbox: wakes this session when idle
            m.save_native(sid, sock, os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN"))
        note = ""
        try:
            st = m.ensure_ssh_config()
            if st == "applied":
                note = (" NOTE for the user: session-mesh added a managed block to the TOP of %s (it Includes %s; "
                        "backup: <config>.session-mesh.bak) so ssh connections reverse-forward the mesh relay. "
                        "Reconnect existing ssh sessions once. Remove it with `meshctl ssh-setup --remove`."
                        % (m.user_ssh_config(), m.p("ssh_config")))
            elif st.startswith("rolled back"):
                print("[session-mesh hook] ssh config: %s" % st, file=sys.stderr)
        except Exception as e:
            print("[session-mesh hook] ssh config: %r" % (e,), file=sys.stderr)
        try:
            m.relay_ensure()          # detached, returns immediately
        except Exception as e:
            print("[session-mesh hook] relay: %r" % (e,), file=sys.stderr)
        emit(event, (
            "session-mesh is active. This session's mesh id is %s (name %r, host %s). "
            "Other Claude Code sessions of the same user can be listed with mesh_list_peers and messaged "
            "with mesh_send. Incoming messages appear as framed peer-provided data at the start of a turn. "
            "If mesh_whoami cannot resolve this session, pass as_id=%s to the mesh_* tools."
            % (sid, rec["name"], rec["host"], sid) + note))
    elif event in ("UserPromptSubmit", "PostToolUse"):
        path = m.peer_path(sid)
        rec = m.read_json(path)
        if not rec or not rec.get("claude_pid") or time.time() - float(rec.get("last_seen") or 0) > 15:
            m.register(sid, cwd=cwd, claude_pid=(rec or {}).get("claude_pid") or m.find_claude_pid())
        if os.environ.get("CLAUDE_MESH_PULL") != "0":
            try:
                due = m.pull_due_hosts()        # stamp files: at most one ssh round per host per 30s
                if due:
                    m.spawn_pull(due)           # detached child; the hook never waits on ssh
            except Exception as e:
                print("[session-mesh hook] pull: %r" % (e,), file=sys.stderr)
        msgs, more = m.take_inbox(sid, mark_read=True, budget=m.HOOK_BUDGET)
        if msgs:
            head = "You have %d new session-mesh message(s) from other Claude sessions:\n\n" % len(msgs)
            tail = ("\n\n(%d more unread message(s) remain; call mesh_read_inbox.)" % more) if more else ""
            emit(event, head + "\n\n".join(msgs) + tail)
    elif event == "Watch":
        watch(sid)
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
