# GHCR-Veröffentlichung und Supervisor-Packaging

Ab b14 wird ausschließlich **amd64 / linux/amd64** gebaut und angeboten:
`ghcr.io/levtos/amd64-wuschel:VERSION`. Maßgeblich ist `wuschel/config.yaml`.
Bereits veröffentlichte ARM-Images bleiben als historische Artefakte erhalten;
sie werden nicht gelöscht. Auftrag: [#68](https://github.com/levtos/wuschel/issues/68).

## Betroffene Prüfungen und vollständiges Release-Gate

`dev/ci_scope.py` vergleicht bei PRs die Basis mit dem getesteten Merge-Commit,
bei Pushes den bisherigen mit dem neuen Commit. Unbekannte Pfade und Änderungen
an Versionsdateien, Build-Regeln oder Workflows erzwingen den vollständigen Satz.
Jeder Release-PR enthält eine Versionsänderung und muss vor dem Merge alle neun
Jobs bestehen. Der Auswahlplan steht im Job `scope` und im Quellbeleg.

| Änderung | Betroffene Anwendungsprüfungen |
|---|---|
| Markdown-Dokumentation, AGENTS/CLAUDE | keine Backend-/Frontend-/Image-Jobs |
| Backend | Backend, Workbench-E2E, Image |
| Frontend | Frontend, Workbench-E2E, Image; bei öffentlichen Assets auch Herdenprüfung |
| Traber | HA-Kernel-Prüfung |
| Python-Client | Backend, HA-Kernel, Workbench-E2E |
| Backendtests | Backend |
| Release-/Build-Regeln, unbekannte Pfade | vollständiger Satz |

Nicht betroffene Jobs erhalten GitHubs erfolgreichen Überspringstatus. `CI result`
lehnt fehlgeschlagene oder abgebrochene Jobs ab. Ein partieller Lauf ist **kein**
Veröffentlichungsnachweis: `dev/release_ci.py` verlangt im Quellbeleg `full=true`,
alle Pflichtjobs erfolgreich, ausschließlich das amd64-Image und den identischen
tatsächlich getesteten Git-Baum.

Bei Wiederholung nur roter Jobs darf der unveränderte Quellbeleg aus einem
früheren Versuch desselben Runs stammen. Run-ID, zulässige Versuchsnummer,
vollständiger Plan, aktuelle Ergebnisse aller Jobs und exakte Quellenbindung
bleiben geprüft. Grüne Jobs müssen nicht erneut ausgeführt werden.

Nach Verfügbarkeit des Workflows auf Main erlaubt der folgende gezielte Lauf
einen Dokumentationsnachweis. `BASE` muss ein echter Vorfahr sein, dessen Diff
zum gewählten Commit ausschließlich Dokumentation enthält:

```sh
gh workflow run ci.yml --ref main -f full=false -f base_sha=BASE
```

`full=true` fordert den vollständigen Satz an. Ohne passende Basis wird
konservativ vollständig geprüft. Repository-Einstellungen und Pflichtprüfungen
werden für die Pfadauswahl nicht verändert.

## Docker-Stufen wiederverwenden

BuildKit verwendet `type=gha,scope=wuschel-amd64,mode=max`. Dieser Cache speichert
die getrennten Stufen `frontend`, Python-`build` und das Laufzeitimage samt
Zwischenlagen. Eine reine Backendänderung verändert weder Frontend-Pakete noch
dessen Quellkopie und Build. Versions-/Quelllabels liegen erst im Laufzeitimage.
Ein fester `SOURCE_DATE_EPOCH=0` verhindert wechselnde Exportzeitpunkte bei
identischen Eingaben.

Bei Änderungen an CI-/Dockerfile-Bauweise läuft einmal der Cache-Nachweis: ein
zweiter Build mit exakt gleichen Argumenten importiert den zuvor exportierten
Stufencache. Seine Image-ID muss identisch sein. Beide Bauten bleiben
`push: false`; Herkunfts-, Inhalts- und Lifecycle-Prüfungen bleiben bestehen.
Schrittdauern und Cache-Treffer gehören in den Bericht. Ein späterer gezielter
Nachweis verwendet `full=false`, eine gültige Basis und `cache_probe=true`.

Die Veröffentlichung baut überhaupt nicht. Sie übernimmt die geprüfte Image-TAR
aus der vollständigen grünen CI, ohne neuen Frontend-/Python-Build.

Quellen: [Docker-GHA-Cache](https://docs.docker.com/build/cache/backends/gha/),
[Cache-Invalidierung und feste Zeitstempel](https://docs.docker.com/build/cache/invalidation/).

## Release und Store-Angebot

1. Python und Frontend tragen den Kandidaten. `wuschel/PENDING_RELEASE.json`
   nennt Kandidat und bisherige Store-Version; `config.yaml` bleibt bis zur
   geprüften Veröffentlichung auf der bisherigen Version.
2. Fokussierte lokale Checks, vollständige grüne Release-PR-CI, serverseitiger
   Merge und Vergleich des Main-Baums mit dem getesteten Baum. Vor Veröffentlichung
   und Tag verlangt `dev/verify_postrelease.py --mode release` eine neue Version
   nach [ADR 0009](adr/0009-prerelease-version-gate.md).
3. `publish-images.yml` ausdrücklich auf der vollständigen Main-SHA starten:

   ```sh
   gh workflow run publish-images.yml --ref main -f expected_sha=MAIN_SHA -f ci_run_id=FULL_GREEN_RUN
   ```

4. Der Guard prüft Repository, Workflow, Quelle, vollständigen Plan und aktuelle
   Job-Ergebnisse. Nur der Publish-Job hat `packages: write`. Er übernimmt exakt
   die geprüften amd64-Bytes; ein vorhandenes Versionstag mit anderer Image-ID
   wird niemals ersetzt. Anmeldung erfolgt nur mit dem kurzlebigen Actions-Token.
5. Der unabhängige anonyme Pull prüft Digest, Image-ID, Plattform, Labels,
   Paketversion, Frontend, Migrationen, Entrypoint und Quellhashes. Danach läuft
   der Lifecycle-Smoke. Erst genau drei grüne Jobs (`guard`, `publish (amd64)`,
   `anonymous-pull (amd64)`) erlauben die Store-Promotion.
6. Im sauberen Checkout genau der veröffentlichten Main-SHA:

   ```sh
   uv run python dev/promote_store.py --run-id PUBLISH_RUN
   ```

   Der Diff darf nur `config.yaml` auf den Kandidaten setzen und den erfüllten
   Marker entfernen. Normalerweise folgt ein Promotion-PR. Beim ausdrücklich auf
   **einen Produkt-PR** begrenzten Sammelauftrag #68/#71 folgt stattdessen ein
   gesonderter Zwei-Dateien-Commit auf Main nach grünem Publikationsnachweis.
   Branch-Schutz wird nicht umgangen. Auch dieser Versionscommit verlangt vor
   dem Tag vollständige grüne CI; `dev/store_offer.py` prüft das öffentliche Angebot.
7. Nach strenger Versionsprüfung: Tag auf dem finalen Store-Commit und öffentlicher
   Pre-Release mit PR-/CI-/Image-/Publish-/Store-Belegen. Der tatsächliche
   Image-Quellcommit bleibt im OCI-Label und JSON-Beleg `published-amd64` sichtbar;
   er kann vom späteren Store-/Tag-Commit abweichen.

Vor **jedem** GitHub-Schreiben muss `levtos-codex` unmittelbar bestätigt werden.
Bei Rot ausschließlich fehlgeschlagene Jobs wiederholen; lokal nur betroffene
Tests. Volle Suites laufen ausschließlich in CI. Nicht blockierende Korrekturen
werden nach den [Arbeitsregeln](https://github.com/Levtos/control/blob/main/docs/workflow/working-rules.md)
gebündelt. Installation, Einlesen, Übernahme und Live-Abnahme bleiben Bennis Gate.

Ein Push belegt keine öffentliche Lesbarkeit. Private Packages oder fehlende
Rechte sind Blocker: keine zusätzliche PAT, keine undokumentierte Änderung der
Sichtbarkeit und keine Änderung an Einstellungen oder Secrets als Abkürzung.
Ein alter Build oder authentifizierter Pull ersetzt keinen aktuellen anonymen
Nachweis. Hash-/Label-/Plattformfehler blockieren die Promotion. Höchstens drei
Versuche je fehlgeschlagenem Schritt, danach Stopp mit Analyse im Issue.
