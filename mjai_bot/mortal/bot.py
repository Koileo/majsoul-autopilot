import json

from . import model
from .logger import logger

class Bot:
    def __init__(self):
        self.player_id: int = None
        self.model = None

    def react(self, events: str) -> str:
        """Feed MJAI events into the local Mortal model and return one MJAI action."""
        try:
            events = json.loads(events)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse events: {events}, {e}")
            return json.dumps({"type":"none"}, separators=(",", ":"))

        return_action = None
        for e in events:
            if e["type"] == "start_game":
                self.player_id = e["id"]
                self.model = model.load_model(self.player_id)
                continue
            if self.model is None or self.player_id is None:
                logger.error(f"Model is not loaded yet")
                continue
            if e["type"] == "end_game":
                self.player_id = None
                self.model = None
                continue
            return_action = self.model.react(json.dumps(e, separators=(",", ":")))

        if return_action is None:
            # Model didn't react to any event - no action needed
            # can_act=False tells the caller NOT to send a skip RPC
            raw_data = {"type":"none", "can_act": False}
            return json.dumps(raw_data, separators=(",", ":"))
        else:
            # Model reacted - either a real action or explicit pass
            # can_act=True tells the caller this is a deliberate decision
            raw_data = json.loads(return_action)
            raw_data["can_act"] = True
            return json.dumps(raw_data, separators=(",", ":"))
