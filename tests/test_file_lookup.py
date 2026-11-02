import tempfile
import unittest
from pathlib import Path

from core.file_lookup import find_file, normalize


class FileLookupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / "Arnel Kovacevic — Curriculum Vitae.pdf").write_bytes(b"x")
        (self.root / "Budget 2026.xlsx").write_bytes(b"x")
        (self.root / "Budget 2025.xlsx").write_bytes(b"x")
        (self.root / "Račun_Čačak.csv").write_bytes(b"x")
        (self.root / ".hidden.pdf").write_bytes(b"x")

    def _find(self, query):
        return find_file(query, [self.root])

    def test_exact_name_with_the_long_dash(self):
        path, _ = self._find("Arnel Kovacevic — Curriculum Vitae.pdf")
        self.assertEqual(path.name, "Arnel Kovacevic — Curriculum Vitae.pdf")

    def test_spoken_variants_all_find_the_same_file(self):
        for query in (
            "Arnel Kovacevic - Curriculum Vitae.pdf",
            "arnel kovacevic curriculum vitae",
            "Arnel Kovacevic Curriculum Vitae pdf",
            "curriculum vitae",
            "ARNEL KOVACEVIC – CURRICULUM VITAE.PDF",
        ):
            path, candidates = self._find(query)
            self.assertIsNotNone(path, query)
            self.assertEqual(path.suffix, ".pdf")

    def test_accents_and_underscores_are_ignored(self):
        path, _ = self._find("racun cacak")
        self.assertEqual(path.name, "Račun_Čačak.csv")

    def test_ambiguous_names_return_candidates_instead_of_guessing(self):
        path, candidates = self._find("budget")
        self.assertIsNone(path)
        self.assertEqual([c.name for c in candidates], ["Budget 2025.xlsx", "Budget 2026.xlsx"])

    def test_an_exact_match_beats_partial_matches(self):
        (self.root / "Budget.xlsx").write_bytes(b"x")
        path, _ = self._find("budget")
        self.assertEqual(path.name, "Budget.xlsx")

    def test_extension_in_the_query_narrows_the_search(self):
        (self.root / "Budget 2026.csv").write_bytes(b"x")
        path, _ = self._find("budget 2026.csv")
        self.assertEqual(path.suffix, ".csv")

    def test_unknown_names_and_hidden_files_are_not_found(self):
        self.assertEqual(self._find("passport"), (None, []))
        self.assertEqual(self._find("hidden"), (None, []))
        self.assertEqual(self._find(""), (None, []))

    def test_normalize(self):
        self.assertEqual(normalize("Čaj — Šećer_1"), "caj secer 1")


if __name__ == "__main__":
    unittest.main()


class LocationPhraseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        (self.root / "CV_Arnel_Kovacevic25.pdf").write_bytes(b"x")

    def test_location_phrases_and_folder_prefixes_are_ignored(self):
        for spoken in (
            "CV_Arnel_Kovacevic25.pdf on the desktop",
            "CV_Arnel_Kovacevic25.pdf na desktopu",
            "CV_Arnel_Kovacevic25 na scrivania",
            "Desktop/CV_Arnel_Kovacevic25.pdf",
            "~/Desktop/CV_Arnel_Kovacevic25.pdf",
            "CV arnel kovacevic 25 on my desktop",
        ):
            path, _ = find_file(spoken, [self.root])
            self.assertIsNotNone(path, spoken)
            self.assertEqual(path.name, "CV_Arnel_Kovacevic25.pdf", spoken)

    def test_location_words_do_not_break_names_that_contain_them(self):
        (self.root / "Desktop Icons Guide.pdf").write_bytes(b"x")
        path, _ = find_file("Desktop Icons Guide", [self.root])
        self.assertEqual(path.name, "Desktop Icons Guide.pdf")
