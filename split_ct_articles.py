#!/usr/bin/env python3
"""Split c't magazine PDFs into one PDF per article for Paperless-ngx."""

from __future__ import annotations

import argparse
import csv
import html
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
    title: str
    printed_page: int
    section: str = ""
    source: str = ""


@dataclass
class SplitRange:
    title: str
    section: str
    printed_start: int
    pdf_start: int
    pdf_end: int
    source: str

    @property
    def page_count(self) -> int:
        return self.pdf_end - self.pdf_start + 1


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def sanitize_filename(name: str, max_len: int = 110) -> str:
    name = unicodedata.normalize("NFC", name)
    name = INVALID_FS.sub(" ", name)
    name = name.replace("…", "...")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "artikel"


def parse_issue_from_name(path: Path) -> tuple[int | None, int | None]:
    match = ISSUE_NAME_RE.search(path.name)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_issue_from_text(text: str) -> tuple[int | None, int | None]:
    match = HEFT_TEXT_RE.search(text[:8000])
    if not match:
        return None, None
    return 2000 + int(match.group(1)), int(match.group(2))


def extract_page_text(reader: PdfReader, index: int) -> str:
    try:
        return reader.pages[index].extract_text() or ""
    except Exception:
        return ""


def outline_entries(reader: PdfReader) -> list[TocEntry]:
    try:
        outline = reader.outline
    except Exception:
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
            except Exception:
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


def guess_printed_page(text: str, pdf_index: int, year: int | None, issue: int | None) -> int | None:
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
    if "leserforum" in title:
        return 3
    if "tipps" in title:
        return 3
    if "vorsicht" in title and "kunde" in title:
        return 3
    if "story" in title:
        return None
    if section.startswith("aktuell"):
        return 3
    return None


def _is_end_matter(title: str) -> bool:
    lowered = title.lower()
    return any(token in lowered for token in ("inserenten", "impressum", "vorschau"))


def cap_and_dedupe(ranges: list[SplitRange], page_count: int) -> list[SplitRange]:
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


def choose_entries(
    reader: PdfReader,
    page_texts: list[str],
    year: int | None,
    issue: int | None,
    source: str,
    toc_pages: list[int] | None,
    allow_network: bool,
) -> list[TocEntry]:
    bookmarks = outline_entries(reader)
    if source in {"auto", "bookmarks"} and len(bookmarks) >= 8:
        return bookmarks
    if source == "bookmarks":
        raise RuntimeError("Keine ausreichenden PDF-Bookmarks gefunden.")

    if source in {"auto", "heise"} and allow_network and year and issue:
        try:
            return fetch_heise_toc(year, issue)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            if source == "heise":
                raise RuntimeError(f"Heise-TOC nicht ladbar: {exc}") from exc
            print(f"  Hinweis: Heise-TOC nicht nutzbar ({exc}), lokale OCR folgt.")

    if source in {"auto", "local", "heise"}:
        local = parse_local_toc(page_texts, toc_pages)
        if len(local) >= 8:
            return local
        if source == "local":
            raise RuntimeError("Lokales Inhaltsverzeichnis lieferte zu wenige Einträge.")

    if bookmarks:
        return bookmarks
    raise RuntimeError("Kein Inhaltsverzeichnis gefunden (Bookmarks, Heise-Shop, lokale OCR).")


def write_csv(path: Path, issue_label: str, ranges: list[SplitRange]) -> None:
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
    title = sanitize_filename(item.title)
    return f"{issue_label} - {item.printed_start:03d} - {title}.pdf"


def write_split(reader: PdfReader, destination: Path, item: SplitRange) -> None:
    writer = PdfWriter()
    writer.append(reader, pages=(item.pdf_start, item.pdf_end + 1))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        writer.write(handle)


def parse_toc_pages(spec: str | None) -> list[int] | None:
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


def process_pdf(
    pdf_path: Path,
    output_root: Path,
    dry_run: bool,
    apply: bool,
    source: str,
    toc_pages: list[int] | None,
    offset: int | None,
    keep_unassigned: bool,
    allow_network: bool,
) -> list[SplitRange]:
    print(f"\n=== {pdf_path.name} ===")
    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    print(f"  PDF-Seiten: {page_count}")

    sample_n = min(page_count, 16)
    page_texts = [extract_page_text(reader, i) for i in range(page_count)]

    year, issue = parse_issue_from_name(pdf_path)
    if not year or not issue:
        for text in page_texts[:sample_n]:
            year, issue = parse_issue_from_text(text)
            if year and issue:
                break
    if year and issue:
        issue_label = f"ct-{year}-{issue:02d}"
        print(f"  Ausgabe: {year}/{issue:02d}")
    else:
        issue_label = sanitize_filename(pdf_path.stem)
        print("  Ausgabe: unbekannt (Dateiname)")

    entries = choose_entries(
        reader, page_texts, year, issue, source, toc_pages, allow_network
    )
    print(f"  TOC-Quelle: {entries[0].source if entries else '-'} ({len(entries)} Einträge)")

    printed_map = build_printed_map(page_texts, year, issue, offset)
    ranges = cap_and_dedupe(ranges_from_toc(entries, printed_map, page_count), page_count)
    if keep_unassigned:
        ranges.extend(unassigned_ranges(ranges, page_count))
        ranges.sort(key=lambda item: item.pdf_start)

    out_dir = output_root / issue_label
    plan_path = output_root / f"{issue_label}_split-plan.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(plan_path, issue_label, ranges)
    print(f"  Plan: {plan_path}")
    for item in ranges:
        marker = "  "
        print(
            f"{marker}S.{item.printed_start:3d}  PDF {item.pdf_start + 1:3d}-{item.pdf_end + 1:3d}  "
            f"({item.page_count:2d})  {item.title}"
        )

    if apply and not dry_run:
        for item in ranges:
            dest = out_dir / output_name(issue_label, item)
            write_split(reader, dest, item)
            print(f"  geschrieben: {dest.name}")
    return ranges


def build_parser() -> argparse.ArgumentParser:
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
                dry_run=args.dry_run,
                apply=args.apply,
                source=args.source,
                toc_pages=toc_pages,
                offset=args.offset,
                keep_unassigned=args.keep_unassigned,
                allow_network=not args.no_network,
            )
        except Exception as exc:
            errors += 1
            print(f"  FEHLER: {exc}", file=sys.stderr)
    if args.dry_run:
        print("\nDry-run beendet. Bei korrektem Plan mit --apply schneiden.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
