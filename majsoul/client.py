"""Majsoul autoplay via the Liqi WebSocket protocol.

The current Majsoul web client is Unity WebGL. The old Laya globals
(`uiscript`, `app.NetAgent`, `GameMgr`) no longer exist, so browser-side JS
runtime calls cannot drive login/matching/actions anymore. This module keeps
the original bot pipeline, but talks to the server directly with the same
protobuf protocol the client uses.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import queue
import random
import ssl
import struct
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse
import uuid
from typing import Any, Callable, Literal

import websockets

from .bridge import (
    MajsoulBridge,
    get_discard_counter,
    get_last_discard_event,
    get_last_operation_context,
    get_last_operation_list,
    get_round_end_counter,
)
from .protocol import LiqiProto, MsgType, toProtobuf
from settings.settings import settings
from .logger import logger


MAJSOUL_ROUTE_ID = "route-2"
MAJSOUL_LOBBY_ROUTE_CANDIDATES = ("route-2", "route-3", "route-4", "route-5", "route-6")
MAJSOUL_GAME_ROUTE_ID = MAJSOUL_ROUTE_ID
MAJSOUL_GAME_ROUTE_CANDIDATES = ("route-6", "route-5", "route-4", "route-3", "route-2")
MAJSOUL_PREP_ROUTE_CANDIDATES = ("route-6", "route-5", "route-4", "route-3", "route-2")
MAJSOUL_VERSION_URL = "https://game.maj-soul.com/1/version.json"
MAJSOUL_CONFIG_URL_TEMPLATE = "https://game.maj-soul.com/1/v{version}/config.json"
MAJSOUL_CLIENTGATE_ROUTES_PATH = "/api/clientgate/routes"
RESOURCE_VERSION = "0.16.229"
PACKAGE_VERSION = "4.0.44"
CLIENT_VERSION_STRING = f"WebGL_2022-{RESOURCE_VERSION.removesuffix('.w')}"
LOGIN_BEAT_CONTRACT = "DF2vkXCnfeXp4WoGrBGNcJBufZiMN3uP"
SERVER_COOLDOWN_ERROR_CODE = 503

StartMatchResult = Literal["queued", "busy", "error"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _client_version_string(resource_version: str) -> str:
    return f"WebGL_2022-{resource_version.removesuffix('.w')}"


def _fetch_json(url: str, timeout: float = 8) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Cache-Control": "no-cache", "User-Agent": UA},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_current_resource_version(timeout: float = 8) -> str | None:
    try:
        payload = _fetch_json(MAJSOUL_VERSION_URL, timeout=timeout)
        version = str(payload.get("version") or "").strip()
        return version or None
    except Exception as exc:
        logger.warning(f"Failed to fetch Majsoul version.json: {exc!r}")
        return None


def _default_route_entries() -> list[dict[str, Any]]:
    return [
        {
            "id": route_id,
            "domain": f"{route_id}.maj-soul.com:{8443 if route_id == 'route-3' else 443}",
            "ssl": True,
            "state": "idle",
            "order": index,
        }
        for index, route_id in enumerate(MAJSOUL_LOBBY_ROUTE_CANDIDATES, start=2)
    ]


def _route_entry_from_url(route_id: str, url: str, *, order: int = 0) -> dict[str, Any] | None:
    parsed = urlparse(url)
    if not parsed.hostname:
        return None
    ssl_enabled = parsed.scheme != "http"
    port = parsed.port or (443 if ssl_enabled else 80)
    return {
        "id": route_id,
        "domain": f"{parsed.hostname}:{port}",
        "ssl": ssl_enabled,
        "state": "idle",
        "order": order,
    }


def _normalize_route_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        route_id = str(entry.get("id") or "").strip()
        if not route_id:
            continue

        state = str(entry.get("state") or "idle").strip().lower()
        if state and state != "idle":
            continue

        ssl_enabled = bool(entry.get("ssl", True))
        domain = str(entry.get("domain") or "").strip().rstrip("/")
        if "://" in domain:
            parsed = urlparse(domain)
            if not parsed.hostname:
                continue
            ssl_enabled = parsed.scheme != "http"
            domain = f"{parsed.hostname}:{parsed.port or (443 if ssl_enabled else 80)}"
        elif not domain:
            from_url = _route_entry_from_url(route_id, str(entry.get("url") or ""), order=index)
            if not from_url:
                continue
            normalized.append(from_url)
            continue
        elif "/" in domain:
            domain = domain.split("/", 1)[0]

        if ":" not in domain:
            domain = f"{domain}:{443 if ssl_enabled else 80}"
        try:
            order = int(entry.get("order") if entry.get("order") is not None else index)
        except (TypeError, ValueError):
            order = index

        normalized.append(
            {
                "id": route_id,
                "domain": domain,
                "ssl": ssl_enabled,
                "state": "idle",
                "order": order,
            }
        )

    normalized.sort(key=lambda item: (int(item.get("order") or 0), str(item.get("id") or "")))
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in normalized:
        route_id = str(entry.get("id") or "")
        if route_id in seen:
            continue
        seen.add(route_id)
        deduped.append(entry)
    return deduped


def _route_ws_url_from_entry(entry: dict[str, Any], tail: str = "gateway") -> str:
    scheme = "wss" if entry.get("ssl", True) else "ws"
    domain = str(entry["domain"]).rstrip("/")
    return f"{scheme}://{domain}/{tail.strip('/')}"


def _fetch_config_route_entries(resource_version: str, timeout: float = 8) -> list[dict[str, Any]]:
    url = MAJSOUL_CONFIG_URL_TEMPLATE.format(version=resource_version)
    payload = _fetch_json(url, timeout=timeout)
    for group in payload.get("ip") or []:
        gateways = group.get("gateways") or []
        if group.get("name") == "player":
            return _normalize_route_entries(gateways)
    groups = payload.get("ip") or []
    if groups:
        return _normalize_route_entries(groups[0].get("gateways") or [])
    return []


def _clientgate_routes_url(entry: dict[str, Any], resource_version: str) -> str:
    scheme = "https" if entry.get("ssl", True) else "http"
    query = urllib.parse.urlencode({"platform": "Web", "version": resource_version})
    return f"{scheme}://{entry['domain']}{MAJSOUL_CLIENTGATE_ROUTES_PATH}?{query}"


def _fetch_clientgate_route_entries(
    seed_entries: list[dict[str, Any]],
    resource_version: str,
    timeout: float = 8,
) -> list[dict[str, Any]]:
    errors: list[str] = []
    for entry in seed_entries:
        try:
            payload = _fetch_json(_clientgate_routes_url(entry, resource_version), timeout=timeout)
            routes = ((payload.get("data") or {}).get("routes") or [])
            normalized = _normalize_route_entries(routes)
            if normalized:
                return normalized
        except Exception as exc:
            errors.append(f"{entry.get('id', '?')}: {exc!r}")
    if errors:
        logger.warning(f"clientgate routes fetch failed: {'; '.join(errors)}")
    return []

# Match mode IDs from cfg.desktop.matchmode.
MATCH_MODE_IDS = {
    ("4p_east", "bronze"): 2,
    ("4p_south", "bronze"): 3,
    ("4p_east", "silver"): 5,
    ("4p_south", "silver"): 6,
    ("4p_east", "gold"): 8,
    ("4p_south", "gold"): 9,
    ("4p_east", "jade"): 11,
    ("4p_south", "jade"): 12,
    ("4p_east", "throne"): 15,
    ("4p_south", "throne"): 16,
}

RANK_TIER_NAMES = {
    1: "初心",
    2: "雀士",
    3: "雀杰",
    4: "雀豪",
    5: "雀圣",
    6: "魂天",
}


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rank_tier_from_level_id(level_id: int | None) -> int:
    if not level_id:
        return 0
    level_id = int(level_id)
    if level_id >= 10000:
        return (level_id // 100) % 100
    return level_id // 100


def _rank_label(level_id: int | None) -> str:
    tier = _rank_tier_from_level_id(level_id)
    rank_name = RANK_TIER_NAMES.get(tier, "unknown")
    return f"{rank_name}(id={level_id or 0})"


def _target_mode_for_rank_level(level_id: int | None) -> tuple[str, str]:
    tier = _rank_tier_from_level_id(level_id)
    if tier >= 3:
        return ("4p_south", "gold")
    if tier >= 2:
        return ("4p_south", "silver")
    return ("4p_east", "bronze")


MJAI_TO_MS_TILE = {
    "5mr": "0m",
    "5pr": "0p",
    "5sr": "0s",
    "E": "1z",
    "S": "2z",
    "W": "3z",
    "N": "4z",
    "P": "5z",
    "F": "6z",
    "C": "7z",
}

def _tile_kind_for_candidate_match(tile: str) -> str:
    if len(tile) == 2 and tile[0] == "0" and tile[1] in "mps":
        return f"5{tile[1]}"
    return tile


def _select_riichi_declaration_tile(model_tile: str, valid_tiles: list[str]) -> tuple[str, str]:
    if not valid_tiles:
        return model_tile, "model-no-candidates"
    if model_tile in valid_tiles:
        return model_tile, "model-exact"

    model_kind = _tile_kind_for_candidate_match(model_tile)
    for valid_tile in valid_tiles:
        if _tile_kind_for_candidate_match(valid_tile) == model_kind:
            return valid_tile, "model-red-equivalent"

    return valid_tiles[0], "fallback-first-candidate"


OP_DISCARD = 1
OP_CHI = 2
OP_PENG = 3
OP_AN_GANG = 4
OP_MING_GANG = 5
OP_JIA_GANG = 6
OP_LIQI = 7
OP_ZIMO = 8
OP_HU = 9
OP_LIU_JU = 10

OPENING_ROUND_DISCARD_MIN_DELAY = 12.0
STALE_SELF_DISCARD_IGNORE_WINDOW = 5.0


def _route_body(kind: int, route_id: str = MAJSOUL_ROUTE_ID) -> bytes:
    return toProtobuf(
        [
            {"id": 2, "type": "varint", "data": kind},
            {"id": 3, "type": "string", "data": route_id.encode()},
            {"id": 4, "type": "varint", "data": int(time.time() * 1000)},
        ]
    )


def _heartbeat_body(seq: int) -> bytes:
    return toProtobuf(
        [
            {"id": 1, "type": "varint", "data": seq},
            {"id": 2, "type": "varint", "data": 0},
            {"id": 3, "type": "varint", "data": 11},
            {"id": 4, "type": "varint", "data": seq},
        ]
    )


def _route_ws_url(route_id: str, tail: str = "gateway") -> str:
    port = 8443 if route_id == "route-3" else 443
    return f"wss://{route_id}.maj-soul.com:{port}/{tail.strip('/')}"


def _game_gateway_tail(location: str) -> str:
    return "game-gateway" if location == "local" else "game-gateway-zone"


def _endpoint_ws_url(endpoint: dict[str, Any]) -> str | None:
    address = str(endpoint.get("address") or "").strip()
    try:
        port = int(endpoint.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    if not address or not port:
        return None
    return f"wss://{address}:{port}/gateway"


def _prepare_login_body(access_token: str) -> bytes:
    return toProtobuf(
        [
            {"id": 1, "type": "string", "data": access_token.encode()},
            {"id": 2, "type": "varint", "data": 0},
        ]
    )


def _route_change_body(from_route_id: str, to_route_id: str, change_type: int = 2) -> bytes:
    return toProtobuf(
        [
            {"id": 1, "type": "string", "data": from_route_id.encode()},
            {"id": 2, "type": "string", "data": to_route_id.encode()},
            {"id": 3, "type": "varint", "data": change_type},
        ]
    )


def _auth_game_body(account_id: int, token: str, game_uuid: str) -> bytes:
    return toProtobuf(
        [
            {"id": 1, "type": "varint", "data": int(account_id)},
            {"id": 2, "type": "string", "data": token.encode()},
            {"id": 3, "type": "string", "data": game_uuid.encode()},
            {"id": 4, "type": "string", "data": b""},
            {"id": 5, "type": "string", "data": b""},
            {"id": 6, "type": "varint", "data": 0},
        ]
    )


def _pack_raw(msg_id: int, method: str, body: bytes = b"") -> bytes:
    return (
        b"\x02"
        + struct.pack("<H", msg_id)
        + toProtobuf(
            [
                {"id": 1, "type": "string", "data": method.encode()},
                {"id": 2, "type": "string", "data": body},
            ]
        )
    )


def _as_error(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {}
    err = data.get("error") or {}
    return err if isinstance(err, dict) else {}


def _format_error(err: dict[str, Any]) -> str:
    if not err:
        return "code=0"

    code = err.get("code", 0)
    parts = [f"code={code}"]
    u32_params = err.get("u32Params", err.get("u32_params"))
    str_params = err.get("strParams", err.get("str_params"))
    json_param = err.get("jsonParam", err.get("json_param"))
    if u32_params:
        parts.append(f"u32={u32_params}")
    if str_params:
        parts.append(f"str={str_params}")
    if json_param:
        parts.append(f"json={json_param}")
    return " ".join(parts)


def _cooldown_until_from_error(err: dict[str, Any]) -> int | None:
    if int(err.get("code") or 0) != SERVER_COOLDOWN_ERROR_CODE:
        return None
    params = err.get("u32Params", err.get("u32_params")) or []
    if len(params) >= 3:
        try:
            return int(params[1]) + int(params[2])
        except (TypeError, ValueError):
            return None
    if len(params) >= 2:
        try:
            return int(params[1])
        except (TypeError, ValueError):
            return None
    return None


def _format_timestamp(ts: int | None) -> str:
    if not ts:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class LiqiSocket:
    """Small async Liqi request/response wrapper."""

    def __init__(
        self,
        name: str,
        url: str,
        *,
        bridge: MajsoulBridge | None = None,
        messages: queue.Queue | None = None,
        notify_handler: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.name = name
        self.url = url
        self.bridge = bridge
        self.messages = messages
        self.notify_handler = notify_handler
        self.proto = LiqiProto()
        self.msg_id = 1
        self.ws = None
        self.reader_task: asyncio.Task | None = None
        self.pending: dict[int, tuple[asyncio.Future, bool]] = {}
        self.bridge_msg_ids: set[int] = set()
        self.send_lock = asyncio.Lock()
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    async def connect(self, open_timeout: float = 15):
        logger.info(f"Connecting {self.name} websocket: {self.url}")
        base_kwargs = {
            "max_size": None,
            "ping_interval": None,
            "open_timeout": open_timeout,
            "close_timeout": 2,
            "origin": "https://game.maj-soul.com",
            "user_agent_header": UA,
            "extra_headers": {
                "Referer": "https://game.maj-soul.com/1/",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
        }
        if self.url.startswith("wss://"):
            base_kwargs["ssl"] = self.ssl_context

        try:
            self.ws = await websockets.connect(self.url, **base_kwargs)
        except Exception as exc:
            logger.warning(f"{self.name} websocket connect failed: {exc!r}")
            self.ws = None
        if not self.ws:
            raise ConnectionError(f"{self.name} websocket connect failed")

        self.reader_task = asyncio.create_task(self._reader(), name=f"liqi-{self.name}")
        logger.info(f"{self.name} websocket connected")

    async def close(self):
        if self.reader_task:
            self.reader_task.cancel()
        if self.ws:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=3)
            except Exception as exc:
                logger.warning(f"{self.name} websocket close failed: {exc!r}")
        for fut, _ in self.pending.values():
            if not fut.done():
                fut.cancel()
        self.pending.clear()

    async def request(
        self,
        method: str,
        data: dict[str, Any] | None = None,
        *,
        timeout: float = 20,
        feed_bridge: bool = True,
    ) -> dict[str, Any]:
        msg_id = await self._next_id()
        packet = self.proto.compose(
            {"type": MsgType.Req, "method": method, "data": data or {}},
            msg_id=msg_id,
        )
        if feed_bridge and self.bridge:
            self.bridge.parse(packet)
            self.bridge_msg_ids.add(msg_id)
        fut = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = (fut, True)
        await self.ws.send(packet)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def raw_request(
        self,
        method: str,
        body: bytes = b"",
        *,
        timeout: float = 20,
        parse_response: bool = False,
        feed_bridge: bool = False,
    ) -> Any:
        msg_id = await self._next_id()
        packet = _pack_raw(msg_id, method, body)
        if parse_response:
            self.proto.parse(packet)
        if feed_bridge and self.bridge:
            self.bridge.parse(packet)
            self.bridge_msg_ids.add(msg_id)
        fut = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = (fut, parse_response)
        await self.ws.send(packet)
        return await asyncio.wait_for(fut, timeout=timeout)

    async def _next_id(self) -> int:
        async with self.send_lock:
            msg_id = self.msg_id
            self.msg_id += 1
            if self.msg_id >= 65535:
                self.msg_id = 1
            return msg_id

    async def _reader(self):
        try:
            async for raw in self.ws:
                if isinstance(raw, str):
                    raw = raw.encode()
                await self._handle_frame(raw)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(f"{self.name} websocket reader stopped: {exc!r}")

    async def _handle_frame(self, raw: bytes):
        msg_type = raw[0]
        msg_id = None
        if msg_type in (MsgType.Req.value, MsgType.Res.value) and len(raw) >= 3:
            msg_id = struct.unpack("<H", raw[1:3])[0]

        if self.bridge and (
            msg_type == MsgType.Notify.value
            or (msg_id is not None and msg_id in self.bridge_msg_ids)
        ):
            try:
                msgs = self.bridge.parse(raw)
            except Exception as exc:
                logger.warning(f"{self.name} bridge parse failed: {exc!r}")
                msgs = None
            if msg_type == MsgType.Res.value and msg_id is not None:
                self.bridge_msg_ids.discard(msg_id)
            if msgs and self.messages:
                for msg in msgs:
                    self.messages.put(msg)

        if msg_type == MsgType.Notify.value:
            if self.notify_handler:
                parsed = self.proto.parse(raw)
                if parsed:
                    await self.notify_handler(parsed)
            return

        if msg_type not in (MsgType.Req.value, MsgType.Res.value) or len(raw) < 3:
            return

        entry = self.pending.pop(msg_id, None)
        if not entry:
            return

        fut, parse_response = entry
        if fut.done():
            return
        if parse_response:
            fut.set_result(self.proto.parse(raw))
        else:
            fut.set_result(raw)


class MajsoulAutomation:
    """Controls Majsoul directly through Liqi RPCs."""

    def __init__(
        self,
        messages: queue.Queue | None = None,
        *,
        should_continue: Callable[[], bool] | None = None,
    ):
        self.messages = messages or queue.Queue()
        self.should_continue = should_continue or (lambda: True)
        self.resource_version = RESOURCE_VERSION
        self.package_version = PACKAGE_VERSION
        self.client_version_string = CLIENT_VERSION_STRING
        self.lobby: LiqiSocket | None = None
        self.game: LiqiSocket | None = None
        self.route_prep: LiqiSocket | None = None
        self.account_id: int | None = None
        self.access_token: str | None = None
        self.device_id = str(uuid.uuid4())
        self.heartbeat_tasks: list[asyncio.Task] = []
        self.game_started = asyncio.Event()
        self.in_game = False
        self.matching = False
        self.game_bridge: MajsoulBridge | None = None
        self.game_connect_task: asyncio.Task | None = None
        self.game_connect_failed = asyncio.Event()
        self.last_game_auth_failed = False
        self.last_game_connect_failed = False
        self.account_busy = False
        self.account_cooldown_until: int | None = None
        self.existing_game_info: dict[str, Any] | None = None
        self.login_reconnect = False
        self.lobby_route_id = MAJSOUL_ROUTE_ID
        self.route_prep_route_id: str | None = None
        self.route_entries = _default_route_entries()
        self.rank_level_id: int | None = None
        self.rank_score: int | None = None

    def _should_continue(self) -> bool:
        try:
            return bool(self.should_continue())
        except Exception:
            return True

    async def login(self, username: str, password: str, *, reconnect: bool = False):
        logger.info(f"Logging in as {username} via Liqi protocol...")
        await self._refresh_client_version()
        await self._refresh_route_entries()
        previous_access_token = self.access_token
        self.account_busy = False
        self.account_cooldown_until = None
        self.existing_game_info = None
        if not reconnect:
            self.access_token = None
        self.login_reconnect = reconnect
        if not await self._connect_lobby_socket():
            return False
        await self.lobby.raw_request(
            ".lq.Route.requestConnection",
            _route_body(1, self.lobby_route_id),
        )
        self._start_heartbeat(self.lobby)

        result = await self.lobby.request(
            ".lq.Lobby.login",
            self._login_payload(username, password, reconnect=reconnect),
        )
        err = _as_error(result.get("data"))
        if err.get("code"):
            if self._mark_server_cooldown(err, source="login"):
                return False
            logger.error(f"Login failed: {_format_error(err)}")
            return False

        data = result.get("data") or {}
        self._apply_login_data(
            data,
            reconnect=reconnect,
            previous_access_token=previous_access_token,
        )
        if self.account_busy:
            logger.warning("Account already has an active game/match; will try protocol reconnect")
        logger.info("Login successful")

        await self.lobby.request(".lq.Lobby.fetchLastPrivacy", {"type": [1, 2]})
        await self.lobby.request(".lq.Lobby.loginBeat", {"contract": LOGIN_BEAT_CONTRACT})
        await self.lobby.request(".lq.Lobby.loginSuccess", {})
        await self.lobby.request(".lq.Lobby.fetchInfo", {})
        return True

    def _apply_login_data(
        self,
        data: dict[str, Any],
        *,
        reconnect: bool,
        previous_access_token: str | None = None,
    ) -> None:
        account = data.get("account") or {}
        account_id = data.get("accountId") or data.get("account_id") or account.get("accountId")
        self.account_id = int(account_id or 0)

        new_access_token = data.get("accessToken") or data.get("access_token")
        if new_access_token:
            self.access_token = new_access_token
        elif reconnect and previous_access_token:
            self.access_token = previous_access_token
        else:
            self.access_token = None

        self.existing_game_info = data.get("gameInfo") or data.get("game_info")
        playing_game = account.get("playingGame") or account.get("playing_game")
        self.account_busy = bool(self.existing_game_info or playing_game)
        self._apply_rank_target_from_account(account, source="login")

    def _apply_rank_target_from_account(self, account: dict[str, Any], *, source: str) -> bool:
        level = account.get("level") or {}
        level_id = _coerce_int(level.get("id"))
        if not level_id:
            logger.warning(f"Rank target skipped ({source}): missing four-player level")
            return False

        score = _coerce_int(level.get("score"))
        self.rank_level_id = level_id
        self.rank_score = score

        target_type, target_room = _target_mode_for_rank_level(level_id)
        changed = (
            settings.autoplay_mode.type != target_type
            or settings.autoplay_mode.room != target_room
        )
        settings.autoplay_mode.type = target_type
        settings.autoplay_mode.room = target_room

        verb = "updated" if changed else "confirmed"
        logger.info(
            f"Rank target {verb} ({source}): 4p {_rank_label(level_id)} "
            f"score={score if score is not None else '?'} -> "
            f"{target_type} {target_room}"
        )
        return True

    async def refresh_rank_target(self, *, quiet: bool = False) -> bool:
        if not self.lobby or not self.account_id:
            if not quiet:
                logger.warning("Rank target refresh skipped: no lobby/account id")
            return False
        try:
            result = await self.lobby.request(
                ".lq.Lobby.fetchAccountInfo",
                {"accountId": self.account_id},
                timeout=10,
            )
        except Exception as exc:
            logger.warning(f"Rank target refresh failed: {exc!r}")
            return False

        data = result.get("data") or {}
        err = _as_error(data)
        if err.get("code"):
            logger.warning(f"Rank target refresh error: {_format_error(err)}")
            return False

        account = data.get("account") or {}
        return self._apply_rank_target_from_account(account, source="fetchAccountInfo")

    async def _connect_lobby_socket(self) -> bool:
        errors: list[str] = []
        for url in self._lobby_ws_url_candidates():
            route_id = url.split("://", 1)[1].split(".", 1)[0]
            sock = LiqiSocket(
                "lobby",
                url,
                notify_handler=self._handle_lobby_notify,
            )
            try:
                await sock.connect()
            except Exception as exc:
                errors.append(f"{route_id}: {exc!r}")
                logger.warning(f"Lobby websocket failed on {route_id}: {exc!r}")
                await sock.close()
                continue

            self.lobby = sock
            self.lobby_route_id = route_id
            return True

        logger.error(f"All lobby websocket routes failed: {'; '.join(errors)}")
        self.lobby = None
        return False

    def _lobby_ws_url_candidates(self) -> list[str]:
        return [_route_ws_url_from_entry(entry) for entry in self.route_entries]

    async def wait_for_lobby(self, timeout: float = 120):
        if not self.lobby:
            return None
        if self.account_busy:
            logger.warning("Account is busy (active game/match detected)")
            if self.existing_game_info:
                if await self.reconnect_existing_game():
                    return "game"
                if not self.login_reconnect:
                    logger.warning(
                        "Existing game reconnect failed; retrying with reconnect=true login"
                    )
                    return await self._retry_login_with_reconnect()
                logger.warning("Existing game reconnect failed; refusing to queue a new match")
                return "busy"
            if not self.login_reconnect:
                return await self._retry_login_with_reconnect()
            return "busy"
        logger.info("In lobby (protocol)")
        return "lobby"

    async def start_match(self) -> StartMatchResult:
        if self.account_busy:
            logger.warning("Refusing to start match because account is already busy")
            return "busy"
        if self.account_cooldown_until and self.account_cooldown_until > int(time.time()):
            logger.warning(
                "Refusing to start match during server cooldown "
                f"(until={_format_timestamp(self.account_cooldown_until)})"
            )
            self.account_busy = True
            return "busy"
        if not self.lobby:
            logger.error("Cannot start match before login")
            return "error"
        await self.refresh_rank_target(quiet=True)
        match_sid = self._get_match_sid()
        if not match_sid:
            logger.error(f"Unknown match mode: {settings.autoplay_mode.type} {settings.autoplay_mode.room}")
            return "error"

        if await self._refresh_existing_game_info(quiet=True):
            logger.warning("Existing game detected before match queue; not starting a new match")
            return "busy"

        if not await self._prepare_game_route(force=True):
            logger.error("Route prepareLogin failed before match queue")
            return "error"

        logger.info(
            f"Starting match: {settings.autoplay_mode.type} "
            f"{settings.autoplay_mode.room} (match_sid={match_sid})"
        )
        start_match_payload = {
            "match_sid": match_sid,
            "client_version_string": self.client_version_string,
        }
        result = await self.lobby.request(".lq.Lobby.startUnifiedMatch", start_match_payload)
        err = _as_error(result.get("data"))
        if int(err.get("code") or 0) == 1304:
            if await self._claim_revive_coin_for_match_queue():
                result = await self.lobby.request(".lq.Lobby.startUnifiedMatch", start_match_payload)
                err = _as_error(result.get("data"))

        if int(err.get("code") or 0) == 1304:
            logger.warning("Server reports stale match queue/unavailable match (1304); cancelling before requeue")
            try:
                await self.lobby.request(
                    ".lq.Lobby.cancelUnifiedMatch",
                    {"match_sid": match_sid},
                    timeout=5,
                )
                logger.info("Cancelled stale match queue after 1304")
            except Exception as exc:
                logger.warning(f"cancelUnifiedMatch after 1304 failed/skipped: {exc!r}")
            result = await self.lobby.request(".lq.Lobby.startUnifiedMatch", start_match_payload)
            err = _as_error(result.get("data"))

        if err.get("code"):
            if self._mark_server_cooldown(err, source="startUnifiedMatch"):
                return "busy"
            if int(err.get("code") or 0) == 1023:
                self.account_busy = True
                self.matching = False
                logger.warning("Server reports account already busy (1023); not waiting as a fresh match")
                return "busy"
            if int(err.get("code") or 0) == 1304:
                self.matching = True
                self.account_busy = False
                self.game_connect_failed.clear()
                logger.warning("Server still reports account is already in match queue (1304); waiting for game start")
                return "queued"
            logger.error(f"startUnifiedMatch error: {_format_error(err)}")
            return "error"
        self.matching = True
        self.account_busy = False
        self.game_connect_failed.clear()
        logger.info("Match queued successfully")
        return "queued"

    async def _claim_revive_coin_for_match_queue(self) -> bool:
        if not self.lobby:
            return False
        try:
            info_result = await self.lobby.request(
                ".lq.Lobby.fetchReviveCoinInfo",
                {},
                timeout=5,
            )
        except Exception as exc:
            logger.warning(f"fetchReviveCoinInfo failed/skipped after 1304: {exc!r}")
            return False

        info_data = info_result.get("data") or {}
        info_err = _as_error(info_data)
        if info_err.get("code"):
            logger.warning(f"fetchReviveCoinInfo error after 1304: {_format_error(info_err)}")
            return False

        has_gained = info_data.get("hasGained", info_data.get("has_gained"))
        if has_gained is None:
            logger.warning("fetchReviveCoinInfo returned no hasGained flag after 1304")
            return False
        if bool(has_gained):
            logger.info("Revive coin already claimed; 1304 is not recoverable by claiming coins")
            return False

        try:
            gain_result = await self.lobby.request(
                ".lq.Lobby.gainReviveCoin",
                {},
                timeout=5,
            )
        except Exception as exc:
            logger.warning(f"gainReviveCoin failed/skipped after 1304: {exc!r}")
            return False

        gain_data = gain_result.get("data") or {}
        gain_err = _as_error(gain_data)
        if gain_err.get("code"):
            logger.warning(f"gainReviveCoin error after 1304: {_format_error(gain_err)}")
            return False

        logger.info("Claimed revive coin after match queue 1304; retrying match queue")
        return True

    async def cancel_match(self):
        if not self.lobby or not self.lobby.ws:
            return
        match_sid = self._get_match_sid() or "1:3"
        try:
            await self.lobby.request(
                ".lq.Lobby.cancelUnifiedMatch",
                {"match_sid": match_sid},
                timeout=5,
            )
        except Exception as exc:
            logger.warning(f"cancelUnifiedMatch failed/skipped: {exc!r}")
            return
        self.matching = False
        logger.info("Match cancelled")

    async def execute_action(self, mjai_action: dict, seat: int):
        action_type = mjai_action.get("type")
        if action_type in ("none", None):
            return await self._send_skip()

        delay = random.uniform(
            settings.autoplay_time.rand_min,
            settings.autoplay_time.rand_max,
        )
        await asyncio.sleep(delay)

        pai = mjai_action.get("pai", "")
        tile = MJAI_TO_MS_TILE.get(pai, pai)

        if action_type == "dahai":
            return await self._send_discard_with_retry(tile, mjai_action.get("tsumogiri", False), seat)
        elif action_type == "reach":
            operations = get_last_operation_list()
            liqi_op = next((op for op in operations if op.get("type") == OP_LIQI), None)
            valid_tiles = liqi_op.get("combination", []) if liqi_op else []
            requested_tile = tile
            tile, selection_source = _select_riichi_declaration_tile(tile, valid_tiles)
            if valid_tiles and (len(valid_tiles) > 1 or requested_tile != tile):
                logger.info(
                    f"Riichi candidates: valid={valid_tiles} model={requested_tile} "
                    f"using={tile} source={selection_source}"
                )
            if selection_source == "fallback-first-candidate":
                logger.warning(
                    f"Riichi model tile {requested_tile} is not in candidates {valid_tiles}; "
                    f"using first candidate {tile}"
                )
            return await self._send_input_operation(
                {
                    "type": OP_LIQI,
                    "tile": tile,
                    "moqie": mjai_action.get("tsumogiri", False),
                    "timeuse": 3,
                }
            )
        elif action_type == "chi":
            return await self._send_chi_peng_gang(OP_CHI, mjai_action)
        elif action_type == "pon":
            return await self._send_chi_peng_gang(OP_PENG, mjai_action)
        elif action_type == "daiminkan":
            return await self._send_chi_peng_gang(OP_MING_GANG, mjai_action)
        elif action_type == "ankan":
            consumed = mjai_action.get("consumed", [""])
            an_tile = MJAI_TO_MS_TILE.get(consumed[0], consumed[0]) if consumed else ""
            return await self._send_input_operation({"type": OP_AN_GANG, "tile": an_tile, "timeuse": 3})
        elif action_type == "kakan":
            return await self._send_input_operation({"type": OP_JIA_GANG, "tile": tile, "timeuse": 3})
        elif action_type == "hora":
            is_tsumo = mjai_action.get("actor") == mjai_action.get("target", mjai_action.get("actor"))
            return await self._send_input_operation({"type": OP_ZIMO if is_tsumo else OP_HU, "timeuse": 1})
        elif action_type == "ryukyoku":
            return await self._send_input_operation({"type": OP_LIU_JU, "timeuse": 1})
        else:
            logger.warning(f"Unknown action type: {action_type}")
            return False

    async def handle_end_game(self):
        self.in_game = False
        self.account_busy = False
        self.existing_game_info = None
        self.game_started.clear()
        if self.game:
            await self.game.close()
            self.game = None
        logger.info("Game ended; protocol client is back in lobby")
        return True

    async def recover(self):
        logger.info("Recovering by reconnecting protocol session")
        await self.close()
        if not await self.login(settings.autoplay_account.username, settings.autoplay_account.password):
            if self.account_busy or self.account_cooldown_until:
                return "busy"
            return False
        return await self.wait_for_lobby()

    async def _retry_login_with_reconnect(self):
        logger.warning("Retrying login with reconnect=true for active game")
        await self.close()
        if not await self.login(
            settings.autoplay_account.username,
            settings.autoplay_account.password,
            reconnect=True,
        ):
            return None
        if not self.account_busy:
            logger.info("Reconnect login reached lobby")
            return "lobby"
        logger.warning("Account is still busy after reconnect login")
        if self.existing_game_info:
            if await self.reconnect_existing_game():
                return "game"
            logger.warning("Reconnect login game auth still failed; not queueing")
        return "busy"

    async def _refresh_existing_game_info(self, *, quiet: bool = False) -> bool:
        if not self.lobby:
            return False
        try:
            result = await self.lobby.request(".lq.Lobby.fetchGamingInfo", {}, timeout=10)
        except Exception as exc:
            if not quiet:
                logger.warning(f"fetchGamingInfo failed/skipped: {exc!r}")
            return False

        data = result.get("data") or {}
        err = _as_error(data)
        if err.get("code"):
            if self._mark_server_cooldown(err, source="fetchGamingInfo"):
                return False
            if not quiet:
                logger.warning(f"fetchGamingInfo error: {_format_error(err)}")
            return False

        info = data.get("gameInfo") or data.get("game_info")
        if not info:
            if not quiet:
                logger.warning("fetchGamingInfo returned no gameInfo")
            return False

        self.existing_game_info = info
        self.account_busy = True
        game_uuid = info.get("gameUuid") or info.get("game_uuid") or ""
        location = info.get("location") or ""
        logger.info(
            "Refreshed existing game info "
            f"(location={location or 'unknown'} uuid={str(game_uuid)[-8:]})"
        )
        return True

    async def wait_until_account_free(
        self,
        *,
        poll_interval: float = 60,
        timeout: float | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> Literal["lobby", "busy"]:
        """Poll lobby state until the server stops reporting an active game."""
        if not self.lobby:
            logger.warning("Cannot poll account state without a lobby websocket")
            return "busy"

        deadline = time.monotonic() + timeout if timeout is not None else None
        while should_continue is None or should_continue():
            try:
                result = await self.lobby.request(".lq.Lobby.fetchGamingInfo", {}, timeout=10)
            except Exception as exc:
                logger.warning(f"fetchGamingInfo failed while waiting for account free: {exc!r}")
            else:
                data = result.get("data") or {}
                err = _as_error(data)
                if err.get("code"):
                    logger.warning(f"fetchGamingInfo error while waiting: {_format_error(err)}")
                    if self._mark_server_cooldown(err, source="fetchGamingInfo"):
                        return "busy"
                else:
                    info = data.get("gameInfo") or data.get("game_info")
                    if not info:
                        self.existing_game_info = None
                        self.account_busy = False
                        self.account_cooldown_until = None
                        self.game_connect_failed.clear()
                        logger.info("Account is free; lobby queue is safe")
                        return "lobby"

                    self.existing_game_info = info
                    self.account_busy = True
                    game_uuid = info.get("gameUuid") or info.get("game_uuid") or ""
                    location = info.get("location") or ""
                    logger.info(
                        "Account still has an active game; waiting "
                        f"(location={location or 'unknown'} uuid={str(game_uuid)[-8:]})"
                    )

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                sleep_for = min(poll_interval, remaining)
            else:
                sleep_for = poll_interval
            await asyncio.sleep(max(0.1, sleep_for))

        return "busy"

    def _mark_server_cooldown(self, err: dict[str, Any], *, source: str) -> bool:
        cooldown_until = _cooldown_until_from_error(err)
        if cooldown_until is None:
            return False
        self.account_cooldown_until = cooldown_until
        self.account_busy = True
        self.matching = False
        self.existing_game_info = None
        logger.warning(
            f"{source} reports server cooldown: {_format_error(err)} "
            f"(until={_format_timestamp(cooldown_until)})"
        )
        return True

    async def _refresh_client_version(self):
        latest = await asyncio.to_thread(_fetch_current_resource_version)
        if latest and latest.endswith(".w") and not self.resource_version.endswith(".w"):
            logger.info(
                f"Ignoring legacy Majsoul version.json resource={latest}; "
                f"using Unity resource={self.resource_version}"
            )
        elif latest:
            self.resource_version = latest
            self.client_version_string = _client_version_string(latest)
        logger.info(
            f"Majsoul web version: resource={self.resource_version} "
            f"package={self.package_version} client={self.client_version_string}"
        )

    async def _refresh_route_entries(self):
        config_entries = []
        if self.resource_version.endswith(".w"):
            try:
                config_entries = await asyncio.to_thread(
                    _fetch_config_route_entries,
                    self.resource_version,
                )
            except Exception as exc:
                logger.warning(f"Failed to fetch Majsoul route config: {exc!r}")

        if config_entries:
            self.route_entries = config_entries

        clientgate_entries = await asyncio.to_thread(
            _fetch_clientgate_route_entries,
            self.route_entries,
            self.resource_version,
        )
        if clientgate_entries:
            self.route_entries = clientgate_entries

        logger.info(
            "Majsoul routes: "
            + ", ".join(
                f"{entry['id']}={entry['domain']}"
                for entry in self.route_entries
            )
        )

    def _route_ws_url_for_id(self, route_id: str, tail: str = "gateway") -> str:
        for entry in self.route_entries:
            if entry.get("id") == route_id:
                return _route_ws_url_from_entry(entry, tail=tail)
        return _route_ws_url(route_id, tail=tail)

    async def reconnect_existing_game(self) -> bool:
        await self._refresh_existing_game_info()
        info = self.existing_game_info or {}
        token = info.get("connectToken") or info.get("connect_token")
        game_uuid = info.get("gameUuid") or info.get("game_uuid")
        location = info.get("location") or ""
        if not (token and game_uuid and self.account_id):
            logger.error(
                "Existing game info is incomplete; cannot reconnect "
                f"(keys={sorted(info.keys())})"
            )
            return False

        logger.info(
            f"Reconnecting existing game via Liqi protocol "
            f"(location={location or 'unknown'} uuid={str(game_uuid)[-8:]})"
        )
        await self._prepare_game_route(force=True)
        tail = _game_gateway_tail(location)
        for route_id in self._game_route_candidates(location, preferred=self.lobby_route_id):
            if not self._should_continue():
                logger.info("Existing game reconnect aborted by shutdown")
                return False
            ws_url = self._route_ws_url_for_id(route_id, tail=tail)
            for request_route_id in self._request_route_candidates(route_id):
                if not self._should_continue():
                    logger.info("Existing game reconnect aborted by shutdown")
                    return False
                ok = await self._open_game_socket(
                    ws_url=ws_url,
                    route_id=route_id,
                    request_route_id=request_route_id,
                    token=token,
                    game_uuid=game_uuid,
                    source="existing-game",
                    prefer_sync=True,
                )
                if ok:
                    self.account_busy = False
                    return True

        self.in_game = False
        self.game_connect_failed.set()
        return False

    async def close(self):
        for task in self.heartbeat_tasks:
            task.cancel()
        self.heartbeat_tasks.clear()
        if self.game_connect_task and not self.game_connect_task.done():
            self.game_connect_task.cancel()
        self.game_connect_task = None
        if self.game:
            await self.game.close()
            self.game = None
        if self.route_prep:
            await self.route_prep.close()
            self.route_prep = None
        if self.lobby:
            await self.lobby.close()
            self.lobby = None
        logger.info("Liqi client closed")

    def _login_payload(self, username: str, password: str, *, reconnect: bool = False) -> dict[str, Any]:
        digest = hmac.new(b"lailai", password.encode(), hashlib.sha256).hexdigest()
        payload = {
            "account": username,
            "password": digest,
            "reconnect": reconnect,
            "device": self._device_payload(),
            "randomKey": self.device_id,
            "clientVersion": {"resource": self.resource_version, "package": self.package_version},
            "currencyPlatforms": [1, 2, 5, 6, 8, 10, 11],
            "clientVersionString": self.client_version_string,
            "tag": "cn",
        }
        if not reconnect:
            payload["genAccessToken"] = True
            payload["type"] = 0
        return payload

    def _device_payload(self) -> dict[str, Any]:
        return {
            "platform": "pc",
            "hardware": "pc",
            "os": "mac",
            "isBrowser": True,
            "software": "Chrome",
            "salePlatform": "web",
            "screenWidth": 1280,
            "screenHeight": 720,
            "osVersion": "",
            "hardwareVendor": "",
            "modelNumber": "",
        }

    def _get_match_sid(self) -> str | None:
        mode_id = MATCH_MODE_IDS.get((settings.autoplay_mode.type, settings.autoplay_mode.room))
        return f"1:{mode_id}" if mode_id is not None else None

    def _start_heartbeat(self, sock: LiqiSocket):
        task = asyncio.create_task(self._heartbeat_loop(sock), name=f"{sock.name}-heartbeat")
        self.heartbeat_tasks.append(task)

    async def _heartbeat_loop(self, sock: LiqiSocket):
        seq = 0
        while True:
            await asyncio.sleep(20)
            try:
                seq += 1
                await sock.raw_request(".lq.Route.heartbeat", _heartbeat_body(seq), timeout=10)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(f"{sock.name} heartbeat failed: {exc!r}")
                break

    async def _handle_lobby_notify(self, msg: dict[str, Any]):
        method = msg.get("method")
        if method == ".lq.NotifyMatchGameStart":
            data = msg.get("data") or {}
            logger.info("Match found; connecting to game server")
            if self.game_connect_task and not self.game_connect_task.done():
                logger.warning("Game connection is already in progress")
                return
            self.game_connect_task = asyncio.create_task(
                self._connect_game(data),
                name="majsoul-connect-game",
            )
        elif method == ".lq.NotifyMatchFailed":
            logger.error(f"Match failed: {msg.get('data')}")
            self.matching = False
        elif method == ".lq.NotifyMatchTimeout":
            logger.warning("Match timeout")
            self.matching = False

    async def _connect_game(self, data: dict[str, Any]):
        game_url = data.get("gameUrl") or data.get("game_url")
        token = data.get("connectToken") or data.get("connect_token")
        game_uuid = data.get("gameUuid") or data.get("game_uuid")
        location = data.get("location") or ""
        if not (game_url and token and game_uuid and self.account_id):
            logger.error("Incomplete game start notification")
            self.in_game = False
            self.game_connect_failed.set()
            return

        self.matching = False
        self.in_game = True
        self.last_game_auth_failed = False
        self.last_game_connect_failed = False
        self.game_connect_failed.clear()
        self.game_started.clear()
        ws_urls = self._game_ws_urls(game_url)
        logger.info(
            f"Game server from match: url={game_url} location={location} "
            f"uuid={str(game_uuid)[-8:]}"
        )

        # The Unity client waits for the game process to be ready before opening
        # the second websocket. Connecting immediately often races the server.
        await asyncio.sleep(5.5)

        if not self.route_prep:
            await self._prepare_game_route(force=True)

        tail = _game_gateway_tail(location)
        route_candidates = self._game_route_candidates(location, preferred=self.lobby_route_id)

        # Unity's NetRouteGroup_Single opens the Mahjong table through the
        # current lobby main route with a game-specific websocket tail. The
        # NotifyMatchGameStart.gameUrl endpoint is kept only as a fallback.
        for route_id in route_candidates:
            if not self._should_continue():
                logger.info("Game connection aborted by shutdown")
                self.in_game = False
                self.matching = False
                return
            ws_url = self._route_ws_url_for_id(route_id, tail=tail)
            for request_route_id in self._request_route_candidates(route_id):
                if not self._should_continue():
                    logger.info("Game connection aborted by shutdown")
                    self.in_game = False
                    self.matching = False
                    return
                ok = await self._open_game_socket(
                    ws_url=ws_url,
                    route_id=route_id,
                    request_route_id=request_route_id,
                    token=token,
                    game_uuid=game_uuid,
                    source="match-start-netroute",
                    prefer_sync=False,
                )
                if ok:
                    return
                if self.last_game_connect_failed:
                    break

        direct_request_routes = self._request_route_candidates(self.lobby_route_id)
        for ws_url in ws_urls:
            socket_unusable = False
            for request_route_id in direct_request_routes:
                if not self._should_continue():
                    logger.info("Game connection aborted by shutdown")
                    self.in_game = False
                    self.matching = False
                    return
                ok = await self._open_game_socket(
                    ws_url=ws_url,
                    route_id=self.lobby_route_id,
                    request_route_id=request_route_id,
                    token=token,
                    game_uuid=game_uuid,
                    source="match-start-direct",
                    prefer_sync=False,
                )
                if ok:
                    return
                if self.last_game_connect_failed:
                    socket_unusable = True
                    break
            if socket_unusable:
                continue

        logger.error("Game websocket handshake failed for all route candidates")
        self.in_game = False
        self.matching = False
        self.account_busy = True
        self.game_connect_failed.set()

    async def _open_game_socket(
        self,
        *,
        ws_url: str,
        route_id: str,
        request_route_id: str,
        token: str,
        game_uuid: str,
        source: str,
        prefer_sync: bool,
    ) -> bool:
        if self.game:
            await self.game.close()
            self.game = None
        self.last_game_auth_failed = False
        self.last_game_connect_failed = False

        self.game_bridge = MajsoulBridge()
        self.game = LiqiSocket(
            "game",
            ws_url,
            bridge=self.game_bridge,
            messages=self.messages,
        )
        try:
            logger.info(
                f"Connecting game websocket ({source}, host_route={route_id}, "
                f"request_route={request_route_id})"
            )
            await self.game.connect(open_timeout=12)
            await self.game.raw_request(
                ".lq.Route.requestConnection",
                _route_body(1, request_route_id),
                timeout=10,
            )
            auth = await self.game.raw_request(
                ".lq.FastTest.authGame",
                _auth_game_body(self.account_id, token, game_uuid),
                feed_bridge=True,
                parse_response=True,
                timeout=20,
            )
        except Exception as exc:
            logger.warning(
                f"Game websocket handshake failed "
                f"({source}, host_route={route_id}, request_route={request_route_id}): {exc!r}"
            )
            self.last_game_connect_failed = True
            await self.game.close()
            self.game = None
            return False

        if not isinstance(auth, dict):
            logger.warning(
                f"authGame returned no parsed response "
                f"({source}, host_route={route_id}, request_route={request_route_id})"
            )
            await self.game.close()
            self.game = None
            return False

        err = _as_error(auth.get("data"))
        if err.get("code"):
            self.last_game_auth_failed = True
            logger.warning(
                f"authGame failed "
                f"({source}, host_route={route_id}, request_route={request_route_id}): "
                f"{_format_error(err)}"
            )
            await self.game.close()
            self.game = None
            return False

        self._start_heartbeat(self.game)

        try:
            if prefer_sync:
                sync = await self.game.request(
                    ".lq.FastTest.syncGame",
                    {"roundId": "-1", "step": 1000000},
                    timeout=20,
                )
                err = _as_error(sync.get("data"))
                if err.get("code"):
                    logger.warning(
                        f"syncGame failed "
                        f"({source}, host_route={route_id}, request_route={request_route_id}): "
                        f"{_format_error(err)}"
                    )
                    await self.game.close()
                    self.game = None
                    return False
            else:
                enter = await self.game.request(".lq.FastTest.enterGame", {}, timeout=20)
                err = _as_error(enter.get("data"))
                if err.get("code"):
                    logger.warning(
                        f"enterGame failed "
                        f"({source}, host_route={route_id}, request_route={request_route_id}): "
                        f"{_format_error(err)}; trying syncGame"
                    )
                    sync = await self.game.request(
                        ".lq.FastTest.syncGame",
                        {"roundId": "-1", "step": 1000000},
                        timeout=20,
                    )
                    err = _as_error(sync.get("data"))
                    if err.get("code"):
                        logger.warning(
                            f"syncGame failed "
                            f"({source}, host_route={route_id}, request_route={request_route_id}): "
                            f"{_format_error(err)}"
                        )
                        await self.game.close()
                        self.game = None
                        return False
        except Exception as exc:
            logger.warning(
                f"Game restore failed "
                f"({source}, host_route={route_id}, request_route={request_route_id}): {exc!r}"
            )
            await self.game.close()
            self.game = None
            return False

        self.matching = False
        self.in_game = True
        self.account_busy = False
        self.game_connect_failed.clear()
        logger.info(
            f"Game websocket authenticated "
            f"({source}, host_route={route_id}, request_route={request_route_id})"
        )
        self.game_started.set()
        return True

    async def _prepare_game_route(
        self,
        target_route_id: str | None = None,
        *,
        force: bool = False,
        change_route: bool = False,
    ) -> bool:
        if (
            not force
            and self.route_prep
            and self.route_prep.ws
            and (not target_route_id or target_route_id == self.route_prep_route_id)
        ):
            return True
        if not self.access_token:
            logger.warning("No access token available; skipping route prepareLogin")
            return False
        if self.route_prep:
            await self.route_prep.close()
            self.route_prep = None
            self.route_prep_route_id = None
        client_endpoint_url = None
        if self.lobby:
            try:
                info = await self.lobby.request(".lq.Lobby.fetchConnectionInfo", {}, timeout=10)
                endpoint = (info.get("data") or {}).get("clientEndpoint") or {}
                if endpoint:
                    logger.info(
                        "Fetched client endpoint "
                        f"{endpoint.get('address', '?')}:{endpoint.get('port', '?')}"
                    )
                    client_endpoint_url = _endpoint_ws_url(endpoint)
            except Exception as exc:
                logger.warning(f"fetchConnectionInfo failed/skipped: {exc!r}")

        route_candidates: list[str] = []
        dynamic_route_ids = [str(entry.get("id") or "") for entry in self.route_entries]
        for route_id in (target_route_id, *MAJSOUL_PREP_ROUTE_CANDIDATES, *dynamic_route_ids):
            if not route_id or route_id in route_candidates:
                continue
            route_candidates.append(route_id)

        for route_id in route_candidates:
            if not self._should_continue():
                logger.info("Route prepareLogin aborted by shutdown")
                return False
            socket_urls: list[tuple[str, float]] = []
            if client_endpoint_url:
                socket_urls.append((client_endpoint_url, 3))
            route_url = self._route_ws_url_for_id(route_id)
            if route_url not in [url for url, _ in socket_urls]:
                socket_urls.append((route_url, 12))
            for socket_url, open_timeout in socket_urls:
                if not self._should_continue():
                    logger.info("Route prepareLogin aborted by shutdown")
                    return False
                sock = LiqiSocket("route-prep", socket_url)
                try:
                    await sock.connect(open_timeout=open_timeout)
                    kind = 3 if change_route and target_route_id else 2
                    await sock.raw_request(
                        ".lq.Route.requestConnection",
                        _route_body(kind, route_id),
                        timeout=10,
                    )
                    if change_route and target_route_id:
                        await sock.raw_request(
                            ".lq.Route.requestRouteChange",
                            _route_change_body(self.lobby_route_id, route_id),
                            timeout=10,
                        )
                    await sock.raw_request(
                        ".lq.Lobby.prepareLogin",
                        _prepare_login_body(self.access_token),
                        timeout=10,
                    )
                    self.route_prep = sock
                    self.route_prep_route_id = route_id
                    logger.info(f"Route prepareLogin completed on {route_id}")
                    return True
                except Exception as exc:
                    logger.warning(
                        f"Route prepareLogin failed on {route_id} via {socket_url}: {exc!r}"
                    )
                    await sock.close()
                    self.route_prep = None
                    self.route_prep_route_id = None

        return False

    def _game_route_candidates(self, location: str = "", preferred: str = MAJSOUL_GAME_ROUTE_ID):
        candidates: list[str] = []
        route_ids = [str(entry.get("id") or "") for entry in self.route_entries]
        for item in (preferred, location, *route_ids):
            if not item:
                continue
            if isinstance(item, str) and item.startswith("route-") and item not in candidates:
                candidates.append(item)
        return candidates

    def _request_route_candidates(self, host_route_id: str) -> list[str]:
        """Route id sent inside Route.requestConnection.

        Historical captures show the game socket using the same route in the
        host and request body. Keep route-2 only as a fallback.
        """
        candidates = [host_route_id]
        if MAJSOUL_ROUTE_ID not in candidates:
            candidates.append(MAJSOUL_ROUTE_ID)
        return candidates

    def _game_ws_urls(self, game_url: str) -> list[str]:
        if game_url.startswith("ws://") or game_url.startswith("wss://"):
            return [game_url if game_url.endswith("/gateway") else f"{game_url}/gateway"]
        return [f"ws://{game_url}/gateway", f"wss://{game_url}/gateway"]

    async def _send_discard_with_retry(self, tile: str, moqie: bool, seat: int | None = None) -> bool:
        operations = get_last_operation_list()
        if not any(op.get("type") == OP_DISCARD for op in operations):
            event = get_last_discard_event()
            try:
                discard_age = time.monotonic() - float(event.get("received_monotonic") or 0)
            except (TypeError, ValueError):
                discard_age = STALE_SELF_DISCARD_IGNORE_WINDOW + 1
            if (
                seat is not None
                and event.get("actor") == seat
                and 0 <= discard_age <= STALE_SELF_DISCARD_IGNORE_WINDOW
            ):
                logger.info(
                    f"Stale discard action ignored; self discard already broadcast "
                    f"actor={seat} tile={event.get('tile')} age={discard_age:.1f}s"
                )
                return True
            logger.error("No discard operation window from server; refusing to send discard")
            return False

        op_context = get_last_operation_context()
        if moqie and op_context.get("source") == "ActionNewRound":
            logger.info("Opening-round discard uses hand-discard mode instead of moqie")
            moqie = False
        await self._wait_for_opening_round_discard_window(op_context)
        if op_context.get("source") == "ActionNewRound":
            current_context = get_last_operation_context()
            current_operations = get_last_operation_list()
            same_window = (
                current_context.get("source") == op_context.get("source")
                and current_context.get("seat") == op_context.get("seat")
                and current_context.get("received_monotonic") == op_context.get("received_monotonic")
                and any(op.get("type") == OP_DISCARD for op in current_operations)
            )
            if not same_window:
                logger.info(
                    "Opening-round discard window changed before submit; "
                    "dropping stale discard action"
                )
                return True

        pre_event = get_last_discard_event()
        pre_count = int(pre_event.get("counter") or get_discard_counter())
        pre_round_end_count = get_round_end_counter()
        ok = await self._send_input_operation(
            {
                "type": OP_DISCARD,
                "tile": tile,
                "moqie": moqie,
                "timeuse": 3,
            }
        )
        if not ok:
            return False

        for _ in range(50):
            await asyncio.sleep(0.2)
            if get_round_end_counter() > pre_round_end_count:
                return True

            event = get_last_discard_event()
            event_count = int(event.get("counter") or 0)
            if event_count <= pre_count:
                continue

            if seat is not None and event.get("actor") != seat:
                continue

            if event.get("tile") == tile:
                return True

            logger.error(
                f"Discard broadcast mismatch: requested actor={seat} tile={tile}, "
                f"got actor={event.get('actor')} tile={event.get('tile')}"
            )
            return False

        logger.error("Discard RPC accepted but no matching broadcast ACK arrived")
        return False

    async def _wait_for_opening_round_discard_window(self, op_context: dict[str, Any]) -> None:
        if op_context.get("source") != "ActionNewRound":
            return
        try:
            passed_waiting = float(op_context.get("passedWaitingTime") or 0)
        except (TypeError, ValueError):
            passed_waiting = 0.0
        try:
            received = float(op_context.get("received_monotonic") or time.monotonic())
            elapsed = time.monotonic() - received
        except (TypeError, ValueError):
            elapsed = 0.0
        wait_time = OPENING_ROUND_DISCARD_MIN_DELAY - max(passed_waiting, elapsed, 0.0)
        if wait_time <= 0:
            return
        logger.info(f"Opening-round discard waits {wait_time:.1f}s before submit")
        await asyncio.sleep(wait_time)

    async def _send_input_operation(self, params: dict[str, Any]) -> bool:
        if not self.game:
            logger.error("No game websocket for inputOperation")
            return False
        result = await self.game.request(".lq.FastTest.inputOperation", params)
        err = _as_error(result.get("data"))
        if err.get("code"):
            logger.error(f"inputOperation error: {_format_error(err)}")
            return False
        logger.info(f"OK: inputOperation type={params.get('type')} tile={params.get('tile', '')}")
        return True

    async def _send_skip(self) -> bool:
        await asyncio.sleep(0.5)
        if not self.game:
            return False
        result = await self.game.request(
            ".lq.FastTest.inputChiPengGang",
            {"cancelOperation": True, "timeuse": 2},
        )
        err = _as_error(result.get("data"))
        if err.get("code"):
            logger.warning(f"skip error: {_format_error(err)} (may be no pending operation)")
            return False
        logger.info("OK: skip (pass)")
        return True

    async def _send_chi_peng_gang(self, type_: int, mjai_action: dict) -> bool:
        if not self.game:
            logger.error("No game websocket for inputChiPengGang")
            return False

        consumed = mjai_action.get("consumed", [])
        ms_consumed = [MJAI_TO_MS_TILE.get(t, t) for t in consumed]
        index = 0
        if type_ == OP_CHI and ms_consumed:
            operations = get_last_operation_list()
            chi_op = next((op for op in operations if op.get("type") == OP_CHI), None)
            if chi_op and chi_op.get("combination"):
                consumed_sorted = "|".join(sorted(ms_consumed))
                for i, combo in enumerate(chi_op["combination"]):
                    combo_sorted = "|".join(sorted(combo.split("|")))
                    if combo_sorted == consumed_sorted:
                        index = i
                        break
                logger.info(f"Chi combinations: {chi_op['combination']} -> index={index}")

        result = await self.game.request(
            ".lq.FastTest.inputChiPengGang",
            {"type": type_, "index": index, "timeuse": 3},
        )
        err = _as_error(result.get("data"))
        if err.get("code"):
            logger.error(f"inputChiPengGang error: {_format_error(err)}")
            return False
        op_name = {OP_CHI: "chi", OP_PENG: "pon", OP_MING_GANG: "kan"}.get(type_, str(type_))
        logger.info(f"OK: {op_name} consumed={ms_consumed} index={index}")
        return True
