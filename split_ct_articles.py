#!/usr/bin/env python3
"""Split c't magazine PDFs into one PDF per article for Paperless-ngx."""

from __future__ import annotations

import argparse
import csv
import datetime
import html
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import Destination

ISSUE_NAME_RE = re.compile(r"ct\s*[-_]?(\d{4})\s*[-_]?(\d{1,2})", re.I)
HEFT_TEXT_RE = re.compile(
    r"c[\W_]*t\s*20(\d{2}).{0,40}Heft\s*(\d{1,2})",
    re.I | re.S,
)
SHOP_SECTION_RE = re.compile(
    r'<h4 class="custom-pd-rubriken">(.*?)</h4>\s*<ul>(.*?)</ul>',
    re.I | re.S,
)
SHOP_ITEM_RE = re.compile(
    r'class="custom-pd-seite">\s*(\d+)\s*</span>\s*'
    r'<span class="custom-pd-titel">(.*?)</span>'
    r'(?:\s*<span class="custom-pd-untertitel">(.*?)</span>)?',
    re.I | re.S,
)
LOCAL_TOC_RE = re.compile(r"^\s*(\d{1,3})\s+(.{6,120}?)\s*$")
PAGE_NUM_RE = re.compile(r"\b(\d{1,3})\b")
INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
SKIP_NAME_RE = re.compile(r"to-proceed", re.I)


@dataclass
class TocEntry:
    """An article entry from a table of contents."""

    title: str
    printed_page: int
    section: str = ""
    source: str = ""


@dataclass
class SplitRange:
    """A range of PDF pages assigned to one article."""

    title: str
    section: str
    printed_start: int
    pdf_start: int
    pdf_end: int
    source: str

    @property
    def page_count(self) -> int:
        """Return the number of PDF pages in this range."""
        return self.pdf_end - self.pdf_start + 1


def strip_tags(value: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def sanitize_filename(name: str, max_len: int = 110) -> str:
    """Return a filesystem-safe version of a title."""
    name = unicodedata.normalize("NFC", name)
    name = INVALID_FS.sub(" ", name)
    name = name.replace("…", "...")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "artikel"


def parse_issue_from_name(path: Path) -> tuple[int | None, int | None]:
    """Extract the year and issue number from a PDF filename."""
    match = ISSUE_NAME_RE.search(path.name)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_issue_from_text(text: str) -> tuple[int | None, int | None]:
    """Extract the year and issue number from PDF text."""
    match = HEFT_TEXT_RE.search(text[:8000])
    if not match:
        return None, None
    return 2000 + int(match.group(1)), int(match.group(2))


def extract_page_text(reader: PdfReader, index: int) -> str:
    """Extract text from one page, returning an empty string on read errors."""
    try:
        return reader.pages[index].extract_text() or ""
    except Exception:  # pylint: disable=broad-exception-caught
        return ""


def outline_entries(reader: PdfReader) -> list[TocEntry]:
    """Convert PDF bookmarks into table-of-contents entries."""
    try:
        outline = reader.outline
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    if not outline:
        return []

    entries: list[TocEntry] = []

    def walk(items) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item)
                continue
            title = strip_tags(getattr(item, "title", "") or str(item))
            page_no = None
            try:
                if isinstance(item, Destination):
                    page_no = reader.get_destination_page_number(item)
            except Exception:  # pylint: disable=broad-exception-caught
                page_no = None
            if page_no is None or not title:
                continue
            entries.append(
                TocEntry(
                    title=title,
                    printed_page=page_no + 1,
                    section="Bookmark",
                    source="bookmarks",
                )
            )

    walk(outline)
    return entries


def fetch_heise_toc(year: int, issue: int) -> list[TocEntry]:
    """Fetch and parse the table of contents from the Heise shop."""
    url = f"https://shop.heise.de/ct-{issue:02d}-{year}/pdf"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    html_text = raw.decode("utf-8", "replace")
    entries: list[TocEntry] = []
    for section_match in SHOP_SECTION_RE.finditer(html_text):
        section = strip_tags(section_match.group(1))
        for item in SHOP_ITEM_RE.finditer(section_match.group(2)):
            title = strip_tags(item.group(2))
            subtitle = strip_tags(item.group(3) or "")
            full = f"{title} {subtitle}".strip() if subtitle else title
            entries.append(
                TocEntry(
                    title=full,
                    printed_page=int(item.group(1)),
                    section=section,
                    source="heise",
                )
            )
    if len(entries) < 8:
        raise ValueError(f"Heise-TOC zu kurz ({len(entries)} Einträge) von {url}")
    return entries


