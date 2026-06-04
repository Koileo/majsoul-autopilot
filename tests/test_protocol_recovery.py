import unittest
import asyncio
import time
import threading
import gc
import warnings
from types import SimpleNamespace
from unittest.mock import patch

import run_autoplay as autoplay_runner
from majsoul.client import (
    CLIENT_VERSION_STRING,
    MajsoulAutomation,
    PACKAGE_VERSION,
    RESOURCE_VERSION,
    _auth_game_body,
    _client_version_string,
    _format_error,
    _route_change_body,
    _route_ws_url,
    _select_riichi_declaration_tile,
    _target_mode_for_rank_level,
)
from majsoul.protocol import LiqiProto, fromProtobuf
from majsoul.bridge import (
    MajsoulBridge,
    get_last_discard_event,
    get_last_operation_context,
    get_last_operation_list,
)
from run_autoplay import (
    ProtocolMessageClient,
    SessionResult,
    _login_failure_session_result,
    _resolve_riichi_discard,
    run_session,
    _session_restart_delay,
)
from settings.settings import get_schema, get_settings, settings, verify_settings


class FakeLobby:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}
        self.ws = True
        self.name = "fake-lobby"

    async def request(self, method, data=None, *, timeout=20, feed_bridge=True):
        self.calls.append((method, data, timeout))
        if method in self.responses:
            response = self.responses[method]
            return response() if callable(response) else response
        if method == ".lq.Lobby.fetchGamingInfo":
            return {
                "data": {
                    "gameInfo": {
                        "connectToken": "fresh-token",
                        "gameUuid": "fresh-game-uuid",
                        "location": "zone",
                    }
                }
            }
        return {"data": {}}

    async def raw_request(self, method, body=b"", *, timeout=20, parse_response=False, feed_bridge=False):
        self.calls.append((method, body, timeout, parse_response, feed_bridge))
        return b"\x03\x01\x00\n\x00\x12\x00"

    async def close(self):
        self.calls.append(("close",))


class SequenceResponse:
    def __init__(self, *responses):
        self.responses = list(responses)

    def __call__(self):
        if len(self.responses) == 1:
            return self.responses[0]
        return self.responses.pop(0)


class FakeRoutePrep:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.ws = True
        self.args = args
        self.kwargs = kwargs

    async def connect(self, open_timeout=15):
        self.calls.append(("connect", open_timeout))

    async def raw_request(self, method, body=b"", *, timeout=20, parse_response=False, feed_bridge=False):
        self.calls.append((method, body, timeout, parse_response, feed_bridge))
        return b"\x03\x01\x00\n\x00\x12\x00"

    async def close(self):
        self.calls.append(("close",))


class ProtocolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_four_player_rank_level_selects_requested_autoplay_target(self):
        self.assertEqual(_target_mode_for_rank_level(101), ("4p_east", "bronze"))
        self.assertEqual(_target_mode_for_rank_level(199), ("4p_east", "bronze"))
        self.assertEqual(_target_mode_for_rank_level(201), ("4p_south", "silver"))
        self.assertEqual(_target_mode_for_rank_level(299), ("4p_south", "silver"))
        self.assertEqual(_target_mode_for_rank_level(301), ("4p_south", "gold"))
        self.assertEqual(_target_mode_for_rank_level(10103), ("4p_east", "bronze"))
        self.assertEqual(_target_mode_for_rank_level(10201), ("4p_south", "silver"))
        self.assertEqual(_target_mode_for_rank_level(10301), ("4p_south", "gold"))

    def test_riichi_resolution_feeds_reach_before_using_model_discard(self):
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
        self.assertEqual(action["type"], "reach")
        self.assertEqual(action["pai"], "4m")
        self.assertEqual(action["tsumogiri"], False)

    def test_riichi_resolution_falls_back_to_last_tsumo_only_without_model_discard(self):
        class FakeController:
            def react(self, events):
                return {"type": "none"}

        action = _resolve_riichi_discard(
            {"type": "reach"},
            FakeController(),
            player_id=1,
            last_tsumo_tile="7s",
        )

        self.assertEqual(action["pai"], "7s")
        self.assertEqual(action["tsumogiri"], True)

    def test_riichi_declaration_prefers_model_tile_with_multiple_candidates(self):
        tile, source = _select_riichi_declaration_tile("7p", ["3m", "7p", "9s"])

        self.assertEqual(tile, "7p")
        self.assertEqual(source, "model-exact")

    def test_riichi_declaration_matches_red_five_candidate_by_tile_kind(self):
        tile, source = _select_riichi_declaration_tile("5m", ["8s", "0m"])

        self.assertEqual(tile, "0m")
        self.assertEqual(source, "model-red-equivalent")

    async def test_execute_reach_uses_red_five_equivalent_instead_of_first_candidate(self):
        sends = []

        class RiichiAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = RiichiAutomation()

        async def fast_sleep(_seconds):
            return None

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch(
                "majsoul.client.get_last_operation_list",
                lambda: [{"type": 7, "combination": ["8s", "0m"]}],
            ),
        ):
            result = await automation.execute_action(
                {"type": "reach", "pai": "5m", "tsumogiri": False},
                seat=0,
            )

        self.assertTrue(result)
        self.assertEqual(sends[0]["tile"], "0m")

    async def test_execute_action_returns_false_when_input_operation_fails(self):
        automation = MajsoulAutomation()

        async def no_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", no_sleep):
            result = await automation.execute_action(
                {"type": "dahai", "pai": "1m", "tsumogiri": True},
                seat=0,
            )

        self.assertIs(result, False)

    async def test_login_reports_server_cooldown_as_busy(self):
        class CooldownLoginAutomation(MajsoulAutomation):
            async def _refresh_client_version(self):
                return None

            async def _refresh_route_entries(self):
                return None

            async def _connect_lobby_socket(self):
                self.lobby = FakeLobby({
                    ".lq.Lobby.login": {
                        "data": {
                            "error": {
                                "code": 503,
                                "u32Params": [1, 1780389194, 7200],
                                "strParams": ["AFK-AUTO-BAN:game-uuid"],
                            }
                        }
                    }
                })
                return True

        automation = CooldownLoginAutomation()

        try:
            result = await automation.login("user@example.com", "secret")

            self.assertFalse(result)
            self.assertTrue(automation.account_busy)
            self.assertEqual(automation.account_cooldown_until, 1780396394)
            self.assertIsNone(automation.existing_game_info)
        finally:
            await automation.close()

    async def test_recover_returns_busy_when_login_hits_server_cooldown(self):
        class CooldownRecoverAutomation(MajsoulAutomation):
            async def _refresh_client_version(self):
                return None

            async def _refresh_route_entries(self):
                return None

            async def _connect_lobby_socket(self):
                self.lobby = FakeLobby({
                    ".lq.Lobby.login": {
                        "data": {
                            "error": {
                                "code": 503,
                                "u32Params": [1, 1780389194, 7200],
                            }
                        }
                    }
                })
                return True

        automation = CooldownRecoverAutomation()

        result = await automation.recover()

        self.assertEqual(result, "busy")
        self.assertTrue(automation.account_busy)
        self.assertEqual(automation.account_cooldown_until, 1780396394)

    def test_fresh_password_login_payload_requests_access_token(self):
        automation = MajsoulAutomation()

        payload = automation._login_payload("user@example.com", "test-password")

        self.assertEqual(payload["type"], 0)
        self.assertTrue(payload["genAccessToken"])
        self.assertNotIn("version", payload)

    async def test_refresh_existing_game_info_uses_fresh_gaming_info(self):
        automation = MajsoulAutomation()
        automation.lobby = FakeLobby()
        automation.existing_game_info = {
            "connectToken": "stale-token",
            "gameUuid": "stale-game-uuid",
        }

        refreshed = await automation._refresh_existing_game_info()

        self.assertTrue(refreshed)
        self.assertEqual(automation.existing_game_info["connectToken"], "fresh-token")
        self.assertEqual(automation.existing_game_info["gameUuid"], "fresh-game-uuid")
        self.assertTrue(automation.account_busy)
        self.assertEqual(
            automation.lobby.calls[0][0],
            ".lq.Lobby.fetchGamingInfo",
        )

    async def test_auth_game_body_matches_current_req_auth_game_schema(self):
        body = _auth_game_body(16581012, "connect-token", "game-uuid")

        self.assertIn(b"connect-token", body)
        self.assertIn(b"game-uuid", body)
        fields = fromProtobuf(body)
        self.assertEqual([field["id"] for field in fields], [1, 2, 3, 4, 5, 6])
        self.assertEqual(fields[3]["data"], b"")
        self.assertEqual(fields[4]["data"], b"")
        self.assertEqual(fields[5]["data"], 0)

    async def test_reconnect_login_payload_uses_reconnect_branch(self):
        automation = MajsoulAutomation()

        payload = automation._login_payload("user@example.com", "secret", reconnect=True)

        self.assertTrue(payload["reconnect"])
        self.assertNotIn("genAccessToken", payload)

    async def test_reconnect_login_preserves_existing_access_token_when_response_omits_one(self):
        automation = MajsoulAutomation()
        automation.access_token = "previous-access-token"

        automation._apply_login_data(
            {
                "accountId": 23744444,
                "gameInfo": {
                    "connectToken": "game-token",
                    "gameUuid": "game-uuid",
                },
            },
            reconnect=True,
            previous_access_token="previous-access-token",
        )

        self.assertEqual(automation.access_token, "previous-access-token")
        self.assertEqual(automation.account_id, 23744444)
        self.assertTrue(automation.account_busy)

    async def test_start_match_reports_busy_without_fresh_queue_wait(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        automation = PrepareOkAutomation()
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": {"data": {}},
            ".lq.Lobby.startUnifiedMatch": {"data": {"error": {"code": 1023}}},
        })

        result = await automation.start_match()

        self.assertEqual(result, "busy")
        self.assertTrue(automation.account_busy)
        self.assertFalse(automation.matching)

    async def test_start_match_cancels_stale_queue_then_requeues_on_1304(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        automation = PrepareOkAutomation()
        automation.game_connect_failed.set()
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": {"data": {}},
            ".lq.Lobby.startUnifiedMatch": SequenceResponse(
                {"data": {"error": {"code": 1304}}},
                {"data": {}},
            ),
            ".lq.Lobby.cancelUnifiedMatch": {"data": {}},
        })

        result = await automation.start_match()

        self.assertEqual(result, "queued")
        self.assertFalse(automation.account_busy)
        self.assertTrue(automation.matching)
        self.assertFalse(automation.game_connect_failed.is_set())
        self.assertEqual(
            [call[0] for call in automation.lobby.calls],
            [
                ".lq.Lobby.fetchGamingInfo",
                ".lq.Lobby.startUnifiedMatch",
                ".lq.Lobby.fetchReviveCoinInfo",
                ".lq.Lobby.cancelUnifiedMatch",
                ".lq.Lobby.startUnifiedMatch",
            ],
        )

    async def test_start_match_claims_revive_coin_then_requeues_on_1304(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        automation = PrepareOkAutomation()
        automation.game_connect_failed.set()
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": {"data": {}},
            ".lq.Lobby.startUnifiedMatch": SequenceResponse(
                {"data": {"error": {"code": 1304}}},
                {"data": {}},
            ),
            ".lq.Lobby.fetchReviveCoinInfo": {"data": {"hasGained": False}},
            ".lq.Lobby.gainReviveCoin": {"data": {}},
        })

        result = await automation.start_match()

        self.assertEqual(result, "queued")
        self.assertFalse(automation.account_busy)
        self.assertTrue(automation.matching)
        self.assertFalse(automation.game_connect_failed.is_set())
        self.assertEqual(
            [call[0] for call in automation.lobby.calls],
            [
                ".lq.Lobby.fetchGamingInfo",
                ".lq.Lobby.startUnifiedMatch",
                ".lq.Lobby.fetchReviveCoinInfo",
                ".lq.Lobby.gainReviveCoin",
                ".lq.Lobby.startUnifiedMatch",
            ],
        )

    async def test_start_match_reports_server_cooldown_as_busy(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        automation = PrepareOkAutomation()
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": {"data": {}},
            ".lq.Lobby.startUnifiedMatch": {
                "data": {"error": {"code": 503, "u32Params": [1, 1780389194, 7200]}}
            },
        })

        result = await automation.start_match()

        self.assertEqual(result, "busy")
        self.assertTrue(automation.account_busy)
        self.assertEqual(automation.account_cooldown_until, 1780396394)
        self.assertFalse(automation.matching)

    async def test_start_match_prepares_route_before_queue(self):
        events = []

        class RecordingLobby(FakeLobby):
            async def request(self, method, data=None, *, timeout=20, feed_bridge=True):
                events.append(method)
                return await super().request(
                    method,
                    data,
                    timeout=timeout,
                    feed_bridge=feed_bridge,
                )

        class RecordingAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                events.append("prepare")
                return True

        automation = RecordingAutomation()
        automation.lobby = RecordingLobby({
            ".lq.Lobby.fetchGamingInfo": {"data": {}},
            ".lq.Lobby.startUnifiedMatch": {"data": {}},
        })

        result = await automation.start_match()

        self.assertEqual(result, "queued")
        self.assertIn("prepare", events)
        self.assertLess(
            events.index("prepare"),
            events.index(".lq.Lobby.startUnifiedMatch"),
        )

    async def test_start_match_refreshes_rank_target_before_queue(self):
        class PrepareOkAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

        old_type = settings.autoplay_mode.type
        old_room = settings.autoplay_mode.room
        settings.autoplay_mode.type = "4p_east"
        settings.autoplay_mode.room = "bronze"
        try:
            automation = PrepareOkAutomation()
            automation.account_id = 23744444
            automation.lobby = FakeLobby({
                ".lq.Lobby.fetchAccountInfo": {
                    "data": {
                        "account": {
                            "level": {"id": 201, "score": 143},
                        }
                    }
                },
                ".lq.Lobby.fetchGamingInfo": {"data": {}},
                ".lq.Lobby.startUnifiedMatch": {"data": {}},
            })

            result = await automation.start_match()

            self.assertEqual(result, "queued")
            queue_call = [
                call for call in automation.lobby.calls
                if call[0] == ".lq.Lobby.startUnifiedMatch"
            ][0]
            self.assertEqual(queue_call[1]["match_sid"], "1:6")
            self.assertEqual(settings.autoplay_mode.type, "4p_south")
            self.assertEqual(settings.autoplay_mode.room, "silver")
        finally:
            settings.autoplay_mode.type = old_type
            settings.autoplay_mode.room = old_room

    async def test_wait_for_lobby_retries_reconnect_login_after_existing_game_auth_failure(self):
        retry_calls = []

        class FailedReconnectAutomation(MajsoulAutomation):
            async def reconnect_existing_game(self):
                return False

            async def _retry_login_with_reconnect(self):
                retry_calls.append("retry")
                return "busy"

        automation = FailedReconnectAutomation()
        automation.account_busy = True
        automation.login_reconnect = False
        automation.existing_game_info = {
            "connectToken": "stale-token",
            "gameUuid": "busy-game",
        }
        automation.lobby = FakeLobby()

        result = await automation.wait_for_lobby()

        self.assertEqual(result, "busy")
        self.assertEqual(retry_calls, ["retry"])

    async def test_match_auth_failure_marks_account_busy(self):
        class FailingGameAuthAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, target_route_id=None, *, force=False):
                return True

            def _game_route_candidates(self, location="", preferred="route-5"):
                return ["route-5"]

            def _request_route_candidates(self, host_route_id):
                return ["route-2"]

            def _game_ws_urls(self, game_url):
                return []

            async def _open_game_socket(self, **kwargs):
                return False

        automation = FailingGameAuthAutomation()
        automation.account_id = 23744444

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.117:4025",
                "connectToken": "token-consumed-elsewhere",
                "gameUuid": "260602-game-uuid",
                "location": "zone",
            })

        self.assertFalse(automation.in_game)
        self.assertTrue(automation.account_busy)
        self.assertTrue(automation.game_connect_failed.is_set())

    async def test_discard_retry_treats_round_end_as_ack(self):
        sends = []

        class RoundEndAckAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = RoundEndAckAutomation()

        async def fast_sleep(_seconds):
            return None

        round_end_calls = {"count": 0}

        def fake_round_end_counter():
            round_end_calls["count"] += 1
            return 10 if round_end_calls["count"] == 1 else 11

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch("majsoul.client.get_last_operation_context", lambda: {}),
            patch("majsoul.client.get_last_discard_event", lambda: {"counter": 20}),
            patch("majsoul.client.get_round_end_counter", fake_round_end_counter),
        ):
            result = await automation._send_discard_with_retry("3z", False, seat=2)

        self.assertTrue(result)
        self.assertEqual(len(sends), 1)

    async def test_discard_retry_waits_for_late_discard_ack_before_resending(self):
        sends = []

        class LateDiscardAckAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = LateDiscardAckAutomation()

        async def fast_sleep(_seconds):
            return None

        discard_calls = {"count": 0}

        def fake_discard_counter():
            discard_calls["count"] += 1
            return 30 if discard_calls["count"] <= 26 else 31

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch("majsoul.client.get_last_operation_context", lambda: {}),
            patch(
                "majsoul.client.get_last_discard_event",
                lambda: (
                    {"counter": 30}
                    if fake_discard_counter() <= 30
                    else {"counter": 31, "actor": 2, "tile": "4z", "pai": "N"}
                ),
            ),
            patch("majsoul.client.get_round_end_counter", lambda: 40),
        ):
            result = await automation._send_discard_with_retry("4z", False, seat=2)

        self.assertTrue(result)
        self.assertEqual(len(sends), 1)

    async def test_discard_rpc_ok_fails_without_broadcast_ack(self):
        sends = []

        class AcceptedDiscardAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = AcceptedDiscardAutomation()

        async def fast_sleep(_seconds):
            return None

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch("majsoul.client.get_last_operation_context", lambda: {}),
            patch("majsoul.client.get_last_discard_event", lambda: {"counter": 50}),
            patch("majsoul.client.get_round_end_counter", lambda: 60),
        ):
            result = await automation._send_discard_with_retry("4z", False, seat=2)

        self.assertFalse(result)
        self.assertEqual(len(sends), 1)

    async def test_stale_discard_action_is_ignored_after_self_discard_broadcast(self):
        sends = []

        class StaleDiscardAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = StaleDiscardAutomation()

        with (
            patch("majsoul.client.get_last_operation_list", lambda: []),
            patch(
                "majsoul.client.get_last_discard_event",
                lambda: {"counter": 10, "actor": 1, "tile": "6s", "received_monotonic": 100.0},
            ),
            patch("majsoul.client.time.monotonic", lambda: 101.0),
        ):
            result = await automation._send_discard_with_retry("8p", False, seat=1)

        self.assertTrue(result)
        self.assertEqual(sends, [])

    async def test_discard_retry_fails_on_mismatched_self_ack(self):
        sends = []

        class MismatchedDiscardAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = MismatchedDiscardAutomation()

        async def fast_sleep(_seconds):
            return None

        discard_events = [
            {"counter": 50},
            {"counter": 51, "actor": 2, "tile": "7z", "pai": "C"},
        ]

        def fake_last_discard_event():
            if len(discard_events) == 1:
                return discard_events[0]
            return discard_events.pop(0)

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch("majsoul.client.get_last_operation_context", lambda: {}),
            patch("majsoul.client.get_last_discard_event", fake_last_discard_event),
            patch("majsoul.client.get_round_end_counter", lambda: 60),
        ):
            result = await automation._send_discard_with_retry("4z", False, seat=2)

        self.assertFalse(result)
        self.assertEqual(len(sends), 1)

    async def test_new_round_dealer_tsumogiri_waits_then_sends_hand_discard(self):
        sends = []
        sleeps = []

        class NewRoundDealerAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = NewRoundDealerAutomation()

        async def fast_sleep(_seconds):
            sleeps.append(_seconds)
            return None

        discard_events = [
            {"counter": 80},
            {"counter": 81, "actor": 3, "tile": "9p", "pai": "9p"},
        ]

        def fake_last_discard_event():
            if len(discard_events) == 1:
                return discard_events[0]
            return discard_events.pop(0)

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch(
                "majsoul.client.get_last_operation_context",
                lambda: {"source": "ActionNewRound", "seat": 3, "received_monotonic": 100.0},
            ),
            patch("majsoul.client.get_last_discard_event", fake_last_discard_event),
            patch("majsoul.client.get_round_end_counter", lambda: 90),
            patch("majsoul.client.time.monotonic", lambda: 100.0),
        ):
            result = await automation._send_discard_with_retry("9p", True, seat=3)

        self.assertTrue(result)
        self.assertEqual(len(sends), 1)
        self.assertFalse(sends[0]["moqie"])
        self.assertGreaterEqual(sleeps[0], 10.0)

    async def test_new_round_discard_drops_if_window_changes_during_wait(self):
        sends = []
        sleeps = []
        contexts = [
            {"source": "ActionNewRound", "seat": 1, "received_monotonic": 100.0},
            {"source": "ActionTsumo", "seat": 1, "received_monotonic": 112.0},
        ]

        class ChangedNewRoundAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = ChangedNewRoundAutomation()

        async def fast_sleep(_seconds):
            sleeps.append(_seconds)
            return None

        def fake_last_operation_context():
            if len(contexts) == 1:
                return contexts[0]
            return contexts.pop(0)

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch("majsoul.client.get_last_operation_context", fake_last_operation_context),
            patch("majsoul.client.get_last_discard_event", lambda: {"counter": 80}),
            patch("majsoul.client.get_round_end_counter", lambda: 90),
            patch("majsoul.client.time.monotonic", lambda: 100.0),
        ):
            result = await automation._send_discard_with_retry("3z", False, seat=1)

        self.assertTrue(result)
        self.assertEqual(sends, [])
        self.assertGreaterEqual(sleeps[0], 10.0)

    async def test_restored_new_round_dealer_discard_does_not_wait_again(self):
        sends = []
        sleeps = []

        class RestoredNewRoundAutomation(MajsoulAutomation):
            async def _send_input_operation(self, params):
                sends.append(params)
                return True

        automation = RestoredNewRoundAutomation()

        async def fast_sleep(_seconds):
            sleeps.append(_seconds)
            return None

        discard_events = [
            {"counter": 80},
            {"counter": 81, "actor": 1, "tile": "2z", "pai": "S"},
        ]

        def fake_last_discard_event():
            if len(discard_events) == 1:
                return discard_events[0]
            return discard_events.pop(0)

        with (
            patch("majsoul.client.asyncio.sleep", fast_sleep),
            patch("majsoul.client.get_last_operation_list", lambda: [{"type": 1}]),
            patch(
                "majsoul.client.get_last_operation_context",
                lambda: {
                    "source": "ActionNewRound",
                    "seat": 1,
                    "received_monotonic": 100.0,
                    "passedWaitingTime": 14,
                    "syncing": True,
                },
            ),
            patch("majsoul.client.get_last_discard_event", fake_last_discard_event),
            patch("majsoul.client.get_round_end_counter", lambda: 90),
            patch("majsoul.client.time.monotonic", lambda: 100.0),
        ):
            result = await automation._send_discard_with_retry("2z", False, seat=1)

        self.assertTrue(result)
        self.assertEqual(len(sends), 1)
        self.assertLess(max(sleeps), 1.0)

    async def test_match_auth_failure_continues_route_scan(self):
        attempts = []

        class AuthFailureAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, target_route_id=None, *, force=False):
                return True

            def _game_route_candidates(self, location="", preferred="route-2"):
                return ["route-2", "route-3"]

            def _request_route_candidates(self, host_route_id):
                return [host_route_id]

            def _game_ws_urls(self, game_url):
                return []

            async def _open_game_socket(self, **kwargs):
                attempts.append(kwargs["route_id"])
                self.last_game_auth_failed = True
                return False

        automation = AuthFailureAutomation()
        automation.account_id = 23744444

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.117:4025",
                "connectToken": "single-use-token",
                "gameUuid": "260602-game-uuid",
                "location": "zone",
            })

        self.assertEqual(attempts, ["route-2", "route-3"])
        self.assertTrue(automation.account_busy)

    async def test_match_game_socket_prefers_lobby_main_route_over_prepared_standby(self):
        attempts = []

        class PreparedRouteAutomation(MajsoulAutomation):
            def _game_ws_urls(self, game_url):
                return []

            async def _open_game_socket(self, **kwargs):
                attempts.append(kwargs["route_id"])
                return True

        automation = PreparedRouteAutomation()
        automation.account_id = 23744444
        automation.lobby_route_id = "route-2"
        automation.route_prep_route_id = "route-6"
        automation.route_prep = SimpleNamespace(ws=True)
        automation.route_entries = [
            {"id": "route-2", "domain": "route-2.maj-soul.com:443", "ssl": True, "order": 2},
            {"id": "route-6", "domain": "route-6.maj-soul.com:443", "ssl": True, "order": 6},
        ]

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.117:4025",
                "connectToken": "connect-token",
                "gameUuid": "260602-game-uuid",
                "location": "local",
            })

        self.assertEqual(attempts[0], "route-2")
        self.assertTrue(automation.in_game)

    async def test_local_match_uses_game_gateway_tail_before_direct_game_url(self):
        attempts = []

        class ZoneGameGatewayAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, target_route_id=None, *, force=False):
                self.route_prep_route_id = "route-6"
                return True

            def _game_ws_urls(self, game_url):
                return [f"ws://{game_url}/gateway"]

            async def _open_game_socket(self, **kwargs):
                attempts.append(kwargs)
                return True

        automation = ZoneGameGatewayAutomation()
        automation.account_id = 23744444
        automation.lobby_route_id = "route-2"
        automation.route_entries = [
            {"id": "route-2", "domain": "route-2.maj-soul.com:443", "ssl": True, "order": 2},
            {"id": "route-6", "domain": "route-6.maj-soul.com:443", "ssl": True, "order": 6},
        ]

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.133:4034",
                "connectToken": "connect-token",
                "gameUuid": "260602-game-uuid",
                "location": "local",
            })

        self.assertEqual(attempts[0]["ws_url"], "wss://route-2.maj-soul.com:443/game-gateway")
        self.assertEqual(attempts[0]["request_route_id"], "route-2")

    async def test_zone_match_uses_game_gateway_zone_tail(self):
        attempts = []

        class ZoneGameGatewayAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, target_route_id=None, *, force=False):
                self.route_prep_route_id = "route-6"
                return True

            def _game_ws_urls(self, game_url):
                return [f"ws://{game_url}/gateway"]

            async def _open_game_socket(self, **kwargs):
                attempts.append(kwargs)
                return True

        automation = ZoneGameGatewayAutomation()
        automation.account_id = 23744444
        automation.lobby_route_id = "route-2"
        automation.route_entries = [
            {"id": "route-2", "domain": "route-2.maj-soul.com:443", "ssl": True, "order": 2},
            {"id": "route-6", "domain": "route-6.maj-soul.com:443", "ssl": True, "order": 6},
        ]

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.133:4034",
                "connectToken": "connect-token",
                "gameUuid": "260602-game-uuid",
                "location": "zone",
            })

        self.assertEqual(
            attempts[0]["ws_url"],
            "wss://route-2.maj-soul.com:443/game-gateway-zone",
        )
        self.assertEqual(attempts[0]["request_route_id"], "route-2")

    async def test_direct_game_url_connection_failure_moves_to_next_scheme(self):
        attempts = []

        class DirectSchemeFallbackAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, target_route_id=None, *, force=False):
                self.route_prep_route_id = "route-6"
                return True

            def _game_ws_urls(self, game_url):
                return [
                    f"ws://{game_url}/gateway",
                    f"wss://{game_url}/gateway",
                ]

            async def _open_game_socket(self, **kwargs):
                attempts.append((kwargs["ws_url"], kwargs["request_route_id"]))
                if kwargs["ws_url"].startswith("wss://route-"):
                    self.last_game_connect_failed = True
                    return False
                if kwargs["ws_url"].startswith("ws://"):
                    self.last_game_connect_failed = True
                    return False
                self.last_game_connect_failed = False
                return True

        automation = DirectSchemeFallbackAutomation()
        automation.account_id = 23744444
        automation.lobby_route_id = "route-2"
        automation.route_entries = [
            {"id": "route-2", "domain": "route-2.maj-soul.com:443", "ssl": True, "order": 2},
            {"id": "route-6", "domain": "route-6.maj-soul.com:443", "ssl": True, "order": 6},
        ]

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            await automation._connect_game({
                "gameUrl": "172.30.16.133:4034",
                "connectToken": "connect-token",
                "gameUuid": "260602-game-uuid",
                "location": "zone",
            })

        self.assertEqual(
            attempts,
            [
                ("wss://route-2.maj-soul.com:443/game-gateway-zone", "route-2"),
                ("wss://route-6.maj-soul.com:443/game-gateway-zone", "route-6"),
                ("ws://172.30.16.133:4034/gateway", "route-2"),
                ("wss://172.30.16.133:4034/gateway", "route-2"),
            ],
        )

    async def test_reconnect_existing_game_stops_route_scan_after_shutdown(self):
        attempts = []

        class InterruptibleReconnectAutomation(MajsoulAutomation):
            async def _prepare_game_route(self, *args, **kwargs):
                return True

            def _game_route_candidates(self, location="", preferred="route-2"):
                return ["route-2", "route-6"]

            def _request_route_candidates(self, host_route_id):
                return [host_route_id]

            async def _open_game_socket(self, **kwargs):
                attempts.append(kwargs["route_id"])
                return False

        automation = InterruptibleReconnectAutomation(
            should_continue=lambda: len(attempts) == 0,
        )
        automation.account_id = 23744444
        automation.lobby = FakeLobby()

        reconnected = await automation.reconnect_existing_game()

        self.assertFalse(reconnected)
        self.assertEqual(attempts, ["route-2"])

    async def test_prepare_specific_route_uses_prepare_login_without_route_change_by_default(self):
        automation = MajsoulAutomation()
        automation.access_token = "access-token"
        automation.lobby_route_id = "route-2"
        fake = FakeRoutePrep()

        with patch("majsoul.client.LiqiSocket", lambda *args, **kwargs: fake):
            prepared = await automation._prepare_game_route(target_route_id="route-5", force=True)

        self.assertTrue(prepared)
        methods = [call[0] for call in fake.calls if isinstance(call, tuple)]
        self.assertEqual(
            methods,
            [
                "connect",
                ".lq.Route.requestConnection",
                ".lq.Lobby.prepareLogin",
            ],
        )
        route_body = fake.calls[1][1]
        route_fields = fromProtobuf(route_body)
        self.assertEqual(route_fields[0]["data"], 2)

    async def test_prepare_game_route_uses_fetched_client_endpoint(self):
        automation = MajsoulAutomation()
        automation.access_token = "access-token"
        automation.lobby_route_id = "route-2"
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchConnectionInfo": {
                "data": {
                    "clientEndpoint": {
                        "family": "IPv4",
                        "address": "23.249.17.217",
                        "port": 42272,
                    }
                }
            }
        })
        fake = FakeRoutePrep()
        socket_args = []

        def fake_socket(*args, **kwargs):
            socket_args.append((args, kwargs))
            return fake

        with patch("majsoul.client.LiqiSocket", fake_socket):
            prepared = await automation._prepare_game_route(target_route_id="route-5", force=True)

        self.assertTrue(prepared)
        self.assertEqual(socket_args[0][0][1], "wss://23.249.17.217:42272/gateway")
        route_body = fake.calls[1][1]
        route_fields = fromProtobuf(route_body)
        self.assertEqual(route_fields[0]["data"], 2)
        self.assertEqual(route_fields[1]["data"], b"route-5")

    async def test_prepare_game_route_prefers_historical_prep_route_order_without_target(self):
        automation = MajsoulAutomation()
        automation.access_token = "access-token"
        automation.lobby_route_id = "route-2"
        automation.route_entries = [
            {"id": "route-2", "domain": "route-2.maj-soul.com:443", "ssl": True, "order": 2},
            {"id": "route-3", "domain": "route-3.maj-soul.com:8443", "ssl": True, "order": 3},
            {"id": "route-4", "domain": "route-4.maj-soul.com:443", "ssl": True, "order": 4},
            {"id": "route-5", "domain": "route-5.maj-soul.com:443", "ssl": True, "order": 5},
            {"id": "route-6", "domain": "route-6.maj-soul.com:443", "ssl": True, "order": 6},
        ]
        fake = FakeRoutePrep()

        with patch("majsoul.client.LiqiSocket", lambda *args, **kwargs: fake):
            prepared = await automation._prepare_game_route(force=True)

        self.assertTrue(prepared)
        route_body = fake.calls[1][1]
        route_fields = fromProtobuf(route_body)
        self.assertEqual(route_fields[1]["data"], b"route-6")

    async def test_prepare_game_route_short_probes_client_endpoint_before_route_url(self):
        automation = MajsoulAutomation()
        automation.access_token = "access-token"
        automation.lobby_route_id = "route-2"
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchConnectionInfo": {
                "data": {
                    "clientEndpoint": {
                        "family": "IPv4",
                        "address": "23.249.17.217",
                        "port": 42272,
                    }
                }
            }
        })
        fake = FakeRoutePrep()

        with patch("majsoul.client.LiqiSocket", lambda *args, **kwargs: fake):
            prepared = await automation._prepare_game_route(target_route_id="route-5", force=True)

        self.assertTrue(prepared)
        self.assertEqual(fake.calls[0], ("connect", 3))

    async def test_prepare_specific_route_can_send_explicit_route_change(self):
        automation = MajsoulAutomation()
        automation.access_token = "access-token"
        automation.lobby_route_id = "route-2"
        fake = FakeRoutePrep()

        with patch("majsoul.client.LiqiSocket", lambda *args, **kwargs: fake):
            prepared = await automation._prepare_game_route(
                target_route_id="route-5",
                force=True,
                change_route=True,
            )

        self.assertTrue(prepared)
        methods = [call[0] for call in fake.calls if isinstance(call, tuple)]
        self.assertEqual(
            methods,
            [
                "connect",
                ".lq.Route.requestConnection",
                ".lq.Route.requestRouteChange",
                ".lq.Lobby.prepareLogin",
            ],
        )
        route_body = fake.calls[1][1]
        route_fields = fromProtobuf(route_body)
        self.assertEqual(route_fields[0]["data"], 3)

    async def test_game_socket_requests_host_route_before_global_default(self):
        automation = MajsoulAutomation()

        self.assertEqual(
            automation._request_route_candidates("route-6"),
            ["route-6", "route-2"],
        )
        self.assertEqual(
            automation._request_route_candidates("route-2"),
            ["route-2"],
        )

    async def test_wait_until_account_free_clears_busy_when_gaming_info_disappears(self):
        automation = MajsoulAutomation()
        automation.account_busy = True
        automation.existing_game_info = {"gameUuid": "old-game"}
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": SequenceResponse(
                {
                    "data": {
                        "gameInfo": {
                            "connectToken": "still-token",
                            "gameUuid": "still-game",
                            "location": "zone",
                        }
                    }
                },
                {"data": {}},
            )
        })

        async def fast_sleep(_seconds):
            return None

        with patch("majsoul.client.asyncio.sleep", fast_sleep):
            result = await automation.wait_until_account_free(
                poll_interval=0.01,
                should_continue=lambda: True,
            )

        self.assertEqual(result, "lobby")
        self.assertFalse(automation.account_busy)
        self.assertIsNone(automation.existing_game_info)
        fetch_calls = [
            call for call in automation.lobby.calls
            if call[0] == ".lq.Lobby.fetchGamingInfo"
        ]
        self.assertEqual(len(fetch_calls), 2)

    async def test_wait_until_account_free_returns_busy_on_server_cooldown(self):
        automation = MajsoulAutomation()
        automation.account_busy = True
        automation.lobby = FakeLobby({
            ".lq.Lobby.fetchGamingInfo": {
                "data": {"error": {"code": 503, "u32Params": [1, 1780389194, 7200]}}
            },
        })

        result = await automation.wait_until_account_free(
            poll_interval=0.01,
            should_continue=lambda: True,
        )

        self.assertEqual(result, "busy")
        self.assertTrue(automation.account_busy)
        self.assertIsNone(automation.existing_game_info)
        self.assertEqual(automation.account_cooldown_until, 1780396394)


