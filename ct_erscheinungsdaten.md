# `ct_erscheinungsdaten.py`

Recherchiert die offiziellen Erscheinungsdaten regulärer c’t-Ausgaben ausschließlich über Heise-/c’t-Seiten unter `https://www.heise.de/select/ct/` und schreibt sie als CSV.

Das Skript erfindet keine Daten: Ein Datum landet nur dann in der CSV, wenn es auf der offiziellen Vorschauseite der **unmittelbar vorherigen** Ausgabe ausdrücklich erkennbar ist. Sonst steht in der Spalte `datum` exakt `FEHLT`.

## Voraussetzungen

```text
python -m pip install -r requirements.txt
```

Abhängigkeiten: `requests`, `beautifulsoup4`, `lxml` (siehe `requirements.txt`).

Netzwerkzugriff auf `www.heise.de` ist erforderlich (außer bei vollständigem Cache-Treffer). Der Ordner `cache/` ist per `.gitignore` ausgeschlossen.

## Rechercheprinzip

Das Erscheinungsdatum der Ausgabe **N** kommt nur von der Vorschauseite der Ausgabe **N−1**:

1. Offizielle Inhaltsseite der vorherigen Ausgabe laden  
   (`https://www.heise.de/select/ct/{jahr}/{ausgabe}`)
2. Im Inhaltsverzeichnis den Eintrag „Vorschau“ finden
3. Auf der Vorschauseite Formulierungen wie  
   `Ab 19. Januar 2019 im Handel und auf ct.de` auswerten
4. Datum nach `YYYY-MM-DD` normalisieren

Für Ausgabe `01` eines Jahrgangs dient die letzte Ausgabe des Vorjahres als Vorschauquelle.

Nicht verwendet werden Wikipedia, Händler-/Bibliotheksdaten, Suchmaschinen-Snippets, 14-Tage-Intervalle oder geschätzte Daten.

## Ausgabenermittlung

Pro Jahrgang liest das Skript das offizielle Archiv:

```text
https://www.heise.de/select/ct/archiv/{jahr}
```

- reguläre Hefte (`c't N/YYYY`) werden übernommen
- `c't Jahresrückblick YYYY` wird zusätzlich aufgenommen (`ausgabe` = `Jahresrückblick`)
- Spezialhefte (Extra, Admin, Linux, Security, Android, Projekte, E-Dossier, …) werden verworfen
- jede Kandidaten-Inhaltsseite wird geprüft; ohne gültige Seite entsteht keine CSV-Zeile

## CSV-Ausgabe

Standarddatei: `ct-zeitschriften-daten.csv`

- Encoding: UTF-8 **ohne BOM**
- Trennzeichen: Semikolon
- Kopfzeile:

```csv
jahr;ausgabe;datum;titel;zeitschrift;quelle
```

Beispiel:

```csv
2019;03;2019-01-19;c't 3/2019;c't;https://www.heise.de/select/ct/2019/2/1546688673966949
2022;Jahresrückblick;FEHLT;c't Jahresrückblick 2022;c't;https://www.heise.de/select/ct/2022/26/seite-194
```

| Spalte | Inhalt |
| --- | --- |
| `jahr` | Jahrgang der Zielausgabe |
| `ausgabe` | zweistellig (`01` …) bzw. `Jahresrückblick` |
| `datum` | `YYYY-MM-DD` oder `FEHLT` |
| `titel` | z. B. `c't 3/2019` |
| `zeitschrift` | immer `c't` |
| `quelle` | konkrete Vorschau-URL; sonst Inhaltsseite der vorherigen Ausgabe |

Die Datei kann von [`update_ct_dates.py`](./update_ct_dates.md) gelesen werden (Spalten `jahr`, `ausgabe`, `datum`).

## Ablauf

1. Archivseiten für `--start-year` … `--end-year` laden (plus letzte Ausgabe des Vorjahres)
2. Reguläre Ausgaben und Jahresrückblick ermitteln und Inhaltsseiten validieren
3. Pro Zielausgabe Vorschau-Link der Vorgängerausgabe suchen
4. Erscheinungsdatum extrahieren und kalendarisch prüfen
5. Chronologie prüfen (`datum` einer Ausgabe ≤ Datum der nächsten vorhandenen Ausgabe)
6. CSV schreiben; optional JSON-Report

## Aufruf

Vom Repository-Wurzelverzeichnis:

```text
# Standard: 2019–2026 → ct-zeitschriften-daten.csv
python ct_erscheinungsdaten.py

# Zeitraum und Logging
python ct_erscheinungsdaten.py --start-year 2019 --end-year 2026 --verbose

# Andere Zieldatei / langsamere Requests / Cache neu laden
python ct_erscheinungsdaten.py --output meine_datei.csv --delay 2 --refresh

# Zusätzlicher JSON-Report
python ct_erscheinungsdaten.py --report
python ct_erscheinungsdaten.py --report mein_report.json
```

## Optionen

| Option | Bedeutung |
| --- | --- |
| `--start-year YYYY` | erstes Jahr (Standard: `2019`) |
| `--end-year YYYY` | letztes Jahr (Standard: `2026`) |
| `--output PFAD` | CSV-Zieldatei (Standard: `ct-zeitschriften-daten.csv`) |
| `--delay SEKUNDEN` | Pause zwischen HTTP-Requests (Standard: `1.25`) |
| `--refresh` | HTML-Cache ignorieren und Seiten neu laden |
| `--verbose` | ausführliches Logging auf stderr |
| `--report [PFAD]` | JSON-Prüfdatei; ohne Pfad: `ct_ausgaben_<start>_<end>_report.json` |
| `--cache-dir PFAD` | Cache-Verzeichnis (Standard: `cache`) |

## Optionaler Report

Mit `--report` entsteht eine JSON-Datei je Ausgabe mit u. a.:

- geprüfte Inhaltsseite der Vorgängerausgabe
- gefundene Vorschauseite
- extrahierter Vorschautext
- extrahiertes Datum
- Validierungsstatus
- Fehlermeldungen
- Zeitstempel der Recherche

## Grenzen

- Fehlt der Vorschau-Link, ist die Vorschauseite nicht erreichbar oder nennt eine andere Zielausgabe → `FEHLT`
- Überspringt die Vorschau der Vorgängerausgabe eine Nummer (z. B. von 10 direkt auf 12), bleibt die übersprungene Ausgabe ohne Beleg → `FEHLT`
- Für den Jahresrückblick gibt es oft keine passende offizielle Vorschau → typischerweise `FEHLT`
- Noch nicht erschienene Ausgaben werden nicht aus dem heutigen Datum oder einem 14-Tage-Rhythmus geschätzt
- Steht im Vorschautext kein Jahr, wird das Jahr der Zielausgabe nur bei eindeutiger Zuordnung abgeleitet (bei Heft `01`/`02` und Monat Dezember: Vorjahr)
