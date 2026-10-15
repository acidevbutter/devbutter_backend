import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from app.accounts.models import Company, User
from app.core.clients.relay_client import TASK_NAME, RelayClient
from app.notifications.service import NotificationService
from app.settings.config import settings


class _SpyRelay:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send(self, template_key, to, payload, project_id=None, queue=None) -> None:
        self.calls.append({"project_id": project_id, "queue": queue, "to": to})


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
    NotificationService(relay).notify_payment_status_changed(_user(), _invoice(), company)
    assert relay.calls == [{"project_id": "proj-acme", "queue": "relay-acme", "to": "dona@ex.com"}]


def test_company_without_project_falls_back_to_platform():
    relay = _SpyRelay()
    company = Company(id=uuid.uuid4(), user_id=uuid.uuid4(), name="Acme")
    NotificationService(relay).notify_payment_status_changed(_user(), _invoice(), company)
    NotificationService(relay).notify_payment_status_changed(_user(), _invoice())
    assert all(c["project_id"] is None and c["queue"] is None for c in relay.calls)


def test_relay_client_uses_override_then_platform_defaults(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(settings, "relay_project_id", "platform-id")
    monkeypatch.setattr(settings, "relay_queue_name", "relay-shared")
    client = RelayClient()
    client._celery = MagicMock()
    client.send("t", "a@b.c", {}, project_id="proj-x", queue="relay-x")
    client.send("t", "a@b.c", {})
    first, second = client._celery.send_task.call_args_list
    assert first.kwargs["queue"] == "relay-x"
    assert first.kwargs["kwargs"]["project_id"] == "proj-x"
    assert second.kwargs["queue"] == "relay-shared"
    assert second.kwargs["kwargs"]["project_id"] == "platform-id"
    assert first.args[0] == TASK_NAME
