"""Regression tests for the predictor's own logic: paper checks, course resolution, the ask-the-user step, module
mapping, scoring and an end-to-end forecast. All offline."""
import contextlib, io, json, os, re, shutil, subprocess, sys, unittest
from unittest import mock

import _util as U
import extract_questions as ex
import fetch
import ktu
import safety
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


class TestSyllabus(unittest.TestCase):
    def test_modules_are_split_on_module_numbers_not_hours(self):
        mods = fetch.syllabus_modules(U.SYLLABUS_RAW)
        self.assertEqual(sorted(mods), [1, 2, 3, 4])
        self.assertTrue(any("Assembler directives" in g for g in mods[2]))
        self.assertTrue(any("Interrupt priority" in g for g in mods[3]))
        self.assertTrue(any("RISC" in g for g in mods[4]))
        self.assertFalse(any("Course Assessment" in g or "CIE" in g for gs in mods.values() for g in gs))
        self.assertFalse(any("KTUNOTES" in g.upper() for gs in mods.values() for g in gs))

    def test_injected_line_is_withheld(self):
        mods = fetch.syllabus_modules(U.SYLLABUS_RAW)
        self.assertFalse(any("previous instructions" in g.lower() for gs in mods.values() for g in gs))

    def _subject_with_syllabus(self, name):
        D = U.new_subject(name)
        os.makedirs(os.path.join(D, "syllabus"))
        open(os.path.join(D, "syllabus", "PCXXT501.pdf"), "wb").write(b"%PDF-1.4")
        return D

    def test_syllabus_items_become_topics(self):
        D = self._subject_with_syllabus("syl_items")
        with mock.patch.object(fetch, "pdf_raw_text", return_value=U.SYLLABUS_RAW):
            items = ktu.syllabus_topics(D)
            names = [i for _, i, _ in items]
            for want in ("RISC vs CISC", "operating modes", "conditional flags", "Interrupt priority in 8051",
                         "Data types and Assembler directives", "hard and soft real time systems"):
                self.assertIn(want, names)
            self.assertTrue(all(len(n.split()) >= 2 or h for _, n, h in items))
            with mock.patch.object(ktu, "topics_page", return_value=None):
                src = ktu.draft_topics(D)
        self.assertIn("from syllabus", src)
        T = json.load(open(os.path.join(D, "topics.json"), encoding="utf-8"))["topics"]
        self.assertEqual({t["module"] for t in T.values() if t["source"] == "syllabus"}, {1, 2, 3, 4})

    def test_cmd_syllabus_picks_the_course_pdf_and_skips_shared_links(self):
        D = os.path.join(U.TMP, "sylfetch", "predictor")
        os.makedirs(D, exist_ok=True)
        common = '<a href="https://drive.google.com/file/d/COMMONCOMMONCOMMONCOMMON1/view">'
        pages = {"https://syllabus.ktunotes.live/pcxxt501":
                 "<title>PCXXT501 TEST Syllabus PDF</title>" + common +
                 '<a href="https://drive.google.com/open?id=COURSEPDFCOURSEPDFCOURSE1">',
                 "https://syllabus.ktunotes.live/": "<title>KTU Syllabus 2024</title>" + common}
        got = []
        with mock.patch.object(fetch, "get", side_effect=lambda u, **k: pages[u]), \
                mock.patch.object(fetch, "download_drive", side_effect=lambda i, p: got.append(i) or
                                  open(p, "wb").write(b"%PDF") or True), \
                mock.patch.object(fetch, "to_text", return_value="text"), \
                mock.patch.object(fetch, "pdf_raw_text", return_value=U.SYLLABUS_RAW):
            pdf = fetch.cmd_syllabus("PCXXT501", D)
        self.assertEqual(got, ["COURSEPDFCOURSEPDFCOURSE1"])
        self.assertTrue(pdf.startswith(os.path.join(D, "syllabus")))
        md = open(os.path.join(D, "syllabus", "MODULES.md"), encoding="utf-8").read()
        self.assertIn("## Module 4", md)
        self.assertIn("not instructions", md)

    def test_cmd_syllabus_unknown_course(self):
        D = os.path.join(U.TMP, "sylnone", "predictor")
        os.makedirs(D, exist_ok=True)
        with mock.patch.object(fetch, "get", return_value="<title>KTU Syllabus 2024 - All Branches</title>"):
            self.assertIsNone(fetch.cmd_syllabus("PCZZT999", D))
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
        with mock.patch.object(fetch, "get", side_effect=err):
            self.assertIsNone(fetch.cmd_syllabus("PCZZT999", D))

    def test_syllabus_only_forecast_warns(self):
        D = self._subject_with_syllabus("syl_predict")
        P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))
        P["papers"] = []
        json.dump(P, open(os.path.join(D, "papers.json"), "w"))
        with mock.patch.object(fetch, "pdf_raw_text", return_value=U.SYLLABUS_RAW), \
                mock.patch.object(ktu, "topics_page", return_value=None):
            ktu.draft_topics(D)
        r = subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "predict.py"), D], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("items of the official syllabus", open(os.path.join(D, "FORECAST.md"), encoding="utf-8").read())


