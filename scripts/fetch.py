#!/usr/bin/env python3
"""
Fetch study sources for a KTU subject from ktunotes.live (public Google Drive files).

  python3 fetch.py setup "DAA" [--papers 10] [--old CST306] [--short DAA] [--name "..."] [--notes-from CODE]
        ONE STEP for any course code or name: resolve the course and its older-scheme predecessors, create
        Study Materials/<SHORT>/predictor/, download the notes and the N most recent distinct past papers,
        and write subject.json, a papers.json skeleton and STATUS.md. Then tag topics and run predict.py.
        A code missing from the ktunotes catalogue (e.g. PCEOT501) needs --name "<course name>" from the paper.
        A course with no notes: --notes-from CODE fetches a similar course's notes (setup lists candidates).
  python3 fetch.py resolve "machine learning"           show how a code/name resolves (course + predecessors)
  python3 fetch.py list-notes PCCST502                  show the ktunotes notes folders for a course
  python3 fetch.py notes PCCST502 SUBJECT_DIR [--folder "M1-M4 NOTES"]   download notes PDFs + OUTLINE.txt
  python3 fetch.py pyqs SUBJECT_DIR CST306 PCCST502     download every past paper whose file name has a code
  python3 fetch.py outline FILE.pdf [...]               print the first lines of every page (to build topics.json)
  python3 fetch.py search machine learning              list past-paper files whose names contain all the words

Downloads go to SUBJECT_DIR/notes/ and SUBJECT_DIR/pyq/. For each PDF a .txt (pdftotext -layout) is written;
PDFs without a text layer (scans) are also rendered to PNG pages in pyq/img/ so they can be read as images.
Subject folders: $KTU_STUDY_DIR or ~/Documents/Study Materials/<SHORT>/predictor/. Cache: ~/.cache/ktu-exam-predictor.
Needs: Python 3.8+ (standard library only). Optional: poppler-utils (pdftotext, pdftoppm) or the pypdf package
for PDF text; without either, PDFs are downloaded but not read (ktu.py status says so).
"""
import functools, html, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import safety  # noqa: E402
def _study_root():
    """Where subject folders live: $KTU_STUDY_DIR, else ~/Documents/Study Materials if it exists, else ~/KTU Exams."""
    if os.environ.get("KTU_STUDY_DIR"):
        return os.path.expanduser(os.environ["KTU_STUDY_DIR"])
    legacy = os.path.expanduser("~/Documents/Study Materials")
    return legacy if os.path.isdir(legacy) else os.path.expanduser("~/KTU Exams")


STUDY = _study_root()
CACHE = os.path.expanduser(os.environ.get("KTU_CACHE_DIR", "~/.cache/ktu-exam-predictor"))   # ktunotes indexes
CACHE_DAYS = 7
WORKERS = 8         # parallel downloads + PDF conversions (each is network- or subprocess-bound)
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) study-helper"}


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an allowed https host (Drive redirects to googleusercontent.com)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safety.check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_CheckedRedirect)


TRIES = 3          # tries per page and per file: a timeout or a dropped connection on the first try is common
RETRY_WAIT = 2     # seconds before the second try, doubled before each later one


def _temporary(e):
    """A network error worth another try: timeouts, dropped connections, cut-off reads (http.client's
    IncompleteRead, or a body shorter than its Content-Length, which get() raises as URLError), HTTP 408/429/5xx.
    Not 403/404 or a blocked URL: those answer the same every time."""
    if isinstance(e, safety.BlockedURL):
        return False
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (408, 429) or e.code >= 500
    return isinstance(e, (urllib.error.URLError, OSError)) or any(c.__name__ == "HTTPException" for c in type(e).__mro__)


def get(url, binary=False, timeout=120, tries=None):
    """The only network call in the skill: https to safety.ALLOWED_HOSTS, at most safety.MAX_DOWNLOAD bytes.
    Temporary errors are retried, up to `tries` (default TRIES) tries in all."""
    safety.check_url(url)
    tries = tries or TRIES
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url, headers=UA)
        try:
            with _OPENER.open(req, timeout=timeout) as r:
                safety.check_url(r.geturl())
                data = r.read(safety.MAX_DOWNLOAD + 1)
                length = (getattr(r, "headers", None) or {}).get("Content-Length")
            if length and length.isdigit() and len(data) < int(length) <= safety.MAX_DOWNLOAD:
                # http.client returns a body cut off by a dropped connection without an error: retry it
                raise urllib.error.URLError(f"response cut off: {len(data)} of {length} bytes")
            break
        except Exception as e:
            if attempt == tries or not _temporary(e):
                raise
            time.sleep(RETRY_WAIT * 2 ** (attempt - 1))
    if len(data) > safety.MAX_DOWNLOAD:
        raise safety.BlockedURL(f"download larger than {safety.MAX_DOWNLOAD // 2**20} MB: {url}")
    return data if binary else data.decode("utf-8", "replace")


def cached(name, fn):
    """Cache an index for CACHE_DAYS. Empty results are never cached, so a site hiccup does not stick."""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < CACHE_DAYS * 86400:
        v = json.load(open(p, encoding="utf-8"))
        if v and any(v.values() if isinstance(v, dict) else v):
            return v
    v = fn()
    if v and any(v.values() if isinstance(v, dict) else v):
        json.dump(v, open(p, "w", encoding="utf-8"))
    else:
        print("warning: could not read the ktunotes index (site down or changed); nothing cached", file=sys.stderr)
    return v


# ------------------------------------------------------------------ notes (view.ktunotes.live -> Drive folders)
def viewer():
    """ktunotes viewer bundle: notes Drive folder per course, the course catalogue (in source order: a new
    2024 code usually sits next to its predecessor), and short names such as DAA."""
    def build():
        page = get("https://view.ktunotes.live/")
        js = re.search(r'/assets/index-[A-Za-z0-9_-]+\.js', page).group(0)
        s = get("https://view.ktunotes.live" + js)
        folders = dict(re.findall(r'([a-z]{2,6}\d{3}):`([A-Za-z0-9_-]{25,})`', s))
        catalog = [{"code": c, "name": n, "sem": sm, "branch": b} for c, n, sm, b in
                   re.findall(r'\w+:\{code:`([A-Z0-9]+)`,name:`([^`]+)`,semester:`(S\d)`,branch:`([A-Za-z&]+)`\}', s)]
        short, keywords = {}, {}
        for keys, name, code in re.findall(r'\{keys:\[([^\]]*)\],name:`([^`]+)`,code:`([A-Z0-9]+)`', s):
            m = re.search(r'\(([A-Za-z&]{2,6})\)\s*$', name)
            if m:
                short[code] = m.group(1).upper()
            keywords[code] = re.findall(r'`([^`]+)`', keys)
        return {"folders": folders, "catalog": catalog, "short": short, "keywords": keywords}
    return cached("viewer.json", build)


def notes_root(code):
    m = viewer()["folders"]
    fid = m.get(code.lower())
    if not fid:
        sys.exit(f"{code}: no notes folder in the ktunotes viewer (known: {', '.join(sorted(m))[:300]}...)")
    return fid


