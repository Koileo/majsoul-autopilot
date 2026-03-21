"""Full-auto Majsoul player: login → match → play → repeat."""

import os
os.environ["LOGURU_AUTOINIT"] = "False"

import sys
import time
import signal
import asyncio
import threading
import traceback
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
import uvicorn
from akagi.core import process_messages
from akagi.jsonl_logger import JsonlLogger
from akagi.webui.server import app, watcher
from mitm.client import Client
from mjai_bot.bot import AkagiBot
from mjai_bot.controller import Controller
from settings.settings import settings
from autoplay.majsoul_automation import MajsoulAutomation

MAX_GAMES_BEFORE_RESTART = 1  # Soft-restart browser/MITM after this many games

running = True
# Shared state between game_loop thread and async main
pending_action = None
pending_action_lock = threading.Lock()
game_active = False
game_ended_event = threading.Event()
game_started_event = threading.Event()


def signal_handler(sig, frame):
    global running
    logger.info("Shutting down...")
    running = False


def game_loop(mitm_client, mjai_controller, mjai_bot, jsonl_logger, session_stop):
    """Process game messages and store actions for the automation to execute."""
    global running, pending_action, game_active

    last_tsumo_tile = None  # Track our last drawn tile for riichi

    while running and not session_stop.is_set():
        try:
            result = process_messages(mitm_client, mjai_controller, mjai_bot, jsonl_logger)
            if result:
                mjai_msgs = result["mjai_msgs"]
                mjai_response = result["mjai_response"]

                player_id = getattr(mjai_bot, 'player_id', None)

                for msg in mjai_msgs:
                    if msg.get("type") == "start_game":
                        logger.info("Game started (MITM detected start_game)")
                        game_active = True
                        game_started_event.set()
                    elif msg.get("type") == "end_game":
                        logger.info("Game ended (MITM detected end_game)")
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
                if (game_active and mjai_response
                        and mjai_response.get("type") == "reach"
                        and not mjai_response.get("pai")):
                    reach_event = {"type": "reach", "actor": player_id}
                    dahai_response = mjai_controller.react([reach_event])
                    if dahai_response and dahai_response.get("type") == "dahai":
                        mjai_response["pai"] = dahai_response["pai"]
                        mjai_response["tsumogiri"] = dahai_response.get("tsumogiri", False)
                        logger.info(f"Riichi: model chose {dahai_response['pai']} "
                                    f"(tsumogiri={mjai_response['tsumogiri']})")
                    elif last_tsumo_tile:
                        mjai_response["pai"] = last_tsumo_tile
                        mjai_response["tsumogiri"] = True
                        logger.warning(f"Riichi: model didn't return dahai, fallback to tsumo {last_tsumo_tile}")
                    else:
                        logger.warning("Riichi requested but no tile available")

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