class SessionResultTests(unittest.TestCase):
    def test_busy_session_uses_long_retry_delay(self):
        result = SessionResult(games=0, state="busy")

        self.assertGreaterEqual(_session_restart_delay(result), 60)

    def test_busy_session_with_retry_at_waits_until_server_cooldown_expires(self):
        result = SessionResult(games=0, state="busy", retry_at=1780396394)

        with patch("run_autoplay.time.time", return_value=1780396300):
            self.assertEqual(_session_restart_delay(result), 99)

    def test_login_failure_with_cooldown_returns_busy_session(self):
        automation = MajsoulAutomation()
        automation.account_busy = True
        automation.account_cooldown_until = 1780396394

        result = _login_failure_session_result(automation, 2)

        self.assertEqual(result, SessionResult(2, "busy", retry_at=1780396394))


class InterruptibleSleepTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_running = autoplay_runner.running

    async def asyncTearDown(self):
        autoplay_runner.running = self.old_running

    async def test_restart_sleep_returns_immediately_after_shutdown(self):
        autoplay_runner.running = False

        started_at = time.monotonic()
        await autoplay_runner._sleep_while_running(60, tick=0.01)

        self.assertLess(time.monotonic() - started_at, 0.1)


class SessionStartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.old_running = autoplay_runner.running

    async def asyncTearDown(self):
        autoplay_runner.running = self.old_running

    async def test_run_session_logs_in_before_play_loop(self):
        events = []

        class FakeAutomation:
            def __init__(self, *args, **kwargs):
                events.append("automation-init")

            async def login(self, username, password):
                events.append(("login", username, password))
                return False

            async def close(self):
                events.append("close")

        class FakeJsonlLogger:
            def close(self):
                events.append("jsonl-close")

        class FakeBot:
            player_id = 0

        message_client = SimpleNamespace(
            messages=None,
            ws_disconnected=threading.Event(),
        )

        with (
            patch("run_autoplay.MajsoulAutomation", FakeAutomation),
            patch("run_autoplay.Controller", lambda: object()),
            patch("run_autoplay.MjaiStateTracker", FakeBot),
            patch("run_autoplay.JsonlLogger", FakeJsonlLogger),
            patch("run_autoplay.process_messages", lambda *args, **kwargs: None),
        ):
            result = await run_session(0, message_client)

        self.assertEqual(result, SessionResult(0))
        self.assertEqual(events[0], "automation-init")
        self.assertTrue(any(event[0] == "login" for event in events if isinstance(event, tuple)))

    async def test_run_session_does_not_count_recovered_action_failure_as_completed_game(self):
        events = []
        autoplay_runner.running = True

        class FakeAutomation:
            def __init__(self, *args, **kwargs):
                events.append("automation-init")

            async def login(self, username, password):
                return True

            async def wait_for_lobby(self):
                return "game"

            async def execute_action(self, action, seat):
                events.append(("execute", action, seat))
                return False

            async def recover(self):
                events.append("recover")
                return "lobby"

            async def handle_end_game(self):
                events.append("handle_end_game")
                autoplay_runner.running = False
                return True

            async def close(self):
                events.append("close")

        class FakeJsonlLogger:
            def close(self):
                events.append("jsonl-close")

        class FakeBot:
            player_id = 0

        def fake_game_loop(*args):
            with autoplay_runner.pending_action_lock:
                autoplay_runner.pending_action = {
                    "type": "dahai",
                    "pai": "1m",
                    "tsumogiri": True,
                }

        message_client = SimpleNamespace(
            messages=None,
            ws_disconnected=threading.Event(),
        )

        with (
            patch("run_autoplay.MajsoulAutomation", FakeAutomation),
            patch("run_autoplay.Controller", lambda: object()),
            patch("run_autoplay.MjaiStateTracker", FakeBot),
            patch("run_autoplay.JsonlLogger", FakeJsonlLogger),
            patch("run_autoplay.game_loop", fake_game_loop),
        ):
            result = await run_session(0, message_client)

        self.assertEqual(result, SessionResult(0))
        self.assertIn("recover", events)
        self.assertNotIn("handle_end_game", events)


