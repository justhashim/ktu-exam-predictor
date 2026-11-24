#!/usr/bin/env python3
"""
One-command KTU exam forecast. Standard library only (poppler-utils is optional; see fetch.py).

  python3 ktu.py prepare "DAA" [--papers 10] [--type regular|supplementary] [--label "Nov 2026"] [--old CODE] [--short NAME]
        fetch papers + notes -> draft questions -> draft topics -> auto-tag -> forecast -> FORECAST.md
  python3 ktu.py status  "DAA"          what is automatic, what is worth checking by hand
  python3 ktu.py topics  "DAA" [--redo] (re)draft topics.json
  python3 ktu.py autotag "DAA" [--all]  tag questions with topics by text similarity (--all: retag everything)
  python3 ktu.py predict "DAA" [--type supplementary] [--label "..."]

The subject can be a course code, a name, or a subject folder path.

Everything after "prepare" is automatic, so a forecast exists within minutes. Its weak spots are listed by
`status`: papers whose extraction failed the KTU marks check, low-confidence tags, and modules whose notes are
scans. An agent (or the student) can then improve those and re-run `predict`.
"""
import html, json, math, os, re, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch  # noqa: E402

STOP = set(fetch.STOPW) | set("""what which how why explain describe write give list define state differentiate compare
discuss illustrate draw find show using with example examples given following the a an of in on for to and or is are
be by from as at this that it its each any all one two three four five suitable neat diagram diagrams detail brief
short note notes algorithm algorithms method methods""".split())


