"""Router-level proof for contract-07's two blocking findings this phase
closes: F06-02 (anti-replay/concurrency) and F06-03 (ownership).

These hit the real HTTP endpoints (`POST /payment-methods/session`,
`POST /payment-methods`) through the app, not just the repository/service
units, so they exercise auth, ownership lookup, the atomic
unused->processing->consumed transition and the generic error contract
together.
"""

import asyncio
import base64
import logging
import os
import re
import time
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.models import Account, Company, User
from app.core.clients.asaas_client import AsaasClient, get_asaas_client
from app.core.secure_payload import encrypt_payload
from app.invoices.models import Invoice
from app.main import app
from app.orders.models import Order
from app.settings.config import settings
from tests.conftest import auth_headers, signup

# F06-06 redaction proof (docs/prompts/payment-tokenization/runs/
# 10-redaction-proof.md): shared helpers used by every "does not leak"
# assertion below, so all of them check the *same* log surface the same way.
_NON_APP_LOGGER_PREFIXES = ("aiosqlite",)


def _app_log_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Records from the application's own loggers, excluding the aiosqlite
    driver's DEBUG-level SQL parameter echo (a dev-only driver capability,
    not something app code chooses to log -- see the note on
    test_card_data_and_provider_token_never_appear_in_response_or_logs)."""

    return [
        record
        for record in caplog.records
        if not record.name.startswith(_NON_APP_LOGGER_PREFIXES)
    ]


def _formatted_log_text(records: list[logging.LogRecord]) -> str:
    """Full formatted text of each record, including any traceback attached
    via exc_info -- this is what a real handler (console/file/aggregator)
    would actually emit, not just the unformatted message."""

    formatter = logging.Formatter()
    return "\n".join(formatter.format(record) for record in records)


def _assert_secret_absent(secret: str, text: str, *, label: str) -> None:
    """Asserts `secret` never appears in `text` as a genuine standalone
    value -- not as an accidental substring of an unrelated random hex/UUID
    id. Every request in these tests logs several random UUIDs (account id,
    company id, context id, request id, primary keys...); a short digits-only
    value like a 3-digit CVV has a non-trivial chance of turning up as a pure
    coincidence inside the dozens of hex characters a single test run
    produces (this is exactly the flake the pre-existing redaction test's
    own comment already describes for aiosqlite's SQL echo -- excluding that
    logger isn't sufficient on its own, since `app.request`'s own per-request
    line also carries a random UUID).

    A hex-digit character immediately before or after the match means it is
    embedded inside a longer hex/UUID token, not a standalone secret --
    excluded. A genuine leak (the app literally printing `"ccv": "318"` or
    similar) is always bordered by JSON/log punctuation -- quotes, colons,
    spaces, brackets -- never a hex digit, so this narrows false positives
    without weakening the real assertion."""

    pattern = re.compile(r"(?<![0-9a-fA-F])" + re.escape(secret) + r"(?![0-9a-fA-F])")
    match = pattern.search(text)
    if match is not None:
        context = text[max(0, match.start() - 40) : match.end() + 40]
        raise AssertionError(f"{label} leaked: {secret!r} found near: {context!r}")

_KID = "info-web-test-01"

# Sandbox-shaped test card -- never a real card, values chosen to satisfy
# CardPayload's schema only. The real field names (holderName/number/
# expiryMonth/expiryYear/ccv, name/email/cpfCnpj/postalCode/addressNumber)
# were confirmed against the live Asaas sandbox for this phase (see
# docs/prompts/payment-tokenization/runs/09-asaas-adapter-implementation.md).
_TEST_CARD_PLAINTEXT = {
    "creditCard": {
        "holderName": "Sandbox Test Buyer",
        "number": "5162306219378829",
        "expiryMonth": "05",
        "expiryYear": "2029",
        "ccv": "318",
    },
    "creditCardHolderInfo": {
        "name": "Sandbox Test Buyer",
        "email": "sandbox.buyer@example.com",
        "cpfCnpj": "24971563792",
        "postalCode": "01310-100",
        "addressNumber": "277",
        "phone": "4738010919",
    },
}


class _StubAsaasClient:
    """Router tests stub the mTLS Asaas call rather than hitting the network
    (which would require certs and a live sandbox) -- returns a fixed but
    realistic tokenize response shaped exactly like the real sandbox call
    made for this phase, so `PaymentMethodService`'s real field-mapping code
    runs unmodified."""

    def __init__(self) -> None:
        self.tokenize_calls: list[dict] = []
        self.pay_calls: list[tuple[str, str]] = []

    async def create_customer(self, payload: dict) -> dict:
        return {"id": "cus_stub000000001"}

    async def tokenize_payment_method(self, payload: dict) -> dict:
        self.tokenize_calls.append(payload)
        return {
            "creditCardNumber": "8829",
            "creditCardBrand": "MASTERCARD",
            "creditCardToken": "stub-provider-token-930c4691",
        }

    async def pay_with_saved_payment_method(self, asaas_payment_id: str, token: str) -> dict:
        self.pay_calls.append((asaas_payment_id, token))
        return {"status": "CONFIRMED"}


class _FailingTokenizeAsaasClient(_StubAsaasClient):
    """Simulates Asaas/asaas_payment being unreachable during tokenize."""

    async def tokenize_payment_method(self, payload: dict) -> dict:
        from app.core.exceptions import UpstreamServiceError

        raise UpstreamServiceError(
            "asaas_payment returned 502 for POST /creditCard/tokenizeCreditCard"
        )


@pytest.fixture(autouse=True)
def _payment_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[bytes]:
    key = os.urandom(32)
    encoded_key = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
    monkeypatch.setattr(settings, "payment_payload_keyring", f"{_KID}:{encoded_key}")
    monkeypatch.setattr(
        settings, "payment_token_encryption_key", Fernet.generate_key().decode()
    )
    stub = _StubAsaasClient()
    app.dependency_overrides[get_asaas_client] = lambda: stub
    yield key
    app.dependency_overrides.pop(get_asaas_client, None)


@pytest.fixture
def _asaas_stub() -> _StubAsaasClient:
    # Reads back the single stub instance the autouse fixture above wired
    # into app.dependency_overrides for this test.
    return app.dependency_overrides[get_asaas_client]()


async def _create_session(client: AsyncClient, tokens: dict) -> dict:
    response = await client.post(
        "/payment-methods/session",
        json={"purpose": "payment_method.create", "supportedKids": [_KID]},
        headers=auth_headers(tokens),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _nonce_bytes(nonce_b64url: str) -> bytes:
    padding = "=" * (-len(nonce_b64url) % 4)
    return base64.urlsafe_b64decode(nonce_b64url + padding)


def _build_envelope(session: dict, *, key: bytes, plaintext: dict | None = None) -> dict:
    return encrypt_payload(
        plaintext if plaintext is not None else _TEST_CARD_PLAINTEXT,
        key=key,
        kid=session["kid"],
        context_id=session["contextId"],
        purpose=session["purpose"],
        method="POST",
        path="/payment-methods",
        nonce=_nonce_bytes(session["nonce"]),
        request_id=str(uuid4()),
        timestamp=int(time.time()),
    )


@pytest.mark.asyncio
async def test_concurrent_requests_against_same_context_exactly_one_wins(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    tokens = await signup(
        client,
        email="concurrency@example.com",
        name="Concurrency Tester",
        company_name="Concurrency Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)
    headers = auth_headers(tokens)

    responses = await asyncio.gather(
        client.post("/payment-methods", json=envelope, headers=headers),
        client.post("/payment-methods", json=envelope, headers=headers),
    )

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [201, 400]
    loser = next(response for response in responses if response.status_code == 400)
    assert loser.json() == {"error": "invalid_secure_payload"}
    winner = next(response for response in responses if response.status_code == 201)
    assert winner.json()["paymentMethod"]["status"] == "active"


@pytest.mark.asyncio
async def test_context_from_one_tenant_is_rejected_for_another_tenant(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    owner_tokens = await signup(
        client,
        email="owner-tenant@example.com",
        name="Owner",
        company_name="Owner Ltda.",
    )
    other_tokens = await signup(
        client,
        email="other-tenant@example.com",
        name="Other",
        company_name="Other Ltda.",
    )

    session = await _create_session(client, owner_tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    # The rightful owner's own AAD/nonce/contextId are used to build the
    # envelope (that part of the crypto contract is unaffected), but the
    # *authenticated session* making the POST /payment-methods call belongs
    # to a different tenant. Ownership must be checked against the
    # authenticated request, not anything carried in the envelope.
    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(other_tokens)
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_secure_payload"}


@pytest.mark.asyncio
async def test_rightful_owner_can_still_use_their_own_context(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    tokens = await signup(
        client,
        email="rightful-owner@example.com",
        name="Rightful Owner",
        company_name="Rightful Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert UUID(body["paymentMethod"]["id"])
    assert body["payment"] is None
    # Real fields from the (stubbed) Asaas tokenize response -- proves the
    # placeholder ("pending"/"0000") is gone: brand/last4 come from Asaas's
    # response, not a hardcoded stub value.
    assert body["paymentMethod"]["brand"] == "mastercard"
    assert body["paymentMethod"]["last4"] == "8829"
    assert body["paymentMethod"]["expiryMonth"] == 5
    assert body["paymentMethod"]["expiryYear"] == 2029


@pytest.mark.asyncio
async def test_tokenize_call_receives_real_card_fields_and_client_ip(
    client: AsyncClient, _payment_settings: bytes, _asaas_stub: _StubAsaasClient
) -> None:
    """Proves the real Asaas field mapping (customer/creditCard/
    creditCardHolderInfo/remoteIp) confirmed against the live sandbox for
    this phase, not the Phase-2 placeholder -- inspects the exact payload
    `PaymentMethodService` sent to `AsaasClient.tokenize_payment_method`."""

    tokens = await signup(
        client,
        email="fields@example.com",
        name="Fields Tester",
        company_name="Fields Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )
    assert response.status_code == 201, response.text

    assert len(_asaas_stub.tokenize_calls) == 1
    sent = _asaas_stub.tokenize_calls[0]
    assert sent["customer"] == "cus_stub000000001"
    assert sent["creditCard"] == {
        "holderName": "Sandbox Test Buyer",
        "number": "5162306219378829",
        "expiryMonth": "05",
        "expiryYear": "2029",
        "ccv": "318",
    }
    assert sent["creditCardHolderInfo"]["cpfCnpj"] == "24971563792"
    assert sent["creditCardHolderInfo"]["postalCode"] == "01310-100"
    # httpx's ASGITransport in tests reports the client host as "testclient"
    # (no real TCP peer) -- the important proof is that *some* remoteIp
    # sourced from the request reached the payload, not a hardcoded value.
    assert isinstance(sent["remoteIp"], str) and sent["remoteIp"]


@pytest.mark.asyncio
async def test_malformed_card_plaintext_is_generic_invalid_secure_payload(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    """A plaintext that doesn't match CardPayload's schema (missing
    creditCard/creditCardHolderInfo) must fail exactly like a crypto/replay
    failure (contract-07 section 5.3) -- never a distinct validation error
    that could hint at what was wrong with the card data."""

    tokens = await signup(
        client,
        email="malformed-card@example.com",
        name="Malformed Card Tester",
        company_name="Malformed Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings, plaintext={"not": "a card"})

    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_secure_payload"}


@pytest.mark.asyncio
async def test_asaas_tokenize_failure_returns_payment_provider_unavailable(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    """When asaas_payment/Asaas itself is unreachable during tokenize, the
    caller gets the contract-07 section 5.3 domain error
    (502 payment_provider_unavailable), not a generic 500 or a leak of the
    upstream error detail."""

    app.dependency_overrides[get_asaas_client] = lambda: _FailingTokenizeAsaasClient()
    tokens = await signup(
        client,
        email="provider-down@example.com",
        name="Provider Down Tester",
        company_name="Provider Down Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )

    assert response.status_code == 502
    assert response.json() == {"error": "payment_provider_unavailable"}


@pytest.mark.asyncio
async def test_card_data_and_provider_token_never_appear_in_response_or_logs(
    client: AsyncClient, _payment_settings: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """F06-06 (redaction proof, this phase's item 8): drives one real
    success request through the app with logging captured, then asserts the
    raw card number, ccv, and the real provider token never appear anywhere
    in the HTTP response body or in anything logged during the request."""

    tokens = await signup(
        client,
        email="redaction@example.com",
        name="Redaction Tester",
        company_name="Redaction Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    with caplog.at_level(logging.DEBUG):
        # Clear anything captured by this fixture's handler before this
        # point (e.g. residual records from this same test's earlier setup
        # calls) so the assertion below is scoped to exactly this request.
        caplog.clear()
        response = await client.post(
            "/payment-methods", json=envelope, headers=auth_headers(tokens)
        )

    assert response.status_code == 201, response.text
    forbidden = [
        _TEST_CARD_PLAINTEXT["creditCard"]["number"],
        _TEST_CARD_PLAINTEXT["creditCard"]["ccv"],
        "stub-provider-token-930c4691",
    ]
    response_text = response.text
    # Scoped to the app's own logging surface (what F06-06 is actually
    # about -- log_call, exception handlers, safe_metadata) rather than the
    # sqlite3/aiosqlite driver's own DEBUG-level SQL parameter echo, which
    # is a dev-only driver capability (only active because this test suite
    # runs with LOG_LEVEL=DEBUG), not something the app chooses to log, and
    # is not part of the real observability pipeline (APM/structured logs)
    # this finding is about. It has produced flaky false positives here: a
    # 3-digit ccv value can randomly appear as a substring inside an
    # unrelated random UUID it echoes for a different SQL parameter.
    log_text = _formatted_log_text(_app_log_records(caplog))
    for secret in forbidden:
        _assert_secret_absent(secret, response_text, label="response body")
        _assert_secret_absent(secret, log_text, label="log output")


@pytest.mark.asyncio
async def test_replay_of_already_consumed_context_leaks_nothing_in_logs_or_response(
    client: AsyncClient, _payment_settings: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """F06-06 item 1/2 (fatia 10): a *failed* request (replay of an
    already-consumed context, contract-07 section 5.3) still has the
    original ciphertext/envelope on hand -- proves the failure path is just
    as clean as the success path, and that the response matches the frozen
    error contract byte for byte (never a distinct message that could hint
    the context was reused rather than, say, forged)."""

    tokens = await signup(
        client,
        email="replay-redaction@example.com",
        name="Replay Redaction Tester",
        company_name="Replay Redaction Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)
    headers = auth_headers(tokens)

    first = await client.post("/payment-methods", json=envelope, headers=headers)
    assert first.status_code == 201, first.text

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        replay = await client.post("/payment-methods", json=envelope, headers=headers)

    assert replay.status_code == 400
    assert replay.json() == {"error": "invalid_secure_payload"}

    forbidden = [
        _TEST_CARD_PLAINTEXT["creditCard"]["number"],
        _TEST_CARD_PLAINTEXT["creditCard"]["ccv"],
        envelope["ciphertext"],
        envelope["tag"],
        session["contextId"],
    ]
    log_text = _formatted_log_text(_app_log_records(caplog))
    for secret in forbidden:
        _assert_secret_absent(secret, replay.text, label="response body")
        _assert_secret_absent(secret, log_text, label="log output")


@pytest.mark.asyncio
async def test_asaas_provider_error_leaks_nothing_in_logs_or_response(
    client: AsyncClient, _payment_settings: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """F06-06 item 1/2 (fatia 10), stronger than the replay case above: here
    the envelope *does* decrypt successfully -- the real plaintext card
    number/ccv/holder info exist in memory inside
    `PaymentMethodService._tokenize_and_create_payment_method` -- and only
    then does the (stubbed) Asaas call fail. Proves that a live plaintext
    value in scope at failure time still never reaches the response or any
    log line, and that the response matches contract-07 section 5.3's
    `payment_provider_unavailable` contract exactly (no upstream detail)."""

    app.dependency_overrides[get_asaas_client] = lambda: _FailingTokenizeAsaasClient()
    tokens = await signup(
        client,
        email="provider-down-redaction@example.com",
        name="Provider Down Redaction Tester",
        company_name="Provider Down Redaction Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        response = await client.post(
            "/payment-methods", json=envelope, headers=auth_headers(tokens)
        )

    assert response.status_code == 502
    assert response.json() == {"error": "payment_provider_unavailable"}

    forbidden = [
        _TEST_CARD_PLAINTEXT["creditCard"]["number"],
        _TEST_CARD_PLAINTEXT["creditCard"]["ccv"],
        _TEST_CARD_PLAINTEXT["creditCardHolderInfo"]["cpfCnpj"],
    ]
    log_text = _formatted_log_text(_app_log_records(caplog))
    for secret in forbidden:
        _assert_secret_absent(secret, response.text, label="response body")
        _assert_secret_absent(secret, log_text, label="log output")


@pytest.mark.asyncio
async def test_unhandled_bug_after_decrypt_leaks_nothing_in_response_or_logs(
    client: AsyncClient, _payment_settings: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    """F06-06 item 3 (fatia 10): simulates a genuine, *undefined* downstream
    bug (not one of the app's own domain exceptions) raised from inside
    `AsaasClient.tokenize_payment_method` at the exact point where the full
    plaintext `payload` dict (card number, ccv, holder CPF/address) is a
    live local variable in the caller's frame
    (`PaymentMethodService._tokenize_and_create_payment_method`).

    Uses a raw `ASGITransport(raise_app_exceptions=False)` -- matching how a
    real ASGI server (uvicorn) actually serves traffic -- instead of the
    `client` fixture's default transport, which re-raises unhandled
    exceptions into the test coroutine rather than returning the response
    Starlette's `ServerErrorMiddleware` sends. This is the one path this
    phase found that is NOT covered by a registered exception handler
    (`app/core/exceptions.py`), so it is the right vehicle to prove the
    framework's own default exception handling -- not app code -- doesn't
    dump request body or local variables into the response or into
    `app.request`'s `logger.exception(...)` call in
    `app/settings/middleware.py`."""

    class _BuggyAsaasClient(_StubAsaasClient):
        async def tokenize_payment_method(self, payload: dict) -> dict:
            # A bug unrelated to the UpstreamServiceError contract (e.g. a
            # KeyError/AttributeError in real code) -- deliberately *not*
            # one of the exception types app/core/exceptions.py registers a
            # handler for, so this exercises the framework's default 500
            # path rather than a contract-07 domain error.
            raise RuntimeError("simulated unexpected bug")

    app.dependency_overrides[get_asaas_client] = lambda: _BuggyAsaasClient()
    tokens = await signup(
        client,
        email="unhandled-bug@example.com",
        name="Unhandled Bug Tester",
        company_name="Unhandled Bug Ltda.",
    )
    session = await _create_session(client, tokens)
    envelope = _build_envelope(session, key=_payment_settings)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as raw_client:
        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            response = await raw_client.post(
                "/payment-methods", json=envelope, headers=auth_headers(tokens)
            )

    assert response.status_code == 500

    forbidden = [
        _TEST_CARD_PLAINTEXT["creditCard"]["number"],
        _TEST_CARD_PLAINTEXT["creditCard"]["ccv"],
        _TEST_CARD_PLAINTEXT["creditCardHolderInfo"]["cpfCnpj"],
        _TEST_CARD_PLAINTEXT["creditCardHolderInfo"]["postalCode"],
    ]
    log_text = _formatted_log_text(_app_log_records(caplog))
    # This request's own failure line ("request %s failed: ...") from
    # app/settings/middleware.py's RequestContextMiddleware must actually be
    # present -- otherwise this test would trivially "pass" by capturing
    # nothing at all, proving nothing about redaction.
    assert "failed" in log_text
    for secret in forbidden:
        _assert_secret_absent(secret, response.text, label="response body")
        _assert_secret_absent(secret, log_text, label="log output")


@pytest.mark.asyncio
async def test_malformed_non_numeric_expiry_is_generic_invalid_secure_payload(
    client: AsyncClient, _payment_settings: bytes
) -> None:
    """Real bug found during the F06-06 redaction audit (fatia 10, see
    app/payment_methods/schemas.py CardInput.expiry_month/expiry_year):
    before this phase, expiryMonth/expiryYear only had length constraints,
    not a digits-only pattern, so a well-typed but non-numeric value (e.g.
    "ab") passed CardPayload validation and only blew up later at
    `int(card.expiry_month)` in
    `PaymentMethodService._tokenize_and_create_payment_method` -- *outside*
    the try/except that maps schema failures to the generic
    `invalid_secure_payload` response, reaching an unhandled 500 instead.
    Fixed by adding a digits-only `pattern` to those fields; this test
    proves malformed expiry now fails exactly like any other malformed card
    field (contract-07 section 5.3), never a distinct error and never an
    unhandled exception."""

    tokens = await signup(
        client,
        email="malformed-expiry@example.com",
        name="Malformed Expiry Tester",
        company_name="Malformed Expiry Ltda.",
    )
    session = await _create_session(client, tokens)
    bad_plaintext = {
        "creditCard": {**_TEST_CARD_PLAINTEXT["creditCard"], "expiryMonth": "ab"},
        "creditCardHolderInfo": _TEST_CARD_PLAINTEXT["creditCardHolderInfo"],
    }
    envelope = _build_envelope(session, key=_payment_settings, plaintext=bad_plaintext)

    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_secure_payload"}


def test_log_call_decorator_is_not_applied_to_functions_carrying_plaintext_card_data() -> None:
    """F06-06 item 4 (fatia 10): `log_call` (app/core/decorators.py) only
    logs the qualified function name, never args/kwargs -- but it still
    logs the *exception* (with traceback) via `log.exception` on failure,
    so it must never wrap a function whose exception could embed plaintext
    (or whose repr a future change might start logging). This mechanically
    verifies every `AsaasClient` call site that receives plaintext card
    data or the raw provider token -- `tokenize_payment_method` and
    `pay_with_saved_payment_method` -- is not wrapped, by checking for the
    `__wrapped__` attribute `functools.wraps` sets, while a genuinely
    decorated sibling method (`create_customer`, which never receives card
    data) *is* wrapped -- proving this isn't just an absent import."""

    assert hasattr(AsaasClient.create_customer, "__wrapped__"), (
        "sanity check: create_customer is expected to carry @log_call"
    )
    assert not hasattr(AsaasClient.tokenize_payment_method, "__wrapped__"), (
        "tokenize_payment_method receives plaintext card data/ccv -- must not carry @log_call"
    )
    assert not hasattr(AsaasClient.pay_with_saved_payment_method, "__wrapped__"), (
        "pay_with_saved_payment_method receives the raw provider token -- must not carry @log_call"
    )


@pytest.mark.asyncio
async def test_create_and_pay_tokenizes_and_charges_the_bound_invoice(
    client: AsyncClient,
    db_session: AsyncSession,
    _payment_settings: bytes,
    _asaas_stub: _StubAsaasClient,
) -> None:
    """`purpose=payment_method.create_and_pay` end to end: tokenizes the
    real (stubbed) card, then reuses the ledger's own `invoice_id` (never a
    client-supplied one) to charge it via `pay_with_saved_payment_method`
    against `invoice.asaas_invoice_id` -- the same call this phase proved
    works against the live Asaas sandbox (tokenize + payWithCreditCard,
    see docs/prompts/payment-tokenization/runs/
    09-asaas-adapter-implementation.md)."""

    tokens = await signup(
        client,
        email="create-and-pay@example.com",
        name="Create And Pay Tester",
        company_name="Create And Pay Ltda.",
    )
    user = (
        await db_session.execute(select(User).where(User.email == "create-and-pay@example.com"))
    ).scalar_one()
    company = (
        await db_session.execute(select(Company).where(Company.user_id == user.id))
    ).scalar_one()
    account = (
        await db_session.execute(select(Account).where(Account.company_id == company.id))
    ).scalar_one()

    order = Order(quote_id=uuid4(), account_id=account.id)
    db_session.add(order)
    await db_session.flush()
    invoice = Invoice(
        order_id=order.id,
        asaas_invoice_id="pay_sandboxtest0001",
        amount=Decimal("25.90"),
        currency="BRL",
        due_date=date(2026, 8, 30),
    )
    db_session.add(invoice)
    await db_session.commit()
    await db_session.refresh(invoice)

    session_response = await client.post(
        "/payment-methods/session",
        json={
            "purpose": "payment_method.create_and_pay",
            "supportedKids": [_KID],
            "invoiceId": str(invoice.id),
        },
        headers=auth_headers(tokens),
    )
    assert session_response.status_code == 201, session_response.text
    session = session_response.json()
    assert session["purpose"] == "payment_method.create_and_pay"

    envelope = _build_envelope(session, key=_payment_settings)
    response = await client.post(
        "/payment-methods", json=envelope, headers=auth_headers(tokens)
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["payment"] is not None
    assert body["payment"]["state"] == "succeeded"
    assert body["payment"]["providerPaymentId"] == "pay_sandboxtest0001"

    assert len(_asaas_stub.tokenize_calls) == 1
    assert _asaas_stub.pay_calls == [
        ("pay_sandboxtest0001", "stub-provider-token-930c4691")
    ]
