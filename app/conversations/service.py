import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar, cast

from app.conversations.models import Conversation
from app.conversations.repository import ConversationRepository
from app.core.clients.butter_mind_client import ButterMindClient
from app.core.decorators import log_errors
from app.core.exceptions import UpstreamServiceError

T = TypeVar("T")

# Sentinel for "the stream ended before yielding anything" -- butter_mind
# always emits at least a `done`, but _open_diagnosis_stream stays honest.
_NO_EVENT: Any = object()


@log_errors
class ConversationService:
    def __init__(self, repository: ConversationRepository, mind_client: ButterMindClient):
        self.repository = repository
        self.mind_client = mind_client

    async def start(self, account_id: uuid.UUID | None) -> Conversation:
        mind_session = await self.mind_client.create_diagnosis_session(str(uuid.uuid4()))
        return await self.repository.create(account_id, mind_session["id"])

    async def get(self, conversation_id: uuid.UUID) -> Conversation | None:
        return await self.repository.get(conversation_id)

    async def _provision_mind_session(self, conversation: Conversation) -> str:
        """The conversation's butter_mind diagnosis session is gone (butter_mind
        404'd its mind_session_id -- its store was reset independently of ours).
        Start a fresh one and repoint the conversation at it so the chat heals
        itself instead of dead-ending on a 404. The local transcript mirror is
        kept as-is; butter_mind just starts this new session's history empty.
        """
        mind_session = await self.mind_client.create_diagnosis_session(str(uuid.uuid4()))
        await self.repository.set_mind_session_id(conversation, mind_session["id"])
        return cast(str, mind_session["id"])

    async def _with_mind_session(
        self, conversation: Conversation, call: Callable[[str], Awaitable[T]]
    ) -> T:
        """Run `call(mind_session_id)`; if butter_mind reports the session gone
        (404), reprovision it once and retry."""
        try:
            return await call(cast(str, conversation.mind_session_id))
        except UpstreamServiceError as exc:
            if exc.status_code != 404:
                raise
            new_id = await self._provision_mind_session(conversation)
            return await call(new_id)

    @staticmethod
    def _user_turn(content: str, selection: dict[str, Any] | None) -> dict[str, Any]:
        """The user's transcript entry. `selection` (field + option_ids) is
        attached verbatim when the visitor answered by tapping option chips --
        it's persistence/audit only; butter_mind only ever gets `content`."""
        entry: dict[str, Any] = {"role": "user", "content": content}
        if selection:
            entry["selected"] = selection
        return entry

    async def send_message(
        self,
        conversation: Conversation,
        content: str,
        selection: dict[str, Any] | None = None,
    ) -> tuple[str, bool, dict[str, Any] | None]:
        turn = await self._with_mind_session(
            conversation, lambda sid: self.mind_client.send_diagnosis_message(sid, content)
        )
        reply = turn["message"]["content"]
        ready_to_submit = turn["ready_to_submit"]
        preview = turn.get("preview")
        await self.repository.append_messages(
            conversation,
            self._user_turn(content, selection),
            {"role": "assistant", "content": reply},
            ready_to_submit=ready_to_submit,
            draft_preview=preview,
        )
        return reply, ready_to_submit, preview

    async def select_option(
        self, conversation: Conversation, option_key: str
    ) -> tuple[str, bool, dict[str, Any] | None]:
        """The visitor picked one of the proposed paths (ADR-0004). Relays to
        butter_mind, mirrors the choice + confirmation into the local
        transcript, and re-persists the preview."""
        turn = await self._with_mind_session(
            conversation,
            lambda sid: self.mind_client.select_diagnosis_option(sid, option_key),
        )
        reply = turn["message"]["content"]
        ready_to_submit = turn["ready_to_submit"]
        preview = turn.get("preview")
        chosen = next(
            (o for o in (preview or {}).get("options", []) if o.get("key") == option_key),
            {"title": option_key},
        )
        await self.repository.append_messages(
            conversation,
            {
                "role": "user",
                "content": f"Escolhi: {chosen.get('title')}",
                "selected_option_key": option_key,
            },
            {"role": "assistant", "content": reply},
            ready_to_submit=ready_to_submit,
            draft_preview=preview,
        )
        return reply, ready_to_submit, preview

    async def patch_business_profile(
        self, conversation: Conversation, patch: dict[str, Any]
    ) -> tuple[str, bool, dict[str, Any] | None]:
        """Merge the collecting-step fields (ADR-0004). No transcript entry --
        this is a form edit, not a chat turn -- but the preview is re-persisted
        so a resumed chat re-renders the updated stage/missing_fields."""
        turn = await self._with_mind_session(
            conversation,
            lambda sid: self.mind_client.patch_diagnosis_business_profile(sid, patch),
        )
        reply = turn["message"]["content"]
        ready_to_submit = turn["ready_to_submit"]
        preview = turn.get("preview")
        await self.repository.append_messages(
            conversation,
            ready_to_submit=ready_to_submit,
            draft_preview=preview,
        )
        return reply, ready_to_submit, preview

    async def _open_diagnosis_stream(
        self, conversation: Conversation, content: str
    ) -> tuple[AsyncIterator[dict[str, Any]], Any]:
        """Open butter_mind's SSE stream for this turn, healing a vanished
        session once. Returns (stream, first_event): the first event is pulled
        here because that's where butter_mind surfaces a 404 for a missing
        session -- the status check runs before its first yield.
        """
        try:
            stream = self.mind_client.stream_diagnosis_message(
                cast(str, conversation.mind_session_id), content
            )
            return stream, await self._first_event(stream)
        except UpstreamServiceError as exc:
            if exc.status_code != 404:
                raise
        new_id = await self._provision_mind_session(conversation)
        stream = self.mind_client.stream_diagnosis_message(new_id, content)
        return stream, await self._first_event(stream)

    @staticmethod
    async def _first_event(stream: AsyncIterator[dict[str, Any]]) -> Any:
        try:
            return await anext(stream)
        except StopAsyncIteration:
            return _NO_EVENT

    async def stream_message(
        self,
        conversation: Conversation,
        content: str,
        selection: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Same persistence as send_message, but relays butter_mind's streamed
        reply chunk by chunk -- used by the Socket.IO `send_message` handler.
        Re-yields each upstream event; on `done` it appends the user + full
        assistant messages to the local transcript mirror, same as send_message.
        """
        full_reply = ""
        stream, first_event = await self._open_diagnosis_stream(conversation, content)

        async def _events() -> AsyncIterator[dict[str, Any]]:
            if first_event is not _NO_EVENT:
                yield first_event
            async for event in stream:
                yield event

        async for event in _events():
            if event["type"] == "delta":
                full_reply += event["content"]
            elif event["type"] == "done":
                await self.repository.append_messages(
                    conversation,
                    self._user_turn(content, selection),
                    {"role": "assistant", "content": full_reply},
                    ready_to_submit=event["ready_to_submit"],
                    draft_preview=event.get("preview"),
                )
            yield event

    async def submit_diagnosis(self, conversation: Conversation) -> dict[str, Any]:
        """Finalizes the butter_mind session into a structured, schema-validated
        extraction. The conversation may still be unclaimed (account_id=None)
        at this point -- see docs/plano-onboarding-conversa-primeiro.md: the
        account is resolved *after* this, in
        conversations/router.py::draft_quote, via
        AccountService.get_or_create_from_extraction using the contact fields
        this extraction includes.

        No vanished-session self-heal here (unlike send_message): submitting
        against a freshly reprovisioned, empty butter_mind session would extract
        a meaningless quote. If the session is gone at this point a 502 is the
        safer failure -- by the time submit is reachable, prior turns on the
        healed session have already rebuilt the history.
        """
        request = await self.mind_client.submit_diagnosis(cast(str, conversation.mind_session_id))
        await self.repository.mark_completed(conversation)
        return request

    async def link_account(self, conversation: Conversation, account_id: uuid.UUID) -> Conversation:
        return await self.repository.link_account(conversation, account_id)
