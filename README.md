# session-mesh

A Claude Code plugin that lets your own Claude Code sessions (other terminals, cluster nodes, your laptop) discover each other and exchange short messages, with replies. Pure Python 3 standard library, no dependencies, Linux and macOS (WSL works like Linux).

## Laptop -> cluster (most common)
You sit at a laptop (often macOS, no sshd, behind NAT) and `ssh` into a cluster login node. Two steps, then it just works in both directions over that one ssh connection:

1. **Install the plugin on both machines** (laptop and cluster):
   ```
   /plugin marketplace add RyanKim17920/session-mesh
   /plugin install session-mesh@session-mesh
   ```
   Optional CLI: `meshctl` lives in the plugin directory (`bin/meshctl`, under `~/.claude/plugins/` for an installed plugin). Either symlink it, e.g. `ln -s <plugin dir>/bin/meshctl ~/.local/bin/meshctl`, or `git clone https://github.com/RyanKim17920/session-mesh` and link `bin/meshctl` from there. Nothing below needs it.
2. **Reconnect your ssh session once** (already-open connections predate the install and have no forward). Open a new Claude Code session on the laptop first (the SessionStart hook sets everything up), then `ssh cluster` as usual.

That is all. On the next ssh connection the laptop's relay socket is reverse-forwarded to the cluster, the cluster side connects back over the same socket, and `mesh_list_peers` on either side shows the other side's sessions (`via link:` on the laptop, `via relay:` on the cluster).

What the SessionStart hook does on each machine, idempotently and in the background:
- starts a small relay daemon (`~/.claude-mesh/relay.sock`, mode 0600);
- writes `~/.claude-mesh/ssh_config` (`RemoteForward /tmp/cmesh-%r-<this host>.sock <relay.sock>` + `StreamLocalBindUnlink yes`, for every host except github/gitlab/bitbucket/azure-devops/amazonaws) and puts a managed block at the very top of `~/.ssh/config`:
  ```
  # >>> session-mesh >>>
  Include ~/.claude-mesh/ssh_config
  # <<< session-mesh <<<
  ```
  The original is backed up once to `~/.ssh/config.session-mesh.bak`. The result is validated with `ssh -G`; if it fails the change is rolled back immediately. The first time it applies, Claude tells you in the SessionStart context.
- **Opt out / remove:** `CLAUDE_MESH_NO_SSH_CONFIG=1`, or `meshctl ssh-setup --remove` (deletes the block and records an opt-out marker; deleting the block by hand is respected too). `meshctl ssh-setup --status` shows the state and `--enable` turns it back on. With a custom `CLAUDE_MESH_DIR` it never touches `~/.ssh/config` unless `CLAUDE_MESH_SSH_USER_CONFIG` names a file.
- if a live ssh ControlMaster to a host in `~/.claude-mesh/hosts` has no forward yet, the hook's background pull attaches it with `ssh -O forward` (no reconnect). Connections that are not ControlMaster-shared pick the forward up on their next reconnect.

Needs OpenSSH >= 8 for the `%r` token (the remote user name); on older clients a fixed `/tmp/cmesh-<local user>-<host>.sock` is used, which only works when the remote user name equals the local one. The remote sshd must allow `AllowStreamLocalForwarding` (default yes).

## How delivery works (layers)
1. **Native socket (wakes idle sessions).** Claude Code >= 2.1.224 gives each session an inbox socket (`CLAUDE_CODE_MESSAGING_SOCKET`, optional token `CLAUDE_CODE_MESSAGING_TOKEN`). The SessionStart hook records them in a local-only file (`~/.claude-mesh/native/<sid>.sock|.token`, token 0600; never in `peers/*.json`, never sent over ssh/relay/list output). On the machine that holds the target session, the final hop posts `{"type":"user","message":{"role":"user","content":...}}` (preceded by the documented auth line when a token exists) to that socket, so an idle session starts a turn and a busy one reads it between tool calls. The text keeps our untrusted-data frame plus sender@host and a reply hint. On success the message is also recorded under `inbox/<sid>/read/` so hooks do not repeat it. If the socket is missing/refused or Claude is too old, the message goes to the inbox file instead. Claude Code does not ack synchronously; if the receiver does write back a refusal/hold it is reported as the `mesh_send` result. The receiver's `crossSessionInbound` setting (accept/hold/refuse) decides acceptance; with its default, a session in bypass-permissions mode shows an approval dialog for each message. Set `CLAUDE_MESH_NATIVE=0` to disable this layer.
2. **Hook injection (next turn).** Inbox files are surfaced by UserPromptSubmit/PostToolUse hooks, framed as untrusted data (capped at 4 KB, control characters stripped).
3. **Optional asyncRewake watcher (older Claude versions without native messaging).** A documented `asyncRewake` command hook (SessionStart/Stop) polls the inbox every second and exits 2 with the framed mail, which wakes an idle session. Off by default: set `CLAUDE_MESH_WAKE=1` in the environment Claude is started from (max lifetime `CLAUDE_MESH_WAKE_MAX` seconds, default 6h). Not needed when layer 1 works. Not verified end to end against a live idle session.
4. **Transports between machines**, in order of preference: same filesystem (shared `~`), the **laptop link** (the held ssh `-R` socket, both ways), **ssh hosts** (`~/.claude-mesh/hosts`, key auth, `meshctl hosts add`), and the **pull fallback** (below). Dedupe is by session id with precedence local > relay > link > ssh > pull.

