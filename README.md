# c’t → Paperless

Werkzeuge rund um digitale c’t-Ausgaben und Paperless-ngx.

![logo](./logo.png)

## Skripte

| Skript | Kurzbeschreibung | Dokumentation |
| --- | --- | --- |
| `split_ct_articles.py` | Teilt Heft-PDFs in einzelne Artikel-PDFs | [split_ct_articles.md](./split_ct_articles.md) |
| `ct_erscheinungsdaten.py` | Recherchiert offizielle c’t-Erscheinungsdaten über Heise-Vorschauseiten | [ct_erscheinungsdaten.md](./ct_erscheinungsdaten.md) |
| `update_ct_dates.py` | Setzt Dokumentdaten in Paperless anhand Ausgabepräfix und CSV | [update_ct_dates.md](./update_ct_dates.md) |

## Setup

```text
python -m pip install -r requirements.txt
```

Heft-PDFs nicht ins Repository legen. Ordner `hefte/` (Eingabe) und `artikel/` (Ausgabe) sind per `.gitignore` ausgeschlossen.

## Schnellstart

Heft in Artikel splitten (Dry-run / Apply). `--publication-date` ist Pflicht:

```text
python split_ct_articles.py --input ./hefte --output ./artikel --dry-run \
  --publication-date 2026-10-01
python split_ct_articles.py --input ./hefte --output ./artikel --apply \
  --publication-date 2026-10-01
```

Dokumentdaten in Paperless setzen (Dry-run / Apply):

```text
python update_ct_dates.py
python update_ct_dates.py -apply
```

Erscheinungsdaten aus offiziellen Heise-Vorschauseiten recherchieren
(schreibt `ct-zeitschriften-daten.csv`, UTF-8 ohne BOM):

```text
python ct_erscheinungsdaten.py
python ct_erscheinungsdaten.py --start-year 2019 --end-year 2026 --verbose
python ct_erscheinungsdaten.py --report
```

Details, Optionen und Grenzen stehen in den jeweiligen Markdown-Dateien oben.
