from __future__ import annotations

import httpx
import pytest

from app.core.clients.asaas_client import AsaasClient
from app.core.exceptions import UpstreamServiceError
from app.settings.config import settings


@pytest.fixture
def asaas_client(monkeypatch) -> AsaasClient:
    # Real certs aren't available outside the docker-compose network -- the
    # local `certs/` directory (used by ASAAS_*_PATH in .env.local for
    # dev-against-localhost) is enough for httpx.AsyncClient's constructor
    # to accept the paths; no request in this file leaves the process.
    monkeypatch.setattr(settings, "asaas_client_cert_path", "certs/devbutter-platform.crt")
    monkeypatch.setattr(settings, "asaas_client_key_path", "certs/devbutter-platform.key")
    monkeypatch.setattr(settings, "asaas_ca_cert_path", "certs/ca.crt")
    return AsaasClient()


class _Recorder:
    """Stands in for httpx.AsyncClient.request: records each call and
    replays the next queued response.
    """

    def __init__(self, responses: list[httpx.Response]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def __call__(self, method, url, *, params=None, json=None):
        self.calls.append({"method": method, "path": url, "params": params, "json": json})
        return self.responses.pop(0)


def _response(status_code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=body if body is not None else {},
        request=httpx.Request("GET", "https://asaas-nginx:8443/subaccounts"),
    )


def _empty_response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("DELETE", "https://x/y"))


@pytest.mark.parametrize(
    ("call", "expected_method", "expected_path", "expected_params", "expected_json"),
    [
        (
            lambda c: c.list_subaccounts({"email": "a@b.com"}),
            "GET",
            "/subaccounts",
            {"email": "a@b.com"},
            None,
        ),
        (lambda c: c.get_subaccount("acc_1"), "GET", "/subaccounts/acc_1", None, None),
        (
            lambda c: c.create_subaccount({"name": "Loja"}),
            "POST",
            "/subaccounts",
            None,
            {"name": "Loja"},
        ),
        (lambda c: c.close_subaccount("acc_1"), "DELETE", "/subaccounts/acc_1", None, None),
        (
            lambda c: c.resend_activation_link("acc_1"),
            "POST",
            "/subaccounts/acc_1/resend-activation-link",
            None,
            None,
        ),
        (
            lambda c: c.list_access_tokens("acc_1"),
            "GET",
            "/subaccounts/acc_1/access-tokens",
            None,
            None,
        ),
        (
            lambda c: c.create_access_token("acc_1", {"name": "tok"}),
            "POST",
            "/subaccounts/acc_1/access-tokens",
            None,
            {"name": "tok"},
        ),
        (
            lambda c: c.revoke_access_token("acc_1", "tok_1"),
            "DELETE",
            "/subaccounts/acc_1/access-tokens/tok_1",
            None,
            None,
        ),
        (
            lambda c: c.create_split_rule("acc_1", {"walletId": "w1"}),
            "POST",
            "/subaccounts/acc_1/split-rules",
            None,
            {"walletId": "w1"},
        ),
        (
            lambda c: c.update_split_rule("acc_1", "rule_1", {"active": False}),
            "PATCH",
            "/subaccounts/acc_1/split-rules/rule_1",
            None,
            {"active": False},
        ),
        (
            lambda c: c.list_payments("acc_1", {"status": "PENDING"}),
            "GET",
            "/subaccounts/acc_1/payments",
            {"status": "PENDING"},
            None,
        ),
        (
            lambda c: c.get_payment("acc_1", "pay_1"),
            "GET",
            "/subaccounts/acc_1/payments/pay_1",
            None,
            None,
        ),
        (
            lambda c: c.refund_payment("acc_1", "pay_1", {"value": 10}),
            "POST",
            "/subaccounts/acc_1/payments/pay_1/refund",
            None,
            {"value": 10},
        ),
        (
            lambda c: c.cancel_payment("acc_1", "pay_1"),
            "DELETE",
            "/subaccounts/acc_1/payments/pay_1",
            None,
            None,
        ),
    ],
)
async def test_client_method_issues_expected_request(
    asaas_client, monkeypatch, call, expected_method, expected_path, expected_params, expected_json
):
    recorder = _Recorder([_response(200, {"ok": True})])
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await call(asaas_client)

    assert result == {"ok": True}
    assert recorder.calls[0]["method"] == expected_method
    assert recorder.calls[0]["path"] == expected_path
    assert recorder.calls[0]["params"] == expected_params
    assert recorder.calls[0]["json"] == expected_json


async def test_list_split_rules_returns_bare_json_array(asaas_client, monkeypatch):
    # Unlike list_subaccounts/list_payments (Asaas's own {data: [...]}
    # envelope), this hits asaas_payment's own /split-rules resource, whose
    # body is a bare JSON array.
    body = [{"id": "rule_1", "walletId": "w1", "type": "PERCENTAGE", "value": "10.00"}]
    recorder = _Recorder(
        [
            httpx.Response(
                200, json=body, request=httpx.Request("GET", "https://x/subaccounts/acc_1")
            )
        ]
    )
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await asaas_client.list_split_rules("acc_1")

    assert result == body
    assert recorder.calls[0]["path"] == "/subaccounts/acc_1/split-rules"


async def test_get_audit_logs_returns_bare_json_array(asaas_client, monkeypatch):
    body = [{"id": "log_1", "client_id": "store-a"}]
    recorder = _Recorder(
        [httpx.Response(200, json=body, request=httpx.Request("GET", "https://x/audit-logs"))]
    )
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await asaas_client.get_audit_logs({"client_id": "store-a"})

    assert result == body
    assert recorder.calls[0]["params"] == {"client_id": "store-a"}


async def test_list_split_rules_returns_empty_list_for_empty_response(asaas_client, monkeypatch):
    recorder = _Recorder([_empty_response(204)])
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await asaas_client.list_split_rules("acc_1")

    assert result == []


async def test_request_retries_once_on_502_then_succeeds(asaas_client, monkeypatch):
    recorder = _Recorder(
        [_response(502, {"detail": "bad gateway"}), _response(200, {"id": "acc_1"})]
    )
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await asaas_client.get_subaccount("acc_1")

    assert result == {"id": "acc_1"}
    assert len(recorder.calls) == 2


async def test_request_raises_after_exhausting_retries_on_502(asaas_client, monkeypatch):
    recorder = _Recorder([_response(502, {}), _response(502, {})])
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    with pytest.raises(UpstreamServiceError):
        await asaas_client.get_subaccount("acc_1")

    assert len(recorder.calls) == 2


async def test_request_raises_on_other_4xx_without_retry(asaas_client, monkeypatch):
    recorder = _Recorder([_response(404, {"detail": "not found"})])
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    with pytest.raises(UpstreamServiceError):
        await asaas_client.get_subaccount("acc_1")

    assert len(recorder.calls) == 1


async def test_request_handles_empty_response_body(asaas_client, monkeypatch):
    recorder = _Recorder([_empty_response(204)])
    monkeypatch.setattr(asaas_client._client, "request", recorder)

    result = await asaas_client.cancel_payment("acc_1", "pay_1")

    assert result == {}
