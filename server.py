#!/usr/bin/env python3
"""Agent Bus — bidirectional message broker between Hermes and CLI agents.

Endpoints:
  GET  /bus                    — list agents with pending messages
  GET  /bus/<agent>/inbox      — dequeue next message for agent (returns null if empty)
  POST /bus/<agent>/inbox      — queue a message for agent (body: {text, from})
  GET  /bus/<agent>/outbox     — dequeue messages from agent
  POST /bus/<agent>/outbox     — agent posts a message (body: {text, type, needs_human?})
  POST /bus/<agent>/inject     — inject stdin into a running agent (via tmux)
  GET  /bus/human              — list unresolved human-intervention requests
  POST /bus/human/resolve      — resolve a human request (body: {id, response})

In-memory storage (ephemeral). Clean exit loses unprocessed messages.
Run on port 9901.
"""

import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional


PORT = int(os.environ.get("AGENT_BUS_PORT", 9901))

# ── In-memory storage ──────────────────────────────────────────────────
# Each agent has: inbox (queue), outbox (queue), session_id (tmux)
agents: dict[str, dict] = {}  # name -> {"inbox": [], "outbox": [], "session": None}

human_requests: list[dict] = []  # unresolved human-intervention requests


def _ensure_agent(agent: str):
    if agent not in agents:
        agents[agent] = {"inbox": [], "outbox": [], "session": None}


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_ansi(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\x1b\].*?\x07", "", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\r", "\n", text)
    return text.strip()


def _tmux_send_keys(session: str, text: str):
    """Send text to a tmux session pane via send-keys."""
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "-l", text],
            capture_output=True, timeout=5,
        )
        subprocess.run(
            ["tmux", "send-keys", "-t", session, "Enter"],
            capture_output=True, timeout=5,
        )
        return True
    except Exception as e:
        return str(e)


def _tmux_capture_pane(session: str) -> Optional[str]:
    """Capture visible pane content from a tmux session."""
    try:
        r = subprocess.run(
            ["tmux", "capture-pane", "-t", session, "-p"],
            capture_output=True, timeout=5, text=True,
        )
        return _strip_ansi(r.stdout) if r.returncode == 0 else None
    except Exception:
        return None


