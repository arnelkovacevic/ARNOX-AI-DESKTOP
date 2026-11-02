import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from openpyxl import Workbook
from PyQt6.QtWidgets import QApplication

import ui  # noqa: F401  (sets up fonts and theme the workspace reads)
from core import workspace_router as router
from ui_workspace import loaders
from ui_workspace.pdf_tab import PdfTab
from ui_workspace.table_tab import TableTab
from ui_workspace.workspace_view import MAX_TABS, WorkspaceView


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _file(self, name, data=b"x"):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_files_are_routed_by_extension(self):
        self.assertEqual(router.classify_file(self._file("a.pdf")).kind, "pdf")
        for name in ("a.csv", "a.tsv", "a.xlsx", "a.xls", "A.XLSX"):
            self.assertEqual(router.classify_file(self._file(name)).kind, "table", name)

    def test_unsupported_and_missing_files_explain_themselves(self):
        route = router.classify_file(self._file("bundle.zip"))
        self.assertEqual(route.kind, "unsupported")
        self.assertIn("PDF, CSV", route.reason)
        self.assertIn("not found", router.classify_file(self.dir / "ghost.pdf").reason)

    def test_oversize_files_are_refused(self):
        with patch.object(router, "MAX_VIEW_BYTES", 0):
            self.assertIn("larger than", router.classify_file(self._file("big.pdf")).reason)

    def test_web_targets_allow_only_https_and_local_http(self):
        self.assertEqual(router.normalize_web_target("example.com/page")[0], "https://example.com/page")
        self.assertEqual(router.normalize_web_target("https://example.com")[0], "https://example.com")
        self.assertEqual(router.normalize_web_target("http://localhost:3000")[0], "http://localhost:3000")
        self.assertEqual(router.normalize_web_target("youtube")[0], "https://www.youtube.com")
        for bad in ("file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi", "http://evil.test",
                    "ftp://host/x", "about:blank", ""):
            url, error = router.normalize_web_target(bad)
            self.assertIsNone(url, bad)
            self.assertTrue(error, bad)

    def test_spotify_is_not_an_embedded_alias_because_drm_is_unavailable(self):
        self.assertNotIn("spotify", router.APP_URLS)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_csv_with_comma_and_semicolon_and_tsv(self):
        for name, text in (("c.csv", "a,b\n1,2\n"), ("s.csv", "a;b\n1;2\n"), ("t.tsv", "a\tb\n1\t2\n")):
            path = self.dir / name
            path.write_text(text, encoding="utf-8")
            frame = next(iter(loaders.load_tables(path).values()))
            self.assertEqual(list(frame.columns), ["a", "b"], name)

    def test_windows_encoded_csv_keeps_special_letters(self):
        path = self.dir / "w.csv"
        path.write_bytes("ime,grad\nĆamil,Čačak\n".encode("cp1250"))
        frame = next(iter(loaders.load_tables(path).values()))
        self.assertEqual(frame.iloc[0, 0], "Ćamil")

    def test_excel_returns_every_sheet(self):
        path = self.dir / "x.xlsx"
        workbook = Workbook()
        workbook.active.title = "Q1"
        workbook.active.append(["Item", "Amount"])
        workbook.active.append(["Laptop", 1200])
        workbook.create_sheet("Q2").append(["Phone"])
        workbook.save(path)
        sheets = loaders.load_tables(path)
        self.assertEqual(list(sheets), ["Q1", "Q2"])
        self.assertEqual(sheets["Q1"].iloc[0, 0], "Laptop")

    def test_empty_and_broken_files_raise_a_readable_error(self):
        empty = self.dir / "e.csv"
        empty.write_text("", encoding="utf-8")
        with self.assertRaises(loaders.TableLoadError):
            loaders.load_tables(empty)
        broken = self.dir / "b.xlsx"
        broken.write_bytes(b"not a spreadsheet")
        with self.assertRaises(loaders.TableLoadError):
            loaders.load_tables(broken)

    def test_very_long_tables_are_truncated(self):
        import pandas

        with patch.object(loaders, "MAX_ROWS", 3):
            frame, truncated = loaders.truncate(pandas.DataFrame({"a": range(10)}))
        self.assertTrue(truncated)
        self.assertEqual(len(frame), 3)


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(__import__("sys").argv[:1] or ["arnox"])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.pdf = cls._make_pdf(cls.dir / "contract.pdf", ["Invoice total 1234 EUR", "Payment terms net 30", "Invoice total 99 EUR"])
        cls.csv = cls.dir / "sales.csv"
        cls.csv.write_text("item,amount\nLaptop,1200\nPhone,800\nCable,9\nMonitor,300\n", encoding="utf-8")
        workbook = Workbook()
        workbook.active.title = "Q1"
        workbook.active.append(["Item", "Amount"])
        workbook.active.append(["Laptop", 1200])
        workbook.create_sheet("Q2").append(["Phone"])
        cls.xlsx = cls.dir / "book.xlsx"
        workbook.save(cls.xlsx)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def _make_pdf(path, pages):
        doc = pymupdf.open()
        for text in pages:
            doc.new_page().insert_text((72, 72), text)
        doc.save(path)
        doc.close()
        return path


