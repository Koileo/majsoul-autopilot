import json
from .base.bot import Bot
from .logger import logger
from .mortal.bot import Bot as MortalBot


class Controller(object):
    def __init__(self):
        self.bot: Bot = MortalBot()
        self.temp_mjai_msg: list[dict] = []
        self.starting_game: bool = False

    def react(self, events: list[dict]) -> dict:
        if not self.bot:
            logger.error("No bot available")
            return {"type": "none"}
        ans = self.bot.react(json.dumps(events, separators=(",", ":")))
        return json.loads(ans)
