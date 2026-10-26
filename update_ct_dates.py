#!/usr/bin/env python3
"""Setzt Paperless-Dokumentdaten anhand der c't-Erscheinungsdaten-CSV."""

import argparse
import csv
import os
import re
import sys
from datetime import date

import requests


# ------------------------------------------------------------
# Konfiguration
# ------------------------------------------------------------

PAPERLESS_URL = os.environ["PAPERLESS_URL"].rstrip("/")
PAPERLESS_TOKEN = os.environ["PAPERLESS_TOKEN"]

CSV_DATEI = "ct-zeitschriften-daten.csv"

# Exakte Schreibweise in Paperless-ngx verwenden
KORRESPONDENT_NAME = "ct"

# Titelpräfix, zum Beispiel:
# ct2024-01 – Vollständige Ausgabe
# ct2024-01 – Artikelname
TITELMUSTER = r"^ct(\d{4})-(\d{1,2})(?!\d)"


# ------------------------------------------------------------
# Kommandozeilenparameter
# ------------------------------------------------------------

def parse_arguments():
    """Parst und validiert die Kommandozeilenargumente."""
    parser = argparse.ArgumentParser(
        description=(
            "Setzt das Dokumentdatum von c't-Dokumenten anhand "
            "des Ausgabepräfixes im Titel."
        )
    )

    parser.add_argument(
        "-apply",
        action="store_true",
        help=(
            "Änderungen tatsächlich ausführen. "
            "Ohne diesen Parameter wird nur ein Dry-Run ausgeführt."
        ),
    )

    parser.add_argument(
        "-year",
        type=int,
        default=date.today().year,
        help=(
            "Nur Ausgaben dieses Jahres berücksichtigen. "
            "Standard: aktuelles Jahr."
        ),
    )

    parser.add_argument(
        "-edition",
        type=str,
        help=(
            "Nur eine bestimmte Ausgabe berücksichtigen, "
            "zum Beispiel 2024-03."
        ),
    )

    args = parser.parse_args()

    # Jahr validieren
    if args.year < 1900 or args.year > 2100:
        parser.error(
            "-year muss zwischen 1900 und 2100 liegen."
        )

    # Ausgabe validieren und normalisieren
    if args.edition:
        match = re.fullmatch(
            r"(\d{4})-(\d{1,2})",
            args.edition.strip(),
        )

        if not match:
            parser.error(
                "-edition muss das Format YYYY-NN haben, "
                "zum Beispiel 2024-03."
            )

        editionsjahr = int(match.group(1))
        editionsnummer = int(match.group(2))

        if editionsnummer < 1 or editionsnummer > 99:
            parser.error(
                "Die Ausgabenummer in -edition muss zwischen 01 und 99 liegen."
            )

        # Einheitliche Schreibweise YYYY-NN
        args.edition = (
            f"{editionsjahr:04d}-{editionsnummer:02d}"
        )

    return args


# ------------------------------------------------------------
# API-Funktionen
# ------------------------------------------------------------

