# Common False Positives — agentctl Health Check

## 1. Antigravity IDE / VS Code ChatGPT Extension Codex

**Pattern:** `agentctl health --json` reports a codex orphan duplicate.

**Symptom:** A codex process with `app-server` in its command line and PPID pointing to Antigravity IDE (VS Code fork) utility processes.

**Example:**
```
PID 71480, PPID 71306
Comm: .../.antigravity-ide/extensions/openai.chatgpt-*/bin/macos-x86_64/codex app-server
```

**Cause:** The "ChatGPT by OpenAI" VS Code extension (bundled with Antigravity IDE) runs its own embedded codex binary (`codex app-server`) for inline editor features. This is unrelated to agentctl-managed codex sessions.

**How to verify:**
```bash
# Check PPID chain — if it leads to Antigravity IDE, it's the extension
ps -p 71480 -o pid,ppid,command
ps -p 71306 -o pid,ppid,command  # should show Antigravity IDE Helper
```

**Action:** None — this is expected behavior. Do not kill it; the IDE depends on it.

---

## 2. Transient tmux Server Orphans

**Pattern:** A codex process briefly appears as orphan during tmux session creation.

**Symptom:** The process has a valid PPID and parent chain back to tmux, but agentctl's PID snapshot didn't pick it up yet (race condition at spawn time).

**How to verify:** Re-run `agentctl health --json` after 30 seconds — the process typically resolves to `in_tmux: true` once tmux fully initializes.

**Action:** Re-check. If still showing as orphan after 2 consecutive checks, investigate further.

---

## 3. Client tmux attach processes

**Pattern:** A transient `tmux attach` or `tmux a` process shows during health check.

**Symptom:** Process with `comm=tmux` and cmdline containing `attach`, PPID != tmux server.

**Action:** None — this is the user or another script attaching to a running session. The process exits once the attachment completes.
