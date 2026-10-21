# c’t-Hefte in Artikel-PDFs splitten

Teilt Heft-PDFs (`ctYYYY-NN--gesamtes-Heft.pdf`) in einzelne Artikel für Paperless-ngx.

![logo](./logo.png)

Digitale c’t-Ausgaben sind oft **Bild-PDFs ohne Bookmarks**. Das Skript holt deshalb zuerst das Inhaltsverzeichnis vom Heise-Shop (`shop.heise.de/ct-NN-YYYY/pdf`). Fehlt die Netzverbindung, wird die OCR-Textschicht der Inhalt-Seiten verwendet (ungenauer).

## Setup

```text
python -m pip install -r requirements.txt
```

Heft-PDFs nicht ins Repository legen. Ordner `hefte/` (Eingabe) und `artikel/` (Ausgabe) sind per `.gitignore` ausgeschlossen.

## Aufruf

Die Beispiele gehen vom Repository-Wurzelverzeichnis aus. `--input` zeigt auf den Ordner mit den Heft-PDFs (oder auf eine einzelne Datei).

Nur Plan (CSV, keine neuen PDFs):

```text
python split_ct_articles.py --input ./hefte --output ./artikel --dry-run
```

Artikel schneiden:

```text
python split_ct_articles.py --input ./hefte --output ./artikel --apply
```

Einzelnes Heft:

```text
python split_ct_articles.py --input ./hefte/ct2026-21--gesamtes-Heft.pdf --output ./artikel --apply
```

Ohne `--input` werden PDFs im aktuellen Verzeichnis gelesen, ohne `--output` nach `./artikel` geschrieben.

## Optionen

| Option | Bedeutung |
| --- | --- |
| `--source auto\|heise\|local\|bookmarks` | TOC-Quelle |
| `--no-network` | nur lokale OCR / Bookmarks |
| `--toc-pages 6-7` | Inhalt-Seiten erzwingen |
| `--offset 0` | feste Differenz Heftseite minus PDF-Seite |
| `--keep-unassigned` / `--no-keep-unassigned` | Restseiten (Cover, Anzeigen, Inhalt) extra speichern (Standard: an) |
| `--include-proceed` | auch `*to-proceed*.pdf` verarbeiten |

Ohne `--apply` und ohne `--dry-run` gilt Dry-run.

Dateien mit `to-proceed` im Namen werden standardmäßig übersprungen.

## Ausgabe

Pro Heft:

- `artikel/ct-YYYY-NN_split-plan.csv` – Titel, Heftseite, PDF-Bereich
- `artikel/ct-YYYY-NN/ct-YYYY-NN - 016 - Titel.pdf`

Die führende Heftseite im Dateinamen sortiert die Artikel in Paperless grob in Heftreihenfolge.

## Grenzen

- Anzeigen ohne TOC-Eintrag hängen oft am vorherigen oder nächsten Artikel.
- Kurzmeldungen auf derselben Heftseite werden zu einem PDF zusammengefasst.
- Die lokale OCR der Inhalt-Seiten ist bei diesen Heften verrauscht; der Heise-Shop ist die zuverlässigere Quelle.
- Am Heftende kann die gedruckte Seitenzahl von der PDF-Seite abweichen (eingelegte Anzeigen). Den Plan in der CSV prüfen, bei Bedarf `--offset` setzen.
