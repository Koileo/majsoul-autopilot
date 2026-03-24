import threading
import traceback
import asyncio
import queue
import time
import mitmproxy.http
import mitmproxy.log
import mitmproxy.tcp
import mitmproxy.websocket
from mitmproxy import proxy, options, ctx
from mitmproxy.tools.dump import DumpMaster
from .bridge import MajsoulBridge
from .mitm_abc import ClientWebSocketABC
from .logger import logger

# Because in Majsouls, every flow's message has an id, we need to use one bridge for each flow
activated_flows: list[str] = [] # store all flow.id ([-1] is the recently opened)
majsoul_bridges: dict[str, MajsoulBridge] = {} # store all flow.id -> MajsoulBridge
mjai_messages: queue.Queue[dict] = queue.Queue() # store all messages
ws_disconnected = threading.Event()
_MAX_WS_MESSAGES = 50


class ClientWebSocket(ClientWebSocketABC):
    def __init__(self):
        self.bridge_lock = threading.Lock()
        pass

    def websocket_start(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global activated_flows, majsoul_bridges
        logger.info(f"WebSocket connection opened: {flow.id}")
        ws_disconnected.clear()
        activated_flows.append(flow.id)
        majsoul_bridges[flow.id] = MajsoulBridge()

    def websocket_message(self, flow: mitmproxy.http.HTTPFlow):
        assert isinstance(flow.websocket, mitmproxy.websocket.WebSocketData)
        global activated_flows, majsoul_bridges
        try:
            if flow.id in activated_flows:
                msg = flow.websocket.messages[-1]
                if msg.from_client:
                    logger.debug(f"<- Message: {msg.content}")
                else: # from server
                    logger.debug(f"-> Message: {msg.content}")
                self.bridge_lock.acquire()
                bridge = majsoul_bridges[flow.id]
                msgs = bridge.parse(msg.content)
                self.bridge_lock.release()
                if msgs is None:
                    return
                for m in msgs:
                    mjai_messages.put(m)
            else:
                logger.error(f"WebSocket message received from unactivated flow: {flow.id}")
        except Exception as e:
            # Release the lock if it is locked
            if self.bridge_lock.locked():
                self.bridge_lock.release()
            logger.error(f"Error: {traceback.format_exc()}")
            logger.error(f"Error: {str(e)}")
            logger.error(f"Error: {e.__traceback__.tb_lineno}")
        finally:
            # Trim accumulated WebSocket messages to prevent memory buildup
            if flow.websocket and len(flow.websocket.messages) > _MAX_WS_MESSAGES:
                del flow.websocket.messages[:-_MAX_WS_MESSAGES]

    def websocket_end(self, flow: mitmproxy.http.HTTPFlow):
        global activated_flows, majsoul_bridges
        if flow.id in activated_flows:
            ws = flow.websocket
            close_info = ""
            if ws:
                close_info = f" close_code={ws.close_code} reason={ws.close_reason!r} by={'client' if ws.closed_by_client else 'server'}"
            logger.info(f"WebSocket connection closed: {flow.id}{close_info}")
            activated_flows.remove(flow.id)
            del majsoul_bridges[flow.id]
            if not activated_flows:
                logger.warning("All game WebSocket connections closed")
                ws_disconnected.set()
        else:
            logger.error(f"WebSocket connection closed from unactivated flow: {flow.id}")

async def start_proxy(host, port):
    opts = options.Options(listen_host=host, listen_port=port, ssl_insecure=True)
    master = DumpMaster(
        opts,
        with_termlog=False,
        with_dumper=False,
    )
    master.addons.add(ClientWebSocket())
    logger.info(f"Starting MITM proxy server at {host}:{port}")
    await master.run()
    logger.info("MITM proxy server stopped")
    return master

def stop_proxy():
    ctx.master.shutdown()
