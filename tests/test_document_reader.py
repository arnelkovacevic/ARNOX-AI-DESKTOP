import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docx
import pymupdf
from openpyxl import Workbook
from pptx import Presentation

from actions import document_reader as reader
from actions import file_processor as processor


class DocumentReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _path(self, name):
        return self.dir / name

    def test_pdf_text_is_returned_with_page_markers(self):
        path = self._path("invoice.pdf")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Invoice total 1234 EUR for ARNOX")
        doc.new_page().insert_text((72, 72), "Second page payment terms")
        doc.save(path)
        doc.close()
        result = reader.read_document(path)
        self.assertIn("Invoice total 1234 EUR", result)
        self.assertIn("[Page 2]", result)
        self.assertIn("2 page(s)", result)
        self.assertIn("untrusted", result)

    def test_scanned_pdf_without_text_is_explained_not_blank(self):
        path = self._path("scan.pdf")
        doc = pymupdf.open()
        doc.new_page()
        doc.save(path)
        doc.close()
        result = reader.read_document(path)
        self.assertIn("no text layer", result)

    def test_docx_paragraphs_and_tables(self):
        path = self._path("contract.docx")
        document = docx.Document()
        document.add_paragraph("Agreement between the parties")
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Price"
        table.rows[0].cells[1].text = "500"
        document.save(path)
        result = reader.read_document(path)
        self.assertIn("Agreement between the parties", result)
        self.assertIn("Price | 500", result)

    def test_xlsx_sheets_and_rows(self):
        path = self._path("sales.xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Q1"
        sheet.append(["Item", "Amount"])
        sheet.append(["Laptop", 1200])
        workbook.create_sheet("Q2").append(["Phone", 800])
        workbook.save(path)
        result = reader.read_document(path)
        self.assertIn("[Sheet: Q1]", result)
        self.assertIn("Laptop | 1200", result)
        self.assertIn("[Sheet: Q2]", result)

    def test_csv_counts_rows_and_limits_output(self):
        path = self._path("big.csv")
        path.write_text("id,name\n" + "\n".join(f"{i},user{i}" for i in range(300)), encoding="utf-8")
        result = reader.read_document(path)
        self.assertIn("301 row(s)", result)
        self.assertIn("showing first 100", result)
        self.assertIn("user5", result)
        self.assertNotIn("user250", result)

    def test_json_is_pretty_printed_and_bad_json_is_reported(self):
        good = self._path("data.json")
        good.write_text(json.dumps({"name": "Arnel", "items": [1, 2]}), encoding="utf-8")
        self.assertIn('"name": "Arnel"', reader.read_document(good))
        bad = self._path("bad.json")
        bad.write_text("{not json", encoding="utf-8")
        self.assertIn("JSON parse error", reader.read_document(bad))

    def test_html_drops_scripts_and_styles(self):
        path = self._path("page.html")
        path.write_text(
            "<html><head><title>Offer</title><style>p{}</style></head>"
            "<body><script>steal()</script><p>Visible text here</p></body></html>",
            encoding="utf-8",
        )
        result = reader.read_document(path)
        self.assertIn("Visible text here", result)
        self.assertNotIn("steal()", result)
        self.assertIn("Offer", result)

    def test_pptx_slide_text_and_notes(self):
        path = self._path("deck.pptx")
        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "Quarterly plan"
        slide.notes_slide.notes_text_frame.text = "Speaker note one"
        presentation.save(path)
        result = reader.read_document(path)
        self.assertIn("Quarterly plan", result)
        self.assertIn("Speaker note one", result)

    def test_legacy_windows_encoding_is_decoded(self):
        path = self._path("bosanski.txt")
        path.write_bytes("Čaj i šećer, đak".encode("cp1250"))
        self.assertIn("Čaj i šećer, đak", reader.read_document(path))

    def test_long_text_is_truncated_with_a_note(self):
        path = self._path("long.txt")
        path.write_text("word " * 10000, encoding="utf-8")
        result = reader.read_document(path, max_chars=500)
        self.assertIn("[Truncated at 500 characters]", result)

    def test_executables_and_unknown_types_are_refused(self):
        exe = self._path("setup.exe")
        exe.write_bytes(b"MZ")
        self.assertIn("never opened", reader.read_document(exe))
        odd = self._path("data.xyz")
        odd.write_bytes(b"abc")
        self.assertIn("not supported", reader.read_document(odd))

    def test_images_point_to_describe_instead_of_failing(self):
        image = self._path("photo.png")
        image.write_bytes(b"\x89PNG")
        self.assertIn("describe or ocr", reader.read_document(image))

    def test_oversize_files_are_refused(self):
        path = self._path("huge.txt")
        path.write_text("x", encoding="utf-8")
        with patch.object(reader, "MAX_READ_BYTES", 0):
            self.assertIn("over the", reader.read_document(path))

    def test_missing_file_is_reported(self):
        self.assertIn("File not found", reader.read_document(self._path("nope.pdf")))

    def test_file_processor_read_returns_text_without_any_ai_call(self):
        path = self._path("notes.md")
        path.write_text("# Plan\nBuy milk and bread", encoding="utf-8")
        with patch.object(processor, "_gemini_client", side_effect=AssertionError("AI must not be called")):
            result = processor.file_processor({"file_path": str(path), "action": "read"})
        self.assertIn("Buy milk and bread", result)

    def test_existing_pdf_extraction_now_uses_pymupdf(self):
        path = self._path("legacy.pdf")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "Legacy extraction works")
        doc.save(path)
        doc.close()
        result = processor.file_processor({"file_path": str(path), "action": "extract_text"})
        self.assertIn("Text extracted", result)


if __name__ == "__main__":
    unittest.main()
