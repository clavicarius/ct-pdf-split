# c’t → Paperless

Werkzeuge rund um digitale c’t-Ausgaben und Paperless-ngx.

![logo](./logo.png)

## Skripte

| Skript | Kurzbeschreibung | Dokumentation |
| --- | --- | --- |
| `split_ct_articles.py` | Teilt Heft-PDFs in einzelne Artikel-PDFs | [split_ct_articles.md](./split_ct_articles.md) |
| `update_ct_dates.py` | Setzt Dokumentdaten in Paperless anhand Ausgabepräfix und CSV | [update_ct_dates.md](./update_ct_dates.md) |

## Setup

```text
python -m pip install -r requirements.txt
```

Heft-PDFs nicht ins Repository legen. Ordner `hefte/` (Eingabe) und `artikel/` (Ausgabe) sind per `.gitignore` ausgeschlossen.

## Schnellstart

Heft in Artikel splitten (Dry-run / Apply):

```text
python split_ct_articles.py --input ./hefte --output ./artikel --dry-run
python split_ct_articles.py --input ./hefte --output ./artikel --apply
```

Dokumentdaten in Paperless setzen (Dry-run / Apply):

```text
python update_ct_dates.py
python update_ct_dates.py -apply
```

Details, Optionen und Grenzen stehen in den jeweiligen Markdown-Dateien oben.
