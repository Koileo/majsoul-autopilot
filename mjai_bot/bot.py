import json
from mjai import Bot
from mjai.mlibriichi.state import PlayerState  # type: ignore
from .logger import logger

class MjaiStateTracker(Bot):
    """Track MJAI game state so the runner can inspect seat/hand details."""
    def __init__(self):
        super().__init__()

    def think(self) -> str:
        """
        tsumogiri
        """
        if self.can_discard:
            tile_str = self.last_self_tsumo
            return self.action_discard(tile_str)
        else:
            return self.action_nothing()

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
                if event["type"] == "start_game":
                    self.player_id = event["id"]
                    self.player_state = PlayerState(self.player_id)
                logger.debug(f"Event: {event}")
                self.player_state.update(json.dumps(event))

            # NOTE: Skip `think()` if the player's riichi is accepted and
            # no call actions are allowed.
            if (
                self.self_riichi_accepted
                and not (self.can_agari or self.can_kakan or self.can_ankan)
                and self.can_discard
            ):
                return self.action_discard(self.last_self_tsumo)

            resp = self.think()
            return resp

        except Exception as e:
            logger.error(f"Exception: {str(e)}")
            logger.error("Brief info:")
            logger.error(self.brief_info())

        return json.dumps({"type": "none"}, separators=(",", ":"))
