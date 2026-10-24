# GHCR-Veröffentlichung und Supervisor-Packaging

Aktueller Ablauf ab b5: [Issue #42](https://github.com/levtos/wuschel/issues/42), [Implementierung und Review-Gate](beta/b5-implementation.md).
Ein erfolgreicher Image-Build allein belegt keine Installierbarkeit. Die
Konfiguration `wuschel/config.yaml` verlangt diese öffentlichen Images:

| Supervisor-Architektur | Docker-Plattform | Image |
|---|---|---|
| amd64 | linux/amd64 | ghcr.io/levtos/amd64-wuschel:1.0.0a8 |
| aarch64 | linux/arm64 | ghcr.io/levtos/aarch64-wuschel:1.0.0a8 |

`{arch}` wird mit dem Supervisor-Namen ersetzt; `arm64-wuschel` wäre
für die zweite Zeile falsch. Version und Image-Vorlage kommen aus der
Supervisor-Konfiguration. Bei einem Release-Marker verwenden Build und Python-Paket
dessen Kandidaten; der Store bietet weiterhin nur seine bereits veröffentlichte Version an.

## Explizit veröffentlichen

Die Tabelle oben zeigt historische Alpha-8-Image-Namen. Die aktuelle angebotene Version
steht in `wuschel/config.yaml`. Während einer Release-Vorbereitung enthält
`wuschel/PENDING_RELEASE.json` Kandidat und bisherige Store-Version. Python und Frontend
tragen den Kandidaten; der Store bleibt bis zur erfolgreichen Veröffentlichung auf
der bisherigen Version. Kein Image-Build allein ist eine Distributionsfreigabe.

1. Vor Veröffentlichung und Tag: uv run python dev/verify_postrelease.py ausführen. Gepinntes
   AwesomeVersion prüft alle Tags und deren gespeicherte Supervisor-Slugs gemäß
   [ADR 0009](adr/0009-prerelease-version-gate.md). Erst grünes Gate erlaubt den Tag.
   Code-PR gemäß Review-/Go-Gate prüfen und mit vollständig grüner CI nach main mergen.
2. Die volle main-SHA ermitteln und den Workflow **Publish Supervisor images**
   (`.github/workflows/publish-images.yml`) ausdrücklich auf main starten.
   `expected_sha` muss exakt dem ausgewählten Commit entsprechen. `ci_run_id` nennt
   die vollständig grüne reguläre CI, deren Git-Baum identisch mit main ist.
   Beispiel mit ersetztem SHA-Platzhalter:

   ```sh
   gh api repos/levtos/wuschel/commits/main --jq .sha
   gh workflow run publish-images.yml --repo levtos/wuschel --ref main -f expected_sha=REPLACE_WITH_FULL_MAIN_SHA -f ci_run_id=REPLACE_WITH_GREEN_CI_RUN
   ```

3. Der Workflow prüft den CI-Beleg (Repository, Workflow, Run-Versuch, alle Jobs und
   identischen Git-Baum). Er übernimmt die Image-TARs aus genau diesem grünen Run
   und veröffentlicht ihre geprüften Bytes ohne neue Suite oder Neubau. Die tatsächliche
   getestete Merge-SHA bleibt im OCI-Label; sie kann von der PR-Branch-SHA abweichen.
   Reguläre Push-/PR-CI baut weiterhin mit `push: false`.
4. Nur die Publish-Jobs erhalten `packages: write`; alle übrigen Jobs haben
   nur Lesezugriff auf Inhalt und benötigte Actions-Artefakte. Registry-Anmeldung erfolgt ausschließlich mit
   dem kurzlebigen `GITHUB_TOKEN`. Es gibt keine zusätzlichen PAT-Secrets,
   keinen Git-Tag, keinen GitHub-Release und kein HA-Deployment.
5. Ein vorhandener Versionstag wird nur akzeptiert, wenn seine Image-ID exakt
   dem geprüften Artefakt entspricht. Andernfalls bricht der Workflow ab.
   Für geänderte Images ist eine bewusst neue Version erforderlich; kein
   stilles Überschreiben von `1.0.0a1`.
6. Erst wenn beide anonymen Pull-Jobs einschließlich Inhalts-/Digest-Prüfungen und
   amd64-Lifecycle-Smoke grün sind, in einem sauberen Checkout der veröffentlichten
   main-SHA `uv run python dev/promote_store.py --run-id PUBLISH_RUN` ausführen.
   Der dadurch vorbereitete PR darf nur `wuschel/config.yaml` auf den Kandidaten setzen
   und `wuschel/PENDING_RELEASE.json` entfernen. Volle reguläre CI, kein Überspringen
   von Checks. `dev/store_offer.py` prüft in jeder CI die öffentliche Erreichbarkeit,
   Version, Plattform und Konfigurations-Digest der angebotenen Version für beide Architekturen.
   Nach grünem Merge: strenges Versionsgate, Tag auf diesem Promotion-Commit,
   Pre-Release mit Code-/Image-/Publish-/Promotion-Belegen. Erst jetzt bietet HA die Version an.
7. GitHub-Schreiben erfordert jeweils unmittelbar zuvor die Identität `levtos-codex`.
   Installation folgt nur dem freigegebenen Auftrag. Live-Abnahme bleibt bei Benni.

## Öffentlich lesbar machen

Neue GHCR-Packages sind standardmäßig **privat**, auch bei einem öffentlichen
Quellrepository. Repository-Zugriff und Package-Sichtbarkeit sind getrennt.
Der erste Push kann deshalb erfolgreich sein, während die anschließenden
anonymen Pull-Jobs fehlschlagen.

Ein Package-Admin prüft für **beide** Pakete in GitHub:

1. Organisation **levtos → Packages → amd64-wuschel** beziehungsweise
   **aarch64-wuschel → Package settings**.
2. Unter **Danger Zone → Change visibility → Public** die Sichtbarkeit ändern.
   GitHub erlaubt danach keinen Wechsel zurück auf privat.
3. Falls der Push bereits an Berechtigungen scheitert: unter **Manage Actions
   access** das Repository `levtos/wuschel` mit Write-Zugriff freigeben.
   Die Workflow-Images tragen bereits das passende Source-Label; bei neu
   erzeugten Paketen wird der Workflow automatisch mit dem Repository verbunden.
4. Fehlende Berechtigungen oder Package-Sichtbarkeit als Blocker melden. Die aktuellen
   Arbeitsregeln erlauben dem Agenten keine Repository-Einstellungs-/Secret-Änderungen.
   Für Fehler gelten höchstens drei Versuche mit neuer Korrektur; ein bloßer Neustart
   ist nur bei belegter Runner-/Netzstörung erlaubt. Keine Store-Promotion bei Rot.

Die Sichtbarkeit wird nicht durch einen undokumentierten API-Aufruf geändert.
Fehlt die erforderliche Package-Admin-Berechtigung, bleibt dies ein konkreter
manueller Schritt. **Erst zwei erfolgreiche anonyme Pull-Jobs belegen die
Distributionsfreigabe.** Bis dahin keine erfolgreiche Installierbarkeit behaupten.

Quellen:
[GitHub: Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[Package-Sichtbarkeit und Zugriffsrechte](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

## Reproduzierbarer Nachweis

`dev/image_package.py` prüft beim CI-Build und bei der Veröffentlichung:

- Supervisor-Version, Image-Namen und Architekturzuordnung aus config.yaml;
- tatsächliches Image-OS/CPU, `io.hass.type=app`, Version/Architektur und
  OCI-Source-/Revision-Labels;
- Python-Quelltext, Migrationen und Entrypoint anhand ihrer SHA-256-Hashes
  gegen den ausgecheckten Commit; vorhandenes Frontend, ausführbaren
  Entrypoint, installierte Python-Paketversion, Ports und Liveness-Healthcheck.

Der Docker-Build-Kontext ist der **Repository-Root**, das Dockerfile
`wuschel/Dockerfile`. Er umfasst Backend/Lockfile, Frontend, Migrationen
und rootfs. Der Supervisor zieht bei gesetztem `image:` das fertige Image;
er baut diesen Repository-Checkout nicht lokal. Dockerfile-Buildargumente sind
`BUILD_VERSION`, `BUILD_ARCH` und `SOURCE_REVISION`. Die ersten beiden Namen
entsprechen den Supervisor-Buildargumenten. Der separate lokale Staging-Weg
in `dev/stage_app.py` bleibt erhalten.

Nach dem Push enthalten die Artefakte `published-amd64` und
`published-aarch64` je einen JSON-Beleg mit Image-Tag, Image-ID, Registry-Digest,
getestetem Commit, Run-ID, Package-URL, Erstellungszeit und Sichtbarkeit beim Push.

Die unabhängigen Pull-Jobs bekommen **keine Registry-Anmeldung**. Das Skript
erzeugt jeweils eine leere temporäre Docker-Konfiguration ohne Credential-Helper
und führt einen echten `docker pull --platform` aus. Danach werden Digest,
Image-ID, Architektur, Labels und Inhalt erneut mit dem CI-Beleg verglichen.
Der amd64-Job führt zusätzlich den bestehenden isolierten Container-Lifecycle-
Smoke mit dem anonym gezogenen Image aus. aarch64-Inhaltsprüfungen laufen unter
QEMU. Das ist keine reale Supervisor-/HA-Abnahme.

Commit, Workflow-Link, Digests und das Ergebnis beider anonymen Pulls gehören
in den Abschlussnachweis des Issues. Ein alter grüner Build oder ein
authentifizierter Pull ersetzt diesen Nachweis nicht.

## Fehler unterscheiden

| Beobachtung | Belastbare Aussage / nächster Schritt |
|---|---|
| Bisher nur `push: false`, kein Publikationsnachweis | CI hat nichts in GHCR veröffentlicht; einen expliziten Publish-Run ausführen. |
| Package-API 403 wegen fehlendem `read:packages` | Die verwendete Identität darf diese Information nicht lesen. Daraus folgt keine Aussage zur Existenz. |
| Package-API 404 | Nicht vorhanden **oder** für diese Identität unsichtbar; allein kein Nichtexistenzbeweis. |
| Authentifizierter Push erfolgreich, Package privat, anonymer Pull DENIED | Sichtbarkeit auf Public korrigieren; ein Supervisor-Login ist nicht die vorgesehene Lösung. |
| Öffentliches Package erreichbar, Versionsmanifest fehlt | Versionstag und config.yaml abgleichen; das konkrete Tag fehlt oder ist falsch. |
| Push DENIED trotz `packages: write` | Package-Actions-Zugriff und Organisationsregeln prüfen, keinen zusätzlichen PAT als Standardlösung einführen. |
| Pull gelingt, Architektur/Revision/ID stimmen nicht | Veröffentlichung nicht abgenommen; falsches Image oder Tag-Zuordnung untersuchen. |

Die tatsächliche Erstdiagnose und der endgültige Veröffentlichungsstand stehen
im Issue; diese Anleitung erklärt das Verfahren und behauptet keine bereits
erteilte Live-Freigabe.
