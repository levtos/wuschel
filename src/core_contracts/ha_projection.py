"""Only explicitly selected, already published values cross the Traber boundary."""

from typing import Any


def primary_field(schema: Any) -> str | None:
    scalar = {"bool", "number", "text", "enum", "timestamp"}
    return next(
        (
            name
            for name in dict.fromkeys(["value", *schema.available_projection, *schema.fields])
            if name in schema.fields and schema.fields[name].type in scalar
        ),
        None,
    )


def snapshot(runtime: Any, *, include_fields: bool = True) -> dict[str, Any]:
    config = runtime.config
    contracts = []
    if config:
        from .registry import registry_types

        types = registry_types(config, runtime.types)
        for contract in config.contracts:
            if not contract.enabled or not contract.ha_projection:
                continue
            envelope = runtime.state.tables["contract_state_current"].get(contract.contract_id, {})
            schema = types.get(contract.type_id, contract.type_version).schema
            primary = primary_field(schema)
            if primary is None:
                continue
            projected = {
                name: {
                    "value": field.get("value")
                    if runtime.confirmed and field.get("status") in {"valid", "held"}
                    else None,
                    "status": field.get("status", "unknown") if runtime.confirmed else "unknown",
                    "quality": field.get("quality", "degraded")
                    if runtime.confirmed
                    else "degraded",
                    "since_at": field.get("since_at"),
                    "unit": declaration.unit,
                }
                for name, declaration in schema.fields.items()
                for field in [envelope.get("fields", {}).get(name, {})]
            }
            field = projected[primary]
            contracts.append(
                {
                    "contract_id": contract.contract_id,
                    "name": contract.display_name or contract.contract_id,
                    **field,
                    **({"primary_field": primary, "fields": projected} if include_fields else {}),
                }
            )
    return {
        "type": "traber/project_contracts",
        "installation_id": runtime.installation_id,
        "epoch_id": runtime.state.epoch_id,
        "publication_seq": runtime.state.publication_seq,
        "contracts": contracts,
    }