def parse_local_toc(page_texts: list[str], toc_pages: list[int] | None) -> list[TocEntry]:
    """Extract table-of-contents entries from local page text."""
    if toc_pages:
        indices = [p - 1 for p in toc_pages if 0 < p <= len(page_texts)]
    else:
        indices = []
        for i, text in enumerate(page_texts[:16]):
            head = text[:400].lower()
            if "inhalt" in head or "titelthemen" in head or "immer in c" in head:
                indices.append(i)
        if not indices:
            indices = list(range(min(8, len(page_texts))))

    blob_lines: list[str] = []
    for i in indices:
        blob_lines.extend(page_texts[i].splitlines())

    skip_titles = {
        "titelthemen",
        "aktuell",
        "test & beratung",
        "wissen",
        "praxis",
        "immer in ct",
        "immer in c't",
        "inhalt",
    }
    entries: list[TocEntry] = []
    seen: set[tuple[int, str]] = set()
    for line in blob_lines:
        line = re.sub(r"[.·•…]{2,}", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        match = LOCAL_TOC_RE.match(line)
        if not match:
            continue
        page = int(match.group(1))
        title = match.group(2).strip(" .-")
        if page < 1 or page > 220:
            continue
        if title.lower() in skip_titles:
            continue
        if sum(ch.isalpha() for ch in title) < 6:
            continue
        key = (page, title.lower())
        if key in seen:
            continue
        seen.add(key)
        entries.append(TocEntry(title=title, printed_page=page, source="local-toc"))
    entries.sort(key=lambda e: e.printed_page)
    return entries


def guess_printed_page(
    text: str, pdf_index: int, year: int | None, issue: int | None
) -> int | None:
    """Guess a printed page number from extracted text."""
    expected = pdf_index + 1
    snippet = f"{text[:160]}\n{text[-350:]}"
    snippet = snippet.replace("l", "1").replace("I", "1").replace("O", "0")
    banned = {year or -1, (year or 0) % 100, issue or -1, 0}
    candidates: list[int] = []
    for match in PAGE_NUM_RE.finditer(snippet):
        value = int(match.group(1))
        if value in banned or value > 220:
            continue
        candidates.append(value)
    if expected in candidates:
        return expected
    close = [n for n in candidates if abs(n - expected) <= 6]
    if close:
        return min(close, key=lambda n: abs(n - expected))
    return None


def build_printed_map(
    page_texts: list[str],
    year: int | None,
    issue: int | None,
    offset: int | None,
) -> list[int]:
    """Map PDF page indexes to printed page numbers."""
    count = len(page_texts)
    mapping = [None] * count
    if offset is not None:
        return [i + 1 + offset for i in range(count)]

    for i, text in enumerate(page_texts):
        mapping[i] = guess_printed_page(text, i, year, issue)

    last = 0
    filled: list[int] = []
    for i, value in enumerate(mapping):
        expected = i + 1
        if value is None:
            value = last + 1 if last else expected
        if last and value < last:
            value = last
        if value > last + 4 and abs(expected - (last + 1)) <= 2:
            value = last + 1
        filled.append(value)
        last = value
    return filled


def printed_to_pdf(printed: int, printed_map: list[int]) -> int:
    """Find the PDF index corresponding to a printed page number."""
    exact = [index for index, value in enumerate(printed_map) if value == printed]
    if exact:
        identity = printed - 1
        return min(exact, key=lambda index: abs(index - identity))
    identity = printed - 1
    if 0 <= identity < len(printed_map):
        mapped = printed_map[identity]
        if abs(mapped - printed) <= 2:
            return identity
    for index, value in enumerate(printed_map):
        if value >= printed:
            return index
    return len(printed_map) - 1


def max_pages_for(item: SplitRange) -> int | None:
    """Return a maximum length for known short articles."""
    title = item.title.lower()
    section = (item.section or "").lower()
    one_page = (
        "standpunkt",
        "schlagseite",
        "impressum",
        "vorschau",
        "inserenten",
    )
    if any(key in title for key in one_page):
        return 1
    if any(key in title for key in ("leserforum", "tipps")) or (
        "vorsicht" in title and "kunde" in title
    ):
        return 3
    if "story" in title:
        return None
    if section.startswith("aktuell"):
        return 3
    return None


def _is_end_matter(title: str) -> bool:
    """Return whether a title names an issue's end matter."""
    lowered = title.lower()
    return any(token in lowered for token in ("inserenten", "impressum", "vorschau"))


def cap_and_dedupe(ranges: list[SplitRange], page_count: int) -> list[SplitRange]:
    """Cap article lengths and remove overlapping page assignments."""
    capped: list[SplitRange] = []
    for item in ranges:
        limit = max_pages_for(item)
        if limit is not None:
            item.pdf_end = min(item.pdf_end, item.pdf_start + limit - 1)
        capped.append(item)

    tail = [item for item in capped if _is_end_matter(item.title)]
    body = [item for item in capped if item not in tail]
    if tail:
        page = page_count - 1
        for item in reversed(tail):
            item.pdf_start = max(0, page)
            item.pdf_end = item.pdf_start
            page -= 1
        first_tail = min(item.pdf_start for item in tail)
        for item in body:
            if item.pdf_end >= first_tail:
                item.pdf_end = first_tail - 1

    resolved: list[SplitRange] = []
    prev_end = -1
    for item in body + tail:
        if item.pdf_start > item.pdf_end:
            continue
        start = max(item.pdf_start, prev_end + 1)
        if start > item.pdf_end:
            if resolved and not _is_end_matter(item.title):
                resolved[-1].title = f"{resolved[-1].title} / {item.title}"
            continue
        item.pdf_start = start
        resolved.append(item)
        prev_end = item.pdf_end
    resolved.sort(key=lambda item: item.pdf_start)
    return resolved


def ranges_from_toc(
    entries: list[TocEntry],
    printed_map: list[int],
    page_count: int,
) -> list[SplitRange]:
    """Build PDF page ranges from table-of-contents entries."""
    unique: list[TocEntry] = []
    by_page: dict[int, TocEntry] = {}
    for entry in sorted(entries, key=lambda e: (e.printed_page, e.title)):
        existing = by_page.get(entry.printed_page)
        if existing:
            if entry.title.lower() not in existing.title.lower():
                existing.title = f"{existing.title} / {entry.title}"
            continue
        by_page[entry.printed_page] = entry
        unique.append(entry)

    ranges: list[SplitRange] = []
    for i, entry in enumerate(unique):
        start = printed_to_pdf(entry.printed_page, printed_map)
        if i + 1 < len(unique):
            next_start = printed_to_pdf(unique[i + 1].printed_page, printed_map)
            end = max(start, next_start - 1)
        else:
            end = page_count - 1
        ranges.append(
            SplitRange(
                title=entry.title,
                section=entry.section,
                printed_start=entry.printed_page,
                pdf_start=start,
                pdf_end=end,
                source=entry.source,
            )
        )
    return ranges


def unassigned_ranges(assigned: list[SplitRange], page_count: int) -> list[SplitRange]:
    """Return contiguous page ranges not assigned to a table-of-contents entry."""
    used = [False] * page_count
    for item in assigned:
        for i in range(item.pdf_start, item.pdf_end + 1):
            used[i] = True
    leftover: list[SplitRange] = []
    i = 0
    while i < page_count:
        if used[i]:
            i += 1
            continue
        start = i
        while i < page_count and not used[i]:
            i += 1
        leftover.append(
            SplitRange(
                title=f"Heftseiten ohne TOC PDF {start + 1}-{i}",
                section="",
                printed_start=start + 1,
                pdf_start=start,
                pdf_end=i - 1,
                source="unassigned",
            )
        )
    return leftover


@dataclass
class TocOptions:
    """Settings that control how a table of contents is selected."""

    source: str
    toc_pages: list[int] | None
    allow_network: bool


def choose_entries(
    reader: PdfReader,
    page_texts: list[str],
    year: int | None,
    issue: int | None,
    options: TocOptions,
) -> list[TocEntry]:
    """Choose table-of-contents entries from the configured source."""
    bookmarks = outline_entries(reader)
    if options.source in {"auto", "bookmarks"} and len(bookmarks) >= 8:
        return bookmarks
    if options.source == "bookmarks":
        raise RuntimeError("Keine ausreichenden PDF-Bookmarks gefunden.")

    if options.source in {"auto", "heise"} and options.allow_network and year and issue:
        try:
            return fetch_heise_toc(year, issue)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            if options.source == "heise":
                raise RuntimeError(f"Heise-TOC nicht ladbar: {exc}") from exc
            print(f"  Hinweis: Heise-TOC nicht nutzbar ({exc}), lokale OCR folgt.")

    if options.source in {"auto", "local", "heise"}:
        local = parse_local_toc(page_texts, options.toc_pages)
        if len(local) >= 8:
            return local
        if options.source == "local":
            raise RuntimeError("Lokales Inhaltsverzeichnis lieferte zu wenige Einträge.")

    if bookmarks:
        return bookmarks
    raise RuntimeError("Kein Inhaltsverzeichnis gefunden (Bookmarks, Heise-Shop, lokale OCR).")


def write_csv(path: Path, issue_label: str, ranges: list[SplitRange]) -> None:
    """Write the split plan as a semicolon-delimited CSV file."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
                "heft",
                "quelle",
                "rubrik",
                "titel",
                "heftseite",
                "pdf_von",
                "pdf_bis",
                "seiten",
                "dateiname",
            ]
        )
        for item in ranges:
            writer.writerow(
                [
                    issue_label,
                    item.source,
                    item.section,
                    item.title,
                    item.printed_start,
                    item.pdf_start + 1,
                    item.pdf_end + 1,
                    item.page_count,
                    output_name(issue_label, item),
                ]
            )


def output_name(issue_label: str, item: SplitRange) -> str:
    """Build the filename for one split article."""
    title = sanitize_filename(item.title)
    return f"{issue_label} - {item.printed_start:03d} - {title}.pdf"


def write_split(
    reader: PdfReader,
    destination: Path,
    item: SplitRange,
    publication_date: datetime.date,
) -> None:
    """Write an article PDF and apply its publication date."""
    writer = PdfWriter()
    writer.append(reader, pages=(item.pdf_start, item.pdf_end + 1))
    writer.add_metadata(
        {"/CreationDate": f"D:{publication_date:%Y%m%d}000000"}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)
    timestamp = datetime.datetime.combine(
        publication_date, datetime.time.min
    ).timestamp()
    os.utime(destination, (destination.stat().st_atime, timestamp))


def parse_publication_date(value: str) -> datetime.date:
    """Parse and validate an ISO-formatted publication date."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError("Datum muss das Format YYYY-MM-DD haben.")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Ungültiges Datum; erwartet wird YYYY-MM-DD."
        ) from exc