class BusHandler(BaseHTTPRequestHandler):

    # ── Routing ────────────────────────────────────────────────────────

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/bus":
            return self._list_bus()
        if path == "/bus/human":
            return self._list_human()
        if m := re.match(r"^/bus/([^/]+)/inbox$", path):
            return self._dequeue_inbox(m.group(1))
        if m := re.match(r"^/bus/([^/]+)/outbox$", path):
            return self._dequeue_outbox(m.group(1))
        if m := re.match(r"^/bus/([^/]+)/capture$", path):
            return self._capture_pane(m.group(1))

        self._json(404, {"error": f"unknown endpoint: {path}"})

    def do_POST(self):
        path = self.path.rstrip("/")

        if m := re.match(r"^/bus/([^/]+)/inbox$", path):
            return self._enqueue_inbox(m.group(1))
        if m := re.match(r"^/bus/([^/]+)/outbox$", path):
            return self._enqueue_outbox(m.group(1))
        if m := re.match(r"^/bus/([^/]+)/inject$", path):
            return self._inject_stdin(m.group(1))
        if m := re.match(r"^/bus/([^/]+)/session$", path):
            return self._register_session(m.group(1))
        if path == "/bus/human":
            return self._resolve_human()

        self._json(404, {"error": f"unknown endpoint: {path}"})

    # ── Bus listing ────────────────────────────────────────────────────

    def _list_bus(self):
        result = {}
        for name, data in agents.items():
            result[name] = {
                "inbox_count": len(data["inbox"]),
                "outbox_count": len(data["outbox"]),
                "session": data["session"],
            }
        self._json(200, result)

    # ── Inbox (messages FOR the agent) ─────────────────────────────────

    def _enqueue_inbox(self, agent: str):
        _ensure_agent(agent)
        body = self._read_body()
        if not body or "text" not in body:
            return self._json(400, {"error": "body must have 'text' field"})

        msg = {
            "id": f"in_{uuid.uuid4().hex[:12]}",
            "from": body.get("from", "hermes"),
            "text": body["text"],
            "timestamp": _ts(),
        }
        agents[agent]["inbox"].append(msg)
        self._json(201, msg)

    def _dequeue_inbox(self, agent: str):
        _ensure_agent(agent)
        inbox = agents[agent]["inbox"]
        if not inbox:
            return self._json(200, None)
        msg = inbox.pop(0)
        self._json(200, msg)

    # ── Outbox (messages FROM the agent) ───────────────────────────────

    def _enqueue_outbox(self, agent: str):
        _ensure_agent(agent)
        body = self._read_body()
        if not body or "text" not in body:
            return self._json(400, {"error": "body must have 'text' field"})

        msg = {
            "id": f"out_{uuid.uuid4().hex[:12]}",
            "from": agent,
            "type": body.get("type", "response"),
            "text": body["text"],
            "needs_human": body.get("needs_human", False),
            "timestamp": _ts(),
        }
        agents[agent]["outbox"].append(msg)

        # If agent is requesting human intervention, add to human_requests too
        if body.get("needs_human"):
            hreq = {
                "id": f"hum_{uuid.uuid4().hex[:12]}",
                "agent": agent,
                "question": body["text"],
                "context": body.get("context", ""),
                "resolved": False,
                "response": None,
                "timestamp": _ts(),
            }
            human_requests.append(hreq)
            msg["human_request_id"] = hreq["id"]

        self._json(201, msg)

    def _dequeue_outbox(self, agent: str):
        _ensure_agent(agent)
        outbox = agents[agent]["outbox"]
        if not outbox:
            return self._json(200, None)
        msg = outbox.pop(0)
        self._json(200, msg)

    # ── Session management ─────────────────────────────────────────────

    def _register_session(self, agent: str):
        _ensure_agent(agent)
        body = self._read_body()
        if not body or "session" not in body:
            return self._json(400, {"error": "body must have 'session' field"})
        agents[agent]["session"] = body["session"]
        self._json(200, {"ok": True, "agent": agent, "session": body["session"]})

    def _inject_stdin(self, agent: str):
        """Inject text into a running agent's tmux session via send-keys."""
        body = self._read_body()
        if not body or "text" not in body:
            return self._json(400, {"error": "body must have 'text' field"})

        session = agents.get(agent, {}).get("session")
        if not session:
            return self._json(404, {"error": f"no session registered for '{agent}'"})

        result = _tmux_send_keys(session, body["text"])
        if result is True:
            self._json(200, {"ok": True, "agent": agent, "injected": body["text"]})
        else:
            self._json(500, {"error": f"tmux send-keys failed: {result}"})

    def _capture_pane(self, agent: str):
        """Capture visible pane output from a running agent."""
        session = agents.get(agent, {}).get("session")
        if not session:
            return self._json(404, {"error": f"no session registered for '{agent}'"})
        output = _tmux_capture_pane(session)
        if output is not None:
            self._json(200, {"agent": agent, "output": output})
        else:
            self._json(500, {"error": "failed to capture tmux pane"})

    # ── Human intervention ─────────────────────────────────────────────

    def _list_human(self):
        unresolved = [r for r in human_requests if not r["resolved"]]
        self._json(200, unresolved)

    def _resolve_human(self):
        body = self._read_body()
        if not body or "id" not in body or "response" not in body:
            return self._json(400, {"error": "body must have 'id' and 'response' fields"})

        for r in human_requests:
            if r["id"] == body["id"] and not r["resolved"]:
                r["resolved"] = True
                r["response"] = body["response"]
                r["resolved_at"] = _ts()

                # Auto-inject response into the agent's inbox
                agent = r["agent"]
                _ensure_agent(agent)
                agents[agent]["inbox"].append({
                    "id": f"in_{uuid.uuid4().hex[:12]}",
                    "from": "human",
                    "text": body["response"],
                    "in_reply_to": r["id"],
                    "timestamp": _ts(),
                })
                return self._json(200, {"ok": True, "id": body["id"]})

        self._json(404, {"error": f"no unresolved request with id '{body['id']}'"})

    # ── Helpers ────────────────────────────────────────────────────────

    def _read_body(self) -> Optional[dict]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _json(self, status: int, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # quiet


def main():
    server = HTTPServer(("127.0.0.1", PORT), BusHandler)
    print(f"Agent Bus on http://127.0.0.1:{PORT}")
    print(f"  Endpoints:")
    print(f"    GET  /bus                    — list agents + message counts")
    print(f"    GET  /bus/<agent>/inbox      — dequeue next message for agent")
    print(f"    POST /bus/<agent>/inbox      — queue message for agent")
    print(f"    GET  /bus/<agent>/outbox     — dequeue messages from agent")
    print(f"    POST /bus/<agent>/outbox     — agent sends message")
    print(f"    POST /bus/<agent>/inject     — inject stdin into agent (tmux)")
    print(f"    POST /bus/<agent>/session    — register agent's tmux session")
    print(f"    GET  /bus/<agent>/capture    — capture visible pane output")
    print(f"    GET  /bus/human              — list human-intervention requests")
    print(f"    POST /bus/human/resolve      — resolve a human request")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
