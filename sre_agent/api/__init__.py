"""FastAPI routes and WebSocket handlers for Agent C."""

from sre_agent.api.middleware import install_middlewares
from sre_agent.api.routes import build_api_router
from sre_agent.api.websocket import build_websocket_router

__all__ = [
    "install_middlewares",
    "build_api_router",
    "build_websocket_router",
]