def toks(text):
    out = []
    for w in re.findall(r"[a-z][a-z0-9+-]+", text.lower()):
        if w in STOP or len(w) < 2:
            continue
        for suf in ("ations", "ation", "ing", "ies", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


def subject_dir(arg):
    if os.path.isdir(arg) and os.path.exists(os.path.join(arg, "subject.json")):
        return os.path.abspath(arg)
    code = fetch.resolve(arg)["code"]
    for d in sorted(os.listdir(fetch.STUDY)) if os.path.isdir(fetch.STUDY) else []:
        sj = os.path.join(fetch.STUDY, d, "predictor", "subject.json")
        if os.path.exists(sj) and json.load(open(sj)).get("code") == code:
            return os.path.join(fetch.STUDY, d, "predictor")
    sys.exit(f"No subject folder for {code} yet. Run: ktu.py prepare \"{arg}\"")


# ------------------------------------------------------------------ topics
def topics_page(code):
    """ktunotes 'important topics' page for a 2024-scheme course: modules -> topics with priority and
    past-paper counts. Returns subjectData or None."""
    try:
        s = fetch.get(f"https://topics.ktunotes.live/{code.lower()}", timeout=60)
    except Exception:
        return None
    payload = ""
    for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', s, re.S):
        try:
            payload += json.loads('"' + m.group(1) + '"')
        except Exception:
            continue
    i = payload.find('"subjectData":')
    if i < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(payload, i + len('"subjectData":'))
        return data if data.get("modules") else None
    except Exception:
        return None


def notes_pages(D):
    """[(pdf_name, page_no, module or None, text)] for every notes page with text."""
    nd = os.path.join(D, "notes")
    out = []
    for f in sorted(os.listdir(nd)) if os.path.isdir(nd) else []:
        if not f.endswith(".txt") or f == "OUTLINE.txt":
            continue
        m = re.search(r"(?:^|[^A-Za-z])(?:M|MOD|MODULE)[\s_-]*0?([1-6])(?!\d)", f, re.I)
        mod = int(m.group(1)) if m else None
        for i, pg in enumerate(open(os.path.join(nd, f), errors="replace").read().split("\f"), 1):
            hm = re.search(r"(?im)^\s*MODULE\s*[-:]?\s*0?([1-6])\b", pg[:400])
            if hm:
                mod = int(hm.group(1))
            if len(pg.strip()) > 80:
                out.append((f[:-4], i, mod, pg))
    return out


def scanned_modules(D, modules):
    with_text = {m for _, _, m, _ in notes_pages(D) if m}
    return [m for m in modules if m not in with_text]


def tfidf(docs):
    df, n = {}, len(docs)
    for d in docs:
        for w in set(d):
            df[w] = df.get(w, 0) + 1
    vecs = []
    for d in docs:
        v = {}
        for w in d:
            v[w] = v.get(w, 0) + 1
        for w in v:
            v[w] *= math.log((n + 1) / (df[w] + 1)) + 1
        norm = math.sqrt(sum(x * x for x in v.values())) or 1
        vecs.append({w: x / norm for w, x in v.items()})
    return vecs


def cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def ranges(nums):
    nums, out = sorted(set(nums)), []
    for x in nums:
        if out and x == out[-1][1] + 1:
            out[-1][1] = x
        else:
            out.append([x, x])
    return out


def draft_topics(D, redo=False):
    tj = os.path.join(D, "topics.json")
    if os.path.exists(tj) and not redo:
        return "kept existing topics.json"
    cfg = json.load(open(os.path.join(D, "subject.json")))
    pages = notes_pages(D)
    data = topics_page(cfg["code"])
    topics = {}
    mods = list(range(1, cfg.get("modules", 4) + 1))
    text_mods = sorted({m for _, _, m, _ in pages if m})

    def add(key, val):
        key = re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_")[:40] or f"T{len(topics) + 1}"
        while key in topics:
            key += "_2"
        topics[key] = val
        return key

    # 1) modules whose notes have text: one topic per notes section (a short title line at the top of a page);
    #    pages without a title continue the previous section. Fine-grained topics are what made forecasts accurate.
    skip = re.compile(r"(?i)^(about figure|explanation|model question|example|solution|answer|note|summary|step|case|"
                      r"figure|fig\.|table|where|given|required|final|click|ktunotes|module|downloaded)")
    cur = None
    for f, i, mod, pg in pages:
        lines = [l.strip() for l in pg.splitlines() if l.strip()]
        head = None
        for l in lines[:2]:
            w = l.split()
            if (3 <= len(l) <= 60 and len(w) <= 8 and l[0].isupper() and not l.endswith((".", ":", ",")) and not skip.match(l)
                    and not re.search(r"[=↓→×]|\bfig\b", l, re.I)
                    and sum(c.isalpha() for c in l) >= 0.7 * len(l.replace(" ", "")) and "  " not in l):
                head = l
                break
        if head and (cur is None or topics[cur]["name"].lower() != head.lower()):
            cur = add(head, {"name": head if not head.isupper() else head.title(), "module": mod or 1, "syllabus": "in",
                             "model": False, "notes": [], "_pages": [], "source": "notes sections"})
        if cur:
            topics[cur]["_pages"].append((f, i))
    # merge one-page sections into the previous section of the same module (keeps topics exam-sized)
    keys = list(topics)
    for k_prev, k in zip(keys, keys[1:]):
        if k in topics and k_prev in topics and len(topics[k]["_pages"]) == 1 and topics[k_prev]["module"] == topics[k]["module"]:
            topics[k_prev]["_pages"] += topics.pop(k)["_pages"]
    # 2) modules without text (scanned notes) or without sections: the ktunotes important-topics list
    covered = {t["module"] for t in topics.values()}
    if data:
        for mod in data["modules"]:
            if int(mod["module"]) in covered:
                continue
            for t in mod.get("topics", []):
                tags = " ".join(t.get("pastExamTags", []))
                add(t["name"], {"name": t["name"], "module": int(mod["module"]), "syllabus": "in",
                                "model": "model" in tags.lower(), "notes": [], "_pages": [],
                                "weight_hint": {"High": 1.5, "Medium": 1.0, "Low": 0.6}.get(t.get("weightage"), 1.0),
                                "source": "ktunotes topics page",
                                "examples": [q["q"] for q in t.get("frequentQuestions", []) if q.get("q")][:6]})
        # model-paper flags for notes-section topics, from the ktunotes list (matched by name similarity)
        kt = [(t["name"], "model" in " ".join(t.get("pastExamTags", [])).lower(), int(m["module"])) for m in data["modules"] for t in m.get("topics", [])]
        for k, t in topics.items():
            if t["source"] == "notes sections":
                t["model"] = any(md and mod == t["module"] and fetch.name_sim(t["name"], nm) >= 0.3 for nm, md, mod in kt)
    src = (f"notes sections for modules {text_mods}" if text_mods else "no notes text") + \
          (f" + ktunotes topics page for the rest" if data else "") + f" ({len(topics)} topics)"
    missing = [m for m in mods if m not in {t["module"] for t in topics.values()}]
    if missing:
        src += f"; NO topics for modules {missing}"
    # map notes pages to topics (same module) by text similarity -> page ranges for the notes prior
    if False:   # sections already carry their pages
        keys = list(topics)
        tv = tfidf([toks((topics[k]["name"] + " ") * 3 + " ".join(topics[k].get("examples", []))) for k in keys]
                   + [toks(p[3]) for p in pages])
        tvec, pvec = tv[:len(keys)], tv[len(keys):]
        for (f, i, mod, _), pv in zip(pages, pvec):
            cands = [(cos(pv, tvec[j]), k) for j, k in enumerate(keys) if mod is None or topics[k]["module"] == mod]
            if cands:
                sc, k = max(cands)
                if sc > 0.05:
                    topics[k].setdefault("_pages", []).append((f, i))
    for k, t in topics.items():
        pg = t.pop("_pages", [])
        byf = {}
        for f, i in pg:
            byf.setdefault(f, []).append(i)
        src_ = [[f, a, b] for f, ps in byf.items() for a, b in ranges(ps)]
        t["notes"] = [f"M{t['module']}:{a}-{b}" if a != b else f"M{t['module']}:{a}" for _, a, b in src_]
        t["notes_src"] = src_      # [notes file, first page, last page]
    json.dump({"_note": f"Drafted automatically from {src}. Edit freely: add topics from scanned notes, merge or split.",
               "topics": topics}, open(tj, "w"), indent=1, ensure_ascii=False)
    return src


# ------------------------------------------------------------------ auto-tagging
def module_of(qid, scheme):
    """Module a question belongs to in a 2024-scheme paper (A1-2 -> M1 ..., B9/10 -> M1 ...)."""
    if scheme != "2024":
        return None
    m = re.match(r"([AB])(\d+)", qid)
    n = int(m.group(2))
    return (n + 1) // 2 if m.group(1) == "A" else (n - 9) // 2 + 1


def autotag(D, retag=False):
    P = json.load(open(os.path.join(D, "papers.json")))
    T = json.load(open(os.path.join(D, "topics.json")))["topics"]
    cfg = json.load(open(os.path.join(D, "subject.json")))
    keys = list(T)
    pages = {(f, i): pg for f, i, _, pg in notes_pages(D)}
    docs = []
    for k in keys:
        t = T[k]
        txt = (t["name"] + " ") * 3 + " ".join(t.get("examples", []))
        for f, a_, b_ in t.get("notes_src", []):
            for i in range(a_, b_ + 1):
                txt += " " + pages.get((f, i), "")[:1500]
        docs.append(toks(txt))
    qs = [(p, q) for p in P["papers"] if not p.get("duplicate_of") for q in p.get("questions", [])
          if retag or not q.get("topics")]
    vecs = tfidf(docs + [toks(q["text"]) for _, q in qs])
    tv, qv = vecs[:len(keys)], vecs[len(keys):]
    low = 0
    for (p, q), v in zip(qs, qv):
        mod = module_of(q["q"], p.get("scheme")) if p.get("course") == cfg["code"] else None
        cands = sorted(((cos(v, tv[j]), k) for j, k in enumerate(keys) if mod is None or T[k]["module"] == mod), reverse=True)
        if not cands:
            continue
        best, k = cands[0]
        tags = [k] + [k2 for s2, k2 in cands[1:2] if best > 0 and s2 >= 0.85 * best]
        q["topics"], q["tag_score"], q["auto_tag"] = tags, round(best, 3), True
        low += best < 0.08
    json.dump(P, open(os.path.join(D, "papers.json"), "w"), indent=1, ensure_ascii=False)
    return len(qs), low


# ------------------------------------------------------------------ status
def status(D, quiet=False):
    cfg = json.load(open(os.path.join(D, "subject.json")))
    P = json.load(open(os.path.join(D, "papers.json")))["papers"] if os.path.exists(os.path.join(D, "papers.json")) else []
    T = json.load(open(os.path.join(D, "topics.json")))["topics"] if os.path.exists(os.path.join(D, "topics.json")) else {}
    live = [p for p in P if not p.get("duplicate_of")]
    ok = [p for p in live if p.get("extract_status") == "ok"]
    empty = [p for p in live if not p.get("questions")]
    review = [p for p in live if p.get("questions") and p.get("extract_status", "ok") != "ok"]
    qs = [q for p in live for q in p.get("questions", [])]
    lowc = [q for q in qs if q.get("auto_tag") and q.get("tag_score", 1) < 0.08]
    mods = list(range(1, cfg.get("modules", 4) + 1))
    scans = scanned_modules(D, mods)
    no_topics = [m for m in mods if not any(t["module"] == m for t in T.values())]
    L = [f"{cfg['name']} ({cfg['code']})  ->  {D}",
         f"  papers: {len(live)} usable ({len(ok)} clean, {len(review)} drafted with problems, {len(empty)} empty), "
         f"{len(P) - len(live)} duplicates skipped",
         f"  questions: {len(qs)} ({sum(1 for q in qs if q.get('auto_tag'))} auto-tagged, {len(lowc)} low-confidence tags)",
         f"  topics: {len(T)} ({T and next(iter(T.values())).get('source', 'hand-written')})",
         f"  forecast: {'FORECAST.md' if os.path.exists(os.path.join(D, 'FORECAST.md')) else 'not yet'}"]
    todo = []
    if no_topics:
        todo.append(f"modules {no_topics} have no topics: add them to topics.json (notes/img pages if the notes are scans)")
    if scans:
        todo.append(f"notes for modules {scans} are scans (notes/img/): topics there have no notes pages")
    if empty:
        todo.append("papers with no questions (scans): transcribe from pyq/img/ or the PDF -> " + ", ".join(p["label"] for p in empty))
    if review:
        todo.append("papers whose drafts failed the KTU marks check (pyq/EXTRACTION.md) -> " + ", ".join(p["label"] for p in review))
    if lowc:
        todo.append(f"{len(lowc)} questions matched no topic well (tag_score < 0.08 in papers.json)")
    L.append("  improve accuracy (optional, re-run `ktu.py predict` after):" if todo else "  nothing left to check")
    L += [f"   - {t}" for t in todo]
    if not quiet:
        print("\n".join(L))
    return L


def run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-1500:], r.stderr[-1500:])
    return r


