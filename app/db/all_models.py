"""Imported by alembic/env.py so Base.metadata sees every table before autogenerate.

Adding a new domain's `models` module here is required -- a domain added
without updating this import list is silently invisible to
`alembic revision --autogenerate`.
"""

from app.accounts import models as accounts_models  # noqa: F401
from app.conversations import models as conversations_models  # noqa: F401
from app.invoices import models as invoices_models  # noqa: F401
from app.orders import models as orders_models  # noqa: F401
from app.quotes import models as quotes_models  # noqa: F401
from app.service_catalog import models as service_catalog_models  # noqa: F401
