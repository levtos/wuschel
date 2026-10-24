# Wuschel — Core Contracts

Core Contracts als **Supervisor-verwaltete Home-Assistant-App**: kanonische fachliche Wahrheiten mit
Status, Quality, Freshness, Evidence und Reasons. Dazu kommen Registry mit stabilen IDs, PostgreSQL-Persistenz,
HA-Bridge Traber und `CoreContractsClient`.

**Stand:** Beta. Aktuelle Veröffentlichungen und Prüfnachweise stehen in den [Releases](https://github.com/levtos/wuschel/releases) und den zugehörigen Issues.
Wuschel nutzt den eigenen Supervisor-Slug `wuschel`; Traber ist die Integration
für die HA-Anbindung mit Protokoll 2. G4-Katalogpflege und portable Konfigurations-
pakete ergänzen die bestehenden Verträge. Alle mitgelieferten Beispiele sind synthetisch.
Veröffentlichung und Installation folgen der jeweiligen Freigabe; die fachliche Live-Abnahme bleibt bei Benni.

## Übergabe / aktueller Stand

- [Übergabe an die Beta](docs/uebergabe.md)

## Dokumentation

- [Alpha-10-Entscheidungen](docs/alpha10/README.md), [A2-Abnahme](docs/alpha10/ABNAHME.md)
- [Alpha-8-Auftrag und Entscheidungen](docs/alpha8/README.md), [Abnahme](docs/alpha8/ABNAHME.md)
- [Namenskonzept](docs/naming.md), [neue Wuschel-Datenbank: vorbereitetes Runbook](docs/operations/wuschel-new-database.md)

- [First-Run-Wizard, DB-Provisionierung und offene Bridge-Grenze](docs/first-run.md)
- [Betrieb, Installation und Backup/Restore](docs/operations.md)
- [API und Python-Client](docs/api.md)
- [Wiederverwendungsanalyse](docs/platform-alpha1/reuse-analysis.md)
- [Technische Auslegungen](docs/platform-alpha1/deviations.md)
- [Build-Time Verification](docs/platform-alpha1/build-time-verification.md)
- [Abschlussbericht / G1–G15](docs/platform-alpha1/completion-report.md)

- [Platform-Alpha-1-Build-Spezifikation](docs/platform-alpha1/build-specification.md): **primäre Build-Quelle**
- [Codex-Build-Prompt Phase 1](docs/platform-alpha1/codex-build-prompt.md)
- [Technischer Baseline-Check](docs/audits/technical-baseline-check-2026-10-07.md)
- [Delta-Audit Core-Profile](docs/audits/profile-delta-audit-2026-10-07.md)
- Übersicht und Historie: [docs/README.md](docs/README.md)

Die bisherige HA-Integration (v0.2.x, Legacy) liegt in
[Levtos/core-contracts](https://github.com/Levtos/core-contracts) und bleibt bis zur Ablösung in Phase 2 unverändert.