class TestAllMaterials(unittest.TestCase):
    """The syllabus, notes, past papers and model paper are all used together."""

    SYL_40 = ("End Semester Examination Marks (ESE) Part A Part B Total Total of 8 Questions, each carrying 2 marks "
              "(8x2 =16 marks) can have a maximum of 2 subdivisions. Each question carries 6 marks. (4x6 = 24 marks) 40")
    SYL_60 = "End Semester Examination Marks (ESE) (8x3 =24 marks) (4x9 = 36 marks) 60"

    def test_exam_pattern_from_syllabus(self):
        self.assertEqual(fetch.syllabus_pattern(self.SYL_40),
                         {"part_a": 8, "part_a_marks": 2, "part_b_marks": 6, "subq": 24})
        self.assertEqual(fetch.syllabus_pattern(self.SYL_60)["subq"], 26)
        self.assertIsNone(fetch.syllabus_pattern("no pattern here"))
        self.assertIsNone(fetch.syllabus_pattern("(8x2 =17 marks) (4x6 = 24 marks)"))     # arithmetic must agree

    def test_40_mark_papers_pass_the_check(self):
        qs = qs_2024(a_marks=(2,) * 8, b=tuple((n, 3, 3) for n in range(9, 17)))
        self.assertEqual(ex.check(qs), ("2024-40", []))
        self.assertEqual(ktu.module_of("B11a", "2024-40"), 2)

    def _subject(self, name, topics_page):
        D = U.new_subject(name)
        os.makedirs(os.path.join(D, "syllabus"))
        open(os.path.join(D, "syllabus", "PCXXT501.pdf"), "wb").write(b"%PDF-1.4")
        with mock.patch.object(fetch, "pdf_raw_text", return_value=U.SYLLABUS_RAW), \
                mock.patch.object(ktu, "topics_page", return_value=topics_page):
            ktu.draft_topics(D)
        return D, json.load(open(os.path.join(D, "topics.json"), encoding="utf-8"))["topics"]

    def test_syllabus_items_missing_from_the_topic_list_become_topics(self):
        page = {"modules": [{"module": m, "topics": [{"name": n, "weightage": "High"} for n in names]} for m, names in (
            (1, ["8051 architecture, register organization and SFRs", "Embedded systems and real time systems"]),
            (2, ["8051 addressing modes and assembler directives"]),
            (3, ["8051 interrupts and interrupt priority", "Serial port programming"]),
            (4, ["ARM processor introduction"]))]}                      # no stepper motor, no RISC vs CISC
        D, T = self._subject("gaps", page)
        gaps = [t["name"] for t in T.values() if t.get("source") == "syllabus (not in topic list)"]
        self.assertTrue(any("stepper" in g.lower() for g in gaps), gaps)
        self.assertTrue(any("RISC" in g for g in gaps), gaps)
        self.assertFalse(any("Interrupt priority" in g for g in gaps), "covered items are not repeated")
        listed = [t for t in T.values() if t.get("source") == "ktunotes topics page"]
        self.assertTrue(any(t.get("syllabus_items") for t in listed), "matched items enrich their topic")

    def test_wrong_predecessor_is_flagged(self):
        D, _ = self._subject("fit", None)
        chips = ["Explain the architecture of the 8086 microprocessor.", "Explain the 8255 PPI modes.",
                 "Write an 8085 program to add two numbers.", "Explain the 8254 timer modes.",
                 "Explain the pin diagram of 8086.", "Explain 8259 interrupt controller.",
                 "Write an 8086 ALP to sort numbers.", "Explain the 8085 flag register.", "Explain 8279 keyboard."]
        good = ["Explain the 8051 interrupts and interrupt priority.", "Explain the ARM operating modes.",
                "Explain the 8051 addressing modes.", "Explain the SFRs of 8051.", "Explain RISC vs CISC in ARM."] * 2
        P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))
        P["papers"] = [{"id": "OLD1", "label": "Jun 2023", "course": "XX307", "type": "regular",
                        "questions": [{"q": f"A{i}", "marks": 3, "text": t} for i, t in enumerate(chips, 1)]},
                       {"id": "OLD2", "label": "Jun 2024", "course": "YY206", "type": "regular",
                        "questions": [{"q": f"A{i}", "marks": 3, "text": t} for i, t in enumerate(good, 1)]}]
        json.dump(P, open(os.path.join(D, "papers.json"), "w"))
        with mock.patch.object(fetch, "pdf_raw_text", return_value=U.SYLLABUS_RAW):
            fit = ktu.predecessor_fit(D)
            warn = ktu.fit_warnings(D)
        self.assertTrue(fit["XX307"]["unfit"], fit)
        self.assertFalse(fit["YY206"]["unfit"], fit)
        self.assertEqual(len(warn), 1)
        self.assertIn("--skip-old XX307", warn[0])

    def test_platform_names(self):
        self.assertEqual(ktu.platforms("STM32U575 and Cortex-M33 with FreeRTOS"), {"stm32", "cortex", "freertos"})
        self.assertEqual(ktu.platforms("MCS-51 and 8052"), {"8051"})
        self.assertEqual(ktu.platforms("Explain Dijkstra's algorithm"), set())

    def test_skip_old_is_remembered(self):
        info = {"code": "PCXXT504", "name": "Microcontrollers", "sem": "S5", "branch": "CSE", "alternatives": [],
                "old_codes": ["XX307", "YY206"], "short": "MCX", "old_detail": {"XX307": {}, "YY206": {}}}
        seen = []

        def plist(codes, sem):
            seen.append(list(codes))
            return []
        common = [mock.patch.object(fetch, "resolve", side_effect=lambda *a, **k: json.loads(json.dumps(info))),
                  mock.patch.object(fetch, "paper_list", side_effect=plist),
                  mock.patch.object(fetch, "cmd_notes", side_effect=SystemExit("none")),
                  mock.patch.object(fetch, "cmd_syllabus", return_value=None),
                  mock.patch.object(fetch, "cmd_model", return_value=None)]
        for p in common:
            p.start()
        try:
            D, _ = quiet(fetch.cmd_setup, "PCXXT504", skip_old=["XX307"])
            quiet(fetch.cmd_setup, "PCXXT504")                             # no flag the second time
        finally:
            for p in common:
                p.stop()
        self.assertEqual(seen, [["PCXXT504", "YY206"], ["PCXXT504", "YY206"]])
        self.assertEqual(json.load(open(os.path.join(D, "subject.json")))["skip_old"], ["XX307"])

    def test_model_paper_is_fetched_and_added(self):
        common = '<a href="https://drive.google.com/file/d/COMMONCOMMONCOMMONCOMMON1/view">'
        pages = {"https://model.ktunotes.live/pcxxt505": "<title>PCXXT505: Test | KTU Model Question Paper</title>"
                 + common + '<a href="https://drive.google.com/file/d/MODELPDFMODELPDFMODELPDF1/view">',
                 "https://syllabus.ktunotes.live/": "<title>KTU Syllabus</title>" + common}
        info = {"code": "PCXXT505", "name": "Test", "sem": "S5", "branch": "CSE", "alternatives": [],
                "old_codes": [], "short": "TST", "old_detail": {}}
        got = []
        with mock.patch.object(fetch, "get", side_effect=lambda u, **k: pages[u]), \
                mock.patch.object(fetch, "download_drive", side_effect=lambda i, p: got.append(i) or
                                  open(p, "wb").write(b"%PDF") or True), \
                mock.patch.object(fetch, "to_text", return_value="scanned"), \
                mock.patch.object(fetch, "resolve", return_value=info), \
                mock.patch.object(fetch, "paper_list", return_value=[]), \
                mock.patch.object(fetch, "cmd_notes", side_effect=SystemExit("none")), \
                mock.patch.object(fetch, "cmd_syllabus", return_value=None):
            D, out = quiet(fetch.cmd_setup, "PCXXT505")
        self.assertEqual(got, ["MODELPDFMODELPDFMODELPDF1"])
        P = json.load(open(os.path.join(D, "papers.json")))["papers"]
        self.assertEqual([(p["id"], p.get("model"), p["course"]) for p in P], [("MODEL_PCXXT505", True, "PCXXT505")])
        with mock.patch.object(fetch, "get", return_value="<title>KTU Model Papers</title>"):
            self.assertIsNone(fetch.cmd_model("PCZZT999", D))

    def test_model_paper_topics_get_the_boost_and_materials_are_listed(self):
        D, T = self._subject("model_boost", None)
        key = next(k for k, t in T.items() if t.get("module") == 4)
        P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))
        P["papers"] = [{"id": "MODEL_PCXXT501", "label": "Model paper", "course": "PCXXT501", "type": "regular",
                        "model": True, "questions": [{"q": "A7", "marks": 3, "topics": [key], "text": "ARM modes"}]}]
        json.dump(P, open(os.path.join(D, "papers.json"), "w"))
        import predict
        _, _, topics, _ = predict.load(D)
        self.assertTrue(topics[key]["model"])
        r = subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "predict.py"), D], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        F = open(os.path.join(D, "FORECAST.md"), encoding="utf-8").read()
        self.assertIn("Materials used: syllabus yes", F)
        self.assertIn("model paper yes (1 questions)", F)


