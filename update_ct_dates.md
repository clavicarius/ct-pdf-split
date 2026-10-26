# `update_ct_dates.py`

Setzt in Paperless-ngx das Dokumentdatum (`created`) von c’t-Dokumenten anhand des Ausgabepräfixes im Titel und einer CSV mit Erscheinungsdaten.

Passende Titel sehen z. B. so aus:

```text
ct2024-01 – Vollständige Ausgabe
ct2024-01 – Artikelname
```

Das Präfix `ctYYYY-NN` wird aus dem Titel gelesen und mit der CSV-Zeile für Jahr/Ausgabe verknüpft.

## Voraussetzungen

Umgebungsvariablen (pflicht):

| Variable | Bedeutung |
| --- | --- |
| `PAPERLESS_URL` | Basis-URL der Paperless-Instanz |
| `PAPERLESS_TOKEN` | API-Token |

Abhängigkeit: `requests`.

CSV-Datei im Arbeitsverzeichnis: `ct-zeitschriften-daten.csv`

Korrespondent in Paperless (exakter Name laut Skriptkonfiguration): `ct`

## CSV-Format

```csv
jahr,ausgabe,datum
2024,01,2024-01-05
2024,02,2024-01-19
2025,01,2025-01-03
```

- Spalten: `jahr`, `ausgabe`, `datum`
- Datum: `YYYY-MM-DD`
- Ausgabenummer wird mit führender Null normalisiert (`1` und `01` sind gleich)

## Ablauf

1. Filter bestimmen: `-edition YYYY-NN` oder alle Ausgaben des Jahres (`-year`, Standard: aktuelles Jahr)
2. Passende CSV-Einträge laden
3. Korrespondent `ct` über die Paperless-API finden
4. Alle Dokumente dieses Korrespondenten laden (inkl. Pagination)
5. Titel mit Präfix `ctYYYY-NN` auswerten
6. Bei Abweichung vom CSV-Datum: im Dry-run nur melden, mit `-apply` per `PATCH` setzen

## Aufruf

```text
# Dry-run für das aktuelle Jahr (Standard)
python update_ct_dates.py

# Änderungen für das aktuelle Jahr ausführen
python update_ct_dates.py -apply

# Dry-run für ein bestimmtes Jahr
python update_ct_dates.py -year 2024

# Nur eine Ausgabe prüfen
python update_ct_dates.py -edition 2024-03

# Nur eine Ausgabe ändern
python update_ct_dates.py -edition 2024-03 -apply
```

## Optionen

| Option | Bedeutung |
| --- | --- |
| `-apply` | Änderungen ausführen; ohne Flag bleibt Dry-run |
| `-year YYYY` | nur Ausgaben dieses Jahres (Standard: aktuelles Jahr) |
| `-edition YYYY-NN` | nur diese Ausgabe; hat Vorrang vor `-year` |

## Zusammenfassung am Ende

Das Skript meldet u. a.:

- geplant / tatsächlich geändert
- bereits korrekt
- ohne passendes Titelpräfix
- außerhalb des Filters
- ohne CSV-Eintrag
- Fehler bei API-Updates
