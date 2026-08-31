"""Entry point for the proactive credential-rotation job --
docs/prompts/asaas-payment-integration/03-prompt-devbutter-relay-e-rotacao.md,
entregavel 3. Not run by anything in this repo automatically: no scheduler
(APScheduler or similar) is a dependency here today, so this is meant to be
invoked by an external scheduler (cron, a Kubernetes CronJob, ECS scheduled
task) hitting `poetry run python scripts/run_proactive_rotation.py` on
whatever cadence ops decides -- adding an in-process scheduler was judged
out of scope for this slice (see the execution log appended to
03-prompt-devbutter-relay-e-rotacao.md).

Every rotation attempt this run makes is independently safe to repeat: a
link that comes back `rotation_blocked` (asaas_payment/Asaas call failed --
commonly the D.1 2-hour management window being closed, see
00-levantamento-tecnico.md) or `blocked` (asaas_payment didn't return an
apiKey) is left exactly as it was before, and will be tried again on the
next run.

Run manually: poetry run python scripts/run_proactive_rotation.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.accounts.repository import AsaasSubaccountLinkRepository  # noqa: E402
from app.core.clients.asaas_client import close_asaas_client, get_asaas_client  # noqa: E402
from app.db.session import session_factory  # noqa: E402
from app.integrations.relay_service import SelolibertariaRelayService  # noqa: E402
from app.integrations.rotation_service import CredentialRotationService  # noqa: E402
from app.integrations.selolibertaria_client import (  # noqa: E402
    close_selolibertaria_client,
    get_selolibertaria_client,
)
from app.settings.config import settings  # noqa: E402
from app.settings.logging_config import setup_logging  # noqa: E402

logger = logging.getLogger("app.integrations.rotation_job")


async def main() -> int:
    setup_logging(settings.log_level)
    asaas_client = get_asaas_client()
    relay_client = get_selolibertaria_client()
    exit_code = 0
    try:
        async with session_factory() as session:
            link_repository = AsaasSubaccountLinkRepository(session)
            relay_service = SelolibertariaRelayService(relay_client, link_repository)
            rotation_service = CredentialRotationService(
                asaas_client, link_repository, relay_service
            )
            touched = await rotation_service.run_proactive_sweep()

        blocked = [
            link
            for link in touched
            if link.selolibertaria_relay_status in ("blocked", "rotation_blocked")
        ]
        # Deliberately no api_key/link contents in this log line -- only
        # counts and the subaccount id, which is not a credential.
        logger.info(
            "proactive rotation sweep: %s link(s) touched, %s blocked",
            len(touched),
            len(blocked),
        )
        for link in blocked:
            logger.warning(
                "proactive rotation blocked for asaas_account_id=%s status=%s error=%s",
                link.asaas_account_id,
                link.selolibertaria_relay_status,
                link.selolibertaria_relay_last_error,
            )
        if blocked:
            # Non-zero exit so an external scheduler (cron/CronJob) surfaces
            # this as a failed run -- ops treats a blocked rotation as an
            # alert, not a silent no-op (see this slice's Restricoes explicitas).
            exit_code = 1
    finally:
        await close_asaas_client()
        await close_selolibertaria_client()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