def list_folder(fid):
    s = get(f"https://drive.google.com/embeddedfolderview?id={fid}")
    out = []
    for m in re.finditer(r'href="(https://drive\.google\.com/[^"]+)"[^>]*>.*?flip-entry-title">([^<]+)', s, re.S):
        url, name = m.group(1), safety.scrub(html.unescape(m.group(2)).strip())   # names are shown to the agent
        f = re.search(r'/folders/([A-Za-z0-9_-]+)', url)
        d = re.search(r'/file/d/([A-Za-z0-9_-]+)', url)
        if f:
            out.append({"type": "folder", "id": f.group(1), "name": name})
        elif d:
            out.append({"type": "file", "id": d.group(1), "name": name})
    return out


def tree(fid, depth=0, maxdepth=2):
    items = list_folder(fid)
    for it in items:
        if it["type"] == "folder" and depth < maxdepth:
            it["children"] = tree(it["id"], depth + 1, maxdepth)
    return items


def show(items, ind=""):
    for it in items:
        print(f"{ind}{'[D]' if it['type'] == 'folder' else '   '} {it['name']}  ({it['id']})")
        if it.get("children"):
            show(it["children"], ind + "    ")


ERRORS = {}        # path -> why its last download failed (one thread per path), for the materials report
PERMANENT = set()  # paths whose last download failed in a way another try won't change (403/404, blocked, private)


def complete_pdf(data):
    """False for a PDF cut off mid-download: a whole PDF ends with %%EOF. Some files carry padding after it, so
    the last 64 KB are searched. Anything that isn't a PDF is judged by the caller."""
    return data[:4] != b"%PDF" or b"%%EOF" in data[-65536:]


def _failed(path, why):
    """Record why a download failed. why: [(message, temporary)], one per URL tried. The download is permanent
    (fetch_pdf stops retrying) only when every URL failed in a way another try won't change."""
    ERRORS[path] = why[0][0] if why else "could not download"
    if why and not any(t for _, t in why):
        PERMANENT.add(path)
    return False


