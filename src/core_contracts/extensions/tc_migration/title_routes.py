"""Temporary TC migration HTTP routes."""

from typing import Any

from aiohttp import web

from core_contracts import slots
from core_contracts.model import checksum


async def restore_plan(runtime: Any, actor: Any, key: Any) -> None:
    if isinstance(key, str) and key not in runtime.title_sync_plans:
        plan = await runtime.store.work_plan("title_sync", key, checksum(actor))
        if plan:
            from core_contracts.extensions.tc_migration.title_sync_runs import position

            if progress := plan.pop("_work_progress", None):
                plan["mapping_version"] = progress["mapping_version"]
                plan["_heads"] = progress["heads"]
                plan["_completed"] = progress["complete"]
                if progress["complete"]:
                    plan["_index"] = progress["applied_page"]
                    plan["page"] = progress["applied_page"] + 1
                    plan["plan_digest"] = progress["page_digest"]
                    for field in ("_snapshot", "_pages", "groups"):
                        plan.pop(field, None)
                    plan["can_apply"] = False
                else:
                    plan["_index"] = progress["applied_page"] + 1
                    position(plan)
            runtime.title_sync_plans[key] = plan


async def dispatch(self: Any, request: web.Request) -> Any:
    path, method = request.path.removeprefix("/api/v1/"), request.method
    runtime, state = self.runtime, self.runtime.state
    result: Any
    if path == "title-sync/mapping" and method == "GET":
        from core_contracts.extensions.tc_migration.title_sync import current_mapping

        result = current_mapping(state)
    elif path == "title-sync/preview" and method == "POST":
        from core_contracts.extensions.tc_migration.title_sync import public_plan

        body = await self.object_body(request)
        if set(body) - {
            "expected_active_revision",
            "mapping",
            "expected_mapping_version",
            "plan_id",
            "run_id",
        }:
            raise ValueError("title_sync_mapping_invalid")
        if self.title_reader is None:
            raise ValueError("title_sync_not_configured")
        await restore_plan(runtime, request["actor"], body.get("plan_id"))
        result = await runtime.submit(
            "title_sync_preview",
            {**body, "_reader": self.title_reader, "_actor": request["actor"]},
        )
        result = public_plan(result)
    elif path == "title-sync/page" and method == "GET":
        from core_contracts.extensions.tc_migration.title_sync_runs import page_view

        key = request.query["plan_id"]
        await restore_plan(runtime, request["actor"], key)
        result = page_view(runtime, request["actor"], key)
    elif path == "title-sync/cancel" and method == "POST":
        from core_contracts.extensions.tc_migration.title_sync_runs import cancel

        body = await self.object_body(request)
        if set(body) != {"plan_id"}:
            raise ValueError("title_sync_plan_conflict")
        key = body["plan_id"]
        result = cancel(runtime, request["actor"], key)
        work = getattr(self, "work", None)
        if work is not None and isinstance(key, str):
            await work.cancel_plan(request, "title_sync", key)
    elif path == "title-sync/diff" and method == "GET":
        from core_contracts.extensions.tc_migration.title_sync import diff_page

        key = request.query["plan_id"]
        await restore_plan(runtime, request["actor"], key)
        result = diff_page(runtime, request["actor"], key, request.query.get("after", ""))
    elif path == "title-sync/apply" and method == "POST":
        body = await self.object_body(request)
        if set(body) != {"plan_id", "plan_digest", "request_id"}:
            raise ValueError("title_sync_plan_conflict")
        await restore_plan(runtime, request["actor"], body["plan_id"])
        result = await runtime.submit("title_sync_apply", {**body, "_actor": request["actor"]})
    elif path == "title-sync/requests" and method == "GET":
        result = await runtime.store.get("catalog_request", request.query["request_id"])
        if result is None:
            raise web.HTTPNotFound()
        result = result["result"]
    elif path in {"title-import/preview", "title-import/stage"} and method == "POST":
        from core_contracts.portable import strict_json

        # The nested v3 JSON string can double in size when JSON-escaped.
        # Only this bounded import envelope needs more than the normal 50 MiB;
        # the decoded source and generated package remain capped at 50 MiB.
        body = strict_json(
            await request.clone(client_max_size=102 * 1024 * 1024).text(),
            max_bytes=102 * 1024 * 1024,
        )
        if not isinstance(body, dict):
            raise ValueError("import_object_required")
        result = await runtime.submit(
            "title_" + path.rsplit("/", 1)[1], {**body, "_actor": request["actor"]}
        )
    else:
        raise web.HTTPNotFound()
    return result


def register(app: web.Application, api: Any) -> None:
    from functools import partial

    handler = partial(api.extension_dispatch, handler=dispatch)
    for path in [
        "title-sync/mapping",
        "title-sync/preview",
        "title-sync/page",
        "title-sync/cancel",
        "title-sync/diff",
        "title-sync/apply",
        "title-sync/requests",
        "title-import/preview",
        "title-import/stage",
    ]:
        app.router.add_route("*", "/api/v1/" + path, handler)


slots.ROUTES.append(register)
