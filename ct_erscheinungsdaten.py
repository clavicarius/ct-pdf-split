#!/usr/bin/env python3
"""
Recherchiert offizielle Erscheinungsdaten regulärer c't-Ausgaben
ausschließlich über Heise-/c't-Vorschauseiten unter heise.de/select/ct/.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.heise.de/select/ct/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CSV_COLUMNS = ["jahr", "ausgabe", "datum", "titel", "zeitschrift", "quelle"]
MISSING = "FEHLT"
JAHRSRUECKBLICK = "Jahresrückblick"

MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

SPECIAL_ISSUE_RE = re.compile(
    r"\b("
    r"extra|admin|linux|security|android|projekte|"
    r"e-?dossier|special|magazin\s+special|best\s+of"
    r")\b",
    re.IGNORECASE,
)
ARCHIVE_ISSUE_RE = re.compile(
    r"c['’]?t\s+(\d{1,2})\s*/\s*(\d{4})",
    re.IGNORECASE,
)
ARCHIVE_JR_RE = re.compile(
    r"c['’]?t\s+jahresrückblick\s+(\d{4})",
    re.IGNORECASE,
)
ISSUE_NUM_YEAR_RE = re.compile(
    r"(?:c['’]?t\s*)?(\d{1,2})\s*/\s*(\d{4})",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"Ab\s+(\d{1,2})\.\s*"
    r"(Januar|Februar|März|Maerz|April|Mai|Juni|Juli|"
    r"August|September|Oktober|November|Dezember)"
    r"(?:\s+(\d{4}))?\s+im\s+Handel",
    re.IGNORECASE | re.DOTALL,
)

logger = logging.getLogger("ct_erscheinungsdaten")


@dataclass
class IssueRef:
    year: int
    path_number: int
    is_jahresrueckblick: bool = False
    title: str = ""
    content_url: str = ""
    preview_url: Optional[str] = None
    sort_key: tuple = field(default_factory=tuple)

    @property
    def ausgabe_field(self) -> str:
        if self.is_jahresrueckblick:
            return JAHRSRUECKBLICK
        return f"{self.path_number:02d}"

    @property
    def display_title(self) -> str:
        if self.title:
            return self.title
        if self.is_jahresrueckblick:
            return f"c't {JAHRSRUECKBLICK} {self.year}"
        return f"c't {self.path_number}/{self.year}"


@dataclass
class ResearchRecord:
    jahr: int
    ausgabe: str
    datum: str
    titel: str
    zeitschrift: str
    quelle: str
    inhaltsseite: str = ""
    vorschauseite: Optional[str] = None
    vorschautext: str = ""
    validierungsstatus: str = "ok"
    fehlermeldungen: list[str] = field(default_factory=list)
    recherchiert_am: str = ""

    def csv_row(self) -> dict[str, str]:
        return {
            "jahr": str(self.jahr),
            "ausgabe": self.ausgabe,
            "datum": self.datum or MISSING,
            "titel": self.titel,
            "zeitschrift": self.zeitschrift,
            "quelle": self.quelle,
        }


class HttpClient:
    def __init__(
        self,
        cache_dir: Path,
        delay: float,
        refresh: bool,
        timeout: float = 30.0,
        max_retries: int = 4,
    ) -> None:
        self.cache_dir = cache_dir
        self.delay = max(0.0, delay)
        self.refresh = refresh
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            }
        )
        self._last_request_at = 0.0

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _throttle(self) -> None:
        if self.delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.delay - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def fetch_html(self, url: str) -> Optional[str]:
        cache_path = self._cache_path(url)
        if not self.refresh and cache_path.is_file():
            logger.debug("Cache-Treffer: %s", url)
            return cache_path.read_text(encoding="utf-8", errors="replace")

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle()
            try:
                logger.debug("GET %s (Versuch %s)", url, attempt)
                response = self.session.get(
                    url,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                self._last_request_at = time.monotonic()

                if response.status_code == 404:
                    logger.info("Seite nicht gefunden (404): %s", url)
                    return None

                if response.status_code == 429 or 500 <= response.status_code < 600:
                    wait = min(30.0, self.delay * attempt * 2 + 1.0)
                    logger.warning(
                        "HTTP %s für %s – warte %.1fs",
                        response.status_code,
                        url,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                if not response.content:
                    logger.warning("Leere Antwort: %s", url)
                    return None

                text = response.text
                if not text.strip():
                    logger.warning("Leerer HTML-Inhalt: %s", url)
                    return None

                cache_path.write_text(text, encoding="utf-8")
                return text
            except requests.RequestException as exc:
                last_error = exc
                wait = min(30.0, self.delay * attempt * 2 + 1.0)
                logger.warning(
                    "Request-Fehler für %s (%s) – warte %.1fs",
                    url,
                    exc,
                    wait,
                )
                time.sleep(wait)

        logger.error("Abruf fehlgeschlagen nach Retries: %s (%s)", url, last_error)
        return None


def make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def normalize_url(href: str, base: str = BASE_URL) -> str:
    absolute = urljoin(base, href.strip())
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"}:
        return absolute
    # Trailing slash vereinheitlichen nur für Inhaltsseiten ohne Artikel-ID
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def is_official_select_ct_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc not in {"www.heise.de", "heise.de"}:
        return False
    return parsed.path.startswith("/select/ct/")


def issue_content_url(year: int, path_number: int) -> str:
    return normalize_url(f"{BASE_URL}{year}/{path_number}")


def archive_url(year: int) -> str:
    return normalize_url(f"{BASE_URL}archiv/{year}")


def visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def parse_archive_issues(html: str, year: int) -> list[IssueRef]:
    soup = make_soup(html)
    found: dict[tuple[int, bool], IssueRef] = {}

    for anchor in soup.find_all("a", href=True):
        raw_href = anchor["href"].strip()
        href = normalize_url(raw_href)
        if not is_official_select_ct_url(href):
            continue

        parsed = urlparse(href)
        # Archivlistet unter /select/ct/archiv/{jahr}/{nr}, Inhaltsseite unter /select/ct/{jahr}/{nr}
        path_match = re.match(
            rf"^/select/ct/(?:archiv/)?{year}/(\d{{1,2}})(?:/|$)",
            parsed.path,
            re.IGNORECASE,
        )
        if not path_match:
            continue

        path_number = int(path_match.group(1))
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if not label:
            continue
        if SPECIAL_ISSUE_RE.search(label):
            logger.debug("Spezialheft übersprungen: %s", label)
            continue

        is_jr = bool(ARCHIVE_JR_RE.search(label)) or (
            "jahresrückblick" in label.lower()
        )
        regular = ARCHIVE_ISSUE_RE.search(label)

        if is_jr:
            title = f"c't {JAHRSRUECKBLICK} {year}"
            key = (path_number, True)
            issue = IssueRef(
                year=year,
                path_number=path_number,
                is_jahresrueckblick=True,
                title=title,
                content_url=issue_content_url(year, path_number),
                sort_key=(year, path_number, 1),
            )
            found[key] = issue
            continue

        if regular:
            issue_no = int(regular.group(1))
            issue_year = int(regular.group(2))
            if issue_year != year:
                logger.debug(
                    "Archiv-Link stimmt nicht mit Jahr überein: %s -> %s",
                    label,
                    href,
                )
                continue
            # Pfadnummer kann bei Sonderheften abweichen; bei regulären Ausgaben erwarten wir Gleichheit
            if issue_no != path_number:
                logger.debug(
                    "Archiv-Pfadnummer weicht vom Label ab: %s -> %s",
                    label,
                    href,
                )
                continue
            title = f"c't {issue_no}/{year}"
            key = (path_number, False)
            found[key] = IssueRef(
                year=year,
                path_number=path_number,
                is_jahresrueckblick=False,
                title=title,
                content_url=issue_content_url(year, path_number),
                sort_key=(year, path_number, 0),
            )

    issues = sorted(found.values(), key=lambda i: i.sort_key)
    logger.info("Archiv %s: %s Ausgaben erkannt", year, len(issues))
    return issues


def extract_preview_link(soup: BeautifulSoup, page_url: str) -> Optional[str]:
    for anchor in soup.select("a.toc__url, a[href]"):
        title_node = anchor.select_one(".toc__title")
        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else anchor.get_text(" ", strip=True)
        )
        title_norm = re.sub(r"\s+", " ", title).strip()
        if not re.match(r"^vorschau\b", title_norm, re.IGNORECASE):
            continue
        href = anchor.get("href")
        if not href:
            continue
        absolute = normalize_url(href, page_url)
        if is_official_select_ct_url(absolute):
            return absolute

    # Fallback: Textknoten „Vorschau“ in Rubriken
    for node in soup.find_all(string=re.compile(r"^\s*Vorschau\b", re.I)):
        parent = node.find_parent("a", href=True)
        if parent is None:
            continue
        absolute = normalize_url(parent["href"], page_url)
        if is_official_select_ct_url(absolute):
            return absolute
    return None


def parse_issue_identity(
    html: str,
    expected_year: int,
    expected_number: int,
    expect_jahresrueckblick: bool,
) -> tuple[bool, str]:
    soup = make_soup(html)
    heading = ""
    h1 = soup.find("h1")
    if h1:
        heading = re.sub(r"\s+", " ", h1.get_text(" ", strip=True))
    title_tag = soup.find("title")
    page_title = (
        re.sub(r"\s+", " ", title_tag.get_text(" ", strip=True))
        if title_tag
        else ""
    )
    blob = f"{heading} {page_title}"

    if expect_jahresrueckblick:
        if "jahresrückblick" not in blob.lower():
            return False, heading or page_title
        year_match = re.search(rf"\b{expected_year}\b", blob)
        return bool(year_match), heading or page_title or (
            f"c't {JAHRSRUECKBLICK} {expected_year}"
        )

    match = ISSUE_NUM_YEAR_RE.search(blob)
    if not match:
        # Manche Seiten: „Inhalt 2/2019“ ohne c't im H1
        match = re.search(
            rf"\b({expected_number})\s*/\s*({expected_year})\b",
            blob,
        )
        if not match:
            return False, heading or page_title
        return True, f"c't {expected_number}/{expected_year}"

    number = int(match.group(1))
    year = int(match.group(2))
    if number != expected_number or year != expected_year:
        return False, heading or page_title
    return True, f"c't {number}/{year}"


def parse_issue_page(
    html: str,
    url: str,
    expected_year: int,
    expected_number: int,
    expect_jahresrueckblick: bool = False,
) -> Optional[IssueRef]:
    ok, title = parse_issue_identity(
        html,
        expected_year,
        expected_number,
        expect_jahresrueckblick,
    )
    if not ok:
        logger.warning(
            "Inhaltsseite passt nicht zu %s/%s: %s (%s)",
            expected_number,
            expected_year,
            title,
            url,
        )
        return None

    soup = make_soup(html)
    preview = extract_preview_link(soup, url)
    return IssueRef(
        year=expected_year,
        path_number=expected_number,
        is_jahresrueckblick=expect_jahresrueckblick,
        title=(
            f"c't {JAHRSRUECKBLICK} {expected_year}"
            if expect_jahresrueckblick
            else title
        ),
        content_url=normalize_url(url),
        preview_url=preview,
        sort_key=(
            expected_year,
            expected_number,
            1 if expect_jahresrueckblick else 0,
        ),
    )


def discover_issues(
    client: HttpClient,
    year: int,
    *,
    only_last: bool = False,
) -> list[IssueRef]:
    html = client.fetch_html(archive_url(year))
    if not html:
        logger.error("Archivseite nicht erreichbar: %s", year)
        return []

    candidates = parse_archive_issues(html, year)
    if only_last and candidates:
        candidates = [max(candidates, key=lambda i: i.sort_key)]

    validated: list[IssueRef] = []
    for candidate in candidates:
        page_html = client.fetch_html(candidate.content_url)
        if not page_html:
            logger.warning(
                "Inhaltsseite fehlt, Ausgabe wird übersprungen: %s",
                candidate.content_url,
            )
            continue
        parsed = parse_issue_page(
            page_html,
            candidate.content_url,
            candidate.year,
            candidate.path_number,
            candidate.is_jahresrueckblick,
        )
        if parsed is None:
            continue
        # Titel aus Archiv bevorzugen, wenn Jahresrückblick
        if candidate.is_jahresrueckblick:
            parsed.title = candidate.title or parsed.title
        validated.append(parsed)

    validated.sort(key=lambda i: i.sort_key)
    return validated


def extract_preview_target(
    text: str,
) -> tuple[Optional[int], Optional[int], bool]:
    """Gibt (ausgabe, jahr, is_jahresrueckblick) zurück."""
    lowered = text.lower()
    is_jr = "jahresrückblick" in lowered
    # Explizite Form „Vorschau 3/2019“ / „Vorschau c't 3/2019“
    m = re.search(
        r"vorschau\s*(?:c['’]?t\s*)?(?:jahresrückblick\s*)?"
        r"(?:(\d{1,2})\s*/\s*)?(\d{4})?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None, is_jr
    number = int(m.group(1)) if m.group(1) else None
    year = int(m.group(2)) if m.group(2) else None
    return number, year, is_jr


def parse_preview_date(
    html: str,
    target: IssueRef,
) -> tuple[str, str, list[str]]:
    """
    Extrahiert Datum und Vorschautext.
    Rückgabe: (datum_oder_FEHLT, vorschautext, fehler)
    """
    errors: list[str] = []
    soup = make_soup(html)
    text = visible_text(soup)

    preview_snippet = ""
    date_match = DATE_RE.search(text)
    if date_match:
        start = max(0, date_match.start() - 80)
        end = min(len(text), date_match.end() + 40)
        preview_snippet = text[start:end].strip()
    else:
        # Kontext um „Vorschau“
        m = re.search(r"Vorschau.{0,120}", text, re.IGNORECASE)
        preview_snippet = m.group(0).strip() if m else text[:200]

    target_num, target_year, target_is_jr = extract_preview_target(text)
    belongs = False
    if target.is_jahresrueckblick:
        belongs = target_is_jr or (
            target_year == target.year and target_num is None and "jahresrückblick" in text.lower()
        )
        # Manche Vorschauen: „Vorschau Jahresrückblick 2022“
        if not belongs and target_year == target.year and target_is_jr:
            belongs = True
        if not belongs and re.search(
            rf"jahresrückblick\s*{target.year}",
            text,
            re.IGNORECASE,
        ):
            belongs = True
    else:
        if target_num == target.path_number and (
            target_year is None or target_year == target.year
        ):
            belongs = True
        elif target_num == target.path_number and target_year == target.year:
            belongs = True

    if not belongs:
        # Zusätzlicher Check: Überschrift enthält Zielausgabe
        h1 = soup.find("h1")
        h1_text = h1.get_text(" ", strip=True) if h1 else ""
        if target.is_jahresrueckblick:
            belongs = "jahresrückblick" in h1_text.lower() and str(target.year) in h1_text
        else:
            if re.search(
                rf"\b{target.path_number}\s*/\s*{target.year}\b",
                h1_text,
            ):
                belongs = True

    if not belongs:
        errors.append(
            "Vorschauseite gehört nicht eindeutig zur Zielausgabe "
            f"{target.ausgabe_field}/{target.year}"
        )
        return MISSING, preview_snippet, errors

    if not date_match:
        errors.append("Kein ausdrückliches Erscheinungsdatum gefunden")
        return MISSING, preview_snippet, errors

    day = int(date_match.group(1))
    month_key = date_match.group(2).lower()
    month = MONTHS.get(month_key) or MONTHS.get(month_key.replace("ä", "ae"))
    year_in_text = date_match.group(3)

    if month is None:
        errors.append(f"Unbekannter Monat: {date_match.group(2)}")
        return MISSING, preview_snippet, errors

    if year_in_text:
        year = int(year_in_text)
    else:
        # Jahr nur aus eindeutig ermittelter Zielausgabe ableiten.
        # Frühe Hefte eines Jahrgangs erscheinen oft noch im Dezember des Vorjahres
        # („Ab 18. Dezember …“ bei 1/2022 → 2021-12-18,
        #  „Ab 31. Dezember …“ bei 2/2023 → 2022-12-31).
        # Späte Hefte mit Dezember-Datum (z. B. 26/2022) gehören zum Zieljahr.
        year = target.year
        if (
            not target.is_jahresrueckblick
            and month == 12
            and target.path_number <= 2
        ):
            year = target.year - 1

    try:
        parsed = date(year, month, day)
    except ValueError:
        errors.append(
            f"Ungültiges Kalenderdatum: {year:04d}-{month:02d}-{day:02d}"
        )
        return MISSING, preview_snippet, errors

    return parsed.isoformat(), preview_snippet, errors


def resolve_release_date(
    client: HttpClient,
    previous: Optional[IssueRef],
    target: IssueRef,
) -> ResearchRecord:
    now = datetime.now(timezone.utc).isoformat()
    record = ResearchRecord(
        jahr=target.year,
        ausgabe=target.ausgabe_field,
        datum=MISSING,
        titel=target.display_title,
        zeitschrift="c't",
        quelle=previous.content_url if previous else target.content_url,
        inhaltsseite=previous.content_url if previous else "",
        recherchiert_am=now,
    )

    if previous is None:
        record.validierungsstatus = "fehlt"
        record.fehlermeldungen.append(
            "Keine vorherige Ausgabe als Vorschauquelle gefunden"
        )
        record.quelle = target.content_url
        return record

    # Vorschau-Link ggf. nachladen
    preview_url = previous.preview_url
    if not preview_url:
        page_html = client.fetch_html(previous.content_url)
        if page_html:
            refreshed = parse_issue_page(
                page_html,
                previous.content_url,
                previous.year,
                previous.path_number,
                previous.is_jahresrueckblick,
            )
            if refreshed and refreshed.preview_url:
                preview_url = refreshed.preview_url
                previous.preview_url = preview_url

    if not preview_url:
        record.validierungsstatus = "fehlt"
        record.fehlermeldungen.append(
            "Vorschau-Link auf Inhaltsseite der vorherigen Ausgabe nicht gefunden"
        )
        record.quelle = previous.content_url
        logger.warning(
            "Kein Vorschau-Link für Vorgänger %s/%s",
            previous.path_number,
            previous.year,
        )
        return record

    if not is_official_select_ct_url(preview_url):
        record.validierungsstatus = "fehlt"
        record.fehlermeldungen.append("Vorschau-URL ist nicht offiziell")
        record.quelle = previous.content_url
        return record

    record.vorschauseite = preview_url
    record.quelle = preview_url

    preview_html = client.fetch_html(preview_url)
    if not preview_html:
        record.validierungsstatus = "fehlt"
        record.fehlermeldungen.append("Vorschauseite nicht erreichbar")
        record.quelle = previous.content_url
        return record

    datum, snippet, errors = parse_preview_date(preview_html, target)
    record.vorschautext = snippet
    record.fehlermeldungen.extend(errors)
    record.datum = datum
    record.validierungsstatus = "ok" if datum != MISSING else "fehlt"
    if datum == MISSING:
        logger.warning(
            "Datum FEHLT für %s/%s: %s",
            target.ausgabe_field,
            target.year,
            "; ".join(errors) or "unbekannt",
        )
    else:
        logger.info(
            "Datum %s für %s/%s aus %s",
            datum,
            target.ausgabe_field,
            target.year,
            preview_url,
        )
    return record


def validate_sequence(records: list[ResearchRecord]) -> None:
    """Setzt Datum auf FEHLT, wenn chronologische Reihenfolge verletzt wird."""
    previous_date: Optional[date] = None
    previous_label = ""
    for record in records:
        if record.datum == MISSING:
            continue
        try:
            current = date.fromisoformat(record.datum)
        except ValueError:
            record.fehlermeldungen.append("Datum ist kein gültiges ISO-Datum")
            record.datum = MISSING
            record.validierungsstatus = "fehlt"
            continue

        if previous_date is not None and current < previous_date:
            msg = (
                f"Datum {record.datum} liegt vor vorherigem Datum "
                f"{previous_date.isoformat()} ({previous_label})"
            )
            logger.warning("%s/%s: %s", record.ausgabe, record.jahr, msg)
            record.fehlermeldungen.append(msg)
            record.datum = MISSING
            record.validierungsstatus = "fehlt"
            continue

        previous_date = current
        previous_label = f"{record.ausgabe}/{record.jahr}"


def dedupe_issues(issues: list[IssueRef]) -> list[IssueRef]:
    seen: set[tuple[int, str]] = set()
    result: list[IssueRef] = []
    for issue in issues:
        key = (issue.year, issue.ausgabe_field)
        if key in seen:
            logger.warning("Doppelte Ausgabe übersprungen: %s", key)
            continue
        seen.add(key)
        result.append(issue)
    return result


def collect_records(
    client: HttpClient,
    start_year: int,
    end_year: int,
) -> list[ResearchRecord]:
    by_year: dict[int, list[IssueRef]] = {}

    # Vorjahr: nur letzte Ausgabe als Vorschauquelle für Heft 01
    prev_year_issues = discover_issues(client, start_year - 1, only_last=True)
    by_year[start_year - 1] = dedupe_issues(prev_year_issues)

    for year in range(start_year, end_year + 1):
        by_year[year] = dedupe_issues(discover_issues(client, year))

    chronological: list[IssueRef] = []
    for year in range(start_year - 1, end_year + 1):
        chronological.extend(by_year.get(year, []))

    # Index für Vorgänger
    index_by_key = {
        (issue.year, issue.ausgabe_field): idx
        for idx, issue in enumerate(chronological)
    }

    records: list[ResearchRecord] = []
    for year in range(start_year, end_year + 1):
        for issue in by_year.get(year, []):
            idx = index_by_key[(issue.year, issue.ausgabe_field)]
            previous = chronological[idx - 1] if idx > 0 else None
            # Für Ausgabe 01: Vorgänger muss Vorjahr sein (letzte Ausgabe)
            if (
                not issue.is_jahresrueckblick
                and issue.path_number == 1
                and previous is not None
                and previous.year != year - 1
            ):
                # Fallback: letzte Ausgabe des Vorjahres
                prev_list = by_year.get(year - 1, [])
                previous = prev_list[-1] if prev_list else None

            record = resolve_release_date(client, previous, issue)
            if previous:
                record.inhaltsseite = previous.content_url
            records.append(record)

    records.sort(
        key=lambda r: (
            r.jahr,
            10_000 if r.ausgabe == JAHRSRUECKBLICK else int(r.ausgabe),
            0 if r.ausgabe != JAHRSRUECKBLICK else 1,
        )
    )
    # Stabile Sortierung: Jahresrückblick an Archiv-/Pfadposition belassen,
    # daher erneut über chronologische Reihenfolge der Issues sortieren.
    order = {
        (i.year, i.ausgabe_field): n
        for n, i in enumerate(
            [x for y in range(start_year, end_year + 1) for x in by_year.get(y, [])]
        )
    }
    records.sort(key=lambda r: order.get((r.jahr, r.ausgabe), 10**9))

    validate_sequence(records)
    return records


def write_csv(path: Path, records: list[ResearchRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_COLUMNS,
            delimiter=";",
            lineterminator="\n",
        )
        writer.writeheader()
        for record in records:
            row = record.csv_row()
            for key in CSV_COLUMNS:
                if not row.get(key):
                    row[key] = MISSING if key == "datum" else ""
            writer.writerow(row)
    logger.info("CSV geschrieben: %s (%s Zeilen)", path, len(records))


def write_report(path: Path, records: list[ResearchRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: list[dict[str, Any]] = []
    for record in records:
        payload.append(
            {
                "jahr": record.jahr,
                "ausgabe": record.ausgabe,
                "titel": record.titel,
                "datum": record.datum,
                "quelle": record.quelle,
                "gepruefte_inhaltsseite": record.inhaltsseite,
                "gefundene_vorschauseite": record.vorschauseite,
                "extrahierter_vorschautext": record.vorschautext,
                "extrahiertes_datum": record.datum,
                "validierungsstatus": record.validierungsstatus,
                "fehlermeldungen": record.fehlermeldungen,
                "zeitstempel_recherche": record.recherchiert_am,
            }
        )
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Report geschrieben: %s", path)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Recherchiert offizielle c't-Erscheinungsdaten "
            "über Heise-Vorschauseiten."
        )
    )
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ct-zeitschriften-daten.csv"),
        help="CSV-Zieldatei (Standard: ct-zeitschriften-daten.csv)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.25,
        help="Pause zwischen HTTP-Requests in Sekunden",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Cache ignorieren und Seiten neu laden",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Ausführliches Logging",
    )
    parser.add_argument(
        "--report",
        nargs="?",
        const="__DEFAULT__",
        default=None,
        help=(
            "Optionale JSON-Prüfdatei. Ohne Pfad: "
            "ct_ausgaben_<start>_<end>_report.json"
        ),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("cache"),
        help="Verzeichnis für HTML-Cache",
    )
    return parser


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    if args.start_year > args.end_year:
        parser.error("--start-year darf nicht größer als --end-year sein")
    if args.start_year < 1980 or args.end_year > 2100:
        parser.error("Jahresbereich ungültig")

    output = args.output
    report_path: Optional[Path] = None
    if args.report is not None:
        if args.report == "__DEFAULT__":
            report_path = Path(
                f"ct_ausgaben_{args.start_year}_{args.end_year}_report.json"
            )
        else:
            report_path = Path(args.report)

    client = HttpClient(
        cache_dir=args.cache_dir,
        delay=args.delay,
        refresh=args.refresh,
    )

    logger.info(
        "Starte Recherche %s–%s (Delay=%.2fs, refresh=%s)",
        args.start_year,
        args.end_year,
        args.delay,
        args.refresh,
    )

    try:
        records = collect_records(client, args.start_year, args.end_year)
    except KeyboardInterrupt:
        logger.error("Abgebrochen.")
        return 130

    if not records:
        logger.error("Keine Ausgaben gefunden.")
        return 1

    write_csv(output, records)
    if report_path is not None:
        write_report(report_path, records)

    missing = sum(1 for r in records if r.datum == MISSING)
    logger.info(
        "Fertig: %s Ausgaben, davon %s mit datum=%s",
        len(records),
        missing,
        MISSING,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
