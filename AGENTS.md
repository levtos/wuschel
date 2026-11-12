# Wuschel

Follow the binding [working rules for agents](https://github.com/Levtos/control/blob/main/docs/workflow/working-rules.md),
the [Codex bootstrap](https://github.com/Levtos/control/blob/main/docs/workflow/codex-bootstrap.md)
and ADR 0002. The assignment is the GitHub Issue named by Benni; it states size and scope.

At the beginning of each new agent session, load the current canonical working
rules and the active Issue contract. For resumed work, verify and use the last
checkpoint instead of rereading entire historical threads. Apply the central
context, focused-test, CI-waiting and scope rules; this file adds only Wuschel-
specific safeguards. If the central rules cannot be accessed, do not assume an
outdated cached copy is current; report the limitation before implementing.

Repository-specific:

- CI retries restart only failed jobs (`gh run rerun <id> --failed`); locally rerun
  only affected tests. Full release checks and release batching follow the canonical working rules.
- Project baseline: `docs/uebergabe.md` (roles, vocabulary, Beta plan, security rules §10).
- Backend, code and identifiers in English; user interface in German (Du-Form, vocabulary §3).
- Never commit real media titles, private data, MQTT addresses of the live system, or secrets.
- Never call tools that return the Wuschel app options (they contain secrets).
- The legacy integration `benni_core_contracts` is not touched.
- Before pushing any change to `custom_components/traber/` or `tests/test_bridge_kernel.py`, run the HA-kernel test locally with the exact Python/package versions from the `ha-bridge` CI job. It must pass before push; on Windows use the venv's `Scripts/python.exe` equivalent.

## Grenzen

Die Paketzuordnung und geprüften Ausnahmen stehen in
`docs/architecture/b25/grenzen.md`: Ein Paket darf nur weiter unten stehende
Pakete importieren. Kein Kernpaket importiert eine Kartusche; nur `app` und
`tooling` dürfen das. Kartuschen dürfen `kernel`, `store`, `rules` und
`management` importieren, nicht `lane`, `adapters` oder `api`. Kartuschen kennen
einander nicht; nur `tc_migration` darf `catalog` benutzen. Die Ausnahmeliste
darf nur kürzer werden. Prüfen mit `uv run lint-imports`.