HEADERS = {
    "Authorization": f"Token {PAPERLESS_TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def api_get(url, params=None):
    """Führt einen GET-Request gegen die Paperless-API aus."""
    response = requests.get(
        url,
        headers=HEADERS,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def api_patch(url, payload):
    """Führt einen PATCH-Request gegen die Paperless-API aus."""
    response = requests.patch(
        url,
        headers=HEADERS,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def finde_korrespondent(name):
    """
    Sucht den Korrespondenten anhand seines Namens.
    """
    url = f"{PAPERLESS_URL}/api/correspondents/"

    daten = api_get(
        url,
        params={
            "name__iexact": name,
            "page_size": 100,
        },
    )

    treffer = daten.get("results", [])

    if not treffer:
        raise RuntimeError(
            f"Korrespondent nicht gefunden: {name!r}"
        )

    if len(treffer) > 1:
        raise RuntimeError(
            f"Mehrere Korrespondenten mit diesem Namen gefunden: "
            f"{treffer}"
        )

    return treffer[0]


def lese_alle_dokumente(korrespondent_id):
    """
    Liest alle Dokumente des Korrespondenten einschließlich Pagination.
    """
    url = f"{PAPERLESS_URL}/api/documents/"
    params = {
        "correspondent": korrespondent_id,
        "page_size": 100,
    }

    dokumente = []

    while url:
        daten = api_get(url, params=params)
        dokumente.extend(daten.get("results", []))

        # Die API liefert bei der nächsten Seite eine vollständige URL.
        url = daten.get("next")
        params = None

    return dokumente


# ------------------------------------------------------------
# CSV-Verarbeitung
# ------------------------------------------------------------

def lese_csv():
    """
    Erwartete CSV-Struktur (UTF-8 ohne BOM, Semikolon):

    jahr;ausgabe;datum;titel;zeitschrift;quelle
    2024;01;2024-01-05;c't 1/2024;c't;https://...
    2024;02;FEHLT;c't 2/2024;c't;https://...

    Zeilen mit datum=FEHLT oder nicht-numerischer ausgabe
    (z. B. Jahresrückblick) werden übersprungen.
    Bei doppelten Schlüsseln gilt der letzte gültige ISO-Eintrag.

    Ergebnis:
    {
        ("2024", "01"): "2024-01-05",
    }
    """
    mapping = {}

    with open(
        CSV_DATEI,
        newline="",
        encoding="utf-8",
    ) as datei:
        reader = csv.DictReader(datei, delimiter=";")

        erwartete_spalten = {
            "jahr",
            "ausgabe",
            "datum",
        }

        vorhandene_spalten = set(reader.fieldnames or [])
        fehlende_spalten = erwartete_spalten - vorhandene_spalten

        if fehlende_spalten:
            raise RuntimeError(
                "Fehlende CSV-Spalten: "
                + ", ".join(sorted(fehlende_spalten))
            )

        for zeilennummer, zeile in enumerate(
            reader,
            start=2,
        ):
            jahr = zeile["jahr"].strip()
            ausgabe_roh = zeile["ausgabe"].strip()
            datum = zeile["datum"].strip()

            if not re.fullmatch(r"\d{4}", jahr):
                raise RuntimeError(
                    f"Ungültiges Jahr in CSV, Zeile "
                    f"{zeilennummer}: {jahr!r}"
                )

            # Nicht-numerische Ausgaben (z. B. Jahresrückblick) ignorieren
            if not re.fullmatch(r"\d{1,2}", ausgabe_roh):
                continue

            ausgabe = ausgabe_roh.zfill(2)

            # Fehlende Erscheinungsdaten überspringen
            if datum == "FEHLT":
                continue

            try:
                date.fromisoformat(datum)
            except ValueError as exc:
                raise RuntimeError(
                    f"Ungültiges Datum in CSV, Zeile "
                    f"{zeilennummer}: {datum!r}. "
                    f"Erwartet wird JJJJ-MM-TT oder FEHLT."
                ) from exc

            # Doppelte Schlüssel: letzter gültiger ISO-Eintrag gewinnt
            mapping[(jahr, ausgabe)] = datum

    return mapping


# ------------------------------------------------------------
# Titelverarbeitung
# ------------------------------------------------------------

def ermittle_ausgabe_aus_titel(titel):
    """
    Erwartet zum Beispiel:

    ct2024-01 – Vollständige Ausgabe
    ct2024-01 – Artikelname

    Rückgabe:

    ("2024", "01")

    oder None, wenn der Titel nicht passt.
    """
    match = re.match(TITELMUSTER, titel)

    if not match:
        return None

    jahr = match.group(1)
    ausgabe = match.group(2).zfill(2)

    return jahr, ausgabe


# ------------------------------------------------------------
# Hauptprogramm
# ------------------------------------------------------------

def ermittle_ausgabenfilter(args, csv_mapping):
    """Bestimmt den Ausgabenfilter und eine lesbare Beschreibung."""
    edition_filter = args.edition

    if edition_filter:
        ausgabenfilter = {
            (
                edition_filter[0:4],
                edition_filter[5:7],
            )
        }
        filterbeschreibung = f"nur Ausgabe {edition_filter}"
    else:
        ausgabenfilter = {
            (jahr, ausgabe)
            for jahr, ausgabe in csv_mapping
            if jahr == str(args.year)
        }
        filterbeschreibung = f"nur Ausgaben des Jahres {args.year}"

    return ausgabenfilter, filterbeschreibung


def _aktualisiere_dokumentdatum(dokument_id, neues_datum):
    """Schreibt das neue Datum via API; True bei Erfolg."""
    try:
        url = f"{PAPERLESS_URL}/api/documents/{dokument_id}/"
        api_patch(url, {"created": neues_datum})
        return True
    except requests.RequestException as exc:
        print(
            f"  FEHLER bei Dokument {dokument_id}: {exc}",
            file=sys.stderr,
        )
        return False


def verarbeite_dokument(
    dokument,
    ausgabenfilter,
    csv_mapping,
    apply_changes,
):
    """
    Verarbeitet ein Dokument.

    Rückgabe: Statuscode
    (ohne_praefix | ausserhalb_filter | ohne_csv |
     unveraendert | geaendert | geplant | fehler)
    """
    dokument_id = dokument["id"]
    titel = dokument.get("title") or ""
    altes_datum = dokument.get("created")
    ausgabe = ermittle_ausgabe_aus_titel(titel)
    status = "ohne_praefix"

    if ausgabe is None:
        status = "ohne_praefix"
    elif ausgabe not in ausgabenfilter:
        status = "ausserhalb_filter"
    else:
        neues_datum = csv_mapping.get(ausgabe)
        if neues_datum is None:
            print(f"KEIN CSV-EINTRAG: ID {dokument_id}: {titel}")
            status = "ohne_csv"
        elif altes_datum == neues_datum:
            print(
                f"UNVERÄNDERT: ID {dokument_id}: {titel}"
                f" ({altes_datum})"
            )
            status = "unveraendert"
        else:
            aktion = "ÄNDERE" if apply_changes else "WÜRDE ÄNDERN"
            print(f"{aktion}: ID {dokument_id}: {titel}")
            print(f"  Datum: {altes_datum} → {neues_datum}")
            if not apply_changes:
                status = "geplant"
            elif _aktualisiere_dokumentdatum(dokument_id, neues_datum):
                status = "geaendert"
            else:
                status = "fehler"

    return status


def drucke_zusammenfassung(zaehler):
    """Gibt die Laufstatistik aus."""
    print()
    print("Zusammenfassung:")
    print(f"  Geplant:                  {zaehler['geplant']}")
    print(f"  Tatsächlich geändert:     {zaehler['geaendert']}")
    print(f"  Bereits korrekt:          {zaehler['unveraendert']}")
    print(f"  Ohne passendes Präfix:    {zaehler['ohne_praefix']}")
    print(f"  Außerhalb des Filters:    {zaehler['ausserhalb_filter']}")
    print(f"  Ohne CSV-Eintrag:         {zaehler['ohne_csv']}")
    print(f"  Fehler:                   {zaehler['fehler']}")


def main():
    """Aktualisiert Paperless-Dokumentdaten gemäß CSV und Filtern."""
    args = parse_arguments()
    csv_mapping = lese_csv()
    ausgabenfilter, filterbeschreibung = ermittle_ausgabenfilter(
        args,
        csv_mapping,
    )

    # Nur die für den Lauf relevanten CSV-Einträge behalten
    csv_mapping = {
        schluessel: datum
        for schluessel, datum in csv_mapping.items()
        if schluessel in ausgabenfilter
    }

    if not csv_mapping:
        raise RuntimeError(
            f"Keine passenden CSV-Einträge für {filterbeschreibung} gefunden."
        )

    correspondent = finde_korrespondent(KORRESPONDENT_NAME)
    correspondent_id = correspondent["id"]
    dokumente = lese_alle_dokumente(correspondent_id)

    print(f"Korrespondent: {correspondent['name']}")
    print(f"Korrespondenten-ID: {correspondent_id}")
    print(f"Filter: {filterbeschreibung}")
    print(f"Modus: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Gefundene Dokumente des Korrespondenten: {len(dokumente)}")
    print()

    zaehler = {
        "geplant": 0,
        "geaendert": 0,
        "unveraendert": 0,
        "ohne_praefix": 0,
        "ausserhalb_filter": 0,
        "ohne_csv": 0,
        "fehler": 0,
    }

    for dokument in dokumente:
        status = verarbeite_dokument(
            dokument,
            ausgabenfilter,
            csv_mapping,
            args.apply,
        )
        zaehler[status] += 1

    drucke_zusammenfassung(zaehler)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"FEHLER: {exc}", file=sys.stderr)
        sys.exit(1)
