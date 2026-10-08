# WMS MCP HTTP 服务
# - 旧版 SSE:        GET  /mcp/sse  (+ POST /mcp/messages/)
# - 新版 Streamable: /mcp          (GET/POST/DELETE，现代客户端用这个)
# 运行：python mcp/http_server.py  （默认 :8001）
import os
import importlib.util
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

# 用文件路径加载本地 mcp/server.py，避免与 pip 的 mcp 包名冲突
_server_path = os.path.join(os.path.dirname(__file__), "server.py")
_spec = importlib.util.spec_from_file_location("wms_mcp_server", _server_path)
_wms_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_wms_mod)
wms_server = _wms_mod.server

# --- 旧版 SSE ---
sse = SseServerTransport("/mcp/messages/")

async def handle_sse(request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await wms_server.run(streams[0], streams[1], wms_server.create_initialization_options())

async def handle_messages(request):
    await sse.handle_post_message(request.scope, request.receive, request._send)

# --- 新版 Streamable HTTP ---
session_manager = StreamableHTTPSessionManager(app=wms_server)

async def handle_streamable(request):
    await session_manager.handle_request(request.scope, request.receive, request._send)

async def health(request):
    return JSONResponse({"ok": True, "transports": ["sse:/mcp/sse", "streamable:/mcp"]})

@asynccontextmanager
async def lifespan(app):
    async with session_manager.run():
        print("[wms-mcp] streamable session manager started", flush=True)
        yield

app = Starlette(
    routes=[
        Route("/mcp", endpoint=handle_streamable, methods=["GET", "POST", "DELETE"]),
        Route("/mcp/sse", endpoint=handle_sse),
        Route("/mcp/messages/", endpoint=handle_messages, methods=["POST"]),
        Route("/mcp/health", endpoint=health),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    port = int(os.getenv("MCP_PORT", "8001"))
    print(f"[wms-mcp] HTTP on :{port}  (streamable: /mcp, sse: /mcp/sse)", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port)
