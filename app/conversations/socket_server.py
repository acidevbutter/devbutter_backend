import socketio

from app.settings.config import settings

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.cors_allowed_origins,
    cors_credentials=True,
)
