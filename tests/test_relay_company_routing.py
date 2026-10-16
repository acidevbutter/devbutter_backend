import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from app.accounts.models import Company, User
from app.core.clients.relay_client import TASK_NAME, RelayClient, is_valid_project_id
from app.core.exceptions import UpstreamServiceError
from app.notifications.service import NotificationService
from app.settings.config import settings


class _SpyRelay:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.payloads: list[tuple[str, dict[str, Any]]] = []

    def send(self, template_key, to, payload, project_id=None, queue=None) -> None:
        self.calls.append({"project_id": project_id, "queue": queue, "to": to})
        self.payloads.append((template_key, payload))


def _service(relay: _SpyRelay) -> NotificationService:
    return NotificationService(cast(RelayClient, relay))


def _invoice() -> Any:
    return SimpleNamespace(
        status="PAID", amount=Decimal("10.00"), payment_link="https://pay", due_date=date.today()
    )


def _user() -> User:
    return User(email="dona@ex.com", password_hash="x", name="Dona")


def test_company_with_own_project_routes_through_its_queue():
    relay = _SpyRelay()
    company = Company(
        id=uuid.uuid4(), user_id=uuid.uuid4(), name="Acme",
        relay_project_id="proj-acme", relay_queue="relay-acme",
    )
    _service(relay).notify_payment_status_changed(_user(), _invoice(), company)
    assert relay.calls == [{"project_id": "proj-acme", "queue": "relay-acme", "to": "dona@ex.com"}]


def test_company_without_project_falls_back_to_platform():
    relay = _SpyRelay()
    company = Company(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Acme")
    _service(relay).notify_payment_status_changed(_user(), _invoice(), company)
    _service(relay).notify_payment_status_changed(_user(), _invoice())
    assert all(c["project_id"] is None and c["queue"] is None for c in relay.calls)


def test_notification_payloads_include_template_reference_and_portal_link(monkeypatch):
    relay = _SpyRelay()
    service = _service(relay)
    monkeypatch.setattr(settings, "frontend_base_url", "https://app.devbutter.com")

    service.notify_quote_approved(
        _user(),
        cast(Any, SimpleNamespace(reference="Q-123", scope_summary="Site institucional")),
        _invoice(),
    )
    service.notify_pending_account_link(_user(), {"problem_summary": "Site institucional"})

    approved_key, approved_payload = relay.payloads[0]
    pending_key, pending_payload = relay.payloads[1]
    assert approved_key == "devbutter_quote_approved"
    assert approved_payload["quote_reference"] == "Q-123"
    assert pending_key == "devbutter_pending_account_link"
    assert pending_payload["portal_url"] == "https://app.devbutter.com/portal"


def test_relay_client_uses_override_then_platform_defaults(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    platform_project_id = "13e54b74-10e7-4202-88ec-b4acadb0ecb6"
    override_project_id = "6853bb81-5177-4912-8d70-2fb3859f6d3d"
    monkeypatch.setattr(settings, "relay_project_id", platform_project_id)
    monkeypatch.setattr(settings, "relay_queue_name", "relay-shared")
    client = RelayClient()
    client._celery = MagicMock()
    client.send("t", "a@b.c", {}, project_id=override_project_id, queue="relay-x")
    client.send("t", "a@b.c", {})
    first, second = client._celery.send_task.call_args_list
    assert first.kwargs["queue"] == "relay-x"
    assert first.kwargs["kwargs"]["project_id"] == override_project_id
    assert second.kwargs["queue"] == "relay-shared"
    assert second.kwargs["kwargs"]["project_id"] == platform_project_id
    assert first.args[0] == TASK_NAME


def test_project_id_must_be_a_uuid():
    assert is_valid_project_id("13e54b74-10e7-4202-88ec-b4acadb0ecb6")
    assert not is_valid_project_id(None)
    assert not is_valid_project_id("")
    assert not is_valid_project_id("platform-id")


def test_relay_client_does_not_queue_an_invalid_project_id(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "relay_project_id", "")
    client = RelayClient()
    client._celery = MagicMock()

    with pytest.raises(UpstreamServiceError, match="RELAY_PROJECT_ID"):
        client.send("devbutter_quote_received", "a@example.com", {})

    client._celery.send_task.assert_not_called()