def download_drive(fid, path):
    """One try per Drive URL (fetch_pdf retries the whole download). True if a whole file was saved."""
    why = []
    for url in (f"https://drive.google.com/uc?export=download&id={fid}",
                f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"):
        try:
            data = get(url, binary=True, timeout=300, tries=1)
        except Exception as e:
            print("   download failed:", e)
            why.append((str(e)[:120], _temporary(e)))
            continue
        if not complete_pdf(data):
            print("   download cut off before the end of the file")
            why.append(("download cut off before the end of the file", True))
            continue
        if data[:4] == b"%PDF" or not data.lstrip().startswith(b"<"):
            open(path, "wb").write(data)
            return True
        why.append(("Drive answered with a web page (private file, or it needs confirmation)", False))
    print("   could not download", fid, "(maybe private or needs confirmation)")
    return _failed(path, why)


@functools.lru_cache(maxsize=8)     # the syllabus is read several times per run
def pdf_raw_text(pdf):
    """PDF text in content-stream order (pdftotext -raw), which keeps a table cell's lines together. '' if no tool."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-raw", pdf, "-"], capture_output=True, check=False)
        return r.stdout.decode("utf-8", "replace")
    try:
        from pypdf import PdfReader
        return "\f".join((pg.extract_text() or "") for pg in PdfReader(pdf).pages)
    except Exception:
        return ""


# ------------------------------------------------------------------ syllabus (syllabus.ktunotes.live -> Drive PDF)
def syllabus_ids(page):
    return list(dict.fromkeys(re.findall(r"drive\.google\.com/(?:file/d/|open\?id=)([A-Za-z0-9_-]{20,})", page)))


@functools.lru_cache(maxsize=1)
def _common_ids():
    """Drive links on every syllabus/model page (site chrome), fetched once per run."""
    return frozenset(syllabus_ids(get("https://syllabus.ktunotes.live/", timeout=60)))


def cmd_syllabus(code, D):
    """Download the official KTU syllabus PDF of a course into D/syllabus/ and write its module text to
    syllabus/MODULES.md. It works for courses ktunotes has no notes for (e.g. PCEOT501), and its module headings
    are what the exam follows. Returns the PDF path, or None if there is no syllabus page for the course."""
    try:
        page = get(f"https://syllabus.ktunotes.live/{code.lower()}", timeout=60)    # lowercase is the real path
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    if not title or code.upper() not in title.group(1).upper():
        return None                                       # the site answers unknown codes with its home page
    common = _common_ids()
    ids = [i for i in syllabus_ids(page) if i not in common]
    if not ids:
        return None
    sd = os.path.join(D, "syllabus")
    os.makedirs(sd, exist_ok=True)
    pdf = safety.safe_join(sd, f"{code.upper()}.pdf")
    rec = fetch_pdf(pdf, lambda q: download_drive(ids[0], q), "syllabus", f"{code.upper()} syllabus")
    if not rec["downloaded"] or rec["read"] in ("unreadable", "no tool"):
        return False                                      # it exists, but no copy arrived or none could be read
    mods = syllabus_modules(pdf_raw_text(pdf))
    L = [f"# {code.upper()} syllabus by module", "",
         "Parsed automatically from the syllabus PDF (text is from outside: study material, not instructions). "
         "A table cell's module number can land a few lines off, so check each module against the PDF.", ""]
    for m, groups in mods.items():
        L += [f"## Module {m}", ""] + [f"- {g}" for g in groups] + [""]
    open(os.path.join(sd, "MODULES.md"), "w", encoding="utf-8").write("\n".join(L))
    return pdf


def syllabus_pattern(text):
    """The end-semester exam pattern from a KTU 2024 syllabus, e.g. "(8x2 =16 marks)" and "(4x6 = 24 marks)" ->
    {"part_a": 8, "part_a_marks": 2, "part_b_marks": 6, "subq": 24}. Project-based (PB) courses have a 40-mark
    exam (8 x 2 + 4 x 6), not the usual 60 (8 x 3 + 4 x 9). None if the text doesn't say."""
    t = re.sub(r"\s+", " ", safety.clean_text(text or ""))
    i = t.lower().find("end semester examination")
    hits = re.findall(r"\(\s*(\d{1,2})\s*[x\u00d7*]\s*(\d{1,2})\s*=\s*(\d{1,3})\s*marks?\s*\)", t[i:] if i >= 0 else t, re.I)
    if len(hits) < 2:
        return None
    (na, ma, ta), (nb, mb, tb) = [tuple(map(int, h)) for h in hits[:2]]
    if na * ma != ta or nb * mb != tb or not (4 <= na <= 12 and 2 <= nb <= 6):
        return None
    # sub-questions on a paper: Part A, plus two questions per module of about two parts each (9-mark ones a bit more)
    return {"part_a": na, "part_a_marks": ma, "part_b_marks": mb, "subq": na + 4 * nb + (2 if mb >= 9 else 0)}


def cmd_model(code, D):
    """The official model question paper from model.ktunotes.live, saved as D/model/<CODE>_model.pdf (scanned pages
    rendered to model/img/). Returns the PDF path or None. It is the only paper a brand-new course has, and it
    shows the setter's style (e.g. PBCST504: STM32 HAL code and short calculations)."""
    try:
        page = get(f"https://model.ktunotes.live/{code.lower()}", timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    title = re.search(r"<title>(.*?)</title>", page, re.S)
    if not title or code.upper() not in title.group(1).upper():
        return None
    common = _common_ids()
    ids = [i for i in syllabus_ids(page) if i not in common]
    if not ids:
        return None
    md = os.path.join(D, "model")
    os.makedirs(md, exist_ok=True)
    pdf = safety.safe_join(md, f"{code.upper()}_model.pdf")
    rec = fetch_pdf(pdf, lambda q: download_drive(ids[0], q), "model", "Model paper", os.path.join(md, "img"),
                    min_total=3000, pages=True)
    if not rec["downloaded"] or rec["read"] == "unreadable":
        return False                                      # it exists, but no copy arrived or none could be read
    return pdf


def syllabus_modules(raw):
    """{module: [syllabus lines]} from the raw text of a KTU 2024 syllabus. The table holds module numbers as
    lone lines, in order (1, 2, 3, ...); other lone numbers are contact hours. A number centred in a tall cell
    can come a few lines late, so a module may start one heading early or late: MODULES.md says to check."""
    raw = safety.clean_text(raw)
    a = re.search(r"(?m)^\s*SYLLABUS\s*$", raw)
    b = re.search(r"(?i)course assessment method|text\s*books?\b|reference\s*books?", raw[a.end():] if a else raw)
    body = raw[a.end():][: b.start()] if a and b else (raw[a.end():] if a else raw)
    mods, cur, want, buf = {}, None, 1, []
    for line in body.replace("\f", "\n").splitlines():
        line = re.sub(r"(?i)downloaded from ktunotes\.live", "", line).strip()
        if not line or re.fullmatch(r"(?i)module|no\.?|syllabus description|contact|hours", line):
            continue
        if re.fullmatch(r"\d{1,2}", line):
            if int(line) == want:
                cur, want = want, want + 1
            continue                                       # contact hours
        if cur is None:
            continue
        mods.setdefault(cur, []).append(line)
    out = {}
    for m, lines in mods.items():                          # join wrapped lines into heading groups
        groups = []
        for l in lines:
            if groups and not re.match(r"^[A-Z0-9][^:]{2,60}:", l) and not groups[-1].endswith((".", ")")) \
                    or (groups and l[:1].islower()):
                groups[-1] += ("" if groups[-1].endswith("-") else " ") + l
            else:
                groups.append(l)
        out[m] = [safety.scrub(re.sub(r"\s+", " ", g)).strip() for g in groups if g.strip()]
    return out


def pdf_text(pdf, txt):
    """PDF -> text file. Uses pdftotext (poppler) if installed, else the pypdf package if installed.
    Returns "ok"; "no tool" if neither exists (the agent can still read the PDF directly); or "unreadable" for a
    damaged or cut-off PDF, which is worth downloading again (a scan reads fine, just with little text)."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-layout", pdf, txt], capture_output=True, check=False)
        if r.returncode == 0 and os.path.exists(txt):
            return "ok"
        if os.path.exists(txt):
            os.remove(txt)
        return "unreadable"
    try:
        from pypdf import PdfReader
    except Exception:
        return "no tool"
    try:
        text = "\f".join((pg.extract_text() or "") for pg in PdfReader(pdf).pages)
    except Exception:
        return "unreadable"
    open(txt, "w", encoding="utf-8").write(text)
    return "ok"


UNREADABLE = "UNREADABLE (damaged or cut-off PDF)"


def render_pages(pdf, imgdir):
    """Page images imgdir/<name>-<n>.jpg. Skipped when this PDF's pages are already there and newer than the PDF,
    so a rerun of prepare doesn't render every paper again."""
    os.makedirs(imgdir, exist_ok=True)
    name = os.path.basename(pdf)[:-4]
    done = [os.path.join(imgdir, f) for f in os.listdir(imgdir)
            if f.startswith(name + "-") and f.endswith(".jpg") and f[len(name) + 1:-4].isdigit()]
    if done and min(os.path.getmtime(f) for f in done) >= os.path.getmtime(pdf):
        return
    subprocess.run(["pdftoppm", "-r", "90", "-jpeg", pdf, os.path.join(imgdir, name)],
                   capture_output=True, check=False)                       # jpeg: ~9x faster than png for scans


def to_text(pdf, imgdir=None, min_total=0, pages=False):
    """PDF -> .txt next to it, and a status line. A PDF with little text is a scan: its pages are rendered to imgdir.
    pages=True renders a question paper's pages even when it has text. ktunotes papers are scans with an OCR text
    layer that passes as text, but the OCR can drop question numbers and words (a watermark or a college stamp
    over the page), and the page images are the only way to read what it lost."""
    txt = pdf[:-4] + ".txt"
    got = pdf_text(pdf, txt)
    if got == "no tool":
        return "no text tool (install poppler-utils, or read the PDF directly)"
    if got != "ok":
        return UNREADABLE
    n = os.path.getsize(txt) if os.path.exists(txt) else 0
    npages = max(1, open(txt, errors="replace", encoding="utf-8").read().count("\f")) if n else 1
    if (n / npages < 300 or n < min_total) and imgdir:   # little text per page = scanned: render pages to read as images
        if not shutil.which("pdftoppm"):
            return "scanned (read the PDF pages directly; pdftoppm not installed)"
        render_pages(pdf, imgdir)
        return "scanned -> " + imgdir
    if pages and imgdir and shutil.which("pdftoppm"):
        render_pages(pdf, imgdir)
        return f"text {n} B (pages in {os.path.basename(imgdir.rstrip(os.sep))}/ to check the text against)"
    return f"text {n} B"


RECORDS = []       # every file this run tried to get and read; setup writes them to materials.json


def read_kind(st):
    """to_text's message -> text | scan | unreadable | no tool."""
    for prefix, kind in (("text", "text"), ("scanned", "scan"), ("UNREADABLE", "unreadable"), ("no text tool", "no tool")):
        if st.startswith(prefix):
            return kind
    return "text" if st else "not downloaded"


def fetch_pdf(path, download, kind, name, imgdir=None, min_total=0, pages=False, **extra):
    """Download `path` (unless it is already there) and read it, with up to TRIES downloads: a failed download, or
    a file that can't be read because it is damaged or cut off, is downloaded again. A file left damaged by an
    earlier run is set aside and put back if no new copy arrives. A failure another try won't change (403/404, a
    blocked URL, a private file) is not retried. `download(path)` returns True once it saved the file (None:
    nothing to download). The outcome goes to RECORDS for the materials report and is returned."""
    attempts, st, err, backup = 0, "", "", None
    PERMANENT.discard(path)
    while True:
        if not os.path.exists(path):
            if download is None or attempts >= TRIES or path in PERMANENT:
                break
            if attempts:
                time.sleep(RETRY_WAIT * 2 ** (attempts - 1))
            attempts += 1
            if not download(path):
                err = ERRORS.pop(path, "") or "download failed"
                continue
        st = to_text(path, imgdir, min_total, pages)
        if st != UNREADABLE:
            err = ""
            break
        err = "the PDF could not be read (damaged or cut off)"
        if download is None or attempts >= TRIES:
            break                                          # keep it: the next prepare tries it again
        if attempts == 0:                                  # from an earlier run: keep it until a new copy arrives
            backup = path + ".old"
            os.replace(path, backup)
        else:
            os.remove(path)
    if backup:
        if os.path.exists(path):
            os.remove(backup)
        else:                                              # every new download failed: the old copy is all there is
            os.replace(backup, path)
            st, err = UNREADABLE, f"the PDF could not be read, and downloading it again failed ({err})"
    permanent = path in PERMANENT
    PERMANENT.discard(path)
    got = os.path.exists(path)
    rec = {"kind": kind, "name": name, "file": path, "downloaded": got, "read": read_kind(st) if got else "not downloaded",
           "status": st if got else "DOWNLOAD FAILED", "attempts": attempts, "error": err, "permanent": permanent, **extra}
    RECORDS.append(rec)
    return rec


def plural_tries(n):
    return f"{n} {'try' if n == 1 else 'tries'}"


def tries_note(rec):
    """One status line for a file: what reading it gave, and how many tries it took or why it failed."""
    if not rec["downloaded"]:
        return f"DOWNLOAD FAILED after {plural_tries(rec['attempts'])} ({rec['error']})"
    extra = f" (on try {rec['attempts']} of {TRIES})" if rec["attempts"] > 1 else ""
    return rec["status"] + extra + (f": {rec['error']}" if rec["error"] else "")


def page_error(e):
    """Why a syllabus or model page wasn't fetched, with the tries get() really made (only temporary errors retry)."""
    return f"page not fetched{f' after {TRIES} tries' if _temporary(e) else ''} ({str(e)[:100]})"


def source_state(kind, result, where):
    """The report line for the syllabus or model paper. result: cmd_syllabus/cmd_model's return value: None (no page),
    False (found, but not downloaded or not readable: the latest RECORDS entry of `kind` says which), or a path."""
    if result is None:
        return f"not found on {where}"
    if result:
        return "downloaded"
    rec = next((r for r in reversed(RECORDS) if r["kind"] == kind), None)
    if rec and rec["downloaded"] and rec["read"] == "no tool":
        return "found, but not read: no PDF text tool installed"
    if rec and rec["downloaded"]:
        return f"found, but the PDF could not be read after {plural_tries(rec['attempts'] or 1)}"
    return f"found, but the download failed after {plural_tries(rec['attempts'] if rec else TRIES)}" + (
        f" ({rec['error']})" if rec and rec["error"] else "")


def outline(pdf, width=110):
    txt = pdf[:-4] + ".txt"
    if not os.path.exists(txt) and pdf_text(pdf, txt) != "ok":
        return [f"(no text for {os.path.basename(pdf)}: read the PDF directly)"]
    pages = open(txt, encoding="utf-8", errors="replace").read().split("\f")
    norm = [[safety.scrub(re.sub(r"\s+", " ", l).strip()) for l in p.splitlines() if l.strip()] for p in pages]
    count = {}
    for ls in norm:
        for l in set(ls):
            count[l] = count.get(l, 0) + 1
    running = {l for l, c in count.items() if c > max(3, len(pages) * 0.3)}   # page headers/footers
    lines = []
    for i, ls in enumerate(norm, 1):
        ls = [l for l in ls if l not in running and "KTUNOTES" not in l.upper()
              and not l.startswith(("About Figure", "Explanation of"))]
        lines.append(f"{i:3d} " + " | ".join(ls[:3])[:width])
    return lines


def siblings(code, name, limit=4):
    """Same-scheme THEORY courses of other branches with a similar name and notes on ktunotes. When a course has
    no notes of its own, their notes are the best stand-in for its syllabus (PCEOT501: PBECT404 covered its 8051
    modules, PBCST504 its ARM module, which no predecessor paper had)."""
    v = viewer()
    out = []
    for c in v["catalog"]:
        cd = c["code"]
        if cd == code or is_old_code(cd) or re.match(r"[A-Z]{4}L", cd) or cd.lower() not in v["folders"]:
            continue
        s = name_sim(name, c["name"])
        if s >= 0.3:
            out.append((s, cd, c["name"], c["branch"]))
    return [f"{cd} {nm} ({br})" for s, cd, nm, br in sorted(out, reverse=True)[:limit]]


def cmd_notes(code, D, folder=None, prefix=""):
    items = tree(notes_root(code))
    show(items)
    pick = []

    def walk(its, path):
        for it in its:
            if it["type"] == "folder":
                walk(it.get("children", []), path + [it["name"]])
            elif it["name"].lower().endswith(".pdf"):
                pick.append((path, it))
    walk(items, [])
    if folder:
        pick = [(p, it) for p, it in pick if any(folder.lower() in x.lower() for x in p)]
    else:   # default: typed notes - skip handwritten/model/Q&A folders; prefer a folder named NOTES
        skip = ("HANDWRITTEN", "MODEL", "Q&A", "QA BANK", "PROBLEM", "PYQ", "QUESTION", "PREVIOUS")
        typed = [(p, it) for p, it in pick if not any(s in x.upper() for x in p for s in skip)]
        pick = [(p, it) for p, it in typed if any("NOTES" in x.upper() for x in p)] or typed
    os.makedirs(os.path.join(D, "notes"), exist_ok=True)
    jobs = {}                                       # path -> (folder path, item, name); first of a duplicate name wins
    for p, it in pick:
        name = (prefix + " " if prefix else "") + safety.safe_name(it["name"])
        if prefix and not re.search(r"(?:^|[^A-Za-z])(?:M|MOD|MODULE)[\s_-]*0?[1-6](?!\d)", name, re.I):
            mm = re.search(r"(?i)\b(?:M|MOD|MODULE)[\s_-]*0?([1-6])\b", " ".join(p))   # module only in the folder name
            if mm:
                name = f"{prefix} M{mm.group(1)} " + name[len(prefix) + 1:]
        jobs.setdefault(safety.safe_join(os.path.join(D, "notes"), name), (p, it, name))

    def get_notes(job):
        path, (p, it, name) = job
        rec = fetch_pdf(path, lambda q: download_drive(it["id"], q), "notes", name, os.path.join(D, "notes", "img"),
                        course=prefix or code)
        return rec
    with ThreadPoolExecutor(WORKERS) as ex:
        done = list(ex.map(get_notes, jobs.items()))
    outl = []
    for (path, (p, it, name)), rec in zip(jobs.items(), done):
        print("->", "/".join(p), "/", it["name"])
        print("  ", tries_note(rec))
        if not rec["downloaded"]:
            continue
        outl += [f"===== {name}"] + outline(path)
    ol = os.path.join(D, "notes", "OUTLINE.txt")
    open(ol, "a" if prefix else "w", encoding="utf-8").write("\n".join(outl) + "\n")
    print("outline written to", os.path.join(D, "notes", "OUTLINE.txt"))


# ------------------------------------------------------------------ past papers (pyq.ktunotes.live static index)
def pyq_index():
    """Crawl the PYQ site's JS chunks for its two static indexes: KTUNOTES_LIVE_*.pdf -> Drive URL, and the MBCET list."""
    def build():
        seen, queue, found = set(), [], {"mapping": {}, "mbcet": []}
        page = get("https://pyq.ktunotes.live/CST303")
        queue += re.findall(r'/_next/static/chunks/[A-Za-z0-9_.-]+\.js', page)
        while queue and len(seen) < 60:
            u = queue.pop(0)
            if u in seen:
                continue
            seen.add(u)
            try:
                s = get("https://pyq.ktunotes.live" + u)
            except Exception:
                continue
            queue += ["/_next/" + c for c in re.findall(r'static/chunks/[A-Za-z0-9_.-]+\.js', s)]
            if "JSON.parse('" in s and ("KTUNOTES_LIVE_" in s or "MBCET Index" in s):
                raw = s[s.index("JSON.parse('") + 12: s.rindex("')")].replace("\\'", "'").replace("\\\\", "\\")
                v = json.loads(raw)
                if isinstance(v, dict):
                    found["mapping"].update(v)
                else:
                    found["mbcet"] += v
            if found["mapping"] and found["mbcet"]:
                break
        return found
    return cached("pyq_index.json", build)


def cmd_pyqs(D, codes):
    idx = pyq_index()
    out = os.path.join(D, "pyq")
    os.makedirs(out, exist_ok=True)
    hits = []
    for name, url in idx["mapping"].items():
        if any(re.search(rf"(^|_){c.upper()}(_|$)", name.upper()) for c in codes):
            m = re.search(r"id=([A-Za-z0-9_-]+)", url) or re.search(r"/d/([A-Za-z0-9_-]+)", url)
            hits.append((name, m.group(1) if m else None, None))
    for r in idx["mbcet"]:
        if any(c.upper() in r["title"].upper() for c in codes):
            hits.append((re.sub(r"[^A-Za-z0-9._-]+", "_", r["title"]) + ".pdf", None, r["url"]))
    print(f"{len(hits)} papers found for {codes}")
    for name, fid, url in sorted(hits):
        path = os.path.join(out, name)
        print("->", name)
        rec = fetch_pdf(path, (lambda q, f=fid: download_drive(f, q)) if fid else (lambda q, u=url: _dl(u, q)),
                        "paper", name, os.path.join(out, "img"), pages=True)
        print("  ", tries_note(rec))


# ------------------------------------------------------------------ one-step setup for any course
STOPW = {"and", "of", "to", "the", "for", "in", "introduction", "fundamentals", "basics", "principles", "a", "with", "amp"}
MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUNE": 6, "JUL": 7, "JULY": 7, "AUG": 8,
          "SEP": 9, "SEPT": 9, "OCT": 10, "NOV": 11, "DEC": 12}
MON_NAME = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def name_tokens(s):
    out = set()
    for w in re.findall(r"[a-z]+", s.lower()):
        if w in STOPW:
            continue
        for suf in ("ations", "ation", "ers", "ing", "ies", "s"):   # crude stemming: algorithms/algorithm, ...
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)] + ("y" if suf == "ies" else "")
                break
        out.add(w)
    return out


