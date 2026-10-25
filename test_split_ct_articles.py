"""Tests for publication-date handling in split article PDFs."""

import argparse
import datetime
import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from split_ct_articles import (
    SplitRange,
    build_parser,
    parse_publication_date,
    write_split,
)


class PublicationDateTests(unittest.TestCase):
    """Validate publication-date parsing and PDF output metadata."""

    def test_date_requires_exact_iso_format_and_valid_calendar_date(self):
        """Accept valid ISO dates and reject malformed or impossible dates."""
        self.assertEqual(
            parse_publication_date("2024-02-29"), datetime.date(2024, 2, 29)
        )
        for value in ("2024-2-09", "2024-02-30", "20240229"):
            with self.subTest(value=value), self.assertRaises(
                argparse.ArgumentTypeError
            ):
                parse_publication_date(value)

    def test_cli_requires_named_publication_date(self):
        """Require the named date option and parse it as a date."""
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])
        self.assertEqual(
            parser.parse_args(["--publication-date", "2024-02-29"]).publication_date,
            datetime.date(2024, 2, 29),
        )

    def test_split_sets_pdf_creation_date_and_file_timestamp(self):
        """Write the publication date to PDF metadata and file timestamps."""
        publication_date = datetime.date(2024, 2, 29)
        item = SplitRange("Article", "", 1, 0, 0, "test")
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "source.pdf"
            destination = Path(temporary_directory) / "article.pdf"
            source_writer = PdfWriter()
            source_writer.add_blank_page(width=100, height=100)
            with source.open("wb") as handle:
                source_writer.write(handle)

            write_split(
                PdfReader(source),
                destination,
                item,
                publication_date,
            )

            metadata = PdfReader(destination).metadata
            self.assertEqual(
                metadata["/CreationDate"], "D:20240229000000"
            )
            self.assertEqual(
                datetime.datetime.fromtimestamp(
                    destination.stat().st_mtime
                ).date(),
                publication_date,
            )


if __name__ == "__main__":
    unittest.main()
