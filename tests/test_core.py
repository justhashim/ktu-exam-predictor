"""Regression tests for the predictor's own logic: paper checks, course resolution, the ask-the-user step, module
mapping, scoring and an end-to-end forecast. All offline."""
import contextlib, io, json, os, shutil, subprocess, sys, unittest
from unittest import mock

import _util as U
import extract_questions as ex
import fetch
import ktu
import score

CATALOG = {"folders": {"pbect404": "F1", "pbcst504": "F2", "pcecl408": "F3"},
           "catalog": [{"code": "PBECT404", "name": "Microcontrollers", "sem": "S4", "branch": "ECE"},
                       {"code": "PBCST504", "name": "Microcontrollers", "sem": "S5", "branch": "CSE"},
                       {"code": "PEADT418", "name": "Microcontrollers", "sem": "S4", "branch": "AIDS"},
                       {"code": "PCECL408", "name": "Microcontroller and Embedded System Lab", "sem": "S4", "branch": "ECE"},
                       {"code": "CST307", "name": "Microprocessors and Microcontrollers", "sem": "S5", "branch": "CSE"}],
           "short": {}, "keywords": {}}
INDEX = {"mapping": {
    "KTUNOTES_LIVE_RAT206_MICROCONTROLLERS_AND_EMBEDDED_SYSTEMS_MAY_2026_REGULAR.pdf": "https://drive.google.com/file/d/AAA/view",
    "KTUNOTES_LIVE_RAT206_MICROCONTROLLERS_AND_EMBEDDED_SYSTEMS_JAN_2026_REGULAR.pdf": "https://drive.google.com/file/d/BBB/view"},
    "mbcet": []}


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = fn(*a, **k)
    return r, out.getvalue()


def qs_2024(a_marks=(3,) * 8, b=((9, 5, 4), (10, 9), (11, 5, 4), (12, 9), (13, 9), (14, 9), (15, 9), (16, 9))):
    out = [{"q": f"A{i}", "marks": m, "text": f"question {i}"} for i, m in enumerate(a_marks, 1)]
    for n, *parts in b:
        out += [{"q": f"B{n}{'abc'[j] if len(parts) > 1 else ''}", "marks": m, "text": f"q {n}{j}"} for j, m in enumerate(parts)]
    return out


class TestPaperChecks(unittest.TestCase):
    def test_2024_pattern(self):
        self.assertEqual(ex.check(qs_2024()), ("2024", []))

    def test_2019_pattern(self):
        qs = [{"q": f"A{i}", "marks": 3, "text": "x"} for i in range(1, 11)]
        qs += [{"q": f"B{n}", "marks": 14, "text": "x"} for n in range(11, 21)]
        self.assertEqual(ex.check(qs), ("2019", []))

    def test_wrong_marks_are_reported(self):
        scheme, probs = ex.check(qs_2024(b=((9, 5, 5),) + tuple((n, 9) for n in range(10, 17))))
        self.assertEqual(scheme, "2024")
        self.assertTrue(any("not totalling 9" in p for p in probs))

    def test_header_type(self):
        self.assertEqual(ex.header_type("APJ ABDUL KALAM\nB.Tech S5 (R) Examination"), "regular")
        self.assertEqual(ex.header_type("APJ ABDUL KALAM\nB.Tech S4 (R,S) Examination"), "regular")
        self.assertEqual(ex.header_type("APJ ABDUL KALAM\nB.Tech S4 (S,FE) Examination"), "supplementary")
        self.assertEqual(ex.header_type("APJ ABDUL KALAM\nFourth Semester Supplementary Examination"), "supplementary")
        self.assertIsNone(ex.header_type("APJ ABDUL KALAM\nFourth Semester Examination June 2022"))

    def test_duplicate_reupload_is_marked_and_dated_copy_kept(self):
        a = qs_2024()
        P = {"papers": [{"id": "Y24", "label": "2024 RAT206", "questions": [dict(q) for q in a]},
                        {"id": "JAN24", "label": "Jan 2024 RAT206", "questions": [dict(q) for q in a]},
                        {"id": "MAY24", "label": "May 2024 RAT206",
                         "questions": [{**q, "text": q["text"] + " other words " + q["q"]} for q in a]}]}
        msgs = ex.mark_duplicates(P)
        self.assertEqual(len(msgs), 1)
        self.assertEqual(P["papers"][0].get("duplicate_of"), "JAN24")
        self.assertNotIn("duplicate_of", P["papers"][1])
        self.assertNotIn("duplicate_of", P["papers"][2])


class TestModules(unittest.TestCase):
    def test_module_of(self):
        self.assertEqual([ktu.module_of(q, "2024") for q in ("A1", "A2", "A3", "A8", "B9a", "B10", "B11b", "B16")],
                         [1, 1, 2, 4, 1, 1, 2, 4])
        self.assertEqual([ktu.module_of(q, "2019") for q in ("A1", "A10", "B11a", "B14", "B20b")], [1, 5, 1, 2, 5])
        self.assertIsNone(ktu.module_of("A1", "?"))

    def test_old_module_proportional_and_table(self):
        self.assertEqual([ktu.old_module(f"B{n}", "2019", 4) for n in (11, 13, 15, 17, 19)], [1, 2, 3, 4, 4])
        self.assertEqual(ktu.old_module("B13", "2019", 4, {"2": 3}), 3)
        self.assertIsNone(ktu.old_module("X1", "2019", 4))


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.p = [mock.patch.object(fetch, "viewer", return_value=CATALOG),
                  mock.patch.object(fetch, "pyq_index", return_value=INDEX)]
        for p in self.p:
            p.start()

    def tearDown(self):
        for p in self.p:
            p.stop()

    def test_uncatalogued_code_needs_a_name(self):
        with self.assertRaises(SystemExit) as e:
            fetch.resolve("PCZZT999")                       # a code with no folder, paper or catalogue entry
        self.assertIn("--name", str(e.exception))

    def test_uncatalogued_code_with_name_finds_predecessor(self):
        info = fetch.resolve("PCEOT501", name="Microcontrollers and Embedded Systems")
        self.assertEqual((info["code"], info["sem"]), ("PCEOT501", "S5"))
        self.assertEqual(info["old_codes"], ["RAT206"])

    def test_name_is_cleaned(self):
        info = fetch.resolve("PCEOT501", name="Microcontrollers\u200b and Embedded Systems")
        self.assertEqual(info["name"], "Microcontrollers and Embedded Systems")

    def test_siblings_skip_labs_self_old_and_no_notes(self):
        sib = fetch.siblings("PCEOT501", "Microcontrollers and Embedded Systems")
        codes = [s.split()[0] for s in sib]
        self.assertEqual(sorted(codes), ["PBCST504", "PBECT404"])