class ProtocolMessageClientTests(unittest.TestCase):
    def test_protocol_message_client_drains_in_process_queue(self):
        client = ProtocolMessageClient()
        client.start()
        client.messages.put({"type": "start_game", "id": 0})
        client.messages.put({"type": "end_game"})

        self.assertTrue(client.running)
        self.assertEqual(
            client.dump_messages(),
            [{"type": "start_game", "id": 0}, {"type": "end_game"}],
        )
        self.assertEqual(client.dump_messages(), [])

        client.stop()
        self.assertFalse(client.running)


class ProtocolConstantTests(unittest.TestCase):
    def test_uses_current_unity_web_client_version(self):
        self.assertEqual(RESOURCE_VERSION, "0.16.229")
        self.assertEqual(PACKAGE_VERSION, "4.0.44")
        self.assertEqual(CLIENT_VERSION_STRING, "WebGL_2022-0.16.229")


class MinimalSettingsTests(unittest.TestCase):
    def test_settings_expose_only_four_player_runtime_fields(self):
        self.assertEqual(
            sorted(get_settings().keys()),
            ["autoplay_account", "autoplay_mode", "autoplay_time", "model_path"],
        )

    def test_schema_rejects_three_player_modes(self):
        mode_type_schema = get_schema()["properties"]["autoplay_mode"]["properties"]["type"]

        self.assertEqual(mode_type_schema["enum"], ["4p_south", "4p_east"])
        self.assertFalse(verify_settings({
            "model_path": "mjai_bot/mortal/mortal.pth",
            "autoplay_account": {"username": "u", "password": "p"},
            "autoplay_mode": {"type": "3p_east", "room": "bronze"},
            "autoplay_time": {"rand_min": 1.0, "rand_max": 3.0},
        }))

    def test_client_version_string_strips_resource_suffix(self):
        self.assertEqual(_client_version_string("0.12.345.w"), "WebGL_2022-0.12.345")

    def test_refresh_client_version_ignores_legacy_laya_version_json(self):
        automation = MajsoulAutomation()

        with patch("majsoul.client._fetch_current_resource_version", return_value="0.11.252.w"):
            asyncio.run(automation._refresh_client_version())

        self.assertEqual(automation.resource_version, "0.16.229")
        self.assertEqual(automation.client_version_string, "WebGL_2022-0.16.229")

    def test_login_payload_uses_dynamic_instance_version(self):
        automation = MajsoulAutomation()
        automation.resource_version = "0.12.345.w"
        automation.package_version = "4.0.45"
        automation.client_version_string = "web-0.12.345"

        payload = automation._login_payload("user@example.com", "secret")

        self.assertEqual(payload["clientVersion"]["resource"], "0.12.345.w")
        self.assertEqual(payload["clientVersion"]["package"], "4.0.45")
        self.assertEqual(payload["clientVersionString"], "web-0.12.345")

    def test_route_three_uses_8443_gateway_port(self):
        self.assertEqual(
            _route_ws_url("route-3"),
            "wss://route-3.maj-soul.com:8443/gateway",
        )

    def test_lobby_websocket_candidates_include_all_public_routes(self):
        automation = MajsoulAutomation()

        self.assertEqual(
            automation._lobby_ws_url_candidates(),
            [
                "wss://route-2.maj-soul.com:443/gateway",
                "wss://route-3.maj-soul.com:8443/gateway",
                "wss://route-4.maj-soul.com:443/gateway",
                "wss://route-5.maj-soul.com:443/gateway",
                "wss://route-6.maj-soul.com:443/gateway",
            ],
        )

    def test_lobby_websocket_candidates_follow_dynamic_route_entries(self):
        automation = MajsoulAutomation()
        automation.route_entries = [
            {
                "id": "route-8",
                "domain": "route-8.example.com:9443",
                "ssl": False,
                "state": "idle",
                "order": 1,
            },
            {
                "id": "route-9",
                "domain": "route-9.example.com:443",
                "ssl": True,
                "state": "idle",
                "order": 2,
            },
        ]

        self.assertEqual(
            automation._lobby_ws_url_candidates(),
            [
                "ws://route-8.example.com:9443/gateway",
                "wss://route-9.example.com:443/gateway",
            ],
        )

    def test_game_route_candidates_follow_dynamic_route_order(self):
        automation = MajsoulAutomation()
        automation.route_entries = [
            {
                "id": "route-4",
                "domain": "route-4.maj-soul.com:443",
                "ssl": True,
                "state": "idle",
                "order": 4,
            },
            {
                "id": "route-6",
                "domain": "route-6.maj-soul.com:443",
                "ssl": True,
                "state": "idle",
                "order": 6,
            },
        ]

        self.assertEqual(
            automation._game_route_candidates(location="zone", preferred="route-6"),
            ["route-6", "route-4"],
        )