def name_sim(a, b):
    A, B = name_tokens(a), name_tokens(b)
    if not A or not B:
        return 0.0
    j = len(A & B) / len(A | B)
    return j   # Jaccard on stemmed name words ("Python for Machine Learning" vs "Machine Learning" = 0.67)


def is_old_code(code):
    return len(code) <= 6          # 2019/2015 scheme codes (CST306, AMT305, CS305) vs 2024 ones (PCCST502)


CODE_RE = re.compile(r"[A-Z]{2,5}\d{3}")


def uncatalogued(code, name=None):
    """A course code missing from the ktunotes catalogue (e.g. PCEOT501, a branch ktunotes does not cover).
    Its name comes from --name, an existing subject folder, or past-paper file names; None if unknown."""
    if not name:
        for d in sorted(os.listdir(STUDY)) if os.path.isdir(STUDY) else []:
            sj = os.path.join(STUDY, d, "predictor", "subject.json")
            if os.path.exists(sj):
                s = json.load(open(sj, encoding="utf-8"))
                if s.get("code") == code:
                    name = s.get("name")
    if not name:
        for f in pyq_index()["mapping"]:
            m = re.match(rf"KTUNOTES_LIVE_{code}_(.+?)_(?:[A-Z]{{3,4}}_)?\d{{4}}_", f)
            if m:
                name = m.group(1).replace("_", " ").replace(" AMP ", " & ").title()
                break
    if not name:
        return None
    m = re.match(r"[A-Z]+(\d)", code)
    return {"code": code, "name": name, "sem": f"S{m.group(1)}" if m and not is_old_code(code) else "", "branch": ""}


