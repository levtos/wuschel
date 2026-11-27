"""Explicit administrative route admissions. Machine endpoints stay local."""

import re

READ_ROUTES = {
    "GET": frozenset(
        {
            "catalogs/entries",
            "catalogs/care",
            "catalogs/care/queue",
            "catalogs/care/detail",
            "catalogs/care/inspect",
            "catalogs/care/export",
            "catalogs/classes/rollback",
            "catalogs/classes/outputs",
            "diagnostics/problems",
            "title-sync/mapping",
            "title-sync/requests",
        }
    ),
    "POST": frozenset(
        {
            "portable/export",
            "portable/preview",
            "title-import/preview",
            "catalogs/classes/migration",
            "catalogs/preview",
            "catalogs/care/preview",
            "catalogs/scope/preview",
            "registry/freshness",
            "registry/freshness/proposals",
            "registry/validate",
            "history/retention/preview",
        }
    ),
}

# These are separate from the read-only worker set because their lifecycle
# includes an artifact change or a truth commit.
PLAN_ROUTES = frozenset(
    {
        "portable/stage",
        "title-import/stage",
        "title-sync/preview",
        "title-sync/page",
        "title-sync/diff",
        "title-sync/apply",
        "title-sync/cancel",
        "catalogs/entries",
        "catalogs/care/write",
        "catalogs/scope/apply",
        "catalogs/cleanup/preview",
        "catalogs/cleanup/apply",
        "history/retention/run",
    }
)
HISTORY_KIND = "contracts|observations|transitions|episodes|deadlines|commands|gaps|diagnostics|revisions|activations"
_HISTORY_QUERY = re.compile(r"history/query/(?:" + HISTORY_KIND + r")")
_DRAFT_PREVIEW = re.compile(r"registry/drafts/[^/]+/(validate|activation-preview)")


def read_route(method: str, path: str) -> bool:
    if path in READ_ROUTES.get(method, ()):
        return True
    if method == "GET":
        return (
            path == "history/rollups"
            or _HISTORY_QUERY.fullmatch(path) is not None
            or re.fullmatch(r"history/[^/]+", path) is not None
        )
    return method == "POST" and _DRAFT_PREVIEW.fullmatch(path) is not None


def is_long(method: str, path: str) -> bool:
    return method == "POST" and not (path == "registry/validate" or _DRAFT_PREVIEW.fullmatch(path))
