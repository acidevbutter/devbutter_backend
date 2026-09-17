import logging
import uuid
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

from app.accounts.repository import AccountRepository
from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.conversations.service import ConversationService
from app.conversations.socket_server import sio
from app.core.clients.butter_mind_client import get_butter_mind_client
from app.core.security import DecodedToken, InvalidTokenError, TokenType, decode_token
from app.db.session import session_factory
from app.settings.config import settings

logger = logging.getLogger(__name__)

# sid -> validated user/account/session-version from the portal cookie (None
# for an unauthenticated/visitor socket). sid -> set of conversation_id strings
# that socket has since been authorized to join. Plain in-process dicts
# (rather than socketio's own session store, which is keyed off a live
# Engine.IO connection) keep the handlers easy to unit test and cheap to
# reason about for a single-process deployment.
@dataclass(frozen=True)
class SocketAuth:
    user_id: uuid.UUID
    account_id: uuid.UUID
    session_version: int


_socket_accounts: dict[str, SocketAuth | None] = {}
_socket_conversations: dict[str, set[str]] = {}


def _room(conversation_id: uuid.UUID | str) -> str:
    return f"conversation:{conversation_id}"


async def _socket_auth_from_token(access_token: str | None) -> SocketAuth | None:
    if not access_token:
        return None
    try:
        claims = decode_token(access_token, TokenType.ACCESS)
    except InvalidTokenError:
        return None
    if await _active_account_id(claims) is None:
        return None
    return SocketAuth(
        user_id=claims.user_id,
        account_id=claims.account_id,
        session_version=claims.session_version,
    )


async def _socket_auth_from_cookie(environ: dict[str, Any]) -> SocketAuth | None:
    """Browser Socket.IO clients can't set an Authorization header on the
    initial engineio handshake, so auth here piggybacks on the same httpOnly
    cookie the frontend already sends with portal REST calls (sent cross-origin
    via CORS credentials -- see socket_server.py's cors_credentials=True).
    """
    raw_cookie = environ.get("HTTP_COOKIE")
    if not raw_cookie:
        return None
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(raw_cookie)
    morsel = cookie.get(settings.portal_access_cookie_name)
    if morsel is None:
        return None
    return await _socket_auth_from_token(morsel.value)


async def _active_account_id(auth: DecodedToken | SocketAuth | None) -> uuid.UUID | None:
    if auth is None:
        return None
    async with session_factory() as db:
        repository = AccountRepository(db)
        user = await repository.get_user(auth.user_id)
        account = await repository.get_for_user(auth.account_id, auth.user_id)
    if (
        user is None
        or not user.is_active
        or user.session_version != auth.session_version
        or account is None
    ):
        return None
    return account.id


def _is_authorized(conversation: Conversation, account_id: uuid.UUID | None) -> bool:
    # A Conversation with account_id=None is an unclaimed visitor chat -- it
    # stays that way for the entire diagnosis, only getting linked to an
    # account at draft-quote time (see conversations/router.py::draft_quote
    # and docs/plano-onboarding-conversa-primeiro.md), so any socket (token or
    # not) may join it. Once account_id is set, only that account's socket may.
    if conversation.account_id is None:
        return True
    return account_id == conversation.account_id


@sio.event  # type: ignore[untyped-decorator]
async def connect(sid: str, environ: dict[str, Any], auth: Any = None) -> None:
    _socket_accounts[sid] = await _socket_auth_from_cookie(environ)


@sio.event  # type: ignore[untyped-decorator]
async def disconnect(sid: str) -> None:
    _socket_accounts.pop(sid, None)
    _socket_conversations.pop(sid, None)


@sio.event  # type: ignore[untyped-decorator]
async def join_conversation(sid: str, data: dict[str, Any]) -> dict[str, Any]:
    """Returns an ack dict ({"ok": bool, "detail": ...}) rather than emitting a
    separate event -- the client awaits this ack before calling send_message,
    which avoids a real race: with async_handlers on (the default), a
    join_conversation and a send_message fired back-to-back by the client
    could otherwise run as concurrent tasks server-side, with send_message's
    authorization check racing ahead of join_conversation's room entry.
    """
    raw_conversation_id = data.get("conversation_id")
    try:
        conversation_id = uuid.UUID(raw_conversation_id)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "conversation_id is required and must be a UUID"}

    socket_auth = _socket_accounts.get(sid)
    account_id = await _active_account_id(socket_auth)
    if socket_auth is not None and account_id is None:
        _socket_conversations.pop(sid, None)
        return {"ok": False, "detail": "Authentication expired"}

    async with session_factory() as db:
        conversation = await ConversationRepository(db).get(conversation_id)

    if conversation is None or not _is_authorized(conversation, account_id):
        return {"ok": False, "detail": "Conversation not found"}

    _socket_conversations.setdefault(sid, set()).add(str(conversation_id))
    await sio.enter_room(sid, _room(conversation_id))
    return {"ok": True}


def _clean_selection(raw: object) -> dict[str, Any] | None:
    """Accept only a well-formed {field: str, option_ids: [str, ...]} from the
    browser -- anything else is dropped rather than persisted as-is."""
    if not isinstance(raw, dict):
        return None
    field = raw.get("field")
    option_ids = raw.get("option_ids")
    if not isinstance(field, str) or not isinstance(option_ids, list):
        return None
    ids = [o for o in option_ids if isinstance(o, str)]
    if not field or not ids:
        return None
    return {"field": field, "option_ids": ids}


@sio.event  # type: ignore[untyped-decorator]
async def send_message(sid: str, data: dict[str, Any]) -> None:
    raw_conversation_id = data.get("conversation_id")
    content = data.get("content", "")
    selection = _clean_selection(data.get("selection"))

    if raw_conversation_id not in _socket_conversations.get(sid, set()):
        await sio.emit(
            "assistant_error", {"detail": "Join the conversation before sending messages"}, to=sid
        )
        return

    socket_auth = _socket_accounts.get(sid)
    if socket_auth is not None and await _active_account_id(socket_auth) is None:
        _socket_conversations.pop(sid, None)
        await sio.emit("assistant_error", {"detail": "Authentication expired"}, to=sid)
        return

    conversation_id = uuid.UUID(raw_conversation_id)
    room = _room(conversation_id)

    async with session_factory() as db:
        service = ConversationService(ConversationRepository(db), get_butter_mind_client())
        conversation = await service.get(conversation_id)
        if conversation is None:
            await sio.emit("assistant_error", {"detail": "Conversation not found"}, room=room)
            return

        try:
            async for event in service.stream_message(conversation, content, selection):
                if event["type"] == "delta":
                    await sio.emit("assistant_chunk", {"content": event["content"]}, room=room)
                elif event["type"] == "done":
                    payload = {
                        "ready_to_submit": event["ready_to_submit"],
                        "message_id": event["message_id"],
                    }
                    if event.get("preview") is not None:
                        payload["preview"] = event["preview"]
                    await sio.emit("assistant_done", payload, room=room)
        except Exception as exc:
            logger.exception("streaming reply failed for conversation %s", conversation_id)
            await sio.emit("assistant_error", {"detail": str(exc)}, room=room)