def resolve(query, extra_old=(), name=None):
    v = viewer()
    cat = v["catalog"]
    q = query.strip()
    alternatives = []
    hit = next((c for c in cat if c["code"].lower() == q.lower()), None)
    if not hit and CODE_RE.fullmatch(q.upper()):
        hit = uncatalogued(q.upper(), name)
        if not hit:
            sys.exit(f"'{q}' is not in the ktunotes catalogue and has no past papers there. Rerun with the course "
                     f"name from the question paper: --name \"<course name>\" (and --old CODE for a predecessor).")
    if not hit:   # by short name / keyword, then by name similarity (prefer 2024-scheme CSE courses)
        kw = [code for code, ks in v["keywords"].items() if q.lower() in [k.lower() for k in ks]]
        if kw:
            hit = next((c for c in cat if c["code"] == kw[0]), {"code": kw[0], "name": q, "sem": "", "branch": ""})
        else:
            ranked = sorted(cat, key=lambda c: (-name_sim(q, c["name"]), is_old_code(c["code"]), c["branch"] != "CSE"))
            if ranked and name_sim(q, ranked[0]["name"]) >= 0.5:
                hit = ranked[0]
                top = name_sim(q, hit["name"])
                alternatives = [f"{c['code']} {c['name']} ({c['branch']} {c['sem']})" for c in ranked[1:12]
                                if name_sim(q, c["name"]) >= 0.75 * top and c["code"] != hit["code"]][:5]
    if not hit:
        sys.exit(f"Could not resolve '{query}'. Try the exact course code, or `fetch.py search <words>`.")
    code, name = hit["code"], safety.scrub(hit["name"])      # catalogue and --name text are printed to the agent
    # predecessors: older-scheme codes with a similar name (catalogue neighbours and past-paper file names)
    idx = pyq_index()
    avail = {}
    for f in idx["mapping"]:
        m = re.match(r"KTUNOTES_LIVE_([A-Z]{2,5}\d{3})_(.+?)_(?:[A-Z]{3,4}_)?\d{4}_", f)
        if m:
            avail.setdefault(m.group(1), {"name": m.group(2).replace("_", " "), "n": 0})["n"] += 1
    cands = {}
    branch = hit.get("branch") or ""
    votes = {}                                   # letter prefix -> branches, to guess the branch of uncatalogued codes
    for c in cat:
        pre = re.match(r"[A-Z]+", c["code"]).group(0)
        votes.setdefault(pre, {}).setdefault(c["branch"], 0)
        votes[pre][c["branch"]] += 1
    cat_branch = {c["code"]: c["branch"] for c in cat}

    def branch_of(cd):
        if cd in cat_branch:
            return cat_branch[cd]
        v_ = votes.get(re.match(r"[A-Z]+", cd).group(0), {})
        return max(v_, key=v_.get) if v_ else ""
    for i, c in enumerate(cat):
        if c["code"] == code:
            for nb in cat[max(0, i - 2): i + 3]:
                if is_old_code(nb["code"]) and name_sim(name, nb["name"]) >= 0.3:
                    other = branch and nb["branch"] and nb["branch"] != branch
                    cands[nb["code"]] = max(cands.get(nb["code"], 0), name_sim(name, nb["name"]) + 0.3 - (0.5 if other else 0))
    for c in cat:
        if is_old_code(c["code"]) and name_sim(name, c["name"]) >= 0.75:
            other = branch and c["branch"] and c["branch"] != branch          # e.g. an ECE course for a CSE subject
            cands[c["code"]] = max(cands.get(c["code"], 0), name_sim(name, c["name"]) + (0.1 if c["branch"] == branch else 0)
                                   - (0.5 if other else 0))
    for oc, a in avail.items():
        if is_old_code(oc) and name_sim(name, a["name"]) >= 0.75:
            other = branch and branch_of(oc) and branch_of(oc) != branch
            cands[oc] = max(cands.get(oc, 0), name_sim(name, a["name"]) - (0.5 if other else 0))
    cands.pop(code, None)                       # a course is never its own predecessor
    cands = {c: v for c, v in cands.items() if v >= 0.6 or c in {x.upper() for x in extra_old}}
    for oc in extra_old:
        cands[oc.upper()] = 9.9
    old = [oc for oc in sorted(cands, key=lambda c: (-cands[c], -avail.get(c, {}).get("n", 0))) if avail.get(oc, {}).get("n") or oc in extra_old]
    short = v["short"].get(code)
    if not short:
        toks = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in STOPW]
        short = "".join(w[0] for w in toks).upper() if len(toks) > 1 else (toks[0][:5].upper() if toks else code)
    sem = hit.get("sem", "")
    return {"code": code, "name": name, "sem": sem, "branch": hit.get("branch", ""), "alternatives": alternatives,
            "old_codes": old[:4], "short": short,
            "old_detail": {oc: {"score": round(cands[oc], 2), "papers": avail.get(oc, {}).get("n", 0),
                                "name": avail.get(oc, {}).get("name", "")} for oc in old[:6]}}