def opt(a, name, default=None):
    return a[a.index(name) + 1] if name in a else default


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    cmd, subj = a[0], a[1]
    if cmd == "prepare":
        print("== 1/5 fetch papers and notes")
        D = fetch.cmd_setup(subj, int(opt(a, "--papers", 10)), [a[i + 1] for i, x in enumerate(a) if x == "--old"], opt(a, "--short"))
        print("\n== 2/5 draft questions from the papers")
        run("extract_questions.py", D)
        print(open(os.path.join(D, "pyq", "EXTRACTION.md")).read().split("\n", 4)[-1][:2500])
        print("== 3/5 topics:", draft_topics(D))
        n, low = autotag(D)
        print(f"== 4/5 auto-tagged {n} questions ({low} low-confidence)")
        print("== 5/5 forecast")
        args = [D, "--type", opt(a, "--type", "regular")] + (["--label", opt(a, "--label")] if opt(a, "--label") else [])
        r = run("predict.py", *args)
        print(r.stdout[-2500:])
        print("\n".join(status(D, quiet=True)))
        print(f"\nRead {os.path.join(D, 'FORECAST.md')}")
    elif cmd == "status":
        status(subject_dir(subj))
    elif cmd == "topics":
        print(draft_topics(subject_dir(subj), redo="--redo" in a))
    elif cmd == "autotag":
        n, low = autotag(subject_dir(subj), retag="--all" in a)
        print(f"tagged {n} questions ({low} low-confidence)")
    elif cmd == "predict":
        D = subject_dir(subj)
        args = [D, "--type", opt(a, "--type", "regular")] + (["--label", opt(a, "--label")] if opt(a, "--label") else [])
        print(run("predict.py", *args).stdout)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
