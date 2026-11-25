#!/usr/bin/env python3
"""
One-command KTU exam forecast. Standard library only (poppler-utils is optional; see fetch.py).

  python3 ktu.py prepare "DAA" [--papers 10] [--type regular|supplementary] [--label "Nov 2026"] [--old CODE] [--short NAME]
        fetch papers + notes -> draft questions -> draft topics -> auto-tag -> forecast -> FORECAST.md
  python3 ktu.py status  "DAA"          what is automatic, what is worth checking by hand
  python3 ktu.py topics  "DAA" [--redo] (re)draft topics.json
  python3 ktu.py autotag "DAA" [--all]  tag questions with topics by text similarity (--all: retag everything)
  python3 ktu.py split   "DAA"          split topics that are too coarse into subtopics from past questions
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
        if os.path.exists(sj) and json.load(open(sj, encoding="utf-8")).get("code") == code:
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
        for i, pg in enumerate(open(os.path.join(nd, f), errors="replace", encoding="utf-8").read().split("\f"), 1):
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


OFF_SYLLABUS = "OFF_SYLLABUS"   # bucket for old-course questions that match no topic of the current syllabus
OFF_SYLLABUS_SIM = 0.05         # below this similarity an old-course question goes to the bucket
LOW_TAG_SIM = 0.08              # below this a tag is reported as low-confidence
GAP_SIM = 0.06                  # a notes page this unlike every listed topic is content the topic list misses
HEADING_SKIP = re.compile(r"(?i)^(about figure|explanation|model question|example|solution|answer|note|summary|step|"
                          r"case|figure|fig\.|table|where|given|required|final|click|ktunotes|module|downloaded)")


def heading(pg):
    """A short title line at the top of a notes page, or None."""
    for l in [l.strip() for l in pg.splitlines() if l.strip()][:2]:
        if (3 <= len(l) <= 60 and len(l.split()) <= 8 and l[0].isupper() and not l.endswith((".", ":", ","))
                and not HEADING_SKIP.match(l) and not re.search(r"[=↓→×]|\bfig\b", l, re.I)
                and sum(c.isalpha() for c in l) >= 0.7 * len(l.replace(" ", "")) and "  " not in l):
            return l
    return None


def draft_topics(D, redo=False):
    """topics.json from (1) the ktunotes important-topics list, (2) notes pages that no listed topic covers
    (new-syllabus content the list misses), (3) notes sections for modules the list lacks, plus an
    OFF_SYLLABUS bucket. Notes pages are linked to topics for display and tagging."""
    tj = os.path.join(D, "topics.json")
    if os.path.exists(tj) and not redo:
        return "kept existing topics.json"
    cfg = json.load(open(os.path.join(D, "subject.json"), encoding="utf-8"))
    pages = notes_pages(D)
    data = topics_page(cfg["code"])
    topics = {}
    mods = list(range(1, cfg.get("modules", 4) + 1))

    def add(key, val):
        key = re.sub(r"[^A-Z0-9]+", "_", key.upper()).strip("_")[:40] or f"T{len(topics) + 1}"
        while key in topics:
            key += "_2"
        topics[key] = val
        return key

    # 1) DEFAULT: the ktunotes list (curated per module). Its per-topic past-paper recurrence is the prior weight.
    #    Coarse topics are split later by split_topics().
    listed = set()
    if data:
        for mod in data["modules"]:
            listed.add(int(mod["module"]))
            for t in mod.get("topics", []):
                rec = t.get("recurrence")
                hint = 0.4 + 1.2 * float(rec) / 100 if isinstance(rec, (int, float)) else \
                    {"High": 1.5, "Medium": 1.0, "Low": 0.6}.get(t.get("weightage"), 1.0)
                add(t["name"], {"name": t["name"], "module": int(mod["module"]), "syllabus": "in",
                                "model": "model" in " ".join(t.get("pastExamTags", [])).lower(), "notes": [],
                                "weight_hint": round(hint, 3), "source": "ktunotes topics page", "_see": [],
                                "examples": [q["q"] for q in t.get("frequentQuestions", []) if q.get("q")][:6]})
        # link notes pages to listed topics; pages unlike every listed topic of their module become gap topics
        keys = list(topics)
        if pages and keys:
            vec = tfidf([toks((topics[k]["name"] + " ") * 3 + " ".join(topics[k]["examples"])) for k in keys]
                        + [toks(pg) for _, _, _, pg in pages])
            tv, pv = vec[:len(keys)], vec[len(keys):]
            gap = []
            for (f, i, mod, pg), v in zip(pages, pv):
                cands = [(cos(v, tv[j]), k) for j, k in enumerate(keys) if mod is None or topics[k]["module"] == mod]
                if not cands:
                    continue
                sc, k = max(cands)
                if sc >= GAP_SIM:
                    topics[k]["_see"].append((f, i))
                elif mod:
                    gap.append((f, i, mod, pg))
            cur, last = None, None
            for f, i, mod, pg in gap:      # 2) consecutive unmatched pages with a title -> a topic the list misses
                h = heading(pg)
                if h and (cur is None or last != (f, i - 1, mod)):
                    cur = add(h, {"name": h if not h.isupper() else h.title(), "module": mod, "syllabus": "in",
                                  "model": False, "notes": [], "weight_hint": 0.6, "_see": [],
                                  "source": "notes (not in ktunotes list)"})
                if cur and (h or last == (f, i - 1, mod)):
                    topics[cur]["_see"].append((f, i))
                last = (f, i, mod)
    # 3) FALLBACK for modules the list lacks: one topic per notes section (measured worse than the list + splitting)
    cur = None
    for f, i, mod, pg in pages:
        if mod in listed:
            continue
        h = heading(pg)
        if h and (cur is None or topics[cur]["name"].lower() != h.lower()):
            cur = add(h, {"name": h if not h.isupper() else h.title(), "module": mod or 1, "syllabus": "in",
                          "model": False, "notes": [], "_pages": [], "source": "notes sections"})
        if cur:
            topics[cur].setdefault("_pages", []).append((f, i))
    keys = list(topics)
    for k_prev, k in zip(keys, keys[1:]):      # merge one-page sections into the previous one
        if (k in topics and k_prev in topics and topics[k].get("source") == "notes sections" == topics[k_prev].get("source")
                and len(topics[k].get("_pages", [])) == 1 and topics[k_prev]["module"] == topics[k]["module"]):
            topics[k_prev]["_pages"] += topics.pop(k)["_pages"]
    # model-paper flags for topics not from the list, matched by name to the list's model-paper topics
    if data:
        kt = [(t["name"], int(m["module"])) for m in data["modules"] for t in m.get("topics", [])
              if "model" in " ".join(t.get("pastExamTags", [])).lower()]
        for t in topics.values():
            if t["source"] != "ktunotes topics page":
                t["model"] = any(mod == t["module"] and fetch.name_sim(t["name"], nm) >= 0.3 for nm, mod in kt)
    for t in topics.values():
        pg, see = t.pop("_pages", []), t.pop("_see", [])
        for field, lst in (("notes", pg), ("notes_see", see)):    # notes = sizes the prior; notes_see = for reading
            byf = {}
            for f, i in lst:
                byf.setdefault(f, []).append(i)
            rr = [[f, a, b] for f, ps in byf.items() for a, b in ranges(ps)]
            if rr or field == "notes":
                t[field] = [f"M{t['module']}:{a}-{b}" if a != b else f"M{t['module']}:{a}" for _, a, b in rr]
            if rr:
                t[field + "_src"] = rr      # [notes file, first page, last page]
    # 4) LAST RESORT for modules with no topics at all (no topic list, no notes text): groups of past questions in
    #    that module (module known from the question number). The course's own papers if it has any; otherwise
    #    the predecessors' papers, their modules mapped onto this course's (a guess: see old_module()).
    have = {t["module"] for t in topics.values()}
    pj = os.path.join(D, "papers.json")
    if any(m not in have for m in mods) and os.path.exists(pj):
        live = [p for p in json.load(open(pj, encoding="utf-8"))["papers"] if not p.get("duplicate_of") and p.get("questions")]
        own = [p for p in live if p.get("course") == cfg["code"]]
        use, mapped = (own, False) if own else (live, True)
        made = []
        for m in [m for m in mods if m not in have]:
            qm = [(p["id"], q) for p in use for q in p.get("questions", [])
                  if (old_module(q["q"], p.get("scheme"), len(mods)) if mapped else module_of(q["q"], p.get("scheme"))) == m]
            if len(qm) < 2:
                continue
            groups = _clusters(tfidf([toks(q["text"]) for _, q in qm]), 0.2)
            for g in groups if mapped else [g for g in groups if len(g) >= 2]:   # predecessor-only: keep one-offs too
                texts = [qm[i][1]["text"] for i in g]
                lab = _label(texts, [q["text"] for _, q in qm])
                made.append((len({qm[i][0] for i in g}), add(f"M{m}_{lab}", {
                    "name": f"Module {m}: {lab}", "module": m, "syllabus": "in", "model": False, "notes": [],
                    "weight_hint": 1.0, "examples": texts[:12], "source": "past questions"})))
        if made:      # with no notes, the prior grows with the number of papers that asked the group
            med = sorted(n for n, _ in made)[len(made) // 2]
            for n, k in made:
                topics[k]["weight_hint"] = round(min(2.5, (n / med) ** 0.5), 3)
        if mapped and made:
            cfg["old_module_map"] = cfg.get("old_module_map") or "proportional"
            json.dump(cfg, open(os.path.join(D, "subject.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    topics[OFF_SYLLABUS] = {"name": "Not in the current syllabus (old-course questions)", "module": 0,
                            "syllabus": "old", "model": False, "notes": [], "source": "bucket"}
    srcs = {}
    for t in topics.values():
        srcs[t["source"]] = srcs.get(t["source"], 0) + 1
    src = ", ".join(f"{n} from {s_}" for s_, n in srcs.items() if s_ != "bucket")
    missing = [m for m in mods if m not in {t["module"] for t in topics.values()}]
    if missing:
        src += f"; NO topics for modules {missing}"
    json.dump({"_note": f"Drafted automatically: {src}. Edit freely: merge, split, rename, add topics from scanned notes.",
               "topics": topics}, open(tj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return src


# ------------------------------------------------------------------ auto-tagging
def module_of(qid, scheme):
    """Module of a question from its number. 2024 scheme: A1-2 -> M1 ..., B9/10 -> M1 ... (4 modules).
    2019 scheme: A1-2 -> M1 ..., B11/12 -> M1 ... (5 modules). None if unknown."""
    m = re.match(r"([AB])(\d+)", qid or "")
    if not m or scheme not in ("2024", "2019"):
        return None
    n = int(m.group(2))
    if m.group(1) == "A":
        return (n + 1) // 2
    return (n - (9 if scheme == "2024" else 11)) // 2 + 1


def old_module(qid, scheme, n_new=4, table=None):
    """A predecessor question's module in the CURRENT course: its old module, scaled onto n_new modules
    (2019's 5 -> 4: 1,2,3,4,5 -> 1,2,3,4,4) unless subject.json gives an explicit old_module_map {"5": 4, ...}.
    A guess when there is no syllabus: the new course may reorder content."""
    m = module_of(qid, scheme)
    if m is None:
        return None
    if isinstance(table, dict) and str(m) in table:
        return int(table[str(m)])
    n_old = 5 if scheme == "2019" else 4
    return min(n_new, max(1, math.ceil(m * n_new / n_old)))


def autotag(D, retag=False):
    P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))
    T = json.load(open(os.path.join(D, "topics.json"), encoding="utf-8"))["topics"]
    cfg = json.load(open(os.path.join(D, "subject.json"), encoding="utf-8"))
    keys = [k for k in T if T[k].get("module")]          # the OFF_SYLLABUS bucket (module 0) is never a match
    from_papers = any(T[k].get("source") == "past questions" for k in keys)
    pages = {(f, i): pg for f, i, _, pg in notes_pages(D)}
    docs = []
    for k in keys:
        t = T[k]
        txt = (t["name"] + " ") * 3 + " ".join(t.get("examples", []))
        for f, a_, b_ in t.get("notes_src", []) + t.get("notes_see_src", []):
            for i in range(a_, b_ + 1):
                txt += " " + pages.get((f, i), "")[:1500]
        docs.append(toks(txt))
    qs = [(p, q) for p in P["papers"] if not p.get("duplicate_of") for q in p.get("questions", [])
          if retag or not q.get("topics")]
    vecs = tfidf(docs + [toks(q["text"]) for _, q in qs])
    tv, qv = vecs[:len(keys)], vecs[len(keys):]
    low = 0
    for (p, q), v in zip(qs, qv):
        if p.get("course") == cfg["code"]:
            mod = module_of(q["q"], p.get("scheme"))
        elif cfg.get("old_module_map") and from_papers:   # topics built from predecessor papers: keep their modules
            mod = old_module(q["q"], p.get("scheme"), cfg.get("modules", 4), cfg["old_module_map"])
        else:
            mod = None
        if mod is not None and not any(T[k]["module"] == mod for k in keys):
            mod = None
        cands = sorted(((cos(v, tv[j]), k) for j, k in enumerate(keys) if mod is None or T[k]["module"] == mod), reverse=True)
        if not cands:
            continue
        best, k = cands[0]
        tags = [k] + [k2 for s2, k2 in cands[1:2] if best > 0 and s2 >= 0.85 * best]
        if best < OFF_SYLLABUS_SIM and p.get("course") != cfg["code"] and OFF_SYLLABUS in T:
            tags = [OFF_SYLLABUS]            # an old-course question unlike everything in the current syllabus
        q["topics"], q["tag_score"], q["auto_tag"] = tags, round(best, 3), True
        low += best < LOW_TAG_SIM
    json.dump(P, open(os.path.join(D, "papers.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return len(qs), low



# ------------------------------------------------------------------ splitting coarse topics
MIN_TOPICS_PER_MODULE = 8     # below this, a module's ranking is too coarse to beat studying everything
SPLIT_SIM = 0.22              # average cosine similarity for questions to form one subtopic


def _clusters(vecs, thr):
    """Average-linkage clustering of question vectors; returns lists of indices."""
    cl = [[i] for i in range(len(vecs))]
    sim = [[cos(a, b) for b in vecs] for a in vecs]
    while True:
        best, pair = thr, None
        for x in range(len(cl)):
            for y in range(x + 1, len(cl)):
                s_ = sum(sim[i][j] for i in cl[x] for j in cl[y]) / (len(cl[x]) * len(cl[y]))
                if s_ > best:
                    best, pair = s_, (x, y)
        if not pair:
            return cl
        x, y = pair
        cl[x] += cl.pop(y)


def _label(texts, background):
    """2-3 readable words that set a group of questions apart from the rest of the topic."""
    cnt, bg, surface = {}, {}, {}
    for t in texts:
        for w in re.findall(r"[A-Za-z][A-Za-z0-9+-]+", t):
            st = toks(w)
            if st:
                surface.setdefault(st[0], {}).setdefault(w.lower(), 0)
                surface[st[0]][w.lower()] += 1
        for w in set(toks(t)):
            cnt[w] = cnt.get(w, 0) + 1
    for t in background:
        for w in set(toks(t)):
            bg[w] = bg.get(w, 0) + 1
    generic = {"perform", "consider", "need", "involv", "use", "explain", "show", "obtain", "solve", "write",
               "complexity", "analysi", "analysis", "find", "give", "suitable", "different", "following", "does"}
    score = {w: c / len(texts) - bg.get(w, 0) / max(len(background), 1) for w, c in cnt.items()
             if (c >= 2 or len(texts) == 1) and w not in generic}
    words = [max(surface.get(w, {w: 1}).items(), key=lambda x: x[1])[0] for w, _ in sorted(score.items(), key=lambda x: -x[1])[:3]]
    return ", ".join(words) if words else "other"


def split_topics(D, exclude_papers=()):
    """Split topics that are too coarse into subtopics built from groups of similar past questions.
    A module with fewer than MIN_TOPICS_PER_MODULE topics has all its topics split; elsewhere only a topic
    holding far more questions than its siblings is split. Questions are re-tagged to their subtopic;
    single leftover questions stay with the parent. Each subtopic gets a share of the parent's weight."""
    tj, pj = os.path.join(D, "topics.json"), os.path.join(D, "papers.json")
    TJ, P = json.load(open(tj, encoding="utf-8")), json.load(open(pj, encoding="utf-8"))
    T = TJ["topics"]
    live = [q for p in P["papers"] if not p.get("duplicate_of") and p["id"] not in exclude_papers for q in p.get("questions", [])]
    held = [q for p in P["papers"] if not p.get("duplicate_of") and p["id"] in exclude_papers for q in p.get("questions", [])]
    by_topic = {}
    for q in live:
        for t in q.get("topics", [])[:1]:
            by_topic.setdefault(t, []).append(q)
    counts = sorted(len(v) for v in by_topic.values()) or [0]
    median = counts[len(counts) // 2]
    per_mod = {}
    for t in T.values():
        per_mod[t["module"]] = per_mod.get(t["module"], 0) + 1
    pages_all = [sum(int(b or a) - int(a) + 1 for a, _, b in (r.split(":")[1].partition("-") for r in t.get("notes", [])))
                 for t in T.values()]
    med_pages = sorted(x for x in pages_all if x > 0)[len([x for x in pages_all if x > 0]) // 2] if any(pages_all) else 1
    made = 0
    for k in list(T):
        t = T[k]
        qs = by_topic.get(k, [])
        coarse_mod = per_mod.get(t["module"], 0) < MIN_TOPICS_PER_MODULE
        hog = len(qs) >= max(15, 3 * median)
        if not t.get("module") or t.get("parent") or t.get("split") or len(qs) < 4 or not (coarse_mod or hog):
            continue
        extra = [q for q in held if q.get("topics", [None])[:1] == [k]]
        allv = tfidf([toks(q["text"]) for q in qs + extra])      # one vector space for training and held-out questions
        vecs, ev = allv[:len(qs)], allv[len(qs):]
        groups = [g for g in _clusters(vecs, SPLIT_SIM) if len(g) >= 2]
        if len(groups) < 2:
            continue
        pg = sum(int(b or a) - int(a) + 1 for a, _, b in (r.split(":")[1].partition("-") for r in t.get("notes", [])))
        parent_raw = min(2.5, (pg / med_pages) ** 0.5) if pg else float(t.get("weight_hint", 1.0))
        grouped = {i for g in groups for i in g}
        child_keys, centroids = [], []
        for g in groups:
            texts = [qs[i]["text"] for i in g]
            key = re.sub(r"[^A-Z0-9]+", "_", (k + "_" + _label(texts, [x["text"] for x in qs]).upper()))[:48].strip("_")
            while key in T:
                key += "_2"
            T[key] = {"name": f"{t['name']}: {_label(texts, [x['text'] for x in qs])}", "module": t["module"],
                      "syllabus": t.get("syllabus", "in"), "model": t.get("model", False), "notes": [],
                      "notes_parent": t.get("notes", []), "parent": k, "source": "split by past questions",
                      "weight_hint": round(parent_raw * (len(g) / len(qs)) ** 0.5, 3),
                      "effort_share": round(len(g) / len(qs), 3)}   # share of the parent's study size
            for i in g:
                qs[i]["topics"] = [key] + [x for x in qs[i]["topics"] if x != k][:1]
            child_keys.append(key)
            cen = {}
            for i in g:
                for w, x in vecs[i].items():
                    cen[w] = cen.get(w, 0) + x / len(g)
            centroids.append(cen)
            made += 1
        # leftover questions (and questions from held-out papers) join the most similar subtopic if close enough
        loose = [(qs[i], vecs[i]) for i in range(len(qs)) if i not in grouped] + list(zip(extra, ev))
        rest = []
        for q, v in loose:
            sc, j = max((cos(v, c), j) for j, c in enumerate(centroids))
            if sc >= SPLIT_SIM * 0.8:
                q["topics"] = [child_keys[j]] + [x for x in q["topics"] if x != k][:1]
            elif q in qs:
                rest.append(q["text"])
        # the parent keeps the leftover questions and the rest of its weight
        t["split"] = True
        t["effort_pages"] = pg            # the parent's size, shared out by effort_share
        t["effort_share"] = round(max(len(rest), 1) / len(qs), 3)
        t["notes_parent"], t["notes"] = t.get("notes", []), []
        t["weight_hint"] = round(parent_raw * max(len(rest), 1) / len(qs) * 0.5 + 0.05, 3)
        t["name"] = t["name"] + " (other)"
        per_mod[t["module"]] += len(groups)
    TJ["topics"] = T
    if made:
        TJ["_split_built_without"] = sorted(exclude_papers)   # papers whose questions did NOT shape the subtopics
    json.dump(TJ, open(tj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(P, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return made

# ------------------------------------------------------------------ status
def text_tool():
    if fetch.shutil.which("pdftotext"):
        return "pdftotext"
    try:
        import pypdf  # noqa: F401
        return "pypdf"
    except Exception:
        return None


def status(D, quiet=False):
    cfg = json.load(open(os.path.join(D, "subject.json"), encoding="utf-8"))
    pj, tj = os.path.join(D, "papers.json"), os.path.join(D, "topics.json")
    P = json.load(open(pj, encoding="utf-8"))["papers"] if os.path.exists(pj) else []
    T = json.load(open(tj, encoding="utf-8"))["topics"] if os.path.exists(tj) else {}
    dups = [p for p in P if p.get("duplicate_of")]
    live = [p for p in P if not p.get("duplicate_of")]
    empty = [p for p in live if not p.get("questions")]
    used = [p for p in live if p.get("questions")]
    ok = [p for p in used if p.get("extract_status") == "ok"]
    review = [p for p in used if p.get("extract_status", "ok") != "ok"]
    qs = [q for p in used for q in p.get("questions", [])]
    off = [q for q in qs if q.get("topics") == [OFF_SYLLABUS]]
    lowc = [q for q in qs if q.get("auto_tag") and q.get("tag_score", 1) < LOW_TAG_SIM and q not in off]
    mods = list(range(1, cfg.get("modules", 4) + 1))
    nd = os.path.join(D, "notes")
    notes_pdfs = [f for f in os.listdir(nd) if f.lower().endswith(".pdf")] if os.path.isdir(nd) else []
    scans = scanned_modules(D, mods) if notes_pdfs else []
    no_topics = [m for m in mods if not any(t["module"] == m for t in T.values())]
    pyq = os.path.join(D, "pyq")
    unread = [f for f in os.listdir(pyq) if f.lower().endswith(".pdf") and not os.path.exists(os.path.join(pyq, f[:-4] + ".txt"))] \
        if os.path.isdir(pyq) else []
    srcs = {}
    for t in T.values():
        if t.get("module"):
            srcs[t.get("source", "hand-written")] = srcs.get(t.get("source", "hand-written"), 0) + 1
    L = [f"{cfg['name']} ({cfg['code']})  ->  {D}",
         f"  papers: {len(used)} with questions ({len(ok)} clean, {len(review)} drafted with problems), "
         f"{len(empty)} without questions, {len(dups)} duplicates skipped",
         f"  questions: {len(qs)} ({sum(1 for q in qs if q.get('auto_tag'))} auto-tagged, {len(lowc)} low-confidence, "
         f"{len(off)} judged outside the current syllabus)",
         f"  topics: {sum(srcs.values())} (" + ", ".join(f"{n} {k}" for k, n in srcs.items()) + ")",
         f"  forecast: {'FORECAST.md' if os.path.exists(os.path.join(D, 'FORECAST.md')) else 'not yet'}"]
    todo = []
    if unread and not text_tool():
        todo.append(f"{len(unread)} PDFs could not be read: install poppler-utils (Linux: apt/dnf install poppler-utils; "
                    "macOS: brew install poppler; Windows: pip install pypdf), then run prepare again")
    if len(used) < 3:
        todo.append(f"only {len(used)} past papers have questions: the history part of the forecast is weak or absent")
    if no_topics:
        todo.append(f"modules {no_topics} have no topics: add them to topics.json")
    if not notes_pdfs:
        todo.append("ASK THE USER: no notes. They can download notes or the syllabus into "
                    f"{os.path.join(os.path.dirname(D), 'notes')} and run prepare again; if they won't, carry on "
                    "(predecessor papers; `--notes-from CODE` for a similar course's notes)")
    elif scans:
        todo.append(f"notes for modules {scans} have no text (scans: see notes/img/)")
    if empty:
        todo.append("papers with no questions: transcribe from pyq/img/ or the PDF -> " + ", ".join(p["label"] for p in empty))
    if review:
        todo.append("papers whose drafts failed the KTU marks check (pyq/EXTRACTION.md) -> " + ", ".join(p["label"] for p in review))
    if lowc:
        todo.append(f"{len(lowc)} questions matched no topic well (tag_score < {LOW_TAG_SIM} in papers.json)")
    L.append("  improve accuracy (optional; run `ktu.py predict` again after):" if todo else "  nothing left to check")
    L += [f"   - {t}" for t in todo]
    if not quiet:
        print("\n".join(L))
    return L


def run(script, *args):
    r = subprocess.run([sys.executable, os.path.join(HERE, script), *args], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-1500:], r.stderr[-1500:])
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["prepare", "status", "topics", "autotag", "split", "predict"])
    ap.add_argument("subject", help="course code, name, or subject folder")
    ap.add_argument("--papers", type=int, default=10)
    ap.add_argument("--old", action="append", default=[], help="force a predecessor course code (repeatable)")
    ap.add_argument("--short", default=None, help="subject folder name, e.g. MC")
    ap.add_argument("--name", default=None, help="course name, for a code missing from the ktunotes catalogue")
    ap.add_argument("--notes-from", action="append", default=[], dest="notes_from",
                    help="also fetch the notes of a similar course (repeatable), for a course with no notes of its own")
    ap.add_argument("--type", default="regular", choices=["regular", "supplementary"])
    ap.add_argument("--label", default=None, help='exam label, e.g. "Nov 2026"')
    ap.add_argument("--redo", action="store_true", help="topics: redraft topics.json")
    ap.add_argument("--all", action="store_true", help="autotag: retag every question")
    a = ap.parse_args()
    pargs = ["--type", a.type] + (["--label", a.label] if a.label else [])
    if a.command == "prepare":
        print("== 1/5 fetch papers and notes")
        D = fetch.cmd_setup(a.subject, a.papers, a.old, a.short, a.name, a.notes_from)
        if not text_tool():
            print("\nWARNING: no PDF text tool (pdftotext or pypdf). Papers and notes were downloaded but cannot be read,"
                  "\nso there will be no questions. Install poppler-utils (or `pip install pypdf`) and run prepare again.")
        print("\n== 2/5 draft questions from the papers")
        r = run("extract_questions.py", D)
        rep = os.path.join(D, "pyq", "EXTRACTION.md")
        if r.returncode == 0 and os.path.exists(rep):
            print(open(rep, encoding="utf-8").read().split("\n", 4)[-1][:2500])
        print("== 3/5 topics:", draft_topics(D))
        n, low = autotag(D)
        made = split_topics(D)
        print(f"== 4/5 auto-tagged {n} questions ({low} low-confidence); split coarse topics into {made} subtopics")
        print("== 5/5 forecast")
        print(run("predict.py", D, *pargs).stdout[-2500:])
        print("\n".join(status(D, quiet=True)))
        print(f"\nRead {os.path.join(D, 'FORECAST.md')}")
    elif a.command == "status":
        status(subject_dir(a.subject))
    elif a.command == "topics":
        print(draft_topics(subject_dir(a.subject), redo=a.redo))
    elif a.command == "split":
        print(f"made {split_topics(subject_dir(a.subject))} subtopics")
    elif a.command == "autotag":
        n, low = autotag(subject_dir(a.subject), retag=a.all)
        print(f"tagged {n} questions ({low} low-confidence)")
    elif a.command == "predict":
        r = run("predict.py", subject_dir(a.subject), *pargs)
        print(r.stdout)


if __name__ == "__main__":
    main()