def paper_list(codes, sem):
    """All past papers for the codes, one per exam session, newest first (current code before predecessors).
    regular/supplementary is guessed from the month and the semester of each paper's own course."""
    idx = pyq_index()
    sems = {c["code"]: c["sem"] for c in viewer()["catalog"]}
    rows = []
    for f, url in idx["mapping"].items():
        m = re.match(r"KTUNOTES_LIVE_([A-Z]{2,5}\d{3})_(.+)\.pdf$", f)
        if not m or m.group(1) not in codes:
            continue
        rest = m.group(2).upper().split("_")
        years = [int(t) for t in rest if re.fullmatch(r"20\d\d", t)]
        mon = next((MONTHS[t] for t in rest if t in MONTHS), 0)
        fid = re.search(r"id=([A-Za-z0-9_-]+)", url) or re.search(r"/d/([A-Za-z0-9_-]+)", url)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(f)).lstrip(".")   # never a path from the index
        rows.append({"file": safe, "code": m.group(1), "year": years[0] if years else 0, "month": mon,
                     "supp": "SUPPLE" in f.upper(),
                     "id": fid.group(1) if fid else None, "url": None})
    for r in idx["mbcet"]:
        c = next((c for c in codes if c in r["title"].upper().replace(" ", "")), None)
        if c:
            t = r["title"].upper()
            mon = next((MONTHS[k] for k in MONTHS if re.search(rf"\b{k}", t)), 0)
            y = re.findall(r"20\d\d", t)
            rows.append({"file": re.sub(r"[^A-Za-z0-9._-]+", "_", r["title"]) + ".pdf", "code": c,
                         "year": int(y[-1]) if y else 0, "month": mon, "id": None, "url": r["url"]})
    best = {}
    for r in rows:   # one file per (code, year, month); a year-only file only if no dated file that year
        k = (r["code"], r["year"], r["month"])
        if k not in best or (best[k]["id"] is None and r["id"]):
            best[k] = r
    out = list(best.values())   # year-only files rank after dated ones; extract_questions.py flags duplicates
    for r in out:
        s = sems.get(r["code"], sem)
        odd = s[1:].isdigit() and int(s[1:]) % 2 == 1
        reg = (r["month"] in (10, 11, 12, 1, 2)) if odd else (r["month"] in (4, 5, 6, 7, 8))
        r["type"] = "supplementary" if r.get("supp") else ("regular" if (r["month"] == 0 or reg) else "supplementary")
        r["label"] = (f"{MON_NAME[r['month']]} {r['year']}" if r["month"] else f"{r['year']}") + f" {r['code']}"
        r["pid"] = (MON_NAME[r["month"]].upper() if r["month"] else "Y") + str(r["year"])[2:] + "_" + r["code"]
    cur = codes[0]
    tier = lambda c: 0 if c == cur else (2 if re.fullmatch(r"[A-Z]{2}\d{3}", c) else 1)   # 2015-scheme codes last
    return sorted(out, key=lambda r: (tier(r["code"]), r["month"] == 0, -r["year"], -r["month"]))


IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def ingest_user_files(D, code):
    """The student's own files, put in the subject folder next to predictor/:
         <SUBJECT>/notes/   notes or syllabus (PDF, or photos)   -> predictor/notes/ ("MY " prefix)
         <SUBJECT>/papers/  past or real question papers: one PDF per paper, or one sub-folder of photos per
                            paper (loose photos count as one paper)  -> predictor/pyq/ + a papers.json entry
       Both folders are created empty so the student knows where to put things. Returns (notes added, papers)."""
    U = os.path.dirname(D)
    un, up = os.path.join(U, "notes"), os.path.join(U, "papers")
    for d in (un, up):
        os.makedirs(d, exist_ok=True)
    added_notes, papers = 0, []
    for f in sorted(os.listdir(un)):
        src, low = os.path.join(un, f), f.lower()
        if os.path.islink(src):          # a link could point anywhere (e.g. a key file); copy only real files
            continue
        if low.endswith(".pdf"):
            dst = safety.safe_join(os.path.join(D, "notes"), "MY " + f)
            new = not os.path.exists(dst)
            if new:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
            rec = fetch_pdf(dst, None, "own notes", safety.scrub(f), os.path.join(D, "notes", "img"))
            if new:
                print("   own notes:", os.path.basename(dst), "->", tries_note(rec))
            added_notes += 1
        elif low.endswith(IMG_EXT):
            os.makedirs(os.path.join(D, "notes", "img"), exist_ok=True)
            dst = safety.safe_join(os.path.join(D, "notes", "img"), "MY " + f)
            shutil.copy(src, dst)
            RECORDS.append({"kind": "own notes", "name": safety.scrub(f), "file": dst, "downloaded": True, "read": "photo",
                            "status": "photo", "attempts": 0, "error": ""})
            added_notes += 1
    groups = {}
    for f in sorted(os.listdir(up)):
        src = os.path.join(up, f)
        if os.path.islink(src):
            continue
        if os.path.isdir(src):
            imgs = [os.path.join(src, x) for x in sorted(os.listdir(src))
                    if x.lower().endswith(IMG_EXT) and not os.path.islink(os.path.join(src, x))]
            if imgs:
                groups[f] = imgs
        elif f.lower().endswith(".pdf"):
            groups[f[:-4]] = [src]
        elif f.lower().endswith(IMG_EXT):
            groups.setdefault("photos", []).append(src)
    for stem, files in groups.items():
        stem = safety.clean_text(stem)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("_.")[:60] or "paper"
        pid = "MY_" + safe.upper()
        if files[0].lower().endswith(".pdf"):
            dst = safety.safe_join(os.path.join(D, "pyq"), "MY_" + safe + ".pdf")
            if not os.path.exists(dst):
                shutil.copy(files[0], dst)
            src_rel = "pyq/" + os.path.basename(dst)
            st = tries_note(fetch_pdf(dst, None, "own paper", safety.scrub(stem), os.path.join(D, "pyq", "img"),
                                      min_total=3000, pages=True, source=src_rel))
        else:
            os.makedirs(os.path.join(D, "pyq", "img"), exist_ok=True)
            for i, f in enumerate(files, 1):
                shutil.copy(f, safety.safe_join(os.path.join(D, "pyq", "img"), f"MY_{safe}-{i}{os.path.splitext(f)[1].lower()}"))
            st, src_rel = f"photos -> pyq/img/MY_{safe}-*", None
            RECORDS.append({"kind": "own paper", "name": safety.scrub(stem), "file": os.path.join(D, "pyq", "img"),
                            "downloaded": True, "read": "photo", "status": st, "attempts": 0, "error": "",
                            "source": "photos:MY_" + safe.upper()})
        cm = re.search(r"(?<![A-Z0-9])[A-Z]{2,5}\d{3}(?![0-9])", os.path.basename(files[0]).upper())   # not "NOV2026"
        mon = next((MON_NAME[MONTHS[t]] for t in re.findall(r"[A-Z]{3,4}", stem.upper()) if t in MONTHS), "")
        yr = re.search(r"20\d\d", stem)
        papers.append({"id": pid, "label": (f"{mon} {yr.group(0)}".strip() if yr else safety.scrub(stem)[:24]) + " (own copy)",
                       "type": "regular", "course": cm.group(0) if cm else code, "source": src_rel, "own": True,
                       "questions": [], "_status": st})
        print(f"   own paper: {safe} -> {st}")
    return added_notes, papers