Replies: every surfaced message ends with a hint `mesh_send(to="<from_id>", message=..., reply_to="<id>")`. Messages carry `thread` and `reply_to` ids, and the reply is routed over whatever path currently reaches the sender.

This complements Claude Code's built-in `ListAgents`/`SendMessage`, which only reaches other machines through Remote Control (claude.ai sign-in); session-mesh works over plain ssh and with any provider.

## Other ways to set up the forward (optional)
- `meshctl ssh <host> [ssh args...]`: ensures the relay is running, adds the host to your `hosts` file and execs `ssh -o StreamLocalBindUnlink=yes -R /tmp/cmesh-<user>-<host>.sock:<relay.sock> <host> ...`.
- `meshctl ssh-setup <host> [--apply] [--config PATH]`: prints (or with `--apply` appends) a per-host `RemoteForward` snippet.
- `meshctl attach <host>`: for an already-open connection. With a ControlMaster it runs `ssh -O forward -R ...` live; otherwise it prints the `~C` escape recipe (type `~C` at the start of a line and enter the `-R` spec; OpenSSH >= 9.2 needs `EnableEscapeCommandline yes`).

## Pull fallback (when the sshd forbids stream-local forwarding)
Y's hooks (UserPromptSubmit/PostToolUse, at most once per 30 s per host, in a detached child so the hook returns in milliseconds) ssh to each entry of `hosts` over a ControlMaster (`ControlPersist=10m`, BatchMode) and do two things: publish Y's local peers into X's `~/.claude-mesh/remote_peers/<Yhost>.json` (X lists them `via pull`), and collect X's `outbox/<Y-session>/*.json` over `tar`, delivering locally first and deleting on X only after a successful write. X queues messages for pull peers in that outbox. Latency is up to ~30 s after Y's next activity.

## Tools and CLI
Tools: `mesh_whoami`, `mesh_set_name`, `mesh_list_peers` (shows `via` per peer), `mesh_send(to, message, reply_to?)`, `mesh_read_inbox`.
CLI: `meshctl list [--all] | send <to> <msg> | inbox <sid> [--mark] | gc | hosts add <alias> | relay [--stop] | ssh | ssh-setup | attach | pull`.
State (`~/.claude-mesh`, or `CLAUDE_MESH_DIR`; dir mode 0700): `peers/`, `inbox/<sid>/` (read mail in `read/`), `by_pid/`, `native/`, `hosts`, `outbox/`, `remote_peers/`, `relay.sock`, `relay.log`.
Other env: `CLAUDE_MESH_SSH_CONFIG` (`-F` file for the ssh calls), `CLAUDE_MESH_SSH_OPTS`, `CLAUDE_MESH_REMOTE_DIR`, `CLAUDE_MESH_RELAY=0` (no relay), `CLAUDE_MESH_PULL=0`, `CLAUDE_MESH_LINK=0` (do not connect to forwarded sockets), `CLAUDE_MESH_LINK_DIR`, `CLAUDE_MESH_SOCK_DIR`.

## Security
- The forwarded socket on the far side is created by sshd with mode 0600 in `/tmp`; only your uid there (and root) can connect, and the relay refuses sockets not owned by you. Anyone who can write to your `~/.claude-mesh` or reach your relay socket can message your sessions; so can root on either machine.
- Messages are untrusted data: framed, capped at 4 KB, control characters stripped, and the bundled skill tells Claude to confirm consequential requests with you. The relay validates `to` against local session ids and rejects path traversal and requests over 64 KB.
- The native inbox token never leaves the machine.
- The managed ssh config forwards your relay to every ssh server you connect to (except git hosts); the far side's root could talk to your relay. Remove the block (`meshctl ssh-setup --remove`) if that is not acceptable.

## Limitations
- Mail to an idle session is woken only through the native socket (Claude Code >= 2.1.224) or the optional watcher; otherwise it is seen on the next turn.
- Connections opened before the install need one reconnect. Messages to ssh-hosts peers are written as files (no native wake) unless the link or relay carries them.
- With a shared home directory across machines, give each machine its own `CLAUDE_MESH_DIR`, since `relay.sock` is per state dir and a unix socket is not shared across hosts.
- Identity binding walks parent processes (via `/proc`, or `ps` where `/proc` does not exist, e.g. macOS) to a process whose name contains `claude`; if not found, pass `as_id` (shown in the SessionStart context).
- Staleness is judged by `last_seen` (2h) and, locally, process liveness.

## Related projects
- [louislva/claude-peers-mcp](https://github.com/louislva/claude-peers-mcp): local peer discovery over a broker plus the dev-channel flag; session-mesh needs no broker, no dev-channel flag, and reaches other machines.
- [yilunzhang/claude-code-inter-session](https://github.com/yilunzhang/claude-code-inter-session): inter-session messaging on one machine; session-mesh adds ssh-native cross-machine links.
- [craftale-agency/interserver](https://github.com/craftale-agency/interserver): a server-based relay between sessions; session-mesh is serverless and rides your existing ssh connection (no Tailscale, no hosted component).
- [Dicklesworthstone/mcp_agent_mail_rust](https://github.com/Dicklesworthstone/mcp_agent_mail_rust): a mailbox service for coding agents; session-mesh is a small stdlib plugin that uses Claude Code's own inbox socket for wake-up.

## Tests
`python3 tests/test_sim.py` (two-session simulation) and `python3 tests/test_relay.py` (relay, native socket against a fake inbox, managed ssh config on temp files, and real ssh to localhost with a throwaway ssh config; the ssh part is skipped when key auth to localhost is unavailable).

## License
MIT
