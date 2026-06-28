#!/bin/bash
# wrapper.sh — Launch a CLI agent inside tmux with session logging.
# Senior wrapper form.
#
# Usage:
#   wrapper.sh [--name <session>] <agent_name> <cli_command...>
#
# Modes:
#   Interactive (TTY): creates tmux, then attaches you.
#     Ctrl+B D to detach — agent keeps running in background.
#     Reattach anytime with: tmux attach -t <session>
#
#   Headless: launched by agentctl or a script — stays alive,
#     prints session info, exits when tmux dies.
#
# Logs everything to ~/.hermes/agent-logs/<agent>/<timestamp>.log

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
      echo "Usage: wrapper.sh [--name <session>] <agent_name> <cli...>"
      echo ""
      echo "Avvia un agente CLI in tmux con logging."
      echo "  tmux attach -t <session>  per interagire"
      echo "  Ctrl+B D                  per staccarti (agente continua)"
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

AGENT_NAME="${1:?Usage: wrapper.sh [--name <session>] <agent_name> <cli...>}"
shift

LOG_DIR="$HOME/.hermes/agent-logs/$AGENT_NAME"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"

if [ -n "$SESSION_NAME_OVERRIDE" ]; then
  SESSION_NAME="$SESSION_NAME_OVERRIDE"
else
  SESSION_NAME="agent-${AGENT_NAME}-$$"
fi

# ── Start tmux session ────────────────────────────────────────────────
tmux new-session -d -s "$SESSION_NAME" \
  "script -q \"$LOGFILE\" $*"

echo "Agent: $AGENT_NAME"
echo "  Session: $SESSION_NAME"
echo "  Log:     $LOGFILE"
echo "  Reattach: tmux attach -t $SESSION_NAME"

# ── Mode switch ───────────────────────────────────────────────────────
if [ -t 1 ]; then
  # Interactive: running in a real terminal — attach the user
  echo "  (attaching — Ctrl+B D to detach, agent keeps running)"
  tmux attach-session -t "$SESSION_NAME"
else
  # Headless: launched by agentctl or a script — just wait
  echo "  (headless mode — use 'tmux attach -t $SESSION_NAME' to interact)"
  while true; do
    if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
      echo "Agent: $AGENT_NAME tmux session died, exiting."
      exit 0
    fi
    sleep 10
  done
fi
