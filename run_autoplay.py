"""Full-auto Majsoul player: login → match → play → repeat."""

import os
os.environ["LOGURU_AUTOINIT"] = "False"

import sys
import time
import signal
import asyncio
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Literal
from loguru import logger as main_logger
from akagi.logger import logger

# Unbuffered stderr wrapper for real-time log output
class _Unbuffered:
    def __init__(self, stream):
        self.stream = stream
    def write(self, data):
        self.stream.write(data)
        self.stream.flush()
    def flush(self):
        self.stream.flush()
    def fileno(self):
        return self.stream.fileno()

_stderr = _Unbuffered(sys.stderr)

# Add stderr handler for autoplay so we see output in console
main_logger.add(_stderr, level="DEBUG",
                filter=lambda record: record["extra"].get("module") in ("akagi", "autoplay", "mjai_bot"),
                format="{time:HH:mm:ss} | {level: <5} | {message}")
from akagi.core import process_messages
from akagi.jsonl_logger import JsonlLogger
from mjai_bot.bot import AkagiBot
from mjai_bot.controller import Controller
from settings.settings import settings
from autoplay.protocol_automation import MajsoulAutomation

MAX_GAMES_BEFORE_RESTART = 3  # Soft-restart protocol session after this many games
BUSY_SESSION_RETRY_SECONDS = 60

SessionState = Literal["stopped", "busy"]


@dataclass(frozen=True)
class SessionResult:
    games: int
    state: SessionState = "stopped"
    retry_at: int | None = None


class ProtocolMessageClient:
    """Minimal in-process MJAI queue used by the Liqi protocol controller."""

    def __init__(self):
        self.messages: queue.Queue[dict] = queue.Queue()
        self.running = False
        self.ws_disconnected = threading.Event()

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def dump_messages(self) -> list[dict]:
        messages = []
        while not self.messages.empty():
            messages.append(self.messages.get())
        return messages


def _session_restart_delay(result: SessionResult) -> int:
    if result.state == "busy":
        if result.retry_at:
            return max(1, int(result.retry_at - time.time()) + 5)
        return BUSY_SESSION_RETRY_SECONDS
    return 10


def _busy_session_result(automation, session_game_count: int) -> SessionResult:
    return SessionResult(
        session_game_count,
        "busy",
        retry_at=getattr(automation, "account_cooldown_until", None),
    )


def _login_failure_session_result(automation, session_game_count: int) -> SessionResult:
    if getattr(automation, "account_busy", False) or getattr(automation, "account_cooldown_until", None):
        return _busy_session_result(automation, session_game_count)
    return SessionResult(session_game_count)


def _resolve_riichi_discard(
    mjai_response: dict | None,
    mjai_controller,
    player_id: int | None,
    last_tsumo_tile: str | None,
) -> dict | None:
    """Ask Mortal for the discard after it chooses riichi."""
    if not (
        mjai_response
        and mjai_response.get("type") == "reach"
        and not mjai_response.get("pai")
    ):
        return mjai_response

    resolved = dict(mjai_response)
    reach_event = {"type": "reach", "actor": player_id}
    dahai_response = mjai_controller.react([reach_event])
    if dahai_response and dahai_response.get("type") == "dahai":
        resolved["pai"] = dahai_response["pai"]
        resolved["tsumogiri"] = dahai_response.get("tsumogiri", False)
        logger.info(
            f"Riichi: model chose {dahai_response['pai']} "
            f"(tsumogiri={resolved['tsumogiri']})"
        )
    elif last_tsumo_tile:
        resolved["pai"] = last_tsumo_tile
        resolved["tsumogiri"] = True
        logger.warning(
            f"Riichi: model didn't return dahai, fallback to tsumo {last_tsumo_tile}"
        )
    else:
        logger.warning("Riichi requested but no tile available")

    return resolved


running = True
_interrupt_count = 0
# Shared state between game_loop thread and async main
pending_action = None
pending_action_lock = threading.Lock()
game_active = False
game_ended_event = threading.Event()
game_started_event = threading.Event()


def signal_handler(sig, frame):
    global running, _interrupt_count
    _interrupt_count += 1
    running = False
    game_started_event.set()
    game_ended_event.set()
    if _interrupt_count >= 2:
        raise KeyboardInterrupt


async def _sleep_while_running(seconds: float, *, tick: float = 1.0) -> bool:
    deadline = time.monotonic() + seconds
    while running:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        await asyncio.sleep(min(tick, remaining))
    return False


async def _cancel_match_safely(automation):
    try:
        await asyncio.wait_for(automation.cancel_match(), timeout=5)
    except Exception as exc:
        logger.warning(f"Cancel match skipped/failed during shutdown: {exc!r}")