class ErrorFormattingTests(unittest.TestCase):
    def test_format_error_includes_structured_params(self):
        text = _format_error({
            "code": 6,
            "u32Params": [12],
            "strParams": ["missing"],
            "jsonParam": '{"reason":"route"}',
        })

        self.assertIn("code=6", text)
        self.assertIn("u32=[12]", text)
        self.assertIn("str=['missing']", text)
        self.assertIn('json={"reason":"route"}', text)


class MajsoulBridgeRuntimeStateTests(unittest.TestCase):
    def test_new_round_with_fourteen_tiles_uses_protocol_draw_tile_not_sorted_tail(self):
        bridge = MajsoulBridge()
        bridge.seat = 0
        first_thirteen = [
            "9s", "2m", "3m", "4m", "5m", "6m", "7m",
            "8m", "1p", "2p", "3p", "4p", "5p",
        ]
        draw_tile = "1m"

        messages = bridge.parse_liqi(
            {
                "method": ".lq.ActionPrototype",
                "type": 1,
                "data": {
                    "name": "ActionNewRound",
                    "data": {
                        "chang": 0,
                        "doras": ["1p"],
                        "ben": 0,
                        "ju": 0,
                        "liqibang": 0,
                        "scores": [25000, 25000, 25000, 25000],
                        "tiles": [*first_thirteen, draw_tile],
                    },
                },
            }
        )

        self.assertEqual(messages[1], {"type": "tsumo", "actor": 0, "pai": "1m"})
        self.assertNotIn("1m", messages[0]["tehais"][0])
        self.assertIn("9s", messages[0]["tehais"][0])

    def test_new_round_unknown_opponent_hands_are_independent_lists(self):
        bridge = MajsoulBridge()
        bridge.seat = 0

        messages = bridge.parse_liqi(
            {
                "method": ".lq.ActionPrototype",
                "type": 1,
                "data": {
                    "name": "ActionNewRound",
                    "data": {
                        "chang": 0,
                        "doras": ["1p"],
                        "ben": 0,
                        "ju": 0,
                        "liqibang": 0,
                        "scores": [25000, 25000, 25000, 25000],
                        "tiles": [
                            "1m", "2m", "3m", "4m", "5m", "6m", "7m",
                            "8m", "9m", "1p", "2p", "3p", "4p",
                        ],
                    },
                },
            }
        )

        tehais = messages[0]["tehais"]
        self.assertIsNot(tehais[1], tehais[2])
        tehais[1][0] = "changed"
        self.assertEqual(tehais[2][0], "?")

    def test_operation_list_clears_when_next_action_has_no_operation_window(self):
        bridge = MajsoulBridge()
        bridge.parse_liqi(
            {
                "method": ".lq.ActionPrototype",
                "type": 1,
                "data": {
                    "name": "ActionDealTile",
                    "data": {
                        "seat": 0,
                        "tile": "",
                        "operation": {
                            "operationList": [
                                {"type": 7, "combination": ["1m"]},
                            ]
                        },
                    },
                },
            }
        )
        self.assertEqual(get_last_operation_list(), [{"type": 7, "combination": ["1m"]}])
        context = get_last_operation_context()
        self.assertEqual(context["source"], "ActionDealTile")
        self.assertIsNone(context["seat"])
        self.assertIsNone(context["timeAdd"])
        self.assertIsNone(context["timeFixed"])
        self.assertFalse(context["syncing"])
        self.assertEqual(context["passedWaitingTime"], 0)
        self.assertIn("received_monotonic", context)

        bridge.parse_liqi(
            {
                "method": ".lq.ActionPrototype",
                "type": 1,
                "data": {
                    "name": "ActionDealTile",
                    "data": {
                        "seat": 1,
                        "tile": "",
                    },
                },
            }
        )

        self.assertEqual(get_last_operation_list(), [])
        self.assertEqual(get_last_operation_context(), {})

    def test_discard_event_tracks_actor_and_tile(self):
        bridge = MajsoulBridge()

        bridge.parse_liqi(
            {
                "method": ".lq.ActionPrototype",
                "type": 1,
                "data": {
                    "name": "ActionDiscardTile",
                    "data": {
                        "seat": 2,
                        "tile": "7z",
                        "moqie": False,
                        "isLiqi": False,
                    },
                },
            }
        )

        event = get_last_discard_event()
        self.assertGreater(event["counter"], 0)
        self.assertEqual(event["actor"], 2)
        self.assertEqual(event["tile"], "7z")
        self.assertEqual(event["pai"], "C")
        self.assertFalse(event["moqie"])


class LiqiProtoResourceTests(unittest.TestCase):
    def test_constructor_closes_liqi_json_file(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            proto = LiqiProto()
            del proto
            gc.collect()

        resource_warnings = [
            warning
            for warning in caught
            if issubclass(warning.category, ResourceWarning)
        ]
        self.assertEqual(resource_warnings, [])
