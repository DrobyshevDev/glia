"""A tiny stdlib HTTP server for the shell — no web framework.

Routes:

* ``GET  /``                       → the single-page UI
* ``GET  /api/config``             → current settings (UI-safe, no keys)
* ``POST /api/config``             → update settings
* ``GET  /api/conversations``      → list saved conversations
* ``POST /api/conversations/new``  → start a fresh conversation
* ``POST /api/conversations/select`` → switch the active conversation
* ``POST /api/conversations/delete`` → delete a conversation
* ``POST /api/new``                → alias for a fresh conversation
* ``POST /api/chat``               → run a turn, streaming events as Server-Sent Events
* ``POST /api/approve``            → resolve a pending human-in-the-loop tool approval
* ``POST /api/quit``               → ask the app to exit

Conversations are persisted as glia trajectory checkpoints (plain JSON) in the
user's config directory, so history survives restarts. The chat endpoint streams
the agent's event stream verbatim — each glia :class:`~glia.trajectory.Event`
becomes one ``data:`` line — so the front end is a thin renderer over the same
events the library emits.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler
from importlib import resources

from ..checkpoint import load, save
from ..trajectory import Trajectory
from .backend import build_agent
from .config import Config


class ShellState:
    """Config, the active conversation, persistent history, and a quit signal."""

    def __init__(self) -> None:
        self.config = Config.load()
        self.lock = threading.Lock()
        self.quit_event = threading.Event()
        self.approvals: dict[str, tuple] = {}
        self.approvals_lock = threading.Lock()

        self.current_id: str | None = None
        self.trajectory = Trajectory.new(system=self.config.system)
        self._conv_dir().mkdir(parents=True, exist_ok=True)
        existing = self.list_conversations()
        if existing:
            self.select(existing[0]["id"])
        else:
            self.new_conversation()

    # -- conversations ---------------------------------------------------------

    def _conv_dir(self):
        from . import config as _cfg  # via the module so tests can patch config_dir

        return _cfg.config_dir() / "conversations"

    def _conv_path(self, cid: str):
        return self._conv_dir() / f"{cid}.json"

    def list_conversations(self) -> list[dict]:
        try:
            paths = sorted(self._conv_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        items = []
        for path in paths:
            try:
                traj = load(path)
            except Exception:  # noqa: BLE001 - skip a corrupt file, don't crash the list
                continue
            turns = sum(1 for m in traj.messages if m.role == "user")
            items.append({"id": path.stem, "title": _conv_title(traj), "turns": turns, "updated": path.stat().st_mtime})
        return items

    def new_conversation(self) -> str:
        # Millisecond timestamps can collide on rapid creation; a uuid can't.
        cid = f"c{int(time.time())}-{uuid.uuid4().hex[:8]}"
        self.current_id = cid
        self.trajectory = Trajectory.new(system=self.config.system)
        self.save_current()
        return cid

    def select(self, cid: str) -> bool:
        path = self._conv_path(cid)
        if not path.exists():
            return False
        try:
            self.trajectory = load(path)
        except Exception:  # noqa: BLE001
            return False
        self.current_id = cid
        return True

    def delete(self, cid: str) -> None:
        path = self._conv_path(cid)
        if path.exists():
            path.unlink()
        if self.current_id == cid:
            existing = self.list_conversations()
            self.select(existing[0]["id"]) if existing else self.new_conversation()

    def save_current(self) -> None:
        if self.current_id:
            try:
                save(self.trajectory, self._conv_path(self.current_id))
            except OSError:
                pass

    def reset(self) -> None:  # kept as the /api/new alias
        self.new_conversation()

    def messages_payload(self) -> list[dict]:
        """The current conversation's messages, flattened for the UI."""
        out = []
        for m in self.trajectory.messages:
            text = m.text()
            if text:
                out.append({"role": m.role, "text": text})
        return out

    # -- approvals -------------------------------------------------------------

    def approval_policy(self):
        """An async approval policy that parks each tool call on a future until
        the UI resolves it via ``/api/approve``."""

        async def policy(request):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            with self.approvals_lock:
                self.approvals[request.tool_use_id] = (loop, future)
            try:
                return await future
            finally:
                with self.approvals_lock:
                    self.approvals.pop(request.tool_use_id, None)

        return policy

    def resolve_approval(self, tool_use_id: str, allow: bool, reason: str = "") -> bool:
        """Resolve a pending approval from another thread (the /api/approve request)."""
        from ..approval import Decision

        with self.approvals_lock:
            entry = self.approvals.get(tool_use_id)
        if not entry:
            return False
        loop, future = entry
        decision = Decision(allow=allow, reason=reason or ("" if allow else "denied in the app"))
        loop.call_soon_threadsafe(lambda: None if future.done() else future.set_result(decision))
        return True


