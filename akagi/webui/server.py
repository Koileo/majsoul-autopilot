from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from .watcher import JsonlWatcher

app = FastAPI(title="Akagi WebUI")
watcher = JsonlWatcher()

static_dir = Path(__file__).parent / "static"


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    await watcher.register(ws)
    try:
        while True:
            data = await ws.receive_text()
    except WebSocketDisconnect:
        watcher.unregister(ws)
    except Exception:
        watcher.unregister(ws)


@app.get("/api/state")
async def get_state():
    """HTTP fallback to get current game state."""
    return watcher.get_full_state()


# Serve React SPA - must be last
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        """Serve React SPA for all non-API routes."""
        file_path = static_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(static_dir / "index.html"))