async def _wait_busy_account_until_lobby(automation) -> Literal["lobby", "busy"]:
    logger.warning(
        "Account is busy; polling server state and will not start a new match "
        "until the active game clears"
    )
    return await automation.wait_until_account_free(
        poll_interval=BUSY_SESSION_RETRY_SECONDS,
        should_continue=lambda: running,
    )


def game_loop(message_client, mjai_controller, mjai_bot, jsonl_logger, session_stop):
    """Process game messages and store actions for the automation to execute."""
    global running, pending_action, game_active

    last_tsumo_tile = None  # Track our last drawn tile for riichi

    while running and not session_stop.is_set():
        try:
            result = process_messages(message_client, mjai_controller, mjai_bot, jsonl_logger)
            if result:
                mjai_msgs = result["mjai_msgs"]
                mjai_response = result["mjai_response"]

                player_id = getattr(mjai_bot, 'player_id', None)

                for msg in mjai_msgs:
                    if msg.get("type") == "start_game":
                        logger.info("Game started (protocol detected start_game)")
                        game_active = True
                        game_started_event.set()
                    elif msg.get("type") == "end_game":
                        logger.info("Game ended (protocol detected end_game)")
                        game_active = False
                        game_ended_event.set()
                    # Track our tsumo tile for riichi
                    elif (msg.get("type") == "tsumo"
                          and msg.get("actor") == player_id
                          and msg.get("pai", "?") != "?"):
                        last_tsumo_tile = msg["pai"]

                # Handle riichi: Mortal returns "reach" without specifying
                # which tile to discard. Feed the reach event back to the
                # model — it will respond with a dahai indicating the tile.
                if game_active:
                    mjai_response = _resolve_riichi_discard(
                        mjai_response,
                        mjai_controller,
                        player_id,
                        last_tsumo_tile,
                    )

                # Debug: log every bot response
                logger.debug(f"Bot response: {mjai_response}")

                # Store action for automation to execute.
                # CRITICAL: Only send skip when the bot explicitly passes on
                # an operation (can_act=True + type=none). Don't send skip for
                # "nothing to do" responses (can_act=False) — those spurious
                # skip RPCs race with the server and cancel chi/pon/kan/riichi
                # operation windows before the real action RPC arrives.
                if game_active and mjai_response:
                    action_type = mjai_response.get("type")
                    can_act = mjai_response.get("can_act", True)

                    if action_type == "none" and not can_act:
                        # No pending operation — don't send skip
                        pass
                    elif action_type:
                        with pending_action_lock:
                            pending_action = mjai_response
                        if action_type != "none":
                            logger.info(
                                f"Bot action: {action_type} | "
                                f"{mjai_response.get('pai', '')}"
                            )
        except Exception as e:
            logger.error(f"game_loop error: {traceback.format_exc()}")

        time.sleep(0.05)


