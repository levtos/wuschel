"""Load extension owners in one explicit application composition root."""

from . import (  # noqa: F401
    authoring,
    editorial,
    management,
    manual_restart,
    mqtt_restore,
    portable,
    reconfiguration,
    retention_ops,
    revision_plan,
    service_jobs,
    title_sync,
    title_sync_runs,
    title_transfer,
    validation,
)
from .extensions.catalog import (  # noqa: F401  # noqa: F401
    catalog_cleanup,
    catalog_contributions,
    catalog_operations,
    catalog_playback,
    catalog_profiles,
    catalog_routes,
    catalog_scope,
    managed_catalog,
)
