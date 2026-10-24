# 1.0.0b5 – Nachweise, optionale HA-Sensoren und Verlauf

Auftrag: [#42](https://github.com/levtos/wuschel/issues/42), einschließlich Bennis Umfangsänderung: Media-Verträge und Media-Importpakete entfallen. A–D werden vollständig gebaut; ein gemeinsamer PR geht vor Merge an Claude. Release und Installation erst nach Review und Go. Dieser Nachweis behauptet keine Live-Abnahme.

## A – identische retained-Messung

`mqtt_restore.retained_replay()` erkennt nur dieselbe Gerätezeit und denselben Roh- und normalisierten Wert derselben MQTT-Quelle/Bindung. Voraussetzung ist ein noch gültiger, zum aktuellen Fingerabdruck passender Gerätenachweis. Fehlerhafte oder anders gealterte Eingaben erhalten keine Ausnahme. Die Runtime verwirft das Duplikat vor dem Ersetzen der Beobachtung. Originalzeit, Referenz, Veröffentlichung und Ablauf bleiben erhalten. Abweichende retained-Meldungen bleiben konservativ; abgelaufene Sensoren werden nicht wiederbelebt.

`tests/test_beta5_retained.py` prüft sieben Quellen über Stop/Start, unveränderte gespeicherte Beobachtungen und Publikationen, anschließend Originalablauf trotz erneutem Replay sowie andere/ältere/neue Zeit, fehlenden Nachweis und ungültige Zeitstempel. Die bestehenden Öffnungstests bleiben unverändert.

Die volle CI fand einen überholten Testaufbau in `test_longer_interval_does_not_promote_invalid_or_retained_evidence`: Er erzeugte aus einem gültigen Nachweis ein identisches retained-Replay und nahm an, damit ungültige Evidenz hergestellt zu haben. Genau diese Annahme hebt A ausdrücklich auf. Der Test verwendet nun einen abweichenden retained-Wert und prüft zusätzlich vor der Intervalländerung den ungültigen Ausgangszustand. Die bisherige Assertion nach der Änderung bleibt unverändert. Kein Prüfschritt wird entfernt oder abgeschwächt.

## B – generische Projektion

`Contract.ha_projection` ist ein standardmäßig ausgeschaltetes Opt-in für ein skalares `value`-Feld. Es beeinflusst keinen Vertragsfingerabdruck. Maximal 256 ausgewählte Verträge; zusammengesetzte Zustände benötigen weiterhin einen ausdrücklich definierten skalaren Vertrag. Die Oberfläche erlaubt die Auswahl am Vertrag, die Servervalidierung bleibt maßgeblich.

Wuschel sendet einen vollständigen, bestätigten Veröffentlichungsstand über den bestehenden administrativen Traber-WebSocket (`contract_projection`, Protokoll 2). Traber bewertet keine Frische oder Vertragslogik. Die begrenzte Transportablage prüft Installation, Epoch-ID, Sequenz, Datentypen und Eigentümer der Verbindung. Alte Sequenzen werden abgewiesen. Bei Verbindungsverlust sind Sensoren nicht verfügbar; abgewählte Sensoren bleiben ohne aktuellen Wert erhalten. Es werden keine HA-Registrierungen gelöscht.

Sensoridentität: Installations-ID plus Vertrags-ID. Zustand: veröffentlichter skalarer Wert bei `valid`/`held`, sonst `unknown`. Attribute: `contract_id`, `status`, `quality`, `since_at`. Texte über HA-Zustandslänge werden nicht als gekürzter, scheinbar gültiger Vertragswert ausgegeben. Traber-Version: 1.0.0b5.

`tests/test_beta5_projection.py` verwendet einen synthetischen deklarativen Wahrheitswert und Traber-Zeitfelder `time_fired`, `last_changed`, `last_updated`, `last_reported`. Snapshot, Änderung und Wiederholungsmeldung prüfen Auswahl der Messuhr, veröffentlichte Werte und Übergang zu unbekannt. Fehlende Messzeit bleibt ein negativer Nachweis. `tests/test_bridge_kernel.py` prüft den Sensor im echten HA-Zustandsmotor und die administrative WebSocket-Grenze im HA-CI-Job.

Der direkt erzeugte HA-Kernel erhält dazu seinen echten `ConfigEntries(hass, {})`-Manager. Nur die Plattform-Weiterleitung wird im Callback-Test abgefangen und ausdrücklich als `sensor`-Weiterleitung geprüft; die bestehenden Transportprüfungen und die neuen Sensor-Zustandsassertions bleiben erhalten. Das behebt den CI-Aufbaufehler `config_entries is None` ohne Produktionsausnahme.

Der Sensor wird über HA-Loader und echte Sensor-EntityComponent hinzugefügt, damit HA selbst Plattform, Entitätsregistrierung und Zustandsschreiben initialisiert. Direktes manuelles Zuweisen von `hass`/`entity_id` erwies sich im zweiten HA-CI-Versuch als unvollständig (`KeyError: integrations`). Keine Zustandsassertion wird dafür entfernt.

Auflösung der drei HA-Aufbaufehler: [Claudes unveränderter Patch](https://github.com/levtos/wuschel/pull/43#issuecomment-6390142245) lädt die Basisfunktionalität mit `bootstrap.async_load_base_functionality(hass)`. Die Sensor-ID wird über das unterstützte `entity_id = f"sensor.wuschel_{slugify(contract_id)}"` vorbelegt; `_attr_suggested_object_id` existiert in HA 2026.10 nicht. Beide Ergebnis-Dateiblobs stimmen mit Claudes Diff überein (`6c6d4b0…`, `e5140fe…`). Lokal vor dem Push: Python 3.14.5, Home Assistant 2026.10.0, asyncpg 0.32.0, pydantic 2.12.5, pytest 9.0.2, pytest-asyncio 1.3.0; `tests/test_bridge_kernel.py -q`: **5 passed**. Unter Windows verwendet die lokale `.ha-venv` den Pfad `Scripts/python.exe`, im Linux-CI-Job `bin/python`. Für jede künftige Änderung an Traber oder diesem Test ist dieser lokale Test vor dem Push verbindlich.

## C – fester Zeitraum

Alle Felddiagramme verwenden gemeinsam 24 Stunden oder 7 Tage mit festen Zeitgrenzen. Cursor-Seiten werden vollständig geladen, zusätzlich eine gespeicherte Randpublikation vor dem Beginn. Deren wirklicher Zeitpunkt bleibt in Tooltip und Tabelle erhalten. Fehlende Werte und gespeicherte Aufzeichnungslücken unterbrechen Linien. Enum-Werte stehen in Schema-Reihenfolge und werden als Stufen gezeichnet; es gibt keine Zwischenkategorie. Jeder Punkt trägt Zeitpunkt, deutschen Wert und Status, auch per Tastaturfokus.

Der bestehende Verlaufstest hatte feste Daten vom 16.10.2026 und erwartete sie unabhängig vom heutigen Datum. Für das nun verbindliche 24-Stunden-Fenster liegen dieselben drei Proben relativ zu jetzt innerhalb des Fensters; die Assertion zweier durch unbekannt getrennten Segmente bleibt gleich. Neue Tests prüfen Schema-Reihenfolge, Lücken, feste Achsengrenzen, mehr als 60 Proben und vollständige Cursor-Abfrage.

## D – angebotene Version und geprüfte Artefakte

Während der Vorbereitung bleiben `wuschel/config.yaml` und damit der HA-Store auf der bereits veröffentlichten Version. `wuschel/PENDING_RELEASE.json` nennt Kandidat und bisherige Store-Version. Python, Frontend und Image-Metadaten verwenden den Kandidaten; das unveränderte AwesomeVersion-Gate prüft weiterhin alle historischen Versionslinien und Slugs. Es entsteht kein neues Versionsformat.

Die reguläre vollständige CI erzeugt Image-Artefakte und einen kleinen Beleg mit tatsächlichem Commit, Git-Baum, Run und Versuch. Der manuell gestartete Publisher verlangt die ausgewählte main-SHA und den grünen CI-Run. Er prüft Repository, Workflow, alle Jobs, Versuch und identischen Git-Baum. Ein PR-Run kann den synthetischen Merge statt der Branch-SHA gebaut haben; dieser echte getestete Commit bleibt im OCI-Label erhalten. Der Publisher lädt dessen Image-TARs und baut nichts neu. Beide anonymen Pull-/Digest-/Inhaltsprüfungen und der amd64-Lifecycle-Smoke bleiben verbindlich.

Erst ein vollständig grüner Publisher erlaubt `dev/promote_store.py --run-id …`: Das Skript ändert nur die angebotene Store-Version und entfernt den Marker. Diese Metadatenänderung geht durch einen normalen PR mit voller CI. Danach wird auf dem Promotion-Commit getaggt und das Pre-Release erstellt. Code-PR, getesteter Image-Commit, Publish-Run und Promotion-Commit werden im Release dokumentiert. Keine automatischen Live-Aktionen.

Zusätzlich prüft die normale CI immer `dev/store_offer.py`: Die tatsächlich angebotene Store-Version muss für beide Architekturen ohne GitHub-Konto öffentlich auflösbar sein, mit passender Version, Plattform und Konfigurations-Digest. Auch ein manuell zu früh geändertes `config.yaml` scheitert damit vor Merge. Dies ersetzt nicht die ausführlichen anonymen Pull- und Lifecycle-Prüfungen des Publishers. Negative Tests decken fehlende Images, Versions-/Plattform-/Digest-Abweichungen ab.

Produktionsabhängigkeiten: `npm audit --omit=dev --audit-level=high` blockiert. Zusätzlich bleibt der vollständige Audit als sichtbare Warnung erhalten; dies ist ausdrücklich Teil D des Auftrags. Changelog b1–b5 wird nachgetragen. Es werden keine Repository-Einstellungen, Pflichtprüfungen oder Secrets verändert.