def parse_toc_pages(spec: str | None) -> list[int] | None:
    """Parse a comma-separated list of page numbers and ranges."""
    if not spec:
        return None
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            pages.extend(range(int(start_s), int(end_s) + 1))
        else:
            pages.append(int(part))
    return pages


def iter_pdfs(input_path: Path, include_proceed: bool) -> list[Path]:
    """Return eligible PDFs from a file or directory."""
    if input_path.is_file():
        return [input_path]
    files = sorted(input_path.glob("*.pdf")) + sorted(input_path.glob("*.PDF"))
    unique = []
    seen = set()
    for file in files:
        key = str(file).lower()
        if key in seen:
            continue
        seen.add(key)
        if not include_proceed and SKIP_NAME_RE.search(file.name):
            continue
        unique.append(file)
    return unique


@dataclass
class ProcessingOptions:
    """Settings used while splitting a PDF."""

    publication_date: datetime.date
    dry_run: bool
    apply: bool
    toc: TocOptions
    offset: int | None
    keep_unassigned: bool


def _issue_details(pdf_path: Path, page_texts: list[str]) -> tuple[int | None, int | None, str]:
    """Find issue identifiers and format the output label."""
    year, issue = parse_issue_from_name(pdf_path)
    if not year or not issue:
        for text in page_texts[:16]:
            year, issue = parse_issue_from_text(text)
            if year and issue:
                break
    if year and issue:
        issue_label = f"ct-{year}-{issue:02d}"
        print(f"  Ausgabe: {year}/{issue:02d}")
    else:
        issue_label = sanitize_filename(pdf_path.stem)
        print("  Ausgabe: unbekannt (Dateiname)")
    return year, issue, issue_label


