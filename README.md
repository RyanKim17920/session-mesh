# session-mesh

A Claude Code plugin that lets your own Claude Code sessions (other terminals, cluster nodes, other machines over SSH) discover each other and exchange short messages. Pure Python 3 standard library, no dependencies.

## Install
```
/plugin marketplace add RyanKim17920/session-mesh
/plugin install session-mesh@session-mesh
```
For a single run from a local checkout: `claude --plugin-dir /path/to/session-mesh`.
Optional CLI: `ln -s /path/to/session-mesh/bin/meshctl ~/.local/bin/meshctl`.

## What it does
- Each session registers itself (SessionStart hook) in `~/.claude-mesh/` (override with `CLAUDE_MESH_DIR`; dir mode 0700).
- Claude gets MCP tools to list peers and message them. Incoming mail is injected on the receiver's next prompt (UserPromptSubmit) or after its next tool call (PostToolUse).
- State layout: `peers/<sid>.json`, `inbox/<sid>/*.json` (read mail moves to `read/`), `by_pid/<host>-<pid>` (session binding), `hosts` (SSH aliases).

## Tools
`mesh_whoami`, `mesh_set_name`, `mesh_list_peers`, `mesh_send`, `mesh_read_inbox`.

## meshctl
`meshctl list [--all] | send <to> <msg> | inbox <sid> [--mark] | gc | hosts add <alias>`

## Shared filesystem vs SSH
If `~/` is the same network mount on every machine, sessions already see each other through `~/.claude-mesh`; no SSH needed. For machines with a separate home, add SSH aliases to `~/.claude-mesh/hosts`, one per line (`meshctl hosts add gpu-node-1`). Requirements:
- Passwordless key auth; connections use `BatchMode=yes`, so no password or passphrase prompts. Test with `ssh -o BatchMode=yes gpu-node-1 true`.
- The host key must already be valid in `known_hosts`.
- The same state dir path (`~/.claude-mesh`, or `CLAUDE_MESH_REMOTE_DIR`) is assumed on the remote, and the remote login shell must be POSIX.
- Delivery only writes files over ssh, but the remote machine also needs this plugin installed for its sessions to read their mail.

## Security
Peer messages are framed as untrusted data (capped at 4 KB, control characters stripped), and the bundled skill tells Claude to confirm consequential requests with you instead of obeying them. Anyone with write access to `~/.claude-mesh` (or who can ssh as you to a listed host) can message your sessions. The state dir is created 0700.

## Limitations
- An idle session does not wake up; it receives mail on its next turn (or if it calls `mesh_read_inbox`).
- Identity binding walks `/proc` parents to a process whose argv[0] basename contains `claude`; if not found, pass `as_id` (shown in the SessionStart context). Linux only.
- Remote staleness is judged by `last_seen` only (2h). Heartbeats happen only on prompts and tool calls.

## Tests
`python3 tests/test_sim.py` runs a two-session simulation in a temp dir.

## License
MIT