def _conv_title(traj: Trajectory) -> str:
    for message in traj.messages:
        if message.role == "user":
            text = message.text().strip()
            if text:
                return text[:52]
    return "New conversation"


def _index_html() -> bytes:
    return resources.files("glia.shell").joinpath("web/index.html").read_bytes()


def make_handler(state: ShellState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "glia-shell"

        def log_message(self, *args: object) -> None:  # keep the console quiet
            pass

        # -- helpers -----------------------------------------------------------

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj) -> None:
            self._send(200, json.dumps(obj).encode())

        def _json_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw or b"{}")
            except Exception:  # noqa: BLE001
                return {}

        # -- routes ------------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, _index_html(), "text/html; charset=utf-8")
            elif path == "/api/config":
                self._json(state.config.public())
            elif path == "/api/conversations":
                self._json({"conversations": state.list_conversations(), "current": state.current_id})
            elif path == "/api/messages":
                self._json({"messages": state.messages_payload(), "current": state.current_id})
            else:
                self._send(404, b'{"error":"not found"}')

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            data = self._json_body()  # always drain the request body first
            if path == "/api/config":
                self._update_config(data)
                self._json(state.config.public())
            elif path in ("/api/new", "/api/conversations/new"):
                self._json({"id": state.new_conversation()})
            elif path == "/api/conversations/select":
                self._json({"ok": state.select(data.get("id", ""))})
            elif path == "/api/conversations/delete":
                state.delete(data.get("id", ""))
                self._json({"ok": True, "current": state.current_id})
            elif path == "/api/quit":
                state.quit_event.set()  # set before responding so callers can rely on it
                self._json({"ok": True})
            elif path == "/api/approve":
                ok = state.resolve_approval(
                    data.get("tool_use_id", ""), bool(data.get("allow")), data.get("reason", "")
                )
                self._send(200 if ok else 404, json.dumps({"ok": ok}).encode())
            elif path == "/api/chat":
                self._chat(data.get("message", ""))
            else:
                self._send(404, b'{"error":"not found"}')

        # -- handlers ----------------------------------------------------------

        def _update_config(self, data: dict) -> None:
            config = state.config
            for field in ("mode", "model", "ollama_host", "ollama_model", "openai_base_url", "openai_model"):
                if data.get(field):
                    setattr(config, field, data[field])
            if "system" in data:
                config.system = data["system"]
            if "use_tools" in data:
                config.use_tools = bool(data["use_tools"])
            if "approve_tools" in data:
                config.approve_tools = bool(data["approve_tools"])
            if data.get("anthropic_api_key"):
                config.anthropic_api_key = data["anthropic_api_key"]
            if data.get("openai_api_key"):
                config.openai_api_key = data["openai_api_key"]
            if data.get("clear_key"):
                config.anthropic_api_key = ""
            if data.get("clear_openai_key"):
                config.openai_api_key = ""
            config.save()

        def _chat(self, message: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            def emit(obj: dict) -> None:
                self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
                self.wfile.flush()

            # One turn at a time — this is a single-user local app.
            with state.lock:
                approval = state.approval_policy() if state.config.approve_tools else None
                agent = build_agent(state.config, stream=True, approval=approval)

                async def run() -> None:
                    async for event in agent.run_events(message, trajectory=state.trajectory):
                        emit(event.to_dict())

                try:
                    asyncio.run(run())
                except Exception as exc:  # noqa: BLE001 - report failures to the UI
                    emit({"kind": "error", "message": str(exc)})
                state.save_current()
                emit({"kind": "__done__"})

    return Handler
