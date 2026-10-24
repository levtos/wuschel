# Wuschel

Follow the binding [working rules for agents](https://github.com/Levtos/control/blob/main/docs/workflow/working-rules.md),
the [Codex bootstrap](https://github.com/Levtos/control/blob/main/docs/workflow/codex-bootstrap.md)
and ADR 0002. The assignment is the GitHub Issue named by Benni; it states size and scope.

Repository-specific:

- Project baseline: `docs/uebergabe.md` (roles, vocabulary, Beta plan, security rules §10).
- Backend, code and identifiers in English; user interface in German (Du-Form, vocabulary §3).
- Never commit real media titles, private data, MQTT addresses of the live system, or secrets.
- Never call tools that return the Wuschel app options (they contain secrets).
- The legacy integration `benni_core_contracts` is not touched.
- Before pushing any change to `custom_components/traber/` or `tests/test_bridge_kernel.py`, run the HA-kernel test locally with the exact Python/package versions from the `ha-bridge` CI job. It must pass before push; on Windows use the venv's `Scripts/python.exe` equivalent.
