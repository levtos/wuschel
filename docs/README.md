# Dokumentation — Übersicht

## Design im Review

| Dokument | Freigabestand |
|---|---|
| [beta/bereich-5/A1.md](beta/bereich-5/A1.md) | Medienwahrheiten für #75: A1-Vorschlag, Laufzeitproben und offene Reviewpunkte; Claude-Review und Benni-Go vor A2 |
| [beta/bereich-4/A1.md](beta/bereich-4/A1.md) | Schlaf und Weckplanung für #61: A1-Vorschlag mit synthetischen Ist-Proben; Review und Benni-Go vor A2 |
| [adr/0032-ordered-machine-commands.md](adr/0032-ordered-machine-commands.md) | Vorgeschlagen: geordnete Übergänge, belegte Ereignisse und Command-Fristreset |
| [adr/0033-wake-planning-staged-evaluation.md](adr/0033-wake-planning-staged-evaluation.md) | Vorgeschlagen: Weckplan-Port, lokale Auswertungsphasen und strukturierte Parameter |

## Aktuell (verbindlich)

| Dokument | Zweck |
|---|---|
| [alpha8/README.md](alpha8/README.md) | Wuschel Alpha 8: freigegebener Umfang, ADRs und Review-Gates |
| [alpha8/ABNAHME.md](alpha8/ABNAHME.md) | Block-Commits, Tests, CI und Firefox-Nachweise |
| [alpha8/platform.md](alpha8/platform.md) | Datenformate und Verhalten der Alpha-8-Plattform |
| [alpha8/retention-scaling.md](alpha8/retention-scaling.md) | Aufbewahrung, Verdichtung, Kapazitätsbudget und Restkosten |
| [naming.md](naming.md) | Wuschel, Traber und getrennte Producer-/Policy-Rollen |
| [alpha4/workbench-api.md](alpha4/workbench-api.md) | Alpha-4-Implementierung: deklarative Definitionen und Manual-first-APIs, noch keine Releasefreigabe |
| [workbench/README.md](workbench/README.md) | Terra Alpha 7: Module, Aufgabenrouten, Editoren, Diagnose und Nachweise |
| [workbench/alpha7/ABNAHME.md](workbench/alpha7/ABNAHME.md) | Vollständige P1–P13-/A.11-/Top-15-Matrix mit Tests und Bildnachweisen |
| [releases/1.0.0a7.md](releases/1.0.0a7.md) | Vorbereiteter Alpha-7-Umfang, Upgrade und Freigabegrenzen |
| [adr/0004-manual-machine-restart.md](adr/0004-manual-machine-restart.md) | Angenommener begrenzter administrativer Ablauf-Neustart |
| [workbench/review-resolution.md](workbench/review-resolution.md) | Phase 1B: Korrektur der Review-Befunde T1B-01…07/UX-01 mit Testnachweis |
| [adr/0003-declarative-platform.md](adr/0003-declarative-platform.md) | Architekturvertrag, Restore, Kompatibilität und Sicherheitsgrenzen für Alpha 4 |
| [alpha4/completion.md](alpha4/completion.md) | Ist-Soll-Matrix, A1–A16, automatisierte Nachweise und verbleibende Live-Gates |
| [platform-alpha1/build-specification.md](platform-alpha1/build-specification.md) | Platform Alpha 1 (Phase 1): Lastenheft, Acceptance Gate, Selbstprüfung |
| [platform-alpha1/codex-build-prompt.md](platform-alpha1/codex-build-prompt.md) | Bauauftrag für Codex, ausschließlich Phase 1 |
| [platform-alpha1/delta-remediation.md](platform-alpha1/delta-remediation.md) | Issue #3: Pflichtkorrekturen DR-01–03, Betriebsdoku DR-05/06, Tests und verbleibende Gates |
| [publishing.md](publishing.md) | Issue #5: explizite GHCR-Veröffentlichung, Supervisor-Packaging und anonymer Distributionsnachweis |
| [audits/technical-baseline-check-2026-10-07.md](audits/technical-baseline-check-2026-10-07.md) | `TECHNICAL BASELINE CONFIRMED`, HA-I/O-Entscheidung |
| [audits/profile-delta-audit-2026-10-07.md](audits/profile-delta-audit-2026-10-07.md) | `REMOVE CORE PROFILES FOR V1`, D1–D3 |

## Historie / superseded

- `Levtos/core-contracts/docs/alpha1/build-specification.md` und `codex-build-prompt.md`: **SUPERSEDED**,
  weil sie Phase 1 und Phase 2 vermischt haben. Ihr DOCUMENTATION DELTA (DD-1 bis DD-8: Profile entfernt,
  `installation_id`, D1–D3, Baseline, No-Shadow, UX-Scope) gilt inhaltlich weiter.
- `Levtos/core-contracts#46`: falsch gescopter Bauauftrag, geschlossen.
- Legacy-Doku der Integration v0.2.x: `Levtos/core-contracts/docs/`.

## Nicht im Repository

Die fachliche Entscheidungsakte (Phase 4 · Auditrevision, Stand P4-65), ihre Nebenakten und die
Original-Gespräche enthalten private Haushaltsdaten. Sie bleiben lokales Archiv des Owners und sind
fachliche Rückfallquelle.