def cmd_setup(query, n=10, extra_old=(), short=None, name=None, notes_from=(), skip_old=()):
    info = resolve(query, extra_old, name)
    sm = STUDY
    os.makedirs(sm, exist_ok=True)
    folder = None
    for d in sorted(os.listdir(sm)):   # reuse a folder that already holds this course
        sj = os.path.join(sm, d, "predictor", "subject.json")
        if os.path.exists(sj) and json.load(open(sj, encoding="utf-8")).get("code") == info["code"]:
            folder = d
    folder = folder or short or info["short"]
    D = os.path.join(sm, folder, "predictor")
    os.makedirs(D, exist_ok=True)
    sj0 = os.path.join(D, "subject.json")              # predecessors ruled out earlier stay out (--skip-old)
    skip = {c.upper() for c in skip_old} | set((json.load(open(sj0, encoding="utf-8")) if os.path.exists(sj0) else {})
                                               .get("skip_old", []))
    if skip:
        info["old_codes"] = [c for c in info["old_codes"] if c not in skip]
        info["old_detail"] = {c: v for c, v in info["old_detail"].items() if c not in skip}
    print(f"Course: {info['code']} {info['name']} ({info['branch']} {info['sem']})  -> {D}")
    if info["alternatives"]:
        print("  NOTE: the name also matches:", "; ".join(info["alternatives"]),
              "\n  If that is the course you meant, rerun with its exact code.")
    print("Predecessors with past papers:", info["old_detail"] or "none found (use --old CODE)")
    codes = [info["code"]] + info["old_codes"]
    RECORDS.clear()
    ERRORS.clear()
    available = paper_list(codes, info["sem"])
    picks = available[:n]
    print(f"\nPast papers: {len(picks)} selected of {len(available)} available (newest first; "
          f"each download is tried up to {TRIES} times)")
    pq = os.path.join(D, "pyq")
    os.makedirs(pq, exist_ok=True)
    def get_paper(r):
        path = safety.safe_join(pq, r["file"])
        rec = fetch_pdf(path, (lambda q: download_drive(r["id"], q)) if r["id"] else (lambda q: _dl(r["url"], q)),
                        "paper", r["label"], os.path.join(pq, "img"), min_total=3000, pages=True, course=r["code"],
                        source="pyq/" + r["file"])
        return tries_note(rec)
    with ThreadPoolExecutor(WORKERS) as ex:       # file names are distinct (one per code/year/month)
        status = list(zip(picks, ex.map(get_paper, picks)))
    for r, st in status:
        print(f"  {r['label']:22s} {r['type']:13s} {st}")
    try:
        print("\nNotes:")
        cmd_notes(info["code"], D)
        notes_ok = True
    except SystemExit as e:
        print("  ", str(e)[:160])
        notes_ok = False
    sib = []
    for sc in notes_from:          # notes of sibling courses stand in for a course with none of its own
        print(f"\nNotes of sibling course {sc.upper()} (stand-in syllabus):")
        try:
            cmd_notes(sc.upper(), D, prefix=sc.upper())
            sib.append(sc.upper())
        except SystemExit as e:
            print("  ", str(e)[:160])
    syl, found = None, {}
    try:
        syl = cmd_syllabus(info["code"], D)
    except Exception as e:                                 # the syllabus is a bonus: never fail setup over it
        found["syllabus"] = page_error(e)
    if syl:
        n_mods = len(syllabus_modules(pdf_raw_text(syl)))
        found["syllabus"] = f"read ({n_mods} modules)"
        print(f"\nSyllabus: {os.path.relpath(syl, D)} ({n_mods} modules) -> syllabus/MODULES.md")
    else:
        found.setdefault("syllabus", source_state("syllabus", syl, "syllabus.ktunotes.live"))
        print("\nSyllabus:", found["syllabus"])
    model = None
    try:
        model = cmd_model(info["code"], D)
    except Exception as e:
        found["model"] = page_error(e)
    found.setdefault("model", source_state("model", model, "model.ktunotes.live"))
    print(f"\nModel paper: {os.path.relpath(model, D)}" if model else f"\nModel paper: {found['model']}")
    print("\nOwn files (the student's notes/ and papers/ folders):")
    own_notes, own_papers = ingest_user_files(D, info["code"])
    if not own_notes and not own_papers:
        print("   none")
    n_papers = sum(1 for _, st in status if "FAILED" not in st) + len(own_papers)
    U = os.path.dirname(D)
    missing = ([] if notes_ok or sib or own_notes else ["notes"]) + ([] if n_papers else ["past papers"])
    if missing:
        try:                                               # a hint only: never fail setup over it
            cands = siblings(info["code"], info["name"]) if "notes" in missing else []
        except Exception:
            cands = []
        print(f"\n== ASK THE USER: ktunotes has no {' and no '.join(missing)} for {info['code']}.\n"
              f"   Ask the student to download them (college portal, classmates, the department, the KTU syllabus PDF)\n"
              f"   and put them in these folders, then run prepare again:\n"
              + ("   - notes or syllabus (PDF or photos):  " + os.path.join(U, "notes") + "\n" if "notes" in missing else "")
              + ("   - past question papers (one PDF, or one folder of photos, per paper):  " + os.path.join(U, "papers") + "\n"
                 if "past papers" in missing else "")
              + ("   The official syllabus was found (syllabus/MODULES.md): its items become the topics, so new\n"
                 "   content is covered, but notes would still sharpen the ranking.\n" if syl and "notes" in missing else "")
              + "   If they can't or would rather not, carry on with the usual process: "
              + ("predecessor papers" + (f" ({', '.join(info['old_codes'])})" if info["old_codes"] else "")
                 + (", and notes of a similar course with --notes-from CODE: " + "; ".join(cands) if cands else "")
                 + ("." if syl else ". Without notes or a syllabus, content new to this course cannot appear in the forecast.")))
    sj = os.path.join(D, "subject.json")
    cfg = json.load(open(sj, encoding="utf-8")) if os.path.exists(sj) else {}
    if sib:
        cfg["notes_from"] = sorted(set(cfg.get("notes_from", [])) | set(sib))
    if skip:
        cfg["skip_old"] = sorted(skip)
    pat = syllabus_pattern(open(syl[:-4] + ".txt", encoding="utf-8", errors="replace").read()) \
        if syl and os.path.exists(syl[:-4] + ".txt") else None
    if pat and str(cfg.get("_pattern_note", "assumed")).startswith("assumed"):
        cfg["pattern"] = pat
        cfg["_pattern_note"] = (f"from the syllabus: {pat['part_a']} x {pat['part_a_marks']} + "
                                f"{cfg.get('modules', 4)} modules x {pat['part_b_marks']}")
        print("Exam pattern", cfg["_pattern_note"])
    cfg.update({"code": info["code"], "name": info["name"], "short": folder, "old_codes": info["old_codes"],
                "semester": info["sem"]})
    if is_old_code(info["code"]):     # a 2019-scheme (or older) course: 10 x 3 + 5 modules x 14
        cfg.setdefault("modules", 5)
        cfg.setdefault("pattern", {"part_a": 10, "part_a_marks": 3, "part_b_marks": 14, "subq": 30})
        cfg.setdefault("_pattern_note", "assumed 2019-scheme pattern (10x3 + 5x14); verify with a recent paper")
    else:
        cfg.setdefault("modules", 4)
        cfg.setdefault("pattern", {"part_a": 8, "part_a_marks": 3, "part_b_marks": 9, "subq": 26})
        cfg.setdefault("_pattern_note", "assumed 2024-scheme pattern (8x3 + 4x9); verify with the model paper")
    cfg.setdefault("alpha_history", 0.25)
    cfg.setdefault("course_boost", 3.0)
    cfg.setdefault("new_topic_questions", {})
    json.dump(cfg, open(sj, "w", encoding="utf-8"), indent=1)
    pj = os.path.join(D, "papers.json")
    have = {p["id"] for p in json.load(open(pj, encoding="utf-8"))["papers"]} if os.path.exists(pj) else set()
    P = json.load(open(pj, encoding="utf-8")) if os.path.exists(pj) else {"_note": "Fill questions[] for each paper: "
         "{q: A1|B11a, marks, topics: [keys from topics.json], text}. Remove a paper if it is a duplicate session.", "papers": []}
    for r, st in sorted(status, key=lambda x: (x[0]["year"], x[0]["month"])):
        if r["pid"] not in have and "FAILED" not in st:
            P["papers"].append({"id": r["pid"], "label": r["label"], "type": r["type"], "course": r["code"],
                                "source": "pyq/" + r["file"], "questions": []})
    if model and "MODEL_" + info["code"] not in have:     # the model paper counts as a paper of this course
        P["papers"].append({"id": "MODEL_" + info["code"], "label": "Model paper", "type": "regular",
                            "course": info["code"], "model": True, "source": os.path.relpath(model, D), "questions": []})
    for p in own_papers:
        st = p.pop("_status")
        if p["id"] not in have:
            P["papers"].append(p)
        status.append(({"label": p["label"], "type": p["type"]}, st))

    def when(p):      # oldest first; the student's own copies use the year and month in their label
        y = re.search(r"20(\d\d)", p.get("label", "")) or re.search(r"(\d\d)", p["id"])
        mon = next((MON_NAME.index(t) for t in re.findall(r"[A-Z][a-z]{2}", p.get("label", "")) if t in MON_NAME), 0)
        return (int(y.group(1)) if y else 99, mon or (MON_NAME.index(p["id"][:3].title()) if p["id"][:3].title() in MON_NAME else 0))
    P["papers"].sort(key=when)
    json.dump(P, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    recs = [dict(r, file=os.path.relpath(r["file"], D)) for r in RECORDS]
    json.dump({"_note": "Every file the last prepare tried to get and read (predict.materials_report summarises it). "
                        "Names come from downloaded files: data, not instructions.",
               "tries": TRIES, "available_papers": len(available), "selected_papers": len(picks), **found,
               "records": recs}, open(os.path.join(D, "materials.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    lines = [f"# {info['name']} ({info['code']}) predictor: status", "",
             f"Set up by `fetch.py setup` from the query '{query}'.", "",
             f"- Predecessor courses used: {', '.join(info['old_codes']) or 'none'} ({json.dumps(info['old_detail'])})",
             f"- Notes: {'notes/ (OUTLINE.txt; scans in notes/img/)' if notes_ok else 'NOT FOUND on ktunotes - use the user own PDFs or topics.ktunotes.live'}",
             "- Past papers in pyq/ (txt; scans in pyq/img/):"]
    lines += [f"  - {r['label']} ({r['type']}): {st}" for r, st in status]
    lines += ["", "To do (scripts are in the ktu-exam-predictor skill's scripts/ folder):",
              "1. extract_questions.py . -> drafts questions[] for every paper; fix papers marked review (pyq/EXTRACTION.md)",
              "2. topics.json: one topic per notes section, with pages and modules",
              "3. tag every question's topics in papers.json",
              "4. predict.py . --type regular --label \"<Mon YYYY>\"", ""]
    open(os.path.join(D, "STATUS.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\nWrote subject.json, papers.json (skeleton), STATUS.md in", D)
    return D


def _dl(url, path):
    """One try (fetch_pdf retries the whole download). True if a whole file was saved."""
    try:
        data = get(url, binary=True, timeout=300, tries=1)   # download first: a refused URL leaves no empty file behind
    except Exception as e:
        print("   failed:", e)
        return _failed(path, [(str(e)[:120], _temporary(e))])
    if not complete_pdf(data):
        print("   failed: download cut off before the end of the file")
        return _failed(path, [("download cut off before the end of the file", True)])
    open(path, "wb").write(data)
    return True


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    def opt(name, default=None):
        return a[a.index(name) + 1] if name in a else default
    if a[0] == "setup":
        olds = [a[i + 1] for i, x in enumerate(a) if x == "--old"]
        sibs = [a[i + 1] for i, x in enumerate(a) if x == "--notes-from"]
        skips = [a[i + 1] for i, x in enumerate(a) if x == "--skip-old"]
        cmd_setup(a[1], int(opt("--papers", 10)), olds, opt("--short"), opt("--name"), sibs, skips)
    elif a[0] == "resolve":
        olds = [a[i + 1] for i, x in enumerate(a) if x == "--old"]
        skip = {i + 1 for i, x in enumerate(a) if x in ("--old", "--name")}
        q = " ".join(x for i, x in enumerate(a[1:], 1) if not x.startswith("--") and i not in skip)
        print(json.dumps(resolve(q, olds, opt("--name")), indent=1))
    elif a[0] == "list-notes":
        show(tree(notes_root(a[1])))
    elif a[0] == "notes":
        f = a[a.index("--folder") + 1] if "--folder" in a else None
        cmd_notes(a[1], a[2], f)
    elif a[0] == "pyqs":
        cmd_pyqs(a[1], a[2:])
    elif a[0] == "outline":
        for p in a[1:]:
            print("=====", p); print("\n".join(outline(p)))
    elif a[0] == "search":   # find past papers by words in the file name, e.g. search machine learning
        idx, words = pyq_index(), [w.upper() for w in a[1:]]
        names = sorted(idx["mapping"]) + sorted(r["title"] for r in idx["mbcet"])
        for n in names:
            if all(w in n.upper().replace("_", " ") for w in words):
                print(n)
    else:
        sys.exit(__doc__)
