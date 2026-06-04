import unittest
from unittest.mock import patch

import jsonschema

from majsoul.bridge import MajsoulBridge
from majsoul.client import (
    MajsoulAutomation,
    _auth_game_body,
    _route_ws_url,
    _select_riichi_declaration_tile,
    _target_mode_for_rank_level,
)
from majsoul.protocol import MsgType, fromProtobuf
from mjai_bot.bot import MjaiStateTracker
from run_autoplay import _resolve_riichi_discard
from settings.settings import get_schema, verify_settings


class FakeLobby:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.ws = True

    async def request(self, method, data=None, *, timeout=20, feed_bridge=True):
        self.calls.append((method, data, timeout))
        response = self.responses.get(method)
        if callable(response):
            return response()
        if response is not None:
            return response
        return {"data": {}}


class FakeRouteSocket:
    instances = []

    def __init__(self, name, url, **_kwargs):
        self.name = name
        self.url = url
        self.ws = True
        self.calls = []
        FakeRouteSocket.instances.append(self)

    async def connect(self, open_timeout=15):
        self.calls.append(("connect", open_timeout))

    async def raw_request(
        self,
        method,
        body=b"",
        *,
        timeout=20,
        parse_response=False,
        feed_bridge=False,
    ):
        self.calls.append((method, body, timeout, parse_response, feed_bridge))
        return b"\x03\x01\x00\n\x00\x12\x00"

    async def close(self):
        self.calls.append(("close",))


class MiniProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_settings_schema_is_minimal(self):
        schema = get_schema()

        self.assertEqual(schema["required"], ["model_path", "autoplay_account"])
        self.assertNotIn("autoplay_mode", schema["properties"])
        self.assertNotIn("autoplay_time", schema["properties"])
        self.assertTrue(
            verify_settings(
                {
                    "model_path": "mjai_bot/mortal/mortal_298k.pth",
                    "autoplay_account": {
                        "username": "user@example.com",
                        "password": "secret",
                    },
                }
            )
        )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(
                {
                    "model_path": "mjai_bot/mortal/mortal_298k.pth",
                    "autoplay_account": {
                        "username": "user@example.com",
                        "password": "secret",
                    },
                    "unexpected": {"mode": "legacy"},
                },
                schema,
            )

    def test_four_player_rank_level_selects_target_room(self):
        self.assertEqual(_target_mode_for_rank_level(101), ("4p_east", "bronze"))
        self.assertEqual(_target_mode_for_rank_level(201), ("4p_south", "silver"))
        self.assertEqual(_target_mode_for_rank_level(301), ("4p_south", "gold"))
        self.assertEqual(_target_mode_for_rank_level(10103), ("4p_east", "bronze"))
        self.assertEqual(_target_mode_for_rank_level(10201), ("4p_south", "silver"))
        self.assertEqual(_target_mode_for_rank_level(10301), ("4p_south", "gold"))

    def test_riichi_resolution_feeds_reach_before_discard(self):
        class FakeController:
            def __init__(self):
                self.calls = []

            def react(self, events):
                self.calls.append(events)
                return {"type": "dahai", "pai": "4m", "tsumogiri": False}

        controller = FakeController()

        action = _resolve_riichi_discard(
            {"type": "reach"},
            controller,
            player_id=2,
            last_tsumo_tile="9p",
        )

        self.assertEqual(controller.calls, [[{"type": "reach", "actor": 2}]])
        self.assertEqual(action["pai"], "4m")
        self.assertFalse(action["tsumogiri"])

    def test_riichi_declaration_uses_model_candidate(self):
        self.assertEqual(
            _select_riichi_declaration_tile("7p", ["3m", "7p", "9s"]),
            ("7p", "model-exact"),
        )
        self.assertEqual(
            _select_riichi_declaration_tile("5m", ["8s", "0m"]),
            ("0m", "model-red-equivalent"),
        )

    async def test_start_match_uses_rank_target_not_config_mode(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        automation = PrepareOkAutomation()
        automation.account_id = 23744444
        automation.lobby = FakeLobby(
            {
                ".lq.Lobby.fetchAccountInfo": {
                    "data": {"account": {"level": {"id": 201, "score": 143}}}
                },
                ".lq.Lobby.fetchGamingInfo": {"data": {}},
                ".lq.Lobby.startUnifiedMatch": {"data": {}},
            }
        )

        result = await automation.start_match()

        self.assertEqual(result, "queued")
        queue_call = [
            call for call in automation.lobby.calls
            if call[0] == ".lq.Lobby.startUnifiedMatch"
        ][0]
        self.assertEqual(queue_call[1]["match_sid"], "1:6")
        self.assertEqual(automation.target_mode, "4p_south")
        self.assertEqual(automation.target_room, "silver")

    async def test_prepare_route_uses_official_route_socket_only(self):
        FakeRouteSocket.instances = []
        automation = MajsoulAutomation()
        automation.access_token = "access-token"

        with patch("majsoul.client.LiqiSocket", FakeRouteSocket):
            prepared = await automation._prepare_game_route(target_route_id="route-5")

        self.assertTrue(prepared)
        sock = FakeRouteSocket.instances[0]
        self.assertEqual(sock.url, "wss://route-5.maj-soul.com:443/gateway")
        self.assertEqual(sock.calls[1][0], ".lq.Route.requestConnection")
        self.assertEqual(sock.calls[2][0], ".lq.Lobby.prepareLogin")

    def test_route_3_keeps_official_8443_port(self):
        self.assertEqual(
            _route_ws_url("route-3"),
            "wss://route-3.maj-soul.com:8443/gateway",
        )

    def test_auth_game_body_matches_current_schema(self):
        body = _auth_game_body(16581012, "connect-token", "game-uuid")

        fields = fromProtobuf(body)
        self.assertEqual([field["id"] for field in fields], [1, 2, 3, 4, 5, 6])
        self.assertIn(b"connect-token", body)
        self.assertIn(b"game-uuid", body)
        self.assertEqual(fields[3]["data"], b"")
        self.assertEqual(fields[4]["data"], b"")
        self.assertEqual(fields[5]["data"], 0)

    def test_bridge_ignores_non_four_player_auth_game(self):
        bridge = MajsoulBridge()
        bridge.parse_liqi(
            {
                "method": ".lq.FastTest.authGame",
                "type": MsgType.Req,
                "data": {"accountId": 1},
            }
        )

        events = bridge.parse_liqi(
            {
                "method": ".lq.FastTest.authGame",
                "type": MsgType.Res,
                "data": {
                    "error": {},
                    "seatList": [1, 2, 3],
                    "gameConfig": {"meta": {"modeId": 22}},
                },
            }
        )

        self.assertEqual(events, [])

    def test_login_payload_uses_password_login_and_access_token(self):
        automation = MajsoulAutomation()

        payload = automation._login_payload("user@example.com", "test-password")
        reconnect_payload = automation._login_payload(
            "user@example.com",
            "test-password",
            reconnect=True,
        )

        self.assertEqual(payload["type"], 0)
        self.assertTrue(payload["genAccessToken"])
        self.assertNotEqual(payload["password"], "test-password")
        self.assertTrue(reconnect_payload["reconnect"])
        self.assertNotIn("genAccessToken", reconnect_payload)

    def test_state_tracker_handles_mjai_honor_tiles_without_native_state(self):
        tracker = MjaiStateTracker()
        events = [
            {"type": "start_game", "id": 1},
            {
                "type": "start_kyoku",
                "bakaze": "E",
                "dora_marker": "7p",
                "honba": 0,
                "kyoku": 1,
                "kyotaku": 0,
                "oya": 0,
                "scores": [25000, 25000, 25000, 25000],
                "tehais": [
                    ["?"] * 13,
                    ["2m", "2m", "3m", "6m", "6m", "1p", "4p", "7p", "7s", "S", "W", "P", "P"],
                    ["?"] * 13,
                    ["?"] * 13,
                ],
            },
            {"type": "tsumo", "actor": 1, "pai": "W"},
            {"type": "dahai", "actor": 1, "pai": "P", "tsumogiri": False},
        ]

        for event in events:
            result = tracker.react(input_list=[event])
            self.assertEqual(result, '{"type":"none","can_act":false}')

        self.assertEqual(tracker.player_id, 1)
        self.assertEqual(tracker.last_self_tsumo, "W")
        self.assertEqual(tracker.tehai_mjai.count("P"), 1)
        self.assertIn("W", tracker.tehai_mjai)


if __name__ == "__main__":
    unittest.main()