async def run_session(total_games, message_client):
    """Run one protocol session: login, play N games, then tear down.

    The MJAI queue stays alive across sessions; only Liqi sockets restart.
    Returns the number of games played plus the terminal session state.
    """
    global running, pending_action, game_active

    # Reset shared state
    pending_action = None
    game_active = False
    game_started_event.clear()
    game_ended_event.clear()

    # Per-session components; MJAI queue is shared across Liqi sessions.
    mjai_controller = Controller()
    mjai_bot = AkagiBot()
    jsonl_logger = JsonlLogger()
    session_stop = threading.Event()

    game_thread = threading.Thread(
        target=game_loop,
        args=(message_client, mjai_controller, mjai_bot, jsonl_logger, session_stop),
        daemon=True,
    )
    game_thread.start()

    automation = MajsoulAutomation(
        message_client.messages,
        should_continue=lambda: running,
    )
    session_game_count = 0

    async def recover_state(reason: str):
        global game_active
        logger.info(f"Recovering to protocol state ({reason})")
        result = await automation.recover()
        if result == "game":
            logger.info("Recovered into existing game")
            game_active = True
            game_started_event.set()
            game_ended_event.clear()
        elif result == "lobby":
            logger.info("Recovered to lobby")
            game_active = False
        elif result == "busy":
            logger.warning("Account is still busy; refusing to queue a new match")
            game_active = False
        else:
            logger.warning("Recovery did not reach a usable protocol state")
            game_active = False
        return result

    try:
        if not await automation.initialize():
            logger.error("Protocol failed to initialize")
            return SessionResult(session_game_count)

        if not await automation.login(
            settings.autoplay_account.username,
            settings.autoplay_account.password,
        ):
            logger.error("Login failed")
            return _login_failure_session_result(automation, session_game_count)

        lobby_result = await automation.wait_for_lobby()
        if lobby_result is None:
            logger.error("Failed to enter lobby or game")
            return SessionResult(session_game_count)
        if lobby_result == "busy":
            lobby_result = await _wait_busy_account_until_lobby(automation)
            if lobby_result == "busy":
                logger.warning("Account is still busy; not queueing a new match")
                return _busy_session_result(automation, session_game_count)

        if lobby_result == "game":
            logger.info("Auto-reconnected to game, will play it out first")
            game_active = True
            game_started_event.set()

        logger.info("Ready to play! Starting match loop...")

        consecutive_errors = 0
        while running:
            try:
                current_total = total_games + session_game_count + 1
                current_session = session_game_count + 1
                logger.info(f"=== Starting game #{current_total} (session #{current_session}) ===")

                # Check if already in a game (auto-reconnected after login)
                if game_active:
                    logger.info("Already in game (auto-reconnected), skipping match queue")
                else:
                    if automation.account_busy:
                        recovered_state = await recover_state("account busy before queue")
                        if recovered_state == "game":
                            continue
                        if recovered_state == "busy":
                            return _busy_session_result(automation, session_game_count)
                        if recovered_state != "lobby":
                            break

                    # Start match queue via RPC
                    start_result = await automation.start_match()
                    if start_result == "busy":
                        lobby_state = await _wait_busy_account_until_lobby(automation)
                        if lobby_state == "lobby":
                            continue
                        logger.warning("Account is already in a match/game state; leaving queue control alone")
                        return _busy_session_result(automation, session_game_count)
                    if start_result != "queued":
                        consecutive_errors += 1
                        logger.error(f"Failed to start match queue (attempt {consecutive_errors}/5)")
                        if consecutive_errors >= 5:
                            logger.error("Too many consecutive errors, stopping")
                            break
                        # Recover instead of blind retry — likely not in lobby
                        logger.info("Recovering to get back to lobby...")
                        recovered_state = await recover_state("start match failed")
                        if recovered_state == "game":
                            continue
                        if recovered_state == "lobby":
                            continue
                        if recovered_state == "busy":
                            return _busy_session_result(automation, session_game_count)
                        if not await _sleep_while_running(30):
                            break
                        continue

                    consecutive_errors = 0

                    # Wait for protocol bridge to emit start_game
                    logger.info("Waiting for match...")
                    game_started_event.clear()
                    game_ended_event.clear()
                    wait_started_at = time.time()

                    while running and not game_started_event.is_set():
                        if automation.game_connect_failed.is_set():
                            logger.error("Game connection failed after match; recovering")
                            break
                        if time.time() - wait_started_at > 420:
                            logger.error("Match/game start wait timed out; recovering")
                            break
                        await asyncio.sleep(0.5)

                    if not running:
                        await _cancel_match_safely(automation)
                        break
                    if automation.game_connect_failed.is_set():
                        game_active = False
                        if automation.account_busy:
                            logger.warning(
                                "Game connection failed after match and account is busy; "
                                "not attempting a fresh queue"
                            )
                            return _busy_session_result(automation, session_game_count)
                        recovered_state = await recover_state("game connect failed")
                        if recovered_state == "game":
                            continue
                        if recovered_state == "lobby":
                            continue
                        if recovered_state == "busy":
                            return _busy_session_result(automation, session_game_count)
                        break

                logger.info("Match found! Playing...")
                game_ended_event.clear()

                # Play the game: execute AI actions
                last_action_time = time.time()
                while running and not game_ended_event.is_set():
                    action = None
                    with pending_action_lock:
                        if pending_action:
                            action = pending_action
                            pending_action = None

                    if action:
                        last_action_time = time.time()
                        try:
                            seat = mjai_bot.player_id if hasattr(mjai_bot, 'player_id') else 0
                            action_ok = await automation.execute_action(action, seat)
                            if action_ok is False:
                                logger.warning("Action execution returned failure — recovering immediately")
                                game_active = False
                                game_ended_event.set()
                                recovered_state = await recover_state("action execution failed")
                                if recovered_state == "game":
                                    logger.info("Reconnected to game after action failure")
                                    last_action_time = time.time()
                                    continue
                                if recovered_state == "lobby":
                                    logger.warning(
                                        "Recovered to lobby after action failure; "
                                        "restarting session without counting this game"
                                    )
                                    return SessionResult(session_game_count)
                                if recovered_state == "busy":
                                    return _busy_session_result(automation, session_game_count)
                                break
                        except Exception as e:
                            logger.error(f"Action execution error: {traceback.format_exc()}")

                    await asyncio.sleep(0.1)

                    # Fast recovery: protocol detected all WebSocket connections closed
                    if message_client.ws_disconnected.is_set():
                        logger.warning("WebSocket disconnected mid-game — recovering immediately")
                        message_client.ws_disconnected.clear()
                        game_active = False
                        game_ended_event.set()
                        recovered_state = await recover_state("websocket disconnected mid-game")
                        if recovered_state == "game":
                            logger.info("Reconnected to game after WS recovery")
                            last_action_time = time.time()
                            continue
                        if recovered_state == "lobby":
                            logger.warning(
                                "Recovered to lobby after websocket disconnect; "
                                "restarting session without counting this game"
                            )
                            return SessionResult(session_game_count)
                        if recovered_state == "busy":
                            return _busy_session_result(automation, session_game_count)
                        break

                    # Watchdog: if no action for 120s during a game, assume stuck
                    if time.time() - last_action_time > 120:
                        logger.warning("No actions for 120s, game may be stuck — recovering")
                        game_active = False
                        game_ended_event.set()
                        # Try recovery instead of continuing to match loop
                        recovered_state = await recover_state("no actions watchdog")
                        if recovered_state == "game":
                            logger.info("Reconnected to game after recovery")
                            last_action_time = time.time()
                            continue
                        if recovered_state == "lobby":
                            logger.warning(
                                "Recovered to lobby after watchdog; "
                                "restarting session without counting this game"
                            )
                            return SessionResult(session_game_count)
                        if recovered_state == "busy":
                            return _busy_session_result(automation, session_game_count)
                        break

                if not running:
                    break

                logger.info(f"Game #{current_total} ended")
                session_game_count += 1

                # Handle end-game screen and return to lobby
                if not await automation.handle_end_game():
                    logger.warning("Failed to return to lobby, recovering...")
                    recovered_state = await recover_state("end game return lobby failed")
                    if recovered_state == "game":
                        continue
                    if recovered_state == "busy":
                        return _busy_session_result(automation, session_game_count)
                    if recovered_state != "lobby":
                        logger.error("Recovery failed after end game")
                        break
                if not await _sleep_while_running(3):
                    break

                consecutive_errors = 0
                logger.info("Returning to lobby for next game...")

                # Periodic soft-restart for stability
                if session_game_count >= MAX_GAMES_BEFORE_RESTART:
                    logger.info(f"Played {session_game_count} games this session, soft-restarting...")
                    break

            except Exception as e:
                logger.error(f"Error in game loop: {traceback.format_exc()}")
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    logger.error("Too many consecutive errors, stopping")
                    break
                logger.info("Attempting recovery...")
                try:
                    recovered_state = await recover_state("game loop exception")
                    if recovered_state == "busy":
                        return _busy_session_result(automation, session_game_count)
                    if recovered_state not in ("game", "lobby"):
                        if not await _sleep_while_running(30):
                            break
                except Exception:
                    if not await _sleep_while_running(30):
                        break

    finally:
        session_stop.set()
        game_thread.join(timeout=5)
        jsonl_logger.close()
        try:
            await asyncio.wait_for(automation.close(), timeout=8)
        except Exception as exc:
            logger.warning(f"Protocol automation close timed out/failed: {exc!r}")

    return SessionResult(session_game_count)


async def main():
    global running

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Starting Akagi (Full-Auto mode)...")
    logger.info(f"Mode: {settings.autoplay_mode.type} | Room: {settings.autoplay_mode.room}")

    # Validate account settings
    if not settings.autoplay_account.username or not settings.autoplay_account.password:
        logger.error("Please configure autoplay_account in settings.json")
        return

    total_games = 0

    # The protocol controller writes MJAI messages directly into this queue.
    message_client = ProtocolMessageClient()
    message_client.start()
    logger.info("Protocol message queue initialized")

    try:
        while running:
            logger.info(f"--- Starting new session (total games so far: {total_games}) ---")
            session_result = await run_session(total_games, message_client)
            total_games += session_result.games

            if not running:
                break

            delay = _session_restart_delay(session_result)
            if session_result.state == "busy":
                logger.warning(
                    "Session ended with account busy; will recheck protocol state "
                    f"in {delay}s without starting a new match"
                )
            else:
                logger.info(f"Session done ({session_result.games} games). Restarting in {delay}s...")
            if not await _sleep_while_running(delay):
                break
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        running = False
        message_client.stop()
        logger.info(f"Akagi (Full-Auto) stopped. Total games played: {total_games}")


if __name__ == "__main__":
    asyncio.run(main())