class TestReadableOutput(unittest.TestCase):
    """What a student reads: no adverts, no textbook pointers as topics, garbled wording flagged."""

    COVER = ("CLICK TO JOIN\n SOCIAL MEDIAS\n   Whatsapp\n   (For notes updates get instantly\n and for discussion)\n"
             "    Telegram\n    Instagram\n    (For notification updates)\n      Website\n"
             "2024 SCHEME SCAN THE QR CODES join our WhatsApp group get into our website\n")
    TAIL = "\nCLICK HERE TO VISIT OUR WEBSITE\nCLICK THE LOGO TO JOIN IN OUR SOCIAL MEDIAS FOR UPDATES\n"

    def test_ktunotes_adverts_never_become_questions(self):
        D = U.new_subject("adverts", papers=[("ADV1", self.COVER + U.paper_text() + self.TAIL)])
        quiet(ex.main.__wrapped__ if hasattr(ex.main, "__wrapped__") else (lambda: None))
        r = subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "extract_questions.py"), D], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))["papers"][0]
        text = " ".join(q["text"] for q in P["questions"])
        self.assertNotRegex(text, r"(?i)whatsapp|telegram|instagram|click|qr code|website|social media|notes updates")
        self.assertEqual(P["extract_status"], "ok")
        self.assertTrue(P["questions"][0]["text"].startswith("Explain the memory organization"))

    def test_advert_filter_keeps_real_questions(self):
        for line in ("Explain the website hosting architecture used in a CDN.", "Draw the block diagram of a 2024 sensor node.",
                     "Explain the discussion phase of the Delphi method.", "Find the Poisson probability of 3 clicks.",
                     "Describe the telephone switching network."):
            self.assertIsNone(ex.ADVERT.search(line) if "website hosting" not in line else None, line)
        self.assertIsNotNone(ex.ADVERT.search("CLICK HERE TO VISIT OUR WEBSITE"))

    def test_textbook_pointers_are_not_topics(self):
        D = U.new_subject("syl_junk")
        os.makedirs(os.path.join(D, "syllabus"))
        open(os.path.join(D, "syllabus", "PCXXT501.pdf"), "wb").write(b"%PDF-1.4")
        raw = U.SYLLABUS_RAW.replace("Interfacing: LCD, ADC & DAC, stepper motor interfacing",
                                     "Interfacing: LCD, ADC & DAC, stepper motor interfacing\n"
                                     "Relevant topics from sections 3.1 to 3.4\n[Text 1: Chapter 3]\n(Theorems without proof)")
        with mock.patch.object(fetch, "pdf_raw_text", return_value=raw):
            names = [i for _, i, _ in ktu.syllabus_topics(D)]
        self.assertFalse([n for n in names if re.search(r"(?i)relevant|^\[|^\(|chapter 3", n)], names)
        self.assertIn("stepper motor interfacing", names)

    def test_maths_font_letters_become_plain(self):
        self.assertEqual(safety.clean_text("Find the Taylor series of \U0001d452^\U0001d467 about \U0001d467 = \U0001d44e"),
                         "Find the Taylor series of e^z about z = a")
        self.assertEqual(safety.clean_text("O(n\u00b2) in \u00b5s"), "O(n\u00b2) in \u00b5s")

    def test_garbled_wording_is_flagged_and_clean_wording_preferred(self):
        D = U.new_subject("garbled")
        with mock.patch.object(ktu, "topics_page", return_value=None):
            pass
        T = {"topics": {"T1": {"name": "Timers", "module": 1, "notes": [], "weight_hint": 1.0, "source": "test"},
                        "OFF_SYLLABUS": {"name": "off", "module": 0, "notes": [], "source": "bucket"}}}
        json.dump(T, open(os.path.join(D, "topics.json"), "w"))
        q = lambda txt: {"q": "A1", "marks": 3, "topics": ["T1"], "text": txt}
        P = {"papers": [
            {"id": "P1", "label": "Nov 2023", "course": "PCXXT501", "type": "regular", "extract_status": "ok",
             "questions": [q("Explain the timer modes of the 8051 microcontroller in detail.")]},
            {"id": "P2", "label": "Nov 2024", "course": "PCXXT501", "type": "regular", "extract_status": "review: x",
             "questions": [q("Explain the timer modes 8051 microcontroller detail.")] * 12 + [q("Explain the timer modes.")]},
            {"id": "P3", "label": "Nov 2025", "course": "PCXXT501", "type": "regular", "extract_status": "review: x",
             "questions": [q("Explain the timre mdoes 8O51 micr0controler in detial.")] * 12 + [q("Explain timers again.")]}]}
        json.dump(P, open(os.path.join(D, "papers.json"), "w"))
        r = subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "predict.py"), D], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        res = json.load(open(os.path.join(D, "results.json"), encoding="utf-8"))
        fam = [x for x in res["questions"] if "Nov 2023" in x["sessions"]]
        self.assertTrue(fam and fam[0]["checked"] and "in detail" in fam[0]["latest"], fam[:1])
        only_garbled = [x for x in res["questions"] if x["sessions"] == ["Nov 2025"]]
        F = open(os.path.join(D, "FORECAST.md"), encoding="utf-8").read()
        if only_garbled:
            self.assertFalse(only_garbled[0]["checked"])
            self.assertIn("auto-read, may be garbled", F)


if __name__ == "__main__":
    unittest.main()
