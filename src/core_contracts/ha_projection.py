"""Only explicitly selected, already published values cross the Traber boundary."""

from typing import Any


def snapshot(runtime: Any) -> dict[str, Any]:
    config = runtime.config
    contracts = []
    if config:
        from .registry import registry_types

        types = registry_types(config, runtime.types)
        for contract in config.contracts:
            if not contract.enabled or not contract.ha_projection:
                continue
            envelope = runtime.state.tables["contract_state_current"].get(contract.contract_id, {})
            field = envelope.get("fields", {}).get("value", {})
            schema = types.get(contract.type_id, contract.type_version).schema.fields["value"]
            contracts.append(
                {
                    "contract_id": contract.contract_id,
                    "name": contract.display_name or contract.contract_id,
                    "value": field.get("value")
                    if field.get("status") in {"valid", "held"} and runtime.confirmed
                    else None,
                    "status": field.get("status", "unknown") if runtime.confirmed else "unknown",
                    "quality": field.get("quality", "degraded")
                    if runtime.confirmed
                    else "degraded",
                    "since_at": field.get("since_at"),
                    "unit": schema.unit,
                }
            )
    return {
        "type": "traber/project_contracts",
        "installation_id": runtime.installation_id,
        "epoch_id": runtime.state.epoch_id,
        "publication_seq": runtime.state.publication_seq,
        "contracts": contracts,
    }
