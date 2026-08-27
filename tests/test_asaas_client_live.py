"""Live smoke test for N-02 (docs/prompts/payment-tokenization/runs/
11-final-validation.md): asaas_payment/scripts/generate_dev_certs.sh used to
generate the nginx server cert with no `subjectAltName` extension at all
(bare `CN=localhost`). curl (via macOS's LibreSSL/SecureTransport) tolerates
a bare CN as a legacy fallback, so every manual dev check with curl looked
fine -- but Python's `ssl` module (used by httpx, used by the real
`AsaasClient`) follows RFC 6125 strictly and rejected every real call with
`CERTIFICATE_VERIFY_FAILED: Hostname mismatch`. Every test in this suite
that touches `AsaasClient` stubs it out via
`app.dependency_overrides[get_asaas_client]` (see test_payment_method_router.py,
test_admin_payments.py, etc.) -- correct for unit tests, but it means no
automated test ever actually opened a TLS connection to asaas_payment, so
none of them could have caught this. This file is the one place that does.

Deliberately NOT wired into the default fast/unit test loop's assumptions:
it needs a real asaas_payment stack (`docker compose up -d` in
asaas_payment/, dev certs generated via
`asaas_payment/scripts/generate_dev_certs.sh`, and matching
ASAAS_CLIENT_CERT_PATH/ASAAS_CLIENT_KEY_PATH/ASAAS_CA_CERT_PATH pointing at
`devbutter_backend/certs/`). It self-skips via a cheap TCP reachability
probe when that isn't available (e.g. a CI job without Docker), so
`poetry run pytest` never fails just because the stack isn't up -- but it
runs, and would have caught N-02, whenever the stack is.

Run explicitly against a live stack with:
    poetry run pytest tests/test_asaas_client_live.py -v
"""
from __future__ import annotations

import socket
from urllib.parse import urlparse

import pytest

from app.core.clients.asaas_client import AsaasClient
from app.settings.config import settings


def _asaas_payment_reachable() -> bool:
    """TCP-probes asaas_payment_base_url's host:port with a short timeout.
    Deliberately just a TCP connect, not a TLS handshake: a host that isn't
    even listening should skip silently (stack not up), while a host that
    *is* listening but fails the TLS handshake (e.g. N-02 itself) must reach
    the real test below and fail loudly instead of being swallowed here.
    """
    parsed = urlparse(settings.asaas_payment_base_url)
    if not parsed.hostname or not parsed.port:
        return False
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _asaas_payment_reachable(),
    reason=(
        f"asaas_payment not reachable at {settings.asaas_payment_base_url} -- "
        "start it with `docker compose up -d` in asaas_payment/ (see its README) "
        "to run this live smoke test"
    ),
)


@pytest.fixture
async def live_asaas_client():
    # No dependency_overrides here -- this is the real class, hitting the
    # real network, over the real cert files at settings.asaas_client_*_path.
    client = AsaasClient()
    try:
        yield client
    finally:
        await client.aclose()


async def test_real_asaas_client_reaches_asaas_payment_over_mtls(
    live_asaas_client: AsaasClient,
) -> None:
    """Regression test for N-02. Before the fix, this failed with:

        httpx.ConnectError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate
        verify failed: Hostname mismatch, certificate is not valid for
        'localhost'. (_ssl.c:1010)

    even though `curl --cacert certs/ca.crt --cert ... https://localhost:8443/
    customers` against the exact same running nginx succeeded -- proving the
    bug was specific to strict-RFC-6125 TLS clients (Python's `ssl`, and
    therefore every real call this application ever makes), not to
    asaas_payment itself. If this test starts failing with the error above
    again, the dev cert has lost its `subjectAltName` -- regenerate it with
    `asaas_payment/scripts/generate_dev_certs.sh` and re-copy the shared
    ca.crt/devbutter-platform.crt/.key into devbutter_backend/certs/.
    """
    customer = await live_asaas_client.create_customer(
        {
            "name": "CI live-smoke test customer (N-02 regression guard)",
            "cpfCnpj": "24971563792",
            "email": "n02-live-smoke-test@example.com",
        }
    )
    assert customer.get("id", "").startswith("cus_")
