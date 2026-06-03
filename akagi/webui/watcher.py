import json
import asyncio
from pathlib import Path
from fastapi import WebSocket
from akagi.logger import logger


class JsonlWatcher:
    """Watches game_flow.jsonl and inference.jsonl, broadcasts new lines to WebSocket clients."""

    def __init__(self, logs_dir: str = "logs"):
        self.logs_dir = Path(logs_dir)
        self.clients: list[WebSocket] = []
        self._gf_pos = 0
        self._inf_pos = 0
        self._gf_first_line: str | None = None
        self._inf_first_line: str | None = None
        self._game_flow_events: list[dict] = []
        self._inference_events: list[dict] = []
        self._player_id: int | None = None
        self._running = False

    def get_full_state(self) -> dict:
        """Return complete current state for new WebSocket connections."""
        return {
            "type": "full_state",
            "game_flow": self._game_flow_events,
            "inference": self._inference_events,
            "player_id": self._player_id,
        }

    async def register(self, ws: WebSocket):
        self.clients.append(ws)
        await ws.send_json(self.get_full_state())

    def unregister(self, ws: WebSocket):
        if ws in self.clients:
            self.clients.remove(ws)

    async def broadcast(self, message: dict):
        disconnected = []
        for ws in self.clients:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.unregister(ws)

    async def start(self):
        """Start polling JSONL files for new lines."""
        self._running = True
        gf_path = self.logs_dir / "game_flow.jsonl"
        inf_path = self.logs_dir / "inference.jsonl"

        while self._running:
            await self._check_file(gf_path, "game_event", is_game_flow=True)
            await self._check_file(inf_path, "inference", is_game_flow=False)
            await asyncio.sleep(0.1)

    def stop(self):
        self._running = False

    def _first_line(self, path: Path) -> str | None:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline()
        return line.rstrip("\n") if line else None

    def _reset_stream(self, is_game_flow: bool):
        if is_game_flow:
            self._gf_pos = 0
            self._gf_first_line = None
            self._inf_pos = 0
            self._inf_first_line = None
            self._game_flow_events = []
            self._inference_events = []
            self._player_id = None
        else:
            self._inf_pos = 0
            self._inf_first_line = None
            self._inference_events = []

    async def _check_file(self, path: Path, event_type: str, is_game_flow: bool):
        if not path.exists():
            return

        pos_attr = "_gf_pos" if is_game_flow else "_inf_pos"
        first_line_attr = "_gf_first_line" if is_game_flow else "_inf_first_line"
        current_pos = getattr(self, pos_attr)

        try:
            file_size = path.stat().st_size
            first_line = self._first_line(path) if file_size else None
            previous_first_line = getattr(self, first_line_attr)
            was_rewritten = (
                current_pos > 0
                and (
                    file_size < current_pos
                    or (
                        previous_first_line is not None
                        and first_line is not None
                        and first_line != previous_first_line
                    )
                )
            )

            if was_rewritten:
                self._reset_stream(is_game_flow)
                current_pos = 0
                previous_first_line = None

            if first_line is not None and previous_first_line is None:
                setattr(self, first_line_attr, first_line)

            with open(path, "r", encoding="utf-8") as f:
                f.seek(current_pos)
                new_lines = f.readlines()
                if new_lines:
                    setattr(self, pos_attr, f.tell())
                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if is_game_flow:
                            if data.get("type") == "start_game":
                                self._player_id = data.get("id")
                                self._game_flow_events = []
                                self._inference_events = []
                            elif data.get("type") == "start_kyoku":
                                self._game_flow_events = []
                                self._inference_events = []
                            self._game_flow_events.append(data)
                        else:
                            self._inference_events.append(data)

                        await self.broadcast({
                            "type": event_type,
                            "data": data,
                        })
        except Exception as e:
            logger.error(f"Error reading {path}: {e}")
