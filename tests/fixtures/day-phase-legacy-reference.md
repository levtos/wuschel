# Tagesphasen-Referenz

`day-phase-legacy-2026-2028.csv` enthält für jeden Tag die erste vollständige
lokale Minute der neun Phasen. Sie wurde lokal aus den unveränderten Funktionen
`compute_day_phase_starts` und deren Abhängigkeiten von
[benni-core-state](https://github.com/Levtos/benni-core-state/blob/c9408fa33f2914b1c5f7316738cb38b43add33b5/custom_components/benni_core_state/logic.py)
extrahiert, Modell `calendar-seasonal-accordion-v2`.
Quellblob: `41f03b40252141112feb0fa333c03b74ec5809cb`.

Die 1.096 Zeilen umfassen 2026–2028 einschließlich Schaltjahr, Jahreswechsel und
beider Zeitumstellungen. Die Referenz enthält nur berechnete Kalendergrenzen;
Legacy-Code, lokale Extraktionsskripte und private Messdaten gehören nicht zum
Repository. Die produktiven Jahreskurven und die Legacy-Funktionen rechnen
unabhängig voneinander; der Test vergleicht 9.864 Übergangsminuten.
