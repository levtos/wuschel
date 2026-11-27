"""Catalog HTTP routes registered at the application boundary."""

import asyncio
from typing import Any

from aiohttp import web

from core_contracts import slots
from core_contracts.extensions.catalog.catalog_care import CatalogReadCache


async def dispatch(self: Any, request: web.Request, *, cache: CatalogReadCache) -> Any:
    path, method = request.path.removeprefix("/api/v1/"), request.method
    runtime, state, config = self.runtime, self.runtime.state, self.runtime.config
    result: Any
    if path == "catalogs" and method == "GET":
        from core_contracts.extensions.catalog.catalog_scope import device_scope
        from core_contracts.rules.catalog_models import ManagedCatalog

        result = (
            [
                {
                    **c.model_dump(mode="json"),
                    "device_scope": device_scope(config, c.catalog_id, c.version),
                    "active_entry_count": len(await cache.rows(state, c)),
                    **(
                        {
                            "class_definitions": [
                                item.model_dump(mode="json") for item in c.assigned_classes.values()
                            ]
                        }
                        if c.class_assignments
                        else {}
                    ),
                }
                for c in config.catalogs
                if isinstance(c, ManagedCatalog)
            ]
            if config
            else []
        )
    elif path == "catalogs/classes/rollback" and method == "GET":
        from core_contracts.extensions.catalog.catalog_class_rollback import preview

        result = preview(state, config)
    elif path == "catalogs/classes/outputs" and method == "GET":
        from core_contracts.extensions.catalog.named_class_migration import output_options

        result = await asyncio.to_thread(output_options, config) if config else []
    elif path == "catalogs/classes/migration" and method == "POST":
        from core_contracts.extensions.catalog.named_class_migration import (
            build_package,
            configure_package,
            migration_blockers,
            projection_changes,
        )
        from core_contracts.portable import objects

        body = await self.object_body(request)
        baseline = await runtime.submit(
            "portable_export", {"expected_active_revision": body.get("expected_active_revision")}
        )
        if "catalog_classes" not in body and (
            blockers := await asyncio.to_thread(migration_blockers, baseline)
        ):
            return {
                "package": {},
                "resolutions": {},
                "blockers": blockers,
                "preview": {
                    "plan_digest": "",
                    "can_stage": False,
                    "unresolved": ["catalog_migration_unmapped"],
                },
            }
        package = (
            await asyncio.to_thread(
                configure_package,
                baseline,
                body["catalog_classes"],
                body.get("assignments", {}),
                body.get("outputs"),
            )
            if "catalog_classes" in body
            else await asyncio.to_thread(build_package, baseline)
        )
        resolutions = {key: "file" for key in objects(package["config"])}
        plan = await runtime.submit(
            "portable_preview", {"package": package, "resolutions": resolutions}
        )
        result = {
            "package": package,
            "resolutions": resolutions,
            "preview": plan,
            "projections": await asyncio.to_thread(
                projection_changes, runtime, baseline["config"], plan["config"]
            )
            if plan.get("can_stage") is not False and not plan["unresolved"]
            else [],
        }
    elif (
        path in {"catalogs/care", "catalogs/care/queue", "catalogs/care/detail"} and method == "GET"
    ):
        from core_contracts.extensions.catalog.catalog_care import detail, listing, queue
        from core_contracts.extensions.catalog.managed_catalog import definition

        filters = {
            key: request.query.get(key, "")
            for key in (
                "scope",
                "media_type",
                "signal_type",
                "context",
                "structure",
                "app",
                "live_tv",
                "sender",
                "classification",
            )
        }
        for key in ("class_key", "class_value"):
            if request.query.get(key):
                filters[key] = request.query[key]
        if path == "catalogs/care" and request.query.get("catalog_id") == "all":
            from core_contracts.extensions.catalog.catalog_views import catalogs_for, page_all
            from core_contracts.extensions.catalog.managed_catalog import head_key

            classification_catalog = request.query.get("classification_catalog", "")
            rows_by_catalog = {
                head_key(catalog): await cache.rows(state, catalog)
                for catalog in catalogs_for(config, "all", 1)
                if not classification_catalog
                or classification_catalog == f"{catalog.catalog_id}@{catalog.version}"
            }
            return page_all(
                state,
                config,
                query=request.query.get("q", ""),
                view=request.query.get("view", "all"),
                after=request.query.get("after", ""),
                filters=filters,
                sort=request.query.get("sort", "newest"),
                variants_first=request.query.get("variants_first") == "true",
                classification_catalog=classification_catalog,
                rows_by_catalog=rows_by_catalog,
            )
        catalog = definition(
            config, request.query["catalog_id"], int(request.query["definition_version"])
        )
        rows = await cache.rows(state, catalog)
        if path.endswith("detail"):
            result = detail(
                state,
                catalog,
                request.query["identity"],
                request.query.get("after", ""),
                query=request.query.get("q", ""),
                view=request.query.get("view", "all"),
                filters=filters,
                rows=rows,
            )
        elif path.endswith("queue"):
            result = queue(
                state,
                catalog,
                request.query.get("q", ""),
                request.query.get("view", "all"),
                filters,
                rows=rows,
            )
        else:
            result = listing(
                state,
                catalog,
                query=request.query.get("q", ""),
                view=request.query.get("view", "all"),
                after=request.query.get("after", ""),
                filters=filters,
                sort=request.query.get("sort", "newest"),
                variants_first=request.query.get("variants_first") == "true",
                grouped=request.query.get("grouped") == "true",
                rows=rows,
            )
    elif path in {"catalogs/care/inspect", "catalogs/care/export"} and method == "GET":
        from core_contracts.extensions.catalog.catalog_views import (
            export_structure,
            inspect_structure,
        )

        catalog_id = request.query.get("catalog_id", "all")
        version = int(request.query.get("definition_version", "1"))
        if path.endswith("export"):
            result = {"csv": export_structure(state, config, catalog_id, version)}
        else:
            offset = int(request.query.get("offset", "0"))
            if offset < 0:
                raise ValueError("catalog_page_invalid")
            result = inspect_structure(state, config, catalog_id, version, offset)
    elif path in {"catalogs/care/preview", "catalogs/care/write"} and method == "POST":
        result = await runtime.submit(
            "catalog_care_" + path.rsplit("/", 1)[1],
            {"request": await self.object_body(request), "_actor": request["actor"]},
        )
    elif path in {"catalogs/scope/preview", "catalogs/scope/apply"} and method == "POST":
        result = await runtime.submit(
            "catalog_scope_" + path.rsplit("/", 1)[1],
            {**await self.object_body(request), "_actor": request["actor"]},
        )
    elif path in {"catalogs/cleanup/preview", "catalogs/cleanup/apply"} and method == "POST":
        from core_contracts.model import checksum

        body = await self.object_body(request)
        key = body.get("plan_id")
        if path.endswith("/apply") and isinstance(key, str):
            plan = await runtime.store.work_plan("catalog_cleanup", key, checksum(request["actor"]))
            if plan:
                runtime.catalog_cleanup_plans[key] = plan
        result = await runtime.submit(
            "catalog_cleanup_" + path.rsplit("/", 1)[1], {**body, "_actor": request["actor"]}
        )
    elif path == "catalogs/entries" and method == "GET":
        from core_contracts.extensions.catalog.managed_catalog import definition, page

        catalog = definition(
            config, request.query["catalog_id"], int(request.query["definition_version"])
        )
        result = page(
            state,
            catalog,
            query=request.query.get("q", ""),
            view=request.query.get("view", "all"),
            after=request.query.get("after", ""),
            limit=int(request.query.get("limit", "100")),
            filters={
                key: request.query.get(key, "") for key in ("scope", "media_type", "signal_type")
            },
        )
    elif path == "catalogs/entries" and method == "POST":
        result = await runtime.submit(
            "catalog_write",
            {"request": await self.object_body(request), "_actor": request["actor"]},
        )
    elif path == "catalogs/preview" and method == "POST":
        result = await runtime.submit(
            "catalog_preview",
            {"request": await self.object_body(request), "_actor": request["actor"]},
        )
    elif path == "catalogs/entry" and method == "GET":
        from core_contracts.extensions.catalog.managed_catalog import definition, entry_view

        catalog = definition(
            config, request.query["catalog_id"], int(request.query["definition_version"])
        )
        result = entry_view(state, catalog, request.query["identity"])
    elif path == "catalogs/history" and method == "GET":
        from core_contracts.extensions.catalog.managed_catalog import definition, head, head_key

        catalog = definition(
            config, request.query["catalog_id"], int(request.query["definition_version"])
        )
        revision = head(state, catalog)["revision"]
        before = min(int(request.query.get("after") or revision + 1), revision + 1)
        rows = await runtime.store.catalog_revisions(head_key(catalog), before)
        result = [
            {**row, "head_revision": revision, "active_revision": state.active_revision}
            for row in rows
        ]
    elif path == "catalogs/revision" and method == "GET":
        result = await runtime.submit(
            "catalog_revision",
            {
                "catalog_id": request.query["catalog_id"],
                "definition_version": int(request.query["definition_version"]),
                "revision": int(request.query["revision"]),
                "after": request.query.get("after", ""),
            },
        )
    else:
        raise web.HTTPNotFound()
    return result


def register(app: web.Application, api: Any) -> None:
    from functools import partial

    handler = partial(api.extension_dispatch, handler=partial(dispatch, cache=CatalogReadCache()))
    for path in [
        "catalogs",
        "catalogs/care",
        "catalogs/care/queue",
        "catalogs/classes/migration",
        "catalogs/classes/rollback",
        "catalogs/classes/outputs",
        "catalogs/care/inspect",
        "catalogs/care/export",
        "catalogs/care/detail",
        "catalogs/care/preview",
        "catalogs/care/write",
        "catalogs/scope/preview",
        "catalogs/scope/apply",
        "catalogs/cleanup/preview",
        "catalogs/cleanup/apply",
        "catalogs/entries",
        "catalogs/preview",
        "catalogs/entry",
        "catalogs/history",
        "catalogs/revision",
    ]:
        app.router.add_route("*", "/api/v1/" + path, handler)


slots.ROUTES.append(register)
