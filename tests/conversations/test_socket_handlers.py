import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import User
from app.conversations import socket_handlers
from app.conversations.repository import ConversationRepository
from app.conversations.socket_server import sio
from app.core.security import TokenType, decode_token
from app.settings.config import settings
from tests.conftest import signup


class _FakeButterMindClient:
    def __init__(self, events: list[dict]):
        self._events = events

    async def stream_diagnosis_message(self, mind_session_id: str, content: str):
        for event in self._events:
            yield event


def _account_id(tokens: dict) -> uuid.UUID:
    return decode_token(tokens["access_token"], TokenType.ACCESS).account_id


def _cookie_environ(access_token: str | None) -> dict:
    if access_token is None:
        return {}
    return {"HTTP_COOKIE": f"{settings.portal_access_cookie_name}={access_token}"}


def _patch_shared_session(monkeypatch, db_session: AsyncSession) -> None:
    @asynccontextmanager
    async def _shared_session_factory():
        yield db_session

    monkeypatch.setattr(socket_handlers, "session_factory", _shared_session_factory)


async def test_join_then_send_message_streams_chunks_then_done(client, db_session, monkeypatch):
    tokens = await signup(client, email="owner@example.com")
    account_id = _account_id(tokens)

    conversation = await ConversationRepository(db_session).create(account_id, "mind-session-1")

    _patch_shared_session(monkeypatch, db_session)
    monkeypatch.setattr(
        socket_handlers,
        "get_butter_mind_client",
        lambda: _FakeButterMindClient(
            [
                {"type": "delta", "content": "Claro"},
                {"type": "delta", "content": ", vamos conversar."},
                {"type": "done", "ready_to_submit": True, "message_id": "msg-1"},
            ]
        ),
    )
    emit_mock = AsyncMock()
    enter_room_mock = AsyncMock()
    monkeypatch.setattr(sio, "emit", emit_mock)
    monkeypatch.setattr(sio, "enter_room", enter_room_mock)

    sid = "sid-owner"
    await socket_handlers.connect(sid, _cookie_environ(tokens["access_token"]))
    ack = await socket_handlers.join_conversation(sid, {"conversation_id": str(conversation.id)})

    assert ack == {"ok": True}
    enter_room_mock.assert_awaited_once_with(sid, f"conversation:{conversation.id}")

    emit_mock.reset_mock()
    await socket_handlers.send_message(
        sid, {"conversation_id": str(conversation.id), "content": "Preciso de um app."}
    )

    calls = emit_mock.await_args_list
    room = f"conversation:{conversation.id}"
    assert [c.args[0] for c in calls] == ["assistant_chunk", "assistant_chunk", "assistant_done"]
    assert calls[0].args[1] == {"content": "Claro"}
    assert calls[0].kwargs == {"room": room}
    assert calls[1].args[1] == {"content": ", vamos conversar."}
    assert calls[2].args[1] == {"ready_to_submit": True, "message_id": "msg-1"}

    refreshed = await ConversationRepository(db_session).get(conversation.id)
    assert refreshed.transcript[-2:] == [
        {"role": "user", "content": "Preciso de um app."},
        {"role": "assistant", "content": "Claro, vamos conversar."},
    ]


async def test_join_rejects_conversation_owned_by_another_account(client, db_session, monkeypatch):
    owner_tokens = await signup(client, email="owner2@example.com")
    stranger_tokens = await signup(client, email="stranger@example.com")
    owner_account_id = _account_id(owner_tokens)

    conversation = await ConversationRepository(db_session).create(
        owner_account_id, "mind-session-2"
    )

    _patch_shared_session(monkeypatch, db_session)
    emit_mock = AsyncMock()
    enter_room_mock = AsyncMock()
    monkeypatch.setattr(sio, "emit", emit_mock)
    monkeypatch.setattr(sio, "enter_room", enter_room_mock)

    sid = "sid-stranger"
    await socket_handlers.connect(sid, _cookie_environ(stranger_tokens["access_token"]))
    ack = await socket_handlers.join_conversation(sid, {"conversation_id": str(conversation.id)})

    assert ack == {"ok": False, "detail": "Conversation not found"}
    enter_room_mock.assert_not_awaited()

    emit_mock.reset_mock()
    mind_client_called = False

    def _fail_if_called():
        nonlocal mind_client_called
        mind_client_called = True
        return _FakeButterMindClient([])

    monkeypatch.setattr(socket_handlers, "get_butter_mind_client", _fail_if_called)

    await socket_handlers.send_message(
        sid, {"conversation_id": str(conversation.id), "content": "Tentando sem autorização."}
    )

    assert mind_client_called is False
    assert emit_mock.await_args_list[-1].args[0] == "assistant_error"


async def test_visitor_without_cookie_can_join_unclaimed_conversation(
    client, db_session, monkeypatch
):
    conversation = await ConversationRepository(db_session).create(None, "mind-session-3")

    _patch_shared_session(monkeypatch, db_session)
    emit_mock = AsyncMock()
    enter_room_mock = AsyncMock()
    monkeypatch.setattr(sio, "emit", emit_mock)
    monkeypatch.setattr(sio, "enter_room", enter_room_mock)

    sid = "sid-visitor"
    await socket_handlers.connect(sid, _cookie_environ(None))
    ack = await socket_handlers.join_conversation(sid, {"conversation_id": str(conversation.id)})

    assert ack == {"ok": True}
    enter_room_mock.assert_awaited_once_with(sid, f"conversation:{conversation.id}")


async def test_join_rejects_a_socket_after_its_session_is_revoked(client, db_session, monkeypatch):
    tokens = await signup(client, email="revoked-socket@example.com")
    account_id = _account_id(tokens)
    conversation = await ConversationRepository(db_session).create(
        account_id, "mind-session-revoked"
    )

    _patch_shared_session(monkeypatch, db_session)
    sid = "sid-revoked"
    await socket_handlers.connect(sid, _cookie_environ(tokens["access_token"]))

    user = (
        await db_session.execute(select(User).where(User.email == "revoked-socket@example.com"))
    ).scalar_one()
    user.session_version += 1
    await db_session.commit()

    ack = await socket_handlers.join_conversation(sid, {"conversation_id": str(conversation.id)})
    assert ack == {"ok": False, "detail": "Authentication expired"}
