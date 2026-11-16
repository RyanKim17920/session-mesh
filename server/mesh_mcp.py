#!/usr/bin/env python3
"""session-mesh MCP stdio server (hand-rolled JSON-RPC, newline-delimited). Logs to stderr only."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "lib"))
import meshlib as m  # noqa: E402

AS_ID = {"as_id": {"type": "string", "description": "Optional: this session's mesh id; only needed if pid binding fails (see SessionStart context)."}}


def schema(props, req=()):
    return {"type": "object", "properties": dict(props, **AS_ID), "required": list(req)}


TOOLS = [
    ("mesh_whoami", "Show this session's session-mesh id, name, host and how identity was resolved.", schema({})),
    ("mesh_set_name", "Rename this session on the mesh (letters, digits, . _ -; max 40 chars).",
     schema({"name": {"type": "string"}}, ["name"])),
    ("mesh_list_peers", "List other Claude Code sessions on this machine/filesystem and on SSH hosts in ~/.claude-mesh/hosts.",
     schema({"include_stale": {"type": "boolean", "default": False}})),
    ("mesh_send", "Send a short message (max 4 KB) to another session by name or session-id prefix.",
     schema({"to": {"type": "string"}, "message": {"type": "string"}}, ["to", "message"])),
    ("mesh_read_inbox", "Read unread session-mesh messages for this session.",
     schema({"mark_read": {"type": "boolean", "default": True}})),
]


def whoami(a):
    sid, how = m.resolve_identity(a.get("as_id"))
    if not sid:
        raise ValueError("cannot determine own session: %s. Pass as_id=<mesh id from SessionStart context>." % how)
    return sid, how


def t_whoami(a):
    sid, how = whoami(a)
    rec = m.read_json(m.peer_path(sid)) or {}
    return json.dumps(dict(rec or {"session_id": sid}, resolved_via=how, state_dir=m.state_dir()), indent=2)


def t_set_name(a):
    sid, _ = whoami(a)
    name = str(a.get("name", ""))
    if not m.NAME_RE.match(name):
        raise ValueError("invalid name; use 1-40 chars of letters, digits, '.', '_', '-'")
    rec = m.read_json(m.peer_path(sid))
    if rec is None:
        rec = m.register(sid, claude_pid=m.find_claude_pid())
    rec["name"] = name
    rec["last_seen"] = int(time.time())
    m.atomic_write(m.peer_path(sid), json.dumps(rec))
    return "name set to %s" % name


def t_list(a):
    try:
        me, _ = m.resolve_identity(a.get("as_id"))
    except ValueError:
        me = None
    peers, errs = m.all_peers(include_stale=bool(a.get("include_stale")))
    lines = []
    for r in peers:
        lines.append("%s  %-24s %-14s %s%s%s%s" % (
            r["session_id"][:8], r.get("name"), r.get("host"), r.get("cwd"),
            "  [via ssh %s]" % r["via"] if r["via"] else "",
            "  [stale]" if r["stale"] else "",
            "  <- you" if r["session_id"] == me else ""))
    out = "\n".join(lines) or "(no peers)"
    if errs:
        out += "\n\nssh problems:\n" + "\n".join(errs)
    return out


def t_send(a):
    sid, _ = whoami(a)
    rec = m.read_json(m.peer_path(sid)) or {}
    return m.send(str(a.get("to", "")), str(a.get("message", "")), sid, rec.get("name") or sid[:8])


def t_inbox(a):
    sid, _ = whoami(a)
    msgs, _ = m.take_inbox(sid, mark_read=a.get("mark_read", True) is not False)
    return "\n\n".join(msgs) if msgs else "(inbox empty)"


HANDLERS = {"mesh_whoami": t_whoami, "mesh_set_name": t_set_name, "mesh_list_peers": t_list,
            "mesh_send": t_send, "mesh_read_inbox": t_inbox}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def handle(req):
    method, id_ = req.get("method"), req.get("id")
    if id_ is None:  # notification
        return
    if method == "initialize":
        pv = (req.get("params") or {}).get("protocolVersion") or "2024-11-05"
        reply(id_, {"protocolVersion": pv, "capabilities": {"tools": {}},
                    "serverInfo": {"name": "session-mesh", "version": "0.1.0"}})
    elif method == "ping":
        reply(id_, {})
    elif method == "tools/list":
        reply(id_, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, d, s in TOOLS]})
    elif method == "tools/call":
        p = req.get("params") or {}
        h = HANDLERS.get(p.get("name"))
        if not h:
            return reply(id_, error={"code": -32602, "message": "unknown tool %r" % p.get("name")})
        try:
            reply(id_, {"content": [{"type": "text", "text": h(p.get("arguments") or {})}]})
        except Exception as e:
            reply(id_, {"content": [{"type": "text", "text": "error: %s" % e}], "isError": True})
    else:
        reply(id_, error={"code": -32601, "message": "method not found: %s" % method})


def main():
    m.ensure_dirs()
    m.log("server up, state dir", m.state_dir())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except Exception as e:
            m.log("error handling line:", repr(e))


if __name__ == "__main__":
    main()
