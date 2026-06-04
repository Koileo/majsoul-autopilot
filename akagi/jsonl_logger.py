import json
import pathlib
from datetime import datetime


class JsonlLogger:
    """Writes game_flow.jsonl and inference.jsonl for external AI agent consumption."""

    def __init__(self):
        logs_dir = pathlib.Path("logs")
        logs_dir.mkdir(exist_ok=True)
        self._gf = open(logs_dir / "game_flow.jsonl", "w", encoding="utf-8")
        self._inf = open(logs_dir / "inference.jsonl", "w", encoding="utf-8")
        self._kyoku = 0
        self._honba = 0
        self._junme = 0
        self._actor_junme: dict[int, int] = {}

    def close(self):
        self._gf.close()
        self._inf.close()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def write_game_flow(self, mjai_msg: dict):
        # Track game state
        if mjai_msg.get("type") == "start_kyoku":
            self._kyoku = mjai_msg.get("kyoku", 0)
            self._honba = mjai_msg.get("honba", 0)
            self._junme = 0
            self._actor_junme = {}
        elif mjai_msg.get("type") == "tsumo":
            actor = mjai_msg.get("actor", -1)
            self._actor_junme[actor] = self._actor_junme.get(actor, 0) + 1
            self._junme = self._actor_junme.get(0, 0)  # player 0's junme

        record = {"ts": self._ts()}
        record.update(mjai_msg)
        self._gf.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._gf.flush()

    def write_inference(self, mjai_response: dict, tehai: list[str]):
        meta = mjai_response.get("meta")
        if not meta:
            return
        from akagi.libriichi_helper import meta_to_recommend
        recommend = meta_to_recommend(meta)
        top3 = [[r[0], round(float(r[1]), 4)] for r in recommend[:3]]

        action = {k: v for k, v in mjai_response.items() if k != "meta"}

        record = {
            "ts": self._ts(),
            "kyoku": self._kyoku,
            "honba": self._honba,
            "junme": self._junme,
            "tehai": tehai,
            "shanten": meta.get("shanten"),
            "furiten": meta.get("at_furiten", False),
            "action": action,
            "top3": top3,
        }
        self._inf.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._inf.flush()
