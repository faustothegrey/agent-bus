#!/bin/bash
# agent-bus-wrapper.sh — Launch a CLI agent inside tmux with Agent Bus support.
#
# Usage:
#   agent-bus-wrapper.sh [--name <session>] <agent_name> <cli_command...>
#
# Modes:
#   Interactive (TTY): creates tmux, starts pollers, then attaches you.
#     Ctrl+B D to detach — agent keeps running in background.
#     Reattach anytime with: tmux attach -t <session>
#
#   Headless (no TTY / Hermes launch): creates tmux, starts pollers,
#     prints session info, and keeps running. The user (or Hermes)
#     can reattach with: tmux attach -t <session>
#
# Agent Bus integration:
#   - Hermes sends messages via POST /bus/<agent>/inbox
#   - Agent writes @HERMES{...} for structured replies
#   - Agent requests human help: @HERMES{"needs_human":true,...}
#   - Human response auto-injected into agent's stdin

set -euo pipefail

# ── Parse options ─────────────────────────────────────────────────────
SESSION_NAME_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --name)
      SESSION_NAME_OVERRIDE="$2"
      shift 2
      ;;
    --help|-h)
      echo "Usage: agent-bus-wrapper.sh [--name <session>] <agent_name> <cli...>"
      echo ""
      echo "Examples:"
      echo "  agent-bus-wrapper.sh codex codex"
      echo "  agent-bus-wrapper.sh --name diagramtalk codex codex"
      echo "  agent-bus-wrapper.sh claude claude"
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

AGENT_NAME="${1:?Usage: agent-bus-wrapper.sh [--name <session>] <agent_name> <cli...>}"
shift

BUS_URL="${AGENT_BUS_URL:-http://127.0.0.1:9901}"
LOG_DIR="$HOME/.hermes/agent-logs/$AGENT_NAME"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"

if [ -n "$SESSION_NAME_OVERRIDE" ]; then
  SESSION_NAME="$SESSION_NAME_OVERRIDE"
else
  SESSION_NAME="agent-bus-${AGENT_NAME}-$$"
fi

# ── Start tmux session ────────────────────────────────────────────────
tmux new-session -d -s "$SESSION_NAME" \
  "script -q \"$LOGFILE\" $*"

# Give it a moment to boot
sleep 0.5

# Register session with bus — pass agent type too
curl -s -X POST "$BUS_URL/bus/$AGENT_NAME/session" \
  -H 'Content-Type: application/json' \
  -d "{\"session\":\"$SESSION_NAME\",\"type\":\"$AGENT_NAME\"}" > /dev/null

echo "Agent Bus: $AGENT_NAME"
echo "  Session: $SESSION_NAME"
echo "  Log:     $LOGFILE"
echo "  Reattach: tmux attach -t $SESSION_NAME"

# ── Background poller: check inbox, inject via tmux ───────────────────
_poll_inbox() {
  local name="$1"
  local session="$2"
  local bus="$3"
  while true; do
    msg=$(curl -s "$bus/bus/$name/inbox" 2>/dev/null)
    if [ "$msg" != "null" ] && [ -n "$msg" ]; then
      text=$(echo "$msg" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))" 2>/dev/null)
      from=$(echo "$msg" | python3 -c "import sys,json; print(json.load(sys.stdin).get('from','unknown'))" 2>/dev/null)
      if [ -n "$text" ]; then
        inject_text="[Hermes/$from]: $text"
        tmux send-keys -t "$session" -l "$inject_text"
        tmux send-keys -t "$session" "Enter"
      fi
    fi
    sleep 3
  done
}

# ── Background monitor: watch for @HERMES in pane output ──────────────
_monitor_hermes() {
  set +e  # resilient — no errexit in the monitor
  local name="$1"
  local session="$2"
  local bus="$3"
  local seen_markers="/tmp/agent-bus-${name}-seen-$$.tmp"
  : > "$seen_markers"
  while true; do
    pane=$(tmux capture-pane -t "$session" -p -S -200 2>/dev/null || true)
    if [ -n "$pane" ]; then
      echo "$pane" | grep -o '@HERMES{[^}]*}' 2>/dev/null | while read -r match; do
        marker=$(echo "$match" | md5 -q 2>/dev/null || echo "$match" | md5sum 2>/dev/null | cut -d' ' -f1)
        [ -z "$marker" ] && continue
        if ! grep -qs "$marker" "$seen_markers" 2>/dev/null; then
          echo "$marker" >> "$seen_markers"
          payload=$(echo "$match" | sed 's/^@HERMES//')
          [ -n "$payload" ] && curl -s -X POST "$bus/bus/$name/outbox" \
            -H 'Content-Type: application/json' \
            -d "$payload" > /dev/null
        fi
      done
    fi
    sleep 2
  done
  rm -f "$seen_markers" 2>/dev/null || true
}

# Launch pollers in background
_poll_inbox "$AGENT_NAME" "$SESSION_NAME" "$BUS_URL" &
POLLER_PID=$!

_monitor_hermes "$AGENT_NAME" "$SESSION_NAME" "$BUS_URL" &
MONITOR_PID=$!

# Cleanup on exit
_cleanup() {
  kill "$POLLER_PID" "$MONITOR_PID" 2>/dev/null || true
  # Deregister session
  curl -s -X POST "$BUS_URL/bus/$AGENT_NAME/session" \
    -H 'Content-Type: application/json' \
    -d '{"session":null}' > /dev/null 2>&1 || true
  echo "Agent Bus: $AGENT_NAME session ended"
}
trap _cleanup EXIT INT TERM

# ── Mode switch ───────────────────────────────────────────────────────
if [ -t 1 ]; then
  # Interactive: running in a real terminal — attach the user
  echo "  (attaching — Ctrl+B D to detach, agent keeps running)"
  tmux attach-session -t "$SESSION_NAME"
else
  # Headless: launched by Hermes or a script — just wait
  echo "  (headless mode — use 'tmux attach -t $SESSION_NAME' to interact)"
  # Block forever so the wrapper keeps running (and pollers stay alive)
  while true; do
    # Check if tmux session is still alive
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Agent Bus: $AGENT_NAME tmux session died, exiting."
      exit 0
    fi
    sleep 10
  done
fi
