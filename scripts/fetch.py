#!/usr/bin/env python3
"""
Fetch study sources for a KTU subject from ktunotes.live (public Google Drive files).

  python3 fetch.py setup "DAA" [--papers 10] [--old CST306] [--short DAA]
        ONE STEP for any course code or name: resolve the course and its older-scheme predecessors, create
        Study Materials/<SHORT>/predictor/, download the notes and the N most recent distinct past papers,
        and write subject.json, a papers.json skeleton and STATUS.md. Then tag topics and run predict.py.
  python3 fetch.py resolve "machine learning"           show how a code/name resolves (course + predecessors)
  python3 fetch.py list-notes PCCST502                  show the ktunotes notes folders for a course
  python3 fetch.py notes PCCST502 SUBJECT_DIR [--folder "M1-M4 NOTES"]   download notes PDFs + OUTLINE.txt
  python3 fetch.py pyqs SUBJECT_DIR CST306 PCCST502     download every past paper whose file name has a code
  python3 fetch.py outline FILE.pdf [...]               print the first lines of every page (to build topics.json)
  python3 fetch.py search machine learning              list past-paper files whose names contain all the words

Downloads go to SUBJECT_DIR/notes/ and SUBJECT_DIR/pyq/. For each PDF a .txt (pdftotext -layout) is written;
PDFs without a text layer (scans) are also rendered to PNG pages in pyq/img/ so they can be read as images.
Subject folders: $KTU_STUDY_DIR or ~/Documents/Study Materials/<SHORT>/predictor/. Cache: ~/.cache/ktu-exam-predictor.
Needs: python3 (stdlib only), poppler-utils (pdftotext, pdftoppm).
"""
import html, json, os, re, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STUDY = os.environ.get("KTU_STUDY_DIR", os.path.expanduser("~/Documents/Study Materials"))   # subject folders live here
CACHE = os.path.expanduser("~/.cache/ktu-exam-predictor")   # ktunotes indexes; delete to refresh
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) study-helper"}


def get(url, binary=False, timeout=120):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


def cached(name, fn):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, name)
    if os.path.exists(p):
        return json.load(open(p))
    v = fn()
    json.dump(v, open(p, "w"))
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
        url, name = m.group(1), html.unescape(m.group(2)).strip()
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


