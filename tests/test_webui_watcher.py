import json
import tempfile
import unittest
from pathlib import Path

from akagi.webui.watcher import JsonlWatcher


def _write_jsonl(path: Path, records: list[dict]):
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


class WebuiWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_game_flow_truncation_resets_position_and_cached_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = JsonlWatcher(tmpdir)
            game_flow = Path(tmpdir) / "game_flow.jsonl"

            _write_jsonl(
                game_flow,
                [
                    {"ts": "20:00:00", "type": "start_game", "id": 1, "names": ["old"]},
                    {"ts": "20:00:01", "type": "start_kyoku", "bakaze": "E", "kyoku": 1},
                    {"ts": "20:00:02", "type": "tsumo", "actor": 0, "pai": "1m"},
                ],
            )
            await watcher._check_file(game_flow, "game_event", is_game_flow=True)

            old_pos = watcher._gf_pos
            self.assertGreater(old_pos, 0)
            self.assertEqual(watcher.get_full_state()["player_id"], 1)
            watcher._inf_pos = 123
            watcher._inf_first_line = '{"ts":"old"}'

            _write_jsonl(
                game_flow,
                [
                    {"ts": "20:01:00", "type": "start_game", "id": 2},
                ],
            )
            self.assertLess(game_flow.stat().st_size, old_pos)

            await watcher._check_file(game_flow, "game_event", is_game_flow=True)

            state = watcher.get_full_state()
            self.assertEqual(state["player_id"], 2)
            self.assertEqual(state["game_flow"], [{"ts": "20:01:00", "type": "start_game", "id": 2}])
            self.assertEqual(watcher._inf_pos, 0)
            self.assertIsNone(watcher._inf_first_line)

    async def test_inference_truncation_resets_position_and_cached_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            watcher = JsonlWatcher(tmpdir)
            inference = Path(tmpdir) / "inference.jsonl"

            _write_jsonl(
                inference,
                [
                    {
                        "ts": "20:00:00",
                        "kyoku": 1,
                        "tehai": ["1m"] * 13,
                        "action": {"type": "dahai", "pai": "1m"},
                    }
                ],
            )
            await watcher._check_file(inference, "inference", is_game_flow=False)

            old_pos = watcher._inf_pos
            self.assertEqual(len(watcher.get_full_state()["inference"]), 1)

            _write_jsonl(
                inference,
                [
                    {"ts": "20:01:00", "action": {"type": "none"}},
                ],
            )
            self.assertLess(inference.stat().st_size, old_pos)

            await watcher._check_file(inference, "inference", is_game_flow=False)

            state = watcher.get_full_state()
            self.assertEqual(state["inference"], [{"ts": "20:01:00", "action": {"type": "none"}}])