def _article_ranges(
    reader: PdfReader,
    page_texts: list[str],
    year: int | None,
    issue: int | None,
    options: ProcessingOptions,
) -> list[SplitRange]:
    """Build and optionally complete the article page ranges."""
    entries = choose_entries(reader, page_texts, year, issue, options.toc)
    print(
        f"  TOC-Quelle: {entries[0].source if entries else '-'} "
        f"({len(entries)} Einträge)"
    )
    ranges = cap_and_dedupe(
        ranges_from_toc(
            entries,
            build_printed_map(page_texts, year, issue, options.offset),
            len(page_texts),
        ),
        len(page_texts),
    )
    if options.keep_unassigned:
        ranges.extend(unassigned_ranges(ranges, len(page_texts)))
        ranges.sort(key=lambda item: item.pdf_start)
    return ranges


def process_pdf(
    pdf_path: Path, output_root: Path, options: ProcessingOptions
) -> list[SplitRange]:
    """Create a split plan and optionally write the article PDFs."""
    print(f"\n=== {pdf_path.name} ===")
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    print(f"  PDF-Seiten: {page_count}")

    page_texts = [extract_page_text(reader, i) for i in range(page_count)]
    year, issue, issue_label = _issue_details(pdf_path, page_texts)
    ranges = _article_ranges(reader, page_texts, year, issue, options)

    out_dir = output_root / issue_label
    plan_path = output_root / f"{issue_label}_split-plan.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(plan_path, issue_label, ranges)
    print(f"  Plan: {plan_path}")
    for item in ranges:
        print(
            f"  S.{item.printed_start:3d}  "
            f"PDF {item.pdf_start + 1:3d}-{item.pdf_end + 1:3d}  "
            f"({item.page_count:2d})  {item.title}"
        )

    if options.apply and not options.dry_run:
        for item in ranges:
            dest = out_dir / output_name(issue_label, item)
            write_split(reader, dest, item, options.publication_date)
            print(f"  geschrieben: {dest.name}")
    return ranges


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Teilt c't-Heft-PDFs anhand des Inhaltsverzeichnisses in Artikel-PDFs."
    )
    parser.add_argument(
        "--input",
        default=".",
        help="PDF-Datei oder Ordner (Standard: aktuelles Verzeichnis)",
    )
    parser.add_argument(
        "--output",
        default="artikel",
        help="Zielordner (Standard: ./artikel)",
    )
    parser.add_argument(
        "--publication-date",
        required=True,
        type=parse_publication_date,
        help="Erscheinungsdatum der Zeitschrift (Format: YYYY-MM-DD)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur Split-Plan (CSV) schreiben, keine PDFs schneiden",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Artikel-PDFs erzeugen",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "heise", "local", "bookmarks"],
        default="auto",
        help="TOC-Quelle (Standard: auto = Bookmarks, sonst Heise-Shop, sonst lokale OCR)",
    )
    parser.add_argument(
        "--toc-pages",
        help="PDF-Seiten des Inhaltsverzeichnisses, z. B. 6-7",
    )
    parser.add_argument(
        "--offset",
        type=int,
        help="Feste Differenz Heftseite − PDF-Seite (sonst automatisch)",
    )
    parser.add_argument(
        "--keep-unassigned",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="PDF-Seiten ohne TOC-Treffer als Extra-Datei behalten (Standard: an)",
    )
    parser.add_argument(
        "--include-proceed",
        action="store_true",
        help="Auch Dateien mit 'to-proceed' im Namen verarbeiten",
    )
    parser.add_argument(
        "--no-network",
        action="store_true",
        help="Heise-Shop nicht abfragen",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line application."""
    args = build_parser().parse_args(argv)
    if not args.dry_run and not args.apply:
        args.dry_run = True

    input_path = Path(args.input).expanduser().resolve()
    output_root = Path(args.output).expanduser().resolve()
    if not input_path.exists():
        print(f"Eingabe nicht gefunden: {input_path}", file=sys.stderr)
        return 1

    pdfs = iter_pdfs(input_path, args.include_proceed)
    if not pdfs:
        print(f"Keine PDFs in {input_path}", file=sys.stderr)
        return 1

    toc_pages = parse_toc_pages(args.toc_pages)
    output_root.mkdir(parents=True, exist_ok=True)
    errors = 0
    for pdf in pdfs:
        try:
            process_pdf(
                pdf_path=pdf,
                output_root=output_root,
                options=ProcessingOptions(
                    publication_date=args.publication_date,
                    dry_run=args.dry_run,
                    apply=args.apply,
                    toc=TocOptions(
                        source=args.source,
                        toc_pages=toc_pages,
                        allow_network=not args.no_network,
                    ),
                    offset=args.offset,
                    keep_unassigned=args.keep_unassigned,
                ),
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors += 1
            print(f"  FEHLER: {exc}", file=sys.stderr)
    if args.dry_run:
        print("\nDry-run beendet. Bei korrektem Plan mit --apply schneiden.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
