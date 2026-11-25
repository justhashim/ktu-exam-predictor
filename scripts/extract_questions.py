#!/usr/bin/env python3
"""
Split KTU past papers (pdftotext output) into sub-questions with ids, marks and text, and fill the
questions[] of each paper in SUBJECT_DIR/papers.json. Topic tags are left empty for the model to add.

  python3 extract_questions.py SUBJECT_DIR [--force]   draft questions for papers that have none (--force: redo drafts)
  python3 extract_questions.py SUBJECT_DIR --check     only validate papers.json (after fixing or transcribing papers)

Works on the noisy OCR text of scanned-then-OCR'd KTU papers: it does not trust printed question numbers.
It uses the marks column "(7)" at the end of a line, "a)/b)/c)" labels and the "Module" / "OR" markers to
rebuild the numbering (Part A: A1..An; Part B: B<n><a|b|c>, starting after the last Part A number).

Every paper gets a check against the KTU pattern:
  2019 scheme: Part A 10 x 3, Part B 5 modules, each question = 14 marks
  2024 scheme: Part A  8 x 3, Part B 4 modules, each question =  9 marks
  2024 project-based (PB) courses, 40 marks: Part A 8 x 2, Part B 4 modules, each question = 6 marks
Papers that fail the check, or have no text layer, are marked "review" in papers.json and listed in
SUBJECT_DIR/pyq/EXTRACTION.md, so they can be corrected from the PDF or the page images in pyq/img/.
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import safety  # noqa: E402

MARK = re.compile(r"^(?P<body>.*?)\s*[({\[]\s*(?P<m>\d{1,2})\s*[)\]}\\|1l]?\s*$")
MARK_BARE = re.compile(r"^(?P<body>.*?\S)?\s{4,}(?P<m>\d{1,2})\s*$")        # marks column without brackets: "...    3"
SUBLBL = re.compile(r"^\s*(?:[0-9IlLtTi|]{1,3}\s*)?(?P<l>[a-c])\s*(?:\)|[l|1](?=\s))\s*")
NUMLEAD = re.compile(r"^\s*(?P<n>[0-9IlLtTi|oO](?:\s?[0-9IlLtTi|oO]){0,2})\s*(?:[.)]\s*|\s+)(?=\S)")
STOP_AT = re.compile(r"(?i)important topics|click here to visit|click the logo")
NOISE = re.compile(r"(downloaded from|ktunotes|page\s*\w*\s*o[fl]\s*\w+|reg\s*no|name\s*:|^\s*\d{6,}|answer\s+\w+\s+(questions|full)|"
                   r"each\s+c\w+\s+\d+\s*m|^\s*[\w'.]{0,3}\d{4,}\w*\s*$|[a-z]{2}t\d{3}\w*\d{4,}|max\.?\s*marks|duration|"
                   r"course\s*(code|name)|apj abdul|b\.?tech|examination|^\s*marks\s*$|^\s*co\s+marks)", re.I)
MARKER = re.compile(r"(?i)^\s*(?:module\s*[-\u2013:]?\s*[0-9ivxl|]+|mod\w*\s+[ivx0-9]+|\(?\s*OR\s*\)?)\s*$")


def ocr_num(tok):
    t = tok.translate(str.maketrans("IlLtTi|oO", "111111100"))
    return int(t) if t.isdigit() else None


def clean_lines(text):
    out = []
    for raw in text.replace("\f", "\n").splitlines():
        if STOP_AT.search(raw) and any(re.fullmatch(r"(?i)\s*PART\s*[B8]\s*", x) for x in out):
            break                                   # ktunotes pages appended after the paper
        s = raw.rstrip()
        if not s.strip():
            continue
        if MARKER.match(s):
            out.append(s); continue
        if re.search(r"(?i)an[sv]wer\s+[ao]ll\s+questions", s):     # papers without PART headings
            out.append("PART A"); continue
        if re.search(r"(?i)an[sv]wer\s+any\s+one\s*full", s):
            out.append("PART B"); continue
        if NOISE.search(s) or not re.search(r"[A-Za-z0-9]{2}", s):   # headers, footers, OCR specks
            continue
        out.append(s)
    return out


def split_parts(lines):
    a = next((i for i, l in enumerate(lines) if re.search(r"\bPART\s*A\b", l, re.I)), None)
    b = next((i for i, l in enumerate(lines) if a is not None and i > a and re.fullmatch(r"(?i)\s*PART\s*[B8]\s*", l)), None)
    if b is None:
        b = next((i for i, l in enumerate(lines) if re.search(r"\bPART\s*[B8]\b", l, re.I)), None)
    if a is None or b is None or b < a:
        return None, None
    return lines[a + 1:b], lines[b + 1:]


def starts_question(x, part):
    """Does this line start a new question? Part A: a leading question number. Part B: a leading number and/or
    a sub-part label a)/b)/c). (In Part A, a)/b) label items inside one question.)"""
    if part == "A":
        return bool(re.match(r"^\s*(?:[0-9IlLt]{1,2}|[0-9IlLt]\s[0-9IlLt])\s*[.)]?\s+[A-Za-z]", x))
    return bool(SUBLBL.match(x) or re.match(r"^\s*[0-9IlLt]{1,2}\s*[a-c]?\s*[.)]?\s+[A-Za-z]", x))


def collect(lines, part, bare=False):
    """Group lines into items, one per marks tag "(7)". A question usually starts on its marks line (KTU prints
    marks on the first line); lines between two marks lines belong to the previous question unless one of them
    visibly starts the next question (a number or, in Part B, a sub-part label)."""
    items, buf, last = [], [], None

    def flush_to_last(lines_):
        if last is not None and lines_:
            last["text"] += " " + " ".join(lines_)

    for l in lines:
        if MARKER.match(l):
            flush_to_last(buf); buf = []
            items.append({"marker": "or" if "or" in l.lower() and "mod" not in l.lower() else "module"})
            last = None
            continue
        m = MARK.match(l) or (MARK_BARE.match(l) if bare else None)
        if m and 1 <= int(m.group("m")) <= 20:
            body = re.sub(r"\s+\d\s*$", "", m.group("body") or "")          # drop a CO column ("... 1 (5)")
            pre, k = buf, None
            if not starts_question(body, part) or not body.strip():
                k = next((i for i, x in enumerate(pre) if starts_question(x, part)), None)
            if k is not None:
                flush_to_last(pre[:k]); qlines = pre[k:] + [body]
            elif len(body.strip()) >= 15 or last is None:
                flush_to_last(pre); qlines = [body] if last is not None else pre + [body]
            else:                                    # marks on an (almost) empty line: text sits above it
                qlines = pre + [body]
            item = {"marks": int(m.group("m")), "text": " ".join(x.strip() for x in qlines).strip(),
                    "head": next((x for x in qlines if x.strip()), body)}
            items.append(item); last = item; buf = []
        else:
            buf.append(l)
    flush_to_last(buf)
    return items


def tidy(t):
    t = re.sub(r"\s+", " ", safety.clean_text(t)).strip()
    t = re.sub(r"^[\s'\",.*`|;:J]*\d{0,2}\s+(?=[A-Za-z(])", "", t)         # OCR specks before the text
    t = SUBLBL.sub("", t, count=1)
    t = NUMLEAD.sub("", t, count=1)
    return safety.scrub(t.strip())      # paper text is untrusted: instructions to an AI are withheld


def number(part_a, part_b):
    qs = []
    a_items = [i for i in part_a if "marks" in i]
    for k, it in enumerate(a_items, 1):
        qs.append({"q": f"A{k}", "marks": it["marks"], "topics": [], "text": tidy(it["text"])})
    n = len(a_items)
    cur, letter, started = n + 1, 0, False
    for it in part_b:
        if it.get("marker"):
            if started and letter:
                cur += 1; letter = 0
            continue
        lab = SUBLBL.match(it["head"])
        lead = NUMLEAD.match(it["head"])
        num = ocr_num(lead.group("n")) if lead else None
        if num and num > n and (not started or num in (cur, cur + 1)):
            if num != cur:
                cur, letter = num, 0
        if lab:
            l = "abc".index(lab.group("l"))
            if l <= letter - 1 and started:          # 'a' again without a marker: next question
                cur += 1
            letter = l + 1
        else:
            letter += 1
        started = True
        qs.append({"q": f"B{cur}{'abc'[min(letter, 3) - 1]}", "marks": it["marks"], "topics": [], "text": tidy(it["text"])})
    return qs


def check(qs):
    A = [q for q in qs if q["q"].startswith("A")]
    B = {}
    for q in qs:
        if q["q"].startswith("B"):
            n = int(re.match(r"B(\d+)", q["q"]).group(1))
            B[n] = B.get(n, 0) + q["marks"]
    probs = []
    if len(A) == 10 and all(q["marks"] == 3 for q in A):
        scheme, per = "2019", 14
    elif len(A) == 8 and all(q["marks"] == 3 for q in A):
        scheme, per = "2024", 9
    elif len(A) == 8 and all(q["marks"] == 2 for q in A):
        scheme, per = "2024-40", 6            # project-based (PB) courses: 40 marks, 8 x 2 + 4 x 6
    else:
        scheme, per = "?", None
        probs.append(f"Part A has {len(A)} items with marks {[q['marks'] for q in A]}")
    if per:
        bad = {n: m for n, m in B.items() if m != per}
        if bad:
            probs.append(f"Part B questions not totalling {per}: {bad}")
        want = 10 if scheme == "2019" else 8
        if len(B) != want:
            probs.append(f"Part B has {len(B)} questions, expected {want} ({sorted(B)})")
    return scheme, probs


def header_type(text):
    """regular / supplementary from the printed header: "(R)", "(R,S)" = regular; "(S,FE)", "(S)",
    "Supplementary" = supplementary. None if the header can't be read. Needed when the predecessor course is
    from another semester (an S4 course behind an S5 one), where the month alone misleads."""
    head = text[text.find("APJ"):][:900] if "APJ" in text else text[:3000]
    if re.search(r"\(\s*R\s*[,)]", head):
        return "regular"
    if re.search(r"(?i)supplementary|\(\s*S\s*[,)]|\bS\s*,\s*FE\b", head):
        return "supplementary"
    return None


def mark_duplicates(P):
    """The same session uploaded twice (e.g. a year-only file and a dated one): >= 70% of question texts equal."""
    norm = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())[:80]
    out = []
    live = [p for p in P["papers"] if p.get("questions") and not p.get("duplicate_of")]
    for i, p in enumerate(live):
        a = {norm(q["text"]) for q in p["questions"]}
        for o in live[:i]:
            b = {norm(q["text"]) for q in o["questions"]}
            if not o.get("duplicate_of") and a and len(a & b) >= 0.7 * min(len(a), len(b)):
                keep, drop = (o, p) if (o.get("label", "")[:3].isalpha() or not p.get("label", "")[:3].isalpha()) else (p, o)
                drop["duplicate_of"], drop["questions"] = keep["id"], []
                out.append(f"{drop['label']} duplicates {keep['label']}: marked duplicate_of")
                break
    return out


def check_only(D):
    P = json.load(open(os.path.join(D, "papers.json"), encoding="utf-8"))
    for msg in mark_duplicates(P):
        print("  " + msg)
    bad = 0
    for p in P["papers"]:
        if p.get("duplicate_of"):
            print(f"  {p['label']:24s} duplicate of {p['duplicate_of']} (skipped)"); continue
        qs = p.get("questions", [])
        if not qs:
            print(f"  {p['label']:24s} NO QUESTIONS"); bad += 1; continue
        for q in qs:
            q["text"] = safety.scrub(q.get("text", ""))
            if q["text"] == safety.WITHHELD:
                print(f"  {p['label']}: {q['q']} looked like instructions to an AI agent and was withheld")
        scheme, probs = check(qs)
        ids = [q["q"] for q in qs]
        probs += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        probs += [f"{q['q']} has no text" for q in qs if len(q.get("text", "").strip()) < 8]
        untagged = sum(1 for q in qs if not q.get("topics"))
        p["extract_status"] = "ok" if not probs else "review: " + "; ".join(probs)
        bad += bool(probs)
        print(f"  {p['label']:24s} {len(qs):2d} sub-questions, {scheme} scheme, {p['extract_status']}"
              + (f", {untagged} untagged" if untagged else ""))
    json.dump(P, open(os.path.join(D, "papers.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("all papers pass" if not bad else f"{bad} paper(s) still need fixing")


def main():
    D = sys.argv[1]
    if "--check" in sys.argv:
        return check_only(D)
    force = "--force" in sys.argv
    pj = os.path.join(D, "papers.json")
    P = json.load(open(pj, encoding="utf-8"))
    report = ["# Question extraction report", "",
              "Status per paper. `ok` = numbering and marks match the KTU pattern (still skim the text). "
              "`review` = fix from the PDF / pyq/img pages. Topics are empty: tag them from topics.json.", ""]
    for p in P["papers"]:
        if p.get("questions") and not force:      # never overwrite existing (possibly hand-fixed) questions
            report.append(f"- {p['label']}: kept (already has {len(p['questions'])} questions)")
            continue
        src = p.get("source")
        txt = os.path.join(D, src[:-4] + ".txt") if src else None
        text = open(txt, errors="replace", encoding="utf-8").read() if txt and os.path.exists(txt) else ""
        ht = header_type(text) if text else None
        if ht and ht != p.get("type"):
            report.append(f"- {p['label']}: exam type set to {ht} from the printed header (was {p.get('type')})")
            p["type"] = ht
        if len(text) < 3000:
            p.update({"questions": [], "auto": True, "extract_status": "review: scanned or no text - transcribe from pyq/img"})
            report.append(f"- **{p['label']}**: SCANNED / little text -> transcribe from "
                          f"`{os.path.dirname(src or 'pyq/x') or 'pyq'}/img/` images")
            continue
        lines = clean_lines(text)
        a, b = split_parts(lines)
        if a is None:
            # no PART headings found (garbled OCR): the opening run of 3-mark items is Part A, the rest Part B
            bare = sum(bool(MARK.match(l)) for l in lines) < 5
            items = collect(lines, "B", bare)
            k = 0
            while k < len(items) and k < 10 and items[k].get("marks") == 3:
                k += 1
            if k < 4:
                p.update({"questions": [], "auto": True, "extract_status": "review: PART A/PART B not found"})
                report.append(f"- **{p['label']}**: could not find Part A / Part B -> transcribe by hand")
                continue
            qs = number(items[:k], [i for i in items[k:] if "marks" in i or i.get("marker")])
        else:
            bare = sum(bool(MARK.match(l)) for l in a + b) < 5      # this paper prints marks without brackets
            qs = number(collect(a, "A", bare), collect(b, "B", bare))
        scheme, probs = check(qs)
        st = "ok" if not probs else "review: " + "; ".join(probs)
        p.update({"questions": qs, "auto": True, "scheme": scheme, "extract_status": st})
        report.append(f"- {'**' if probs else ''}{p['label']}{'**' if probs else ''}: {len(qs)} sub-questions, "
                      f"{scheme} scheme, {st}")
    # duplicate sessions (the same paper saved under two names): mark the later-listed one
    def toks(p):
        """Words of the paper: from its full text file if there is one (works even when extraction failed)."""
        src = p.get("source")
        txt = os.path.join(D, src[:-4] + ".txt") if src else ""
        if txt and os.path.exists(txt):
            body = open(txt, errors="replace", encoding="utf-8").read()
            if "PART" in body:
                words = set(re.findall(r"[a-z]{4,}", body[body.find("PART"):].lower()))   # skip the ktunotes cover page
                if len(words) >= 150:          # enough real paper text to compare
                    return words
        words = {w for q in p.get("questions", []) for w in re.findall(r"[a-z]{4,}", q["text"].lower())}
        return words if len(words) >= 80 else set()     # too little content: never call it a duplicate
    for p in P["papers"]:
        p.pop("duplicate_of", None)
    for i, p in enumerate(P["papers"]):
        for o in P["papers"][:i]:
            A_, B_ = toks(p), toks(o)
            if A_ and B_ and len(A_ & B_) / len(A_ | B_) > 0.7 and not o.get("duplicate_of"):
                p["duplicate_of"] = o["id"]
                report.append(f"- **{p['label']}** looks like a duplicate of {o['label']}: marked duplicate_of (skipped by predict.py)")
                break
    json.dump(P, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    open(os.path.join(D, "pyq", "EXTRACTION.md"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