async def run_session(total_games, mitm_client):
    """Run one session: start browser/bot, play N games, then tear down.

    MITM proxy is kept alive across sessions; only the browser restarts.
    Returns the number of games played in this session.
    """
    global running, pending_action, game_active

    # Reset shared state
    pending_action = None
    game_active = False
    game_started_event.clear()
    game_ended_event.clear()

    # Per-session components (MITM is passed in from main)
    mjai_controller = Controller()
    mjai_bot = AkagiBot()
    jsonl_logger = JsonlLogger()
    session_stop = threading.Event()

    game_thread = threading.Thread(
        target=game_loop,
        args=(mitm_client, mjai_controller, mjai_bot, jsonl_logger, session_stop),
        daemon=True,
    )
    game_thread.start()

    automation = MajsoulAutomation()
    session_game_count = 0

    try:
        await automation.start_browser(proxy_port=settings.mitm.port)
        await automation.navigate_to_game()

        if not await automation.wait_for_entrance():
            logger.error("Game failed to initialize")
            return session_game_count

        if not await automation.login(
            settings.autoplay_account.username,
            settings.autoplay_account.password,
        ):
            logger.error("Login failed")
            return session_game_count

        lobby_result = await automation.wait_for_lobby()
        if lobby_result is None:
            logger.error("Failed to enter lobby or game")
            return session_game_count

        if lobby_result == "game":
            logger.info("Auto-reconnected to game, will play it out first")
            game_active = True
            game_started_event.set()

        logger.info("Ready to play! Starting match loop...")

        consecutive_errors = 0
        while running:
            try:
                session_game_count += 1
                current_total = total_games + session_game_count
                logger.info(f"=== Starting game #{current_total} (session #{session_game_count}) ===")

                # Check if already in a game (auto-reconnected after login)
                if game_active:
                    logger.info("Already in game (auto-reconnected), skipping match queue")
                else:
                    # Start match queue via RPC
                    if not await automation.start_match():
                        consecutive_errors += 1
                        logger.error(f"Failed to start match queue (attempt {consecutive_errors}/5)")
                        if consecutive_errors >= 5:
                            logger.error("Too many consecutive errors, stopping")
                            break
                        # Recover instead of blind retry — likely not in lobby
                        logger.info("Recovering to get back to lobby...")
                        if await automation.recover():
                            continue
                        await asyncio.sleep(30)
                        continue

                    consecutive_errors = 0

                    # Wait for game to start (MITM detects start_game)
                    logger.info("Waiting for match...")
                    game_started_event.clear()
                    game_ended_event.clear()

                    while running and not game_started_event.is_set():
                        await asyncio.sleep(0.5)

                    if not running:
                        await automation.cancel_match()
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
                            await automation.execute_action(action, seat)
                        except Exception as e:
                            logger.error(f"Action execution error: {traceback.format_exc()}")

                    await asyncio.sleep(0.1)

                    # Watchdog: if no action for 120s during a game, assume stuck
                    if time.time() - last_action_time > 120:
                        logger.warning("No actions for 120s, game may be stuck — recovering")
                        game_active = False
                        game_ended_event.set()
                        # Try recovery instead of continuing to match loop
                        if await automation.recover():
                            lobby_result = await automation.wait_for_lobby()
                            if lobby_result == "game":
                                logger.info("Reconnected to game after recovery")
                                game_active = True
                                game_started_event.set()
                                continue
                        break

                if not running:
                    break

                logger.info(f"Game #{current_total} ended")

                # Handle end-game screen and return to lobby
                if not await automation.handle_end_game():
                    logger.warning("Failed to return to lobby, recovering...")
                    if not await automation.recover():
                        logger.error("Recovery failed after end game")
                        break
                await asyncio.sleep(3)

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
                    if not await automation.recover():
                        await asyncio.sleep(30)
                except Exception:
                    await asyncio.sleep(30)

    finally:
        session_stop.set()
        game_thread.join(timeout=5)
        jsonl_logger.close()
        await automation.close()

    return session_game_count


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

    # Start WebUI server (persists across soft-restarts)
    webui_port = settings.webui_port
    webui_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=webui_port, log_level="warning"),
        daemon=True,
    )
    webui_thread.start()

    watcher_thread = threading.Thread(
        target=lambda: asyncio.run(watcher.start()),
        daemon=True,
    )
    watcher_thread.start()
    logger.info(f"WebUI available at http://localhost:{webui_port}")

    total_games = 0

    # MITM proxy lives for the entire process lifetime
    mitm_client = Client()
    mitm_client.start()
    logger.info("MITM proxy started")

    try:
        while running:
            logger.info(f"--- Starting new session (total games so far: {total_games}) ---")
            session_games = await run_session(total_games, mitm_client)
            total_games += session_games

            if not running:
                break

            logger.info(f"Session done ({session_games} games). Restarting in 10s...")
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        running = False
        mitm_client.stop()
        watcher.stop()
        logger.info(f"Akagi (Full-Auto) stopped. Total games played: {total_games}")


if __name__ == "__main__":
    asyncio.run(main())
