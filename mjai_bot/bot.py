import json
from .logger import logger


class MjaiStateTracker:
    """Track MJAI game state so the runner can inspect seat/hand details."""

    def __init__(self):
        self.player_id: int = 0
        self.tehai_mjai: list[str] = []
        self.last_self_tsumo: str | None = None

    @staticmethod
    def _none() -> str:
        return json.dumps({"type": "none", "can_act": False}, separators=(",", ":"))

    def react(self, input_str: str = None, input_list: list[dict] = None) -> str:
        try:
            if input_str:
                events = json.loads(input_str)
            elif input_list:
                events = input_list
            else:
                raise ValueError("Empty input")
            if len(events) == 0:
                raise ValueError("Empty events")
            for event in events:
                logger.debug(f"Event: {event}")
                self._apply_event(event)

        except Exception as e:
            logger.warning(f"State tracker ignored event: {type(e).__name__}: {e!r}")

        return self._none()

    def _apply_event(self, event: dict) -> None:
        event_type = event.get("type")
        if event_type == "start_game":
            self.player_id = int(event.get("id") or 0)
            self.tehai_mjai = []
            self.last_self_tsumo = None
            return

        if event_type == "start_kyoku":
            tehais = event.get("tehais") or []
            if 0 <= self.player_id < len(tehais):
                self.tehai_mjai = list(tehais[self.player_id])
            self.last_self_tsumo = None
            return

        actor = event.get("actor")
        if actor != self.player_id:
            return

        if event_type == "tsumo":
            pai = event.get("pai")
            if pai and pai != "?":
                self.tehai_mjai.append(pai)
                self.last_self_tsumo = pai
            return

        if event_type == "dahai":
            self._remove_tile(event.get("pai"))
            if event.get("pai") == self.last_self_tsumo:
                self.last_self_tsumo = None
            return

        if event_type in {"chi", "pon", "daiminkan", "ankan"}:
            for tile in event.get("consumed") or []:
                self._remove_tile(tile)
            return

        if event_type == "kakan":
            self._remove_tile(event.get("pai"))

    def _remove_tile(self, tile: str | None) -> None:
        if not tile:
            return
        try:
            self.tehai_mjai.remove(tile)
        except ValueError:
            logger.debug(f"State tracker tile not in hand: {tile}")
