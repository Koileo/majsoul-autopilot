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

MAX_GAMES_BEFORE_RESTART = 3  # Restart after this many games for stability

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


def game_loop(mitm_client, mjai_controller, mjai_bot, jsonl_logger):
    """Process game messages and store actions for the automation to execute."""
    global running, pending_action, game_active

    last_tsumo_tile = None  # Track our last drawn tile for riichi

    while running:
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

                # Handle riichi: Mortal returns "reach" without a tile.
                # We attach the tsumo tile so execute_action can send
                # inputOperation type=7 with the correct tile.
                if (game_active and mjai_response
                        and mjai_response.get("type") == "reach"
                        and not mjai_response.get("pai")):
                    if last_tsumo_tile:
                        mjai_response["pai"] = last_tsumo_tile
                        mjai_response["tsumogiri"] = True
                        logger.info(f"Riichi: using tsumo tile {last_tsumo_tile}")
                    else:
                        logger.warning("Riichi requested but no tsumo tile tracked")

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


async def main():
    global running, pending_action, game_active

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("Starting Akagi (Full-Auto mode)...")
    logger.info(f"Mode: {settings.autoplay_mode.type} | Room: {settings.autoplay_mode.room}")

    # Validate account settings
    if not settings.autoplay_account.username or not settings.autoplay_account.password:
        logger.error("Please configure autoplay_account in settings.json")
        return

    # Start MITM proxy
    mitm_client = Client()
    mjai_controller = Controller()
    mjai_bot = AkagiBot()
    jsonl_logger = JsonlLogger()

    mitm_client.start()
    logger.info("MITM proxy started")

    # Start WebUI server in background thread
    webui_port = 3002
    webui_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host="0.0.0.0", port=webui_port, log_level="warning"),
        daemon=True,
    )
    webui_thread.start()

    # Start file watcher for WebUI
    watcher_thread = threading.Thread(
        target=lambda: asyncio.run(watcher.start()),
        daemon=True,
    )
    watcher_thread.start()
    logger.info(f"WebUI available at http://localhost:{webui_port}")

    # Start game processing loop in background thread
    game_thread = threading.Thread(
        target=game_loop,
        args=(mitm_client, mjai_controller, mjai_bot, jsonl_logger),
        daemon=True,
    )
    game_thread.start()

    # Start browser automation
    automation = MajsoulAutomation()
    game_count = 0
    try:
        await automation.start_browser(proxy_port=settings.mitm.port)
        await automation.navigate_to_game()

        # Wait for game engine to fully initialize
        if not await automation.wait_for_entrance():
            logger.error("Game failed to initialize")
            return

        # Login via game's native JS API
        if not await automation.login(
            settings.autoplay_account.username,
            settings.autoplay_account.password,
        ):
            logger.error("Login failed")
            return

        lobby_result = await automation.wait_for_lobby()
        if lobby_result is None:
            logger.error("Failed to enter lobby or game")
            return

        if lobby_result == "game":
            logger.info("Auto-reconnected to game, will play it out first")
            game_active = True
            game_started_event.set()

        logger.info("Ready to play! Starting match loop...")

        # Main loop: match → play → repeat
        consecutive_errors = 0
        while running:
            try:
                game_count += 1
                logger.info(f"=== Starting game #{game_count} ===")

                # Check if already in a game (auto-reconnected after login)
                if game_active:
                    logger.info("Already in game (auto-reconnected), skipping match queue")
                else:
                    # Start match queue via RPC
                    if not await automation.start_match():
                        consecutive_errors += 1
                        wait_time = min(30 * consecutive_errors, 120)
                        logger.error(f"Failed to start match queue, retrying in {wait_time}s... "
                                     f"(attempt {consecutive_errors}/5)")
                        if consecutive_errors >= 5:
                            logger.error("Too many consecutive errors, stopping")
                            break
                        await asyncio.sleep(wait_time)
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

                logger.info(f"Game #{game_count} ended")

                # Handle end-game screen and return to lobby
                await automation.handle_end_game()
                await asyncio.sleep(3)

                logger.info("Returning to lobby for next game...")

                # Periodic restart for stability
                if game_count >= MAX_GAMES_BEFORE_RESTART:
                    logger.info(f"Played {game_count} games, restarting for stability...")
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

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {traceback.format_exc()}")
    finally:
        running = False
        watcher.stop()
        jsonl_logger.close()
        await automation.close()
        mitm_client.stop()
        logger.info(f"Akagi (Full-Auto) stopped. Games played: {game_count}")


if __name__ == "__main__":
    asyncio.run(main())
