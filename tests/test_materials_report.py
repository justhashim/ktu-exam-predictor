"""Downloads and reads are retried, and the student is told what was found, downloaded, read and used.

A paper or notes PDF that fails once (a timeout, a dropped connection, a truncated file) used to be left out
silently; rerunning prepare by hand found it on the third try. Now each file gets up to fetch.TRIES tries, and
every file is recorded in materials.json, which predict.materials_report() turns into the report that `prepare`
prints before the forecast."""
import contextlib, io, json, os, subprocess, sys, unittest, urllib.error
from unittest import mock

import _util as U
import fetch
import ktu
import predict

GOOD_PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
CUT_PDF = b"%PDF-1.4\n1 0 obj << >> end"                       # the connection dropped mid-file


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = fn(*a, **k)
    return r, out.getvalue()


def fake_pdf_text(pdf, txt):
    """pdftotext stand-in: a complete PDF gives a page of text, a cut one fails like the real tool."""
    if b"%%EOF" not in open(pdf, "rb").read():
        return "unreadable"
    open(txt, "w", encoding="utf-8").write("x" * 4000)
    return "ok"


class Resp:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return "https://drive.google.com/x"

    def read(self, n=-1):
        return self.data


def setUpModule():
    os.environ.pop("KTU_NO_NETWORK", None)       # these tests reach fetch.get; _OPENER is always mocked
    fetch.RETRY_WAIT = 0


def tearDownModule():
    os.environ["KTU_NO_NETWORK"] = "1"


