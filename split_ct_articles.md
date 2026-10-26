# `split_ct_articles.py`

Teilt digitale c’t-Heft-PDFs anhand des Inhaltsverzeichnisses in einzelne Artikel-PDFs für Paperless-ngx.

Viele Heft-PDFs sind Bild-PDFs ohne Bookmarks. Das Skript ermittelt die Artikelgrenzen deshalb über TOC-Quellen (PDF-Bookmarks, Heise-Shop, lokale OCR der Inhalt-Seiten) und schneidet daraus getrennte PDFs.

## Voraussetzungen

```text
python -m pip install -r requirements.txt
```

Abhängigkeit: `pypdf` (siehe `requirements.txt`).

Heft-PDFs gehören nicht ins Repository. Typische Ordner: `hefte/` (Eingabe), `artikel/` (Ausgabe) — beide sind per `.gitignore` ausgeschlossen.

## Ablauf

1. PDF laden und Seitenzahl lesen
2. Ausgabejahr/-nummer aus Dateiname oder Text erkennen (`ct2024-01`, `ct-2024-01`, …)
3. Inhaltsverzeichnis wählen (`--source`):
   - `auto`: zuerst Bookmarks (≥ 8 Einträge), sonst Heise-Shop, sonst lokale OCR
   - `bookmarks` / `heise` / `local`: erzwingt die jeweilige Quelle
4. Gedruckte Heftseiten auf PDF-Seiten abbilden (automatisch oder mit `--offset`)
5. Split-Plan als CSV schreiben
6. Mit `--apply` Artikel-PDFs erzeugen

Ohne `--apply` und ohne `--dry-run` gilt Dry-run (nur Plan).

## Aufruf

Vom Repository-Wurzelverzeichnis:

```text
# Nur Plan (CSV)
python split_ct_articles.py --input ./hefte --output ./artikel --dry-run

# Artikel schneiden
python split_ct_articles.py --input ./hefte --output ./artikel --apply

# Einzelnes Heft
python split_ct_articles.py --input ./hefte/ct2026-21--gesamtes-Heft.pdf --output ./artikel --apply
```

Ohne `--input` werden PDFs im aktuellen Verzeichnis gelesen, ohne `--output` nach `./artikel` geschrieben.

## Optionen

| Option | Bedeutung |
| --- | --- |
| `--input` | PDF-Datei oder Ordner (Standard: `.`) |
| `--output` | Zielordner (Standard: `./artikel`) |
| `--dry-run` | Nur Split-Plan (CSV), keine PDFs |
| `--apply` | Artikel-PDFs erzeugen |
| `--source auto\|heise\|local\|bookmarks` | TOC-Quelle |
| `--no-network` | Heise-Shop nicht abfragen |
| `--toc-pages 6-7` | Inhalt-Seiten erzwingen |
| `--offset N` | feste Differenz Heftseite − PDF-Seite |
| `--keep-unassigned` / `--no-keep-unassigned` | Restseiten ohne TOC-Treffer extra speichern (Standard: an) |
| `--include-proceed` | auch Dateien mit `to-proceed` im Namen verarbeiten |

Dateien mit `to-proceed` im Namen werden standardmäßig übersprungen.

## Ausgabe

Pro Heft:

- `artikel/ct-YYYY-NN_split-plan.csv` — Titel, Rubrik, Heftseite, PDF-Bereich, Dateiname
- `artikel/ct-YYYY-NN/ct-YYYY-NN - 016 - Titel.pdf`

Die führende Heftseite im Dateinamen sortiert die Artikel in Paperless grob in Heftreihenfolge.

## Grenzen

- Anzeigen ohne TOC-Eintrag hängen oft am vorherigen oder nächsten Artikel.
- Kurzmeldungen auf derselben Heftseite werden zu einem PDF zusammengefasst.
- Lokale OCR der Inhalt-Seiten ist oft verrauscht; der Heise-Shop ist zuverlässiger.
- Am Heftende kann die gedruckte Seitenzahl von der PDF-Seite abweichen. Plan in der CSV prüfen, bei Bedarf `--offset` setzen.