class TableTabTests(_QtCase):
    def test_table_shows_rows_and_filters_across_all_columns(self):
        tab = TableTab(str(self.csv))
        self.assertEqual(tab.total_rows(), 4)
        tab.search("laptop")
        self.assertEqual(tab.visible_rows(), 1)
        tab.search("")
        self.assertEqual(tab.visible_rows(), 4)

    def test_numbers_sort_as_numbers_not_text(self):
        tab = TableTab(str(self.csv))
        tab._view.sortByColumn(1, ui.Qt.SortOrder.AscendingOrder)
        values = [tab._proxy.data(tab._proxy.index(row, 1)) for row in range(tab.visible_rows())]
        self.assertEqual(values, ["9", "300", "800", "1200"])

    def test_excel_sheets_can_be_switched(self):
        tab = TableTab(str(self.xlsx))
        self.assertEqual(tab.sheet_names(), ["Q1", "Q2"])
        tab._sheet_box.setCurrentText("Q2")
        self.assertEqual(tab.total_rows(), 0)

    def test_broken_file_shows_an_error_instead_of_crashing(self):
        bad = self.dir / "bad.xlsx"
        bad.write_bytes(b"nope")
        tab = TableTab(str(bad))
        self.assertTrue(tab.error)


class PdfTabTests(_QtCase):
    def test_pages_zoom_and_navigation(self):
        tab = PdfTab(str(self.pdf))
        tab.resize(900, 700)
        tab.show()
        self.app.processEvents()
        self.assertEqual(tab.page_count(), 3)
        before = tab.zoom()
        tab.set_zoom(before * 100)
        self.assertLessEqual(tab.zoom(), 4.0)
        tab.set_zoom(0.0001)
        self.assertGreaterEqual(tab.zoom(), 0.4)
        self.app.processEvents()
        tab.go_to_page(99)
        self.app.processEvents()
        self.assertEqual(tab.current_page(), 2)
        tab.close_document()

    def test_visible_pages_are_rendered_to_images(self):
        tab = PdfTab(str(self.pdf))
        tab.resize(900, 700)
        tab.show()
        self.app.processEvents()
        tab._render_visible()
        self.assertFalse(tab._labels[0].pixmap().isNull())
        tab.close_document()

    def test_search_counts_matches_and_jumps_to_them(self):
        tab = PdfTab(str(self.pdf))
        self.assertEqual(tab.search("Invoice total"), 2)
        self.assertEqual(tab.current_hit_page(), 0)
        tab._step_hit(1)
        self.assertEqual(tab.current_hit_page(), 2)
        self.assertEqual(tab.search("does not occur"), 0)
        tab.close_document()

    def test_password_protected_pdf_is_reported_not_opened(self):
        path = self.dir / "locked.pdf"
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "secret")
        doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
        doc.close()
        tab = PdfTab(str(path))
        self.assertIn("password", tab.error)

    def test_corrupt_pdf_is_reported(self):
        path = self.dir / "corrupt.pdf"
        path.write_bytes(b"%PDF-1.4 garbage")
        self.assertTrue(PdfTab(str(path)).error)


class WorkspaceViewTests(_QtCase):
    def setUp(self):
        self.view = WorkspaceView()

    def tearDown(self):
        self.view.close_all()

    def test_opens_documents_in_tabs_and_names_them(self):
        self.assertIn("Opened contract.pdf", self.view.open_document(str(self.pdf)))
        self.view.open_document(str(self.csv))
        self.assertEqual(self.view.tab_titles(), ["contract.pdf", "sales.csv"])
        self.assertEqual(self.view.current_title(), "sales.csv")

    def test_same_file_is_not_opened_twice(self):
        self.view.open_document(str(self.pdf))
        self.view.open_document(str(self.csv))
        message = self.view.open_document(str(self.pdf))
        self.assertIn("already open", message)
        self.assertEqual(self.view.tab_count(), 2)
        self.assertEqual(self.view.current_title(), "contract.pdf")

    def test_unsupported_file_is_explained_and_opens_nothing(self):
        path = self.dir / "bundle.zip"
        path.write_bytes(b"x")
        self.assertIn("cannot be shown", self.view.open_document(str(path)))
        self.assertEqual(self.view.tab_count(), 0)

    def test_corrupt_document_does_not_leave_a_dead_tab(self):
        path = self.dir / "dead.pdf"
        path.write_bytes(b"%PDF-1.4 garbage")
        self.assertTrue(self.view.open_document(str(path)))
        self.assertEqual(self.view.tab_count(), 0)

    def test_closing_the_last_tab_signals_emptied(self):
        seen = []
        self.view.emptied.connect(lambda: seen.append(True))
        self.view.open_document(str(self.csv))
        self.assertIn("Closed sales.csv", self.view.close_current())
        self.assertEqual(seen, [True])
        self.assertIn("no open tab", self.view.close_current())

    def test_back_button_emits_hide_request(self):
        seen = []
        self.view.hide_requested.connect(lambda: seen.append(True))
        self.view._back.click()
        self.assertEqual(seen, [True])

    def test_oldest_tab_is_closed_beyond_the_limit(self):
        for number in range(MAX_TABS + 2):
            path = self.dir / f"t{number}.csv"
            path.write_text("a,b\n1,2\n", encoding="utf-8")
            self.view.open_document(str(path))
        self.assertEqual(self.view.tab_count(), MAX_TABS)
        self.assertNotIn("t0.csv", self.view.tab_titles())

    def test_stylesheet_follows_the_live_theme_colours(self):
        ui.ThemeManager.set_theme("arc_reactor")
        self.view.refresh_theme()
        self.assertIn(ui.C.PRI.lower(), self.view.styleSheet().lower())
        ui.ThemeManager.apply_status("standby")
        self.view.refresh_theme()
        self.assertIn("#800f22", self.view.styleSheet().lower())
        ui.ThemeManager.set_theme("arc_reactor")


if __name__ == "__main__":
    unittest.main()