def download_drive(fid, path):
    for url in (f"https://drive.google.com/uc?export=download&id={fid}",
                f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"):
        try:
            data = get(url, binary=True, timeout=300)
        except Exception as e:
            print("   download failed:", e)
            continue
        if data[:4] == b"%PDF" or not data.lstrip().startswith(b"<"):
            open(path, "wb").write(data)
            return True
    print("   could not download", fid, "(maybe private or needs confirmation)")
    return False


def to_text(pdf, imgdir=None, min_total=0):
    txt = pdf[:-4] + ".txt"
    subprocess.run(["pdftotext", "-layout", pdf, txt], check=False)
    n = os.path.getsize(txt) if os.path.exists(txt) else 0
    pages = max(1, open(txt, errors="replace").read().count("\f")) if n else 1
    if (n / pages < 300 or n < min_total) and imgdir:   # little text per page = scanned: render pages to read as images
        os.makedirs(imgdir, exist_ok=True)
        base = os.path.join(imgdir, os.path.basename(pdf)[:-4])
        subprocess.run(["pdftoppm", "-r", "90", "-png", pdf, base], check=False)
        return "scanned -> " + imgdir
    return f"text {n} B"


def outline(pdf, width=110):
    txt = pdf[:-4] + ".txt"
    if not os.path.exists(txt):
        subprocess.run(["pdftotext", "-layout", pdf, txt], check=False)
    pages = open(txt, encoding="utf-8", errors="replace").read().split("\f")
    norm = [[re.sub(r"\s+", " ", l).strip() for l in p.splitlines() if l.strip()] for p in pages]
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


def cmd_notes(code, D, folder=None):
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
        skip = ("HANDWRITTEN", "MODEL", "Q&A", "QA BANK", "SET ")
        typed = [(p, it) for p, it in pick if not any(s in x.upper() for x in p for s in skip)]
        pick = [(p, it) for p, it in typed if any("NOTES" in x.upper() for x in p)] or typed
    os.makedirs(os.path.join(D, "notes"), exist_ok=True)
    outl = []
    for p, it in pick:
        name = re.sub(r"[^A-Za-z0-9._ -]+", "_", it["name"])
        path = os.path.join(D, "notes", name)
        print("->", "/".join(p), "/", it["name"])
        if not os.path.exists(path) and not download_drive(it["id"], path):
            continue
        print("  ", to_text(path, os.path.join(D, "notes", "img")))
        outl += [f"===== {name}"] + outline(path)
    open(os.path.join(D, "notes", "OUTLINE.txt"), "w").write("\n".join(outl) + "\n")
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
        if not os.path.exists(path):
            ok = download_drive(fid, path) if fid else _dl(url, path)
            if not ok:
                continue
        print("  ", to_text(path, os.path.join(out, "img")))


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
    return max(j, 0.8 if (A <= B or B <= A) else 0.0)


def is_old_code(code):
    return len(code) <= 6          # 2019/2015 scheme codes (CST306, AMT305, CS305) vs 2024 ones (PCCST502)


def resolve(query, extra_old=()):
    v = viewer()
    cat = v["catalog"]
    q = query.strip()
    hit = next((c for c in cat if c["code"].lower() == q.lower()), None)
    if not hit:   # by short name / keyword, then by name similarity (prefer 2024-scheme CSE courses)
        kw = [code for code, ks in v["keywords"].items() if q.lower() in [k.lower() for k in ks]]
        if kw:
            hit = next((c for c in cat if c["code"] == kw[0]), {"code": kw[0], "name": q, "sem": "", "branch": ""})
        else:
            ranked = sorted(cat, key=lambda c: (-name_sim(q, c["name"]), is_old_code(c["code"]), c["branch"] != "CSE"))
            if ranked and name_sim(q, ranked[0]["name"]) >= 0.5:
                hit = ranked[0]
    if not hit:
        sys.exit(f"Could not resolve '{query}'. Try the exact course code, or `fetch.py search <words>`.")
    code, name = hit["code"], hit["name"]
    # predecessors: older-scheme codes with a similar name (catalogue neighbours and past-paper file names)
    idx = pyq_index()
    avail = {}
    for f in idx["mapping"]:
        m = re.match(r"KTUNOTES_LIVE_([A-Z]{2,5}\d{3})_(.+?)_(?:[A-Z]{3,4}_)?\d{4}_", f)
        if m:
            avail.setdefault(m.group(1), {"name": m.group(2).replace("_", " "), "n": 0})["n"] += 1
    cands = {}
    for i, c in enumerate(cat):
        if c["code"] == code:
            for nb in cat[max(0, i - 2): i + 3]:
                if is_old_code(nb["code"]) and name_sim(name, nb["name"]) >= 0.3:
                    cands[nb["code"]] = max(cands.get(nb["code"], 0), name_sim(name, nb["name"]) + 0.3)
    for c in cat:
        if is_old_code(c["code"]) and name_sim(name, c["name"]) >= 0.5:
            cands[c["code"]] = max(cands.get(c["code"], 0), name_sim(name, c["name"]) + (0.1 if c["branch"] == "CSE" else 0))
    for oc, a in avail.items():
        if is_old_code(oc) and name_sim(name, a["name"]) >= 0.5:
            cands[oc] = max(cands.get(oc, 0), name_sim(name, a["name"]))
    for oc in extra_old:
        cands[oc.upper()] = 9.9
    old = [oc for oc in sorted(cands, key=lambda c: (-cands[c], -avail.get(c, {}).get("n", 0))) if avail.get(oc, {}).get("n") or oc in extra_old]
    short = v["short"].get(code)
    if not short:
        toks = [w for w in re.findall(r"[A-Za-z]+", name) if w.lower() not in STOPW]
        short = "".join(w[0] for w in toks).upper() if len(toks) > 1 else (toks[0][:5].upper() if toks else code)
    sem = hit.get("sem", "")
    return {"code": code, "name": name, "sem": sem, "short": short, "old_codes": old[:4],
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
        rows.append({"file": f, "code": m.group(1), "year": years[0] if years else 0, "month": mon,
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
        r["type"] = "regular" if (r["month"] == 0 or reg) else "supplementary"
        r["label"] = (f"{MON_NAME[r['month']]} {r['year']}" if r["month"] else f"{r['year']}") + f" {r['code']}"
        r["pid"] = (MON_NAME[r["month"]].upper() if r["month"] else "Y") + str(r["year"])[2:] + "_" + r["code"]
    cur = codes[0]
    tier = lambda c: 0 if c == cur else (2 if re.fullmatch(r"[A-Z]{2}\d{3}", c) else 1)   # 2015-scheme codes last
    return sorted(out, key=lambda r: (tier(r["code"]), r["month"] == 0, -r["year"], -r["month"]))


def cmd_setup(query, n=10, extra_old=(), short=None):
    info = resolve(query, extra_old)
    sm = STUDY
    folder = None
    for d in sorted(os.listdir(sm)):   # reuse a folder that already holds this course
        sj = os.path.join(sm, d, "predictor", "subject.json")
        if os.path.exists(sj) and json.load(open(sj)).get("code") == info["code"]:
            folder = d
    folder = folder or short or info["short"]
    D = os.path.join(sm, folder, "predictor")
    os.makedirs(D, exist_ok=True)
    print(f"Course: {info['code']} {info['name']} ({info['sem']})  -> {D}")
    print("Predecessors with past papers:", info["old_detail"] or "none found (use --old CODE)")
    codes = [info["code"]] + info["old_codes"]
    picks = paper_list(codes, info["sem"])[:n]
    print(f"\nPast papers: {len(picks)} selected (newest first)")
    pq = os.path.join(D, "pyq")
    os.makedirs(pq, exist_ok=True)
    status = []
    for r in picks:
        path = os.path.join(pq, r["file"])
        ok = os.path.exists(path) or (download_drive(r["id"], path) if r["id"] else _dl(r["url"], path))
        st = to_text(path, os.path.join(pq, "img"), min_total=3000) if ok else "DOWNLOAD FAILED"
        status.append((r, st))
        print(f"  {r['label']:22s} {r['type']:13s} {st}")
    try:
        print("\nNotes:")
        cmd_notes(info["code"], D)
        notes_ok = True
    except SystemExit as e:
        print("  ", e)
        notes_ok = False
    sj = os.path.join(D, "subject.json")
    cfg = json.load(open(sj)) if os.path.exists(sj) else {}
    cfg.update({"code": info["code"], "name": info["name"], "short": folder, "old_codes": info["old_codes"],
                "semester": info["sem"]})
    cfg.setdefault("modules", 4)
    cfg.setdefault("pattern", {"part_a": 8, "part_a_marks": 3, "part_b_marks": 9, "subq": 26})
    cfg.setdefault("_pattern_note", "assumed 2024-scheme pattern (8x3 + 4x9); verify with the model paper")
    cfg.setdefault("alpha_history", 0.25)
    cfg.setdefault("course_boost", 3.0)
    cfg.setdefault("new_topic_questions", {})
    json.dump(cfg, open(sj, "w"), indent=1)
    pj = os.path.join(D, "papers.json")
    have = {p["id"] for p in json.load(open(pj))["papers"]} if os.path.exists(pj) else set()
    P = json.load(open(pj)) if os.path.exists(pj) else {"_note": "Fill questions[] for each paper: "
         "{q: A1|B11a, marks, topics: [keys from topics.json], text}. Remove a paper if it is a duplicate session.", "papers": []}
    for r, st in sorted(status, key=lambda x: (x[0]["year"], x[0]["month"])):
        if r["pid"] not in have and "FAILED" not in st:
            P["papers"].append({"id": r["pid"], "label": r["label"], "type": r["type"], "course": r["code"],
                                "source": "pyq/" + r["file"], "questions": []})
    P["papers"].sort(key=lambda p: (int(re.search(r"\d\d", p["id"]).group(0)), MON_NAME.index(p["id"][:3].title()) if p["id"][:3].title() in MON_NAME else 0))
    json.dump(P, open(pj, "w"), indent=1, ensure_ascii=False)
    lines = [f"# {info['name']} ({info['code']}) predictor: status", "",
             f"Set up by `fetch.py setup` from the query '{query}'.", "",
             f"- Predecessor courses used: {', '.join(info['old_codes']) or 'none'} ({json.dumps(info['old_detail'])})",
             f"- Notes: {'notes/ (OUTLINE.txt; scans in notes/img/)' if notes_ok else 'NOT FOUND on ktunotes - use the user own PDFs or topics.ktunotes.live'}",
             "- Past papers in pyq/ (txt; scans in pyq/img/):"]
    lines += [f"  - {r['label']} ({r['type']}): {st}" for r, st in status]
    lines += ["", "To do (scripts are in ~/.claude/skills/ktu-exam-predictor/scripts/):",
              "1. extract_questions.py . -> drafts questions[] for every paper; fix papers marked review (pyq/EXTRACTION.md)",
              "2. topics.json: one topic per notes section, with pages and modules",
              "3. tag every question's topics in papers.json",
              "4. predict.py . --type regular --label \"<Mon YYYY>\"", ""]
    open(os.path.join(D, "STATUS.md"), "w").write("\n".join(lines))
    print("\nWrote subject.json, papers.json (skeleton), STATUS.md in", D)


def _dl(url, path):
    try:
        open(path, "wb").write(get(url, binary=True, timeout=300))
        return True
    except Exception as e:
        print("   failed:", e)
        return False


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    def opt(name, default=None):
        return a[a.index(name) + 1] if name in a else default
    if a[0] == "setup":
        olds = [a[i + 1] for i, x in enumerate(a) if x == "--old"]
        cmd_setup(a[1], int(opt("--papers", 10)), olds, opt("--short"))
    elif a[0] == "resolve":
        print(json.dumps(resolve(" ".join(x for x in a[1:] if not x.startswith("--"))), indent=1))
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
