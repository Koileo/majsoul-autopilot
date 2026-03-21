"""WebUI only — view game state without autoplay."""

import asyncio
import threading
import uvicorn
from akagi.webui.server import app, watcher
from settings.settings import settings


def main():
    port = settings.webui_port

    watcher_thread = threading.Thread(
        target=lambda: asyncio.run(watcher.start()),
        daemon=True,
    )
    watcher_thread.start()

    print(f"WebUI running at http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
