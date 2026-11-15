# b26: Korrekturen aus der Abschlussprüfung

Auftrag: [#143](https://github.com/Levtos/wuschel/issues/143), nach der
[Abschlussprüfung #135](https://github.com/Levtos/wuschel/issues/135), innerhalb
des [Masterplans #115](https://github.com/Levtos/wuschel/issues/115).

## Lastschutz

Commit-Proben enthalten den monotonen Zeitpunkt und die gemessene Dauer.
`load_commit_window_s` begrenzt sie auf ein Zeitfenster (Standard 60 s,
erlaubt 10–600 s, wirksam nach Neustart). Veraltete Proben werden sowohl beim
Speichern als auch beim Messen entfernt. Bei weniger als fünf Proben sind
Median und p95 nicht gemessen. Verwaltungsoperationen aus beiden
Vorgangsregistern zählen nicht mit; Ereignisblöcke und das Nachholen
gepufferter Ereignisse zählen mit. Start und Beenden zählen nicht mit.
Die bisherige Einzelanzeige `commit_duration` bleibt unverändert.

Die bestehende Haltezeit des Lastschutzes gilt weiter, auch für die Erholung.
Die neue Einstellung verändert keine fachlichen Fristen, Qualitätsregeln,
Warteschlangen oder Projektionseinstellungen. Einzelheiten:
[Betriebswerte](b26-betrieb.md), [Selbstschutz](b26-selbstschutz.md).

## Übernehmen

Ein bestätigter Erfolg schließt den Arbeitsentwurf sofort und entfernt seine
gespeicherte Kennung aus dem Tab. Der Erfolgsdialog bleibt für alle Wege
sichtbar. Auch beim Aufruf seiner Detailseite wird ein erledigter Entwurf nur
als vergangener Stand angezeigt. Der Auswahldialog verwendet einen normalen
Hash-Verweis und benötigt keinen Router. Eine Fehlergrenze schließt Einrichtung
und Provider ein; der deutsche Hinweis bietet „Neu laden“, technische Fehler
werden in die Konsole geschrieben.

## Prüfhinweise und Bedienung

Die Texte nennen Ursache und nächsten Schritt. Ein Test liest die Codes der
Backend-Validierung einschließlich Pydantic-Fehlern und registrierter
Regelprüfer und verlangt einen deutschen Text. Wirklich unbekannte Codes
behalten den Rückfalltext.

Ein Fehlersprung öffnet den Vertrag im Bearbeitungsmodus. Der Fokus wartet,
bis das Feld bedienbar ist, und rollt zum Ziel. Der Deaktivieren-Dialog nennt
eingeschaltete Pflichtverbraucher und die dadurch gesperrte Übernahme.
Die Aktualitätsauswahl enthält Beispiele und sichtbare Gründe für einen
gesperrten Anlegeknopf. Änderungsansichten zeigen Anzeigenamen mit Kennungen,
übersetzte Feldnamen und Wertelisten im passenden Singular/Plural.
Die Backend-Prüfregeln bleiben unverändert.

## Prüfumfang und Grenze

Die Bereichsauswahl liefert wegen der Kernänderung `ALL`. Lokal werden die
direkten Tests für Runtime, Ereignisblöcke, Verwaltungs-Commit, Lastschutz,
Betriebseinstellungen und Wiedergabe sowie die berührten Frontend-Komponenten
geprüft. Die volle Suite bleibt der CI vorbehalten.
Die beiden Firefox-Regressionen durchlaufen Umbenennen → Übernehmen → X →
zweiten Vertrag bearbeiten bzw. neuen Vertrag anlegen → Umbenennen →
Übernehmen und Neuladen ohne Seitenfehler.

Alle Wiedergabeaufzeichnungen bleiben unverändert. Dieser Korrekturschritt
liefert einen PR nach `b26`; Release, Installation und Live-Abnahme gehören
weiter zu #135 und Bennis gesondertem Go.