class TestAskTheUser(unittest.TestCase):
    def test_no_notes_prompts_for_own_files(self):
        info = {"code": "PCEOT501", "name": "Microcontrollers and Embedded Systems", "sem": "S5", "branch": "",
                "alternatives": [], "old_codes": [], "short": "PCEOT501", "old_detail": {}}
        with mock.patch.object(fetch, "resolve", return_value=info), \
                mock.patch.object(fetch, "paper_list", return_value=[]), \
                mock.patch.object(fetch, "cmd_notes", side_effect=SystemExit("no notes folder")), \
                mock.patch.object(fetch, "siblings", return_value=["PBECT404 Microcontrollers (ECE)"]):
            D, out = quiet(fetch.cmd_setup, "PCEOT501", name=info["name"])
        self.assertIn("ASK THE USER", out)
        self.assertIn("no notes and no past papers", out)
        for sub in ("notes", "papers"):
            self.assertTrue(os.path.isdir(os.path.join(os.path.dirname(D), sub)))
            self.assertIn(os.path.join(os.path.dirname(D), sub), out)
        self.assertIn("--notes-from", out)
        self.assertIn("usual process", out)

    def test_own_pdf_paper_is_picked_up(self):
        base = os.path.join(U.TMP, "ownpdf")
        D = os.path.join(base, "predictor")
        os.makedirs(os.path.join(base, "papers"), exist_ok=True)
        os.makedirs(os.path.join(D, "pyq"), exist_ok=True)
        open(os.path.join(base, "papers", "nov2026 PCEOT501.pdf"), "wb").write(b"%PDF-1.4")
        with mock.patch.object(fetch, "to_text", return_value="text 100 B"):
            (n, papers), _ = quiet(fetch.ingest_user_files, D, "PCEOT501")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["label"], "Nov 2026 (own copy)")
        self.assertEqual(papers[0]["course"], "PCEOT501")
        self.assertTrue(os.path.exists(os.path.join(D, papers[0]["source"])))


class TestForecastEndToEnd(unittest.TestCase):
    """Three distinct papers -> extract -> papers-only topics -> autotag -> predict -> score."""

    @classmethod
    def setUpClass(cls):
        texts = [U.paper_text(), U.paper_text().replace("8051", "8085").replace("ARM", "Cortex"),
                 U.paper_text().replace("Explain", "Describe").replace("Write", "Develop")]
        cls.D = U.new_subject("e2e", code="PCXXT501", papers=[(f"P{i}", t) for i, t in enumerate(texts)])
        subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "extract_questions.py"), cls.D],
                       capture_output=True, text=True, check=True)
        with mock.patch.object(ktu, "topics_page", return_value=None):
            cls.src = ktu.draft_topics(cls.D)
        ktu.autotag(cls.D)
        r = subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "predict.py"), cls.D, "--label", "Nov 2026"],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.R = json.load(open(os.path.join(cls.D, "results.json"), encoding="utf-8"))
        cls.F = open(os.path.join(cls.D, "FORECAST.md"), encoding="utf-8").read()

    def test_papers_only_topics_cover_every_module(self):
        self.assertIn("from past questions", self.src)
        self.assertLessEqual({1, 2, 3, 4}, {t["module"] for t in self.R["topics"]})

    def test_study_order_is_module_balanced(self):
        first = [t["module"] for t in self.R["topics"][:4]]
        self.assertEqual(sorted(first), [1, 2, 3, 4])

    def test_papers_only_warning(self):
        self.assertTrue(any("No notes or syllabus" in w for w in self.R["warnings"]))
        self.assertIn("No notes or syllabus", self.F)

    def test_assembled_paper_has_every_module(self):
        self.assertEqual(sorted(self.R["paper"]), ["1", "2", "3", "4"])

    def test_score_against_snapshot(self):
        snap = os.path.join(U.TMP, "e2e_snap")
        os.makedirs(snap, exist_ok=True)
        for f in ("topics.json", "results.json"):
            shutil.copy(os.path.join(self.D, f), snap)
        top = self.R["topics"][0]["key"]
        paper = {"label": "real", "questions": [{"q": "A1", "marks": 3, "topics": [top]},
                                                {"q": "A2", "marks": 3, "topics": ["NEW_X"]},
                                                {"q": "B9", "marks": 9, "topics": [top]},
                                                {"q": "B10", "marks": 9, "topics": ["NEW_X"]}]}
        o = score.score(snap, paper)
        rows = {round(f, 1): c for f, c, _ in o["rows"]}
        self.assertAlmostEqual(rows[1.0], 12 / 15)            # NEW_X can never be covered
        self.assertEqual(o["lost_marks"], {"NEW_X": 12})
        self.assertEqual(o["ranks"]["A1"], [1])


if __name__ == "__main__":
    unittest.main()
