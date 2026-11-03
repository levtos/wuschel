# Synthetische Leistungsmessung

Nur lokal und ausschließlich mit generierten Daten ausführen:

```text
uv sync --locked
uv run python -m tests.perf.benchmark
uv run python -m tests.perf.history_prototypes
uv run python -m tests.perf.option_models
```

Das erste Skript nutzt einen isolierten MemoryStore und einen temporären lokalen
HTTP-Testserver. Es aktiviert elf synthetische Revisionen, erzeugt neun offene
Entwürfe sowie sieben Kataloge mit 11.200 Einträgen und Master-/Variantenpaaren.
Die vier HTTP-Operationen laufen je dreimal. Ein separater Thread versucht
während der Runtime-Operation 20 Beobachtungen pro Sekunde einzureihen; volle
Queues werden gezählt. Die Latenz beginnt bei Erzeugung und endet nach
Verarbeitung. Anders als der produktive Ingest-Pfad bildet dieser Messpfad
Überlauf-Zusammenfassung und Wiederanmeldung nicht nach. Die tatsächlich
erzeugte Anzahl und verworfene Einträge sind daher zusammen mit den Perzentilen
zu betrachten. Die Aktivierungsläufe erzeugen weitere Revisionen.

Die Konfiguration hat einfache synthetische Echo-Verträge und bildet deshalb
nicht die Komplexität der Live-Verträge ab. Die Aktivierung enthält keine
Katalogmigration. Der MemoryStore misst weder Netzwerk- noch PostgreSQL-Zeit.
`db_calls` sind Store-Aufrufe, keine SQL-Statements; `db_ms` enthält auch
Python-Arbeit innerhalb dieser Aufrufe. Phasen und Store-Zeit überlappen sich.
Der Cache- und der Überspringen-Prototyp gelten nur für die identischen
synthetischen Quellen und ersetzen keine vollständige Validierung.

Das zweite Skript vergleicht die Historienprototypen auf elf identischen
synthetischen Revisionen. Das dritte Skript misst isolierte Kosten für Kopieren,
Digest und Prozesse.
Diese Modellmessungen sind keine Aussage zur Atomarität oder zum tatsächlichen
End-to-End-Gewinn. PostgreSQL-Bulk-Schreiben und Indizes werden nicht simuliert.

Für den isolierten Oberflächenversuch: im Verzeichnis `frontend`
`npm ci`, danach `npx vitest run --config perf/vitest.config.ts`. Die
jsdom-Messung ist keine Browsermessung. Der separate Chrome-Versuch benötigt
einen lokalen Vite-Server und `node perf/browser.mjs`; er verwendet nur die
synthetische Fake-API in `perf/browser.tsx`. Sie liefert 6.000 absichtlich
nicht passende Discovery-Einträge in einer Antwort, um den Verarbeitungspfad
nach dem Laden zu belasten; die produktive API paginiert. Die Messung beweist
nicht, dass dieser Datenumfang im Live-System vorliegt.