class TestGetRetries(unittest.TestCase):
    def test_temporary_errors_are_retried(self):
        calls = []

        def flaky(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.URLError("timed out")
            return Resp(b"page")
        with mock.patch.object(fetch._OPENER, "open", side_effect=flaky):
            self.assertEqual(fetch.get("https://drive.google.com/a"), "page")
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_the_last_try(self):
        with mock.patch.object(fetch._OPENER, "open", side_effect=TimeoutError("timed out")) as op:
            with self.assertRaises(TimeoutError):
                fetch.get("https://drive.google.com/a")
        self.assertEqual(op.call_count, fetch.TRIES)

    def test_missing_pages_and_blocked_urls_are_not_retried(self):
        e404 = urllib.error.HTTPError("https://drive.google.com/a", 404, "Not Found", {}, None)
        with mock.patch.object(fetch._OPENER, "open", side_effect=e404) as op:
            with self.assertRaises(urllib.error.HTTPError):
                fetch.get("https://drive.google.com/a")
        self.assertEqual(op.call_count, 1)
        with mock.patch.object(fetch._OPENER, "open") as op:
            with self.assertRaises(fetch.safety.BlockedURL):
                fetch.get("https://evil.example/a")
        self.assertEqual(op.call_count, 0)


class TestFetchPdf(unittest.TestCase):
    def setUp(self):
        self.d = os.path.join(U.TMP, "fetchpdf", self._testMethodName)
        os.makedirs(self.d, exist_ok=True)
        fetch.RECORDS.clear()

    def test_truncated_download_is_rejected_and_retried(self):
        data = iter([CUT_PDF, GOOD_PDF])
        with mock.patch.object(fetch, "get", side_effect=lambda *a, **k: next(data)), \
                mock.patch.object(fetch, "pdf_text", side_effect=fake_pdf_text):
            rec, _ = quiet(fetch.fetch_pdf, os.path.join(self.d, "p.pdf"), lambda p: fetch._dl("https://drive.google.com/x", p),
                           kind="paper", name="Nov 2025")
        self.assertEqual((rec["downloaded"], rec["read"], rec["attempts"]), (True, "text", 2))
        self.assertEqual(fetch.RECORDS, [rec])

    def test_unreadable_file_is_downloaded_again(self):
        data = iter([CUT_PDF, CUT_PDF, GOOD_PDF])

        def dl(path):                       # a downloader that does not check completeness itself
            open(path, "wb").write(next(data))
            return True
        with mock.patch.object(fetch, "pdf_text", side_effect=fake_pdf_text):
            rec, _ = quiet(fetch.fetch_pdf, os.path.join(self.d, "n.pdf"), dl, kind="notes", name="M1 notes")
        self.assertEqual((rec["read"], rec["attempts"]), ("text", 3))

    def test_a_file_left_damaged_by_an_earlier_run_is_replaced(self):
        path = os.path.join(self.d, "old.pdf")
        open(path, "wb").write(CUT_PDF)
        with mock.patch.object(fetch, "pdf_text", side_effect=fake_pdf_text):
            rec, _ = quiet(fetch.fetch_pdf, path, lambda p: open(p, "wb").write(GOOD_PDF) or True, kind="paper", name="x")
        self.assertEqual((rec["read"], rec["attempts"]), ("text", 1))

    def test_failure_after_every_try_is_recorded_with_the_reason(self):
        e500 = urllib.error.HTTPError("https://drive.google.com/x", 500, "Server Error", {}, None)
        path = os.path.join(self.d, "f.pdf")
        with mock.patch.object(fetch, "get", side_effect=e500) as g:
            rec, _ = quiet(fetch.fetch_pdf, path, lambda p: fetch.download_drive("ID", p), kind="paper", name="May 2022")
        self.assertEqual(g.call_count, 2 * fetch.TRIES, "both Drive URLs, on every try")
        self.assertEqual((rec["downloaded"], rec["read"], rec["attempts"]), (False, "not downloaded", fetch.TRIES))
        self.assertIn("HTTP Error 500", rec["error"])

    def test_damaged_pdf_is_not_reported_as_a_scan_or_a_missing_tool(self):
        path = os.path.join(self.d, "bad.pdf")
        open(path, "wb").write(CUT_PDF)
        with mock.patch.object(fetch, "pdf_text", return_value="unreadable"):
            st = fetch.to_text(path, os.path.join(self.d, "img"))
        self.assertTrue(st.startswith("UNREADABLE"), st)
        self.assertNotIn("no text tool", st)

    def test_a_scan_is_not_downloaded_again(self):
        n = []

        def dl(path):
            n.append(1)
            open(path, "wb").write(GOOD_PDF)
            return True
        with mock.patch.object(fetch, "to_text", return_value="scanned -> img"):
            rec, _ = quiet(fetch.fetch_pdf, os.path.join(self.d, "s.pdf"), dl, kind="paper", name="scan")
        self.assertEqual((rec["read"], len(n)), ("scan", 1))


class TestSetupManifest(unittest.TestCase):
    """setup records every file it tried; the report says what was found, downloaded, read and used."""

    @classmethod
    def setUpClass(cls):
        info = {"code": "PCXXT509", "name": "Test Course", "sem": "S5", "branch": "CSE", "alternatives": [],
                "old_codes": ["XX309"], "short": "MAN", "old_detail": {"XX309": {}}}
        picks = [{"file": f"P{i}.pdf", "id": f"ID{i}", "url": None, "label": f"Nov 202{i}", "type": "regular",
                  "code": "XX309", "pid": f"Nov2{i}", "year": 2020 + i, "month": 11} for i in range(1, 4)]
        calls = {}

        def get(url, **k):               # ID2 is cut off on both Drive URLs of the first try; ID3 never downloads
            fid = url.split("id=")[1].split("&")[0]
            calls[fid] = calls.get(fid, 0) + 1
            if fid == "ID3":
                raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
            return CUT_PDF if fid == "ID2" and calls[fid] <= 2 else GOOD_PDF
        notes = [{"type": "folder", "id": "F", "name": "NOTES", "children": [
            {"type": "file", "id": "N1", "name": "M1.pdf"}, {"type": "file", "id": "N2", "name": "M2.pdf"}]}]
        with mock.patch.object(fetch, "resolve", return_value=info), \
                mock.patch.object(fetch, "paper_list", return_value=picks + [dict(picks[0], file="P9.pdf")]), \
                mock.patch.object(fetch, "notes_root", return_value="ROOT"), \
                mock.patch.object(fetch, "tree", return_value=notes), \
                mock.patch.object(fetch, "outline", return_value=[]), \
                mock.patch.object(fetch, "get", side_effect=get), \
                mock.patch.object(fetch, "pdf_text", side_effect=fake_pdf_text), \
                mock.patch.object(fetch, "cmd_syllabus", return_value=None), \
                mock.patch.object(fetch, "cmd_model", return_value=None):
            cls.D, cls.out = quiet(fetch.cmd_setup, "PCXXT509", n=3)
        cls.M = json.load(open(os.path.join(cls.D, "materials.json"), encoding="utf-8"))

    def test_manifest_lists_every_file_with_its_tries(self):
        papers = {r["name"]: r for r in self.M["records"] if r["kind"] == "paper"}
        self.assertEqual(sorted(papers), ["Nov 2021", "Nov 2022", "Nov 2023"])
        self.assertEqual(papers["Nov 2022"]["attempts"], 2)
        self.assertEqual(papers["Nov 2023"]["attempts"], fetch.TRIES)
        self.assertIn("HTTP Error 503", papers["Nov 2023"]["error"])
        self.assertEqual(papers["Nov 2022"]["read"], "text")
        self.assertFalse(papers["Nov 2023"]["downloaded"])
        self.assertEqual(self.M["available_papers"], 4)
        self.assertEqual(len([r for r in self.M["records"] if r["kind"] == "notes"]), 2)

    def test_report_counts_and_names_what_was_missed(self):
        rep = "\n".join(predict.materials_report(self.D))
        self.assertIn("Past papers: 3 selected of 4 available", rep)
        self.assertIn("2 downloaded (1 after a retry)", rep)
        self.assertIn("1 failed after 3 tries", rep)
        self.assertIn("Nov 2023 XX309: download failed after 3 tries (HTTP Error 503: Service Unavailable)", rep)
        self.assertIn("Needed more than one try: Nov 2022 (2 tries)", rep)
        self.assertIn("Notes: 2 PDFs found, 2 downloaded, 2 with text", rep)
        self.assertIn("Syllabus: not found", rep)
        self.assertIn("rerun prepare", rep)

    def test_notes_kept_from_an_earlier_run_are_counted(self):
        nd = os.path.join(self.D, "notes")
        open(os.path.join(nd, "OLD Problems.pdf"), "wb").write(GOOD_PDF)       # downloaded by an earlier prepare
        open(os.path.join(nd, "OLD Problems.txt"), "w").write("x" * 4000)
        try:
            rep = "\n".join(predict.materials_report(self.D))
        finally:
            os.remove(os.path.join(nd, "OLD Problems.pdf"))
            os.remove(os.path.join(nd, "OLD Problems.txt"))
        self.assertIn("Notes: 2 PDFs found, 2 downloaded, 2 with text", rep)
        self.assertIn("plus 1 kept from an earlier run (OLD Problems.pdf)", rep)
        self.assertIn("3 used for topics and ranking", rep)

    def test_report_works_without_a_manifest(self):
        D = U.new_subject("nomanifest", papers=[("P0", U.paper_text())])
        rep = "\n".join(predict.materials_report(D))
        self.assertIn("Past papers: 1 in papers.json", rep)
        self.assertIn("Used in the forecast: 0 papers", rep)


class TestUsedInForecast(unittest.TestCase):
    def test_each_unused_paper_has_a_reason(self):
        D = U.new_subject("used", papers=[("P0", U.paper_text())])
        good = [{"q": f"A{i}", "marks": 3, "topics": ["T"], "text": f"q{i}"} for i in range(1, 9)] + \
               [{"q": f"B{n}", "marks": 9, "topics": ["T"], "text": f"b{n}"} for n in range(9, 17)]
        P = {"papers": [
            {"id": "A", "label": "Nov 2021", "course": "PCXXT501", "type": "regular", "questions": good, "source": "pyq/A.pdf"},
            {"id": "B", "label": "Nov 2022", "course": "PCXXT501", "type": "regular", "questions": [], "source": "pyq/B.pdf"},
            {"id": "C", "label": "Dec 2022", "course": "PCXXT501", "type": "regular", "questions": good,
             "duplicate_of": "A", "source": "pyq/C.pdf"},
            {"id": "E", "label": "May 2023", "course": "PCXXT501", "type": "regular", "source": "pyq/E.pdf",
             "questions": good[:3], "extract_status": "review: 3 questions"},
            {"id": "MODEL_PCXXT501", "label": "Model paper", "course": "PCXXT501", "type": "regular", "model": True,
             "questions": good[:5], "source": "model/PCXXT501_model.pdf"}]}
        json.dump(P, open(os.path.join(D, "papers.json"), "w"))
        os.makedirs(os.path.join(D, "pyq", "img"), exist_ok=True)
        open(os.path.join(D, "pyq", "img", "B-1.jpg"), "wb").write(b"jpg")
        rep = "\n".join(predict.materials_report(D))
        self.assertIn("Used in the forecast: 1 paper (16 questions)", rep)
        self.assertIn("Nov 2022: no questions yet (scan: transcribe it from pyq/img/)", rep)
        self.assertIn("Dec 2022: duplicate of Nov 2021", rep)
        self.assertIn("May 2023: drafted questions look garbled", rep)
        self.assertIn("Model paper: 5 questions used", rep)
        _, out = quiet(ktu.status, D)                  # status's "Materials used" line agrees with the report
        self.assertIn("past papers 1 PCXXT501;", out)


class TestReportBeforeForecast(unittest.TestCase):
    def test_prepare_prints_the_report_before_the_forecast_and_forecast_md_has_it(self):
        texts = [U.paper_text(), U.paper_text().replace("8051", "8085"), U.paper_text().replace("Explain", "Describe")]
        D = U.new_subject("order", papers=[(f"P{i}", t) for i, t in enumerate(texts)])
        argv = ["ktu.py", "prepare", "PCXXT501", "--label", "Nov 2026"]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(ktu.fetch, "cmd_setup", return_value=D), \
                mock.patch.object(ktu, "topics_page", return_value=None):
            _, out = quiet(ktu.main)
        self.assertIn("== MATERIALS REPORT", out)
        self.assertLess(out.index("== MATERIALS REPORT"), out.index("== 5/5 forecast"))
        F = open(os.path.join(D, "FORECAST.md"), encoding="utf-8").read()
        self.assertIn("## Materials report", F)
        self.assertLess(F.index("## Materials report"), F.index("## Study order"))
        self.assertIn("Used in the forecast: 3 papers", F)

    def test_status_includes_the_report(self):
        D = U.new_subject("statusrep", papers=[("P0", U.paper_text())])
        subprocess.run([sys.executable, os.path.join(U.SCRIPTS, "extract_questions.py"), D], capture_output=True, check=True)
        _, out = quiet(ktu.status, D)
        self.assertIn("Past papers:", out)
        self.assertIn("Used in the forecast:", out)


if __name__ == "__main__":
    unittest.main()
