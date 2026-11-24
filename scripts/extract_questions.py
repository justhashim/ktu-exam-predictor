#!/usr/bin/env python3
"""
Split KTU past papers (pdftotext output) into sub-questions with ids, marks and text, and fill the
questions[] of each paper in SUBJECT_DIR/papers.json. Topic tags are left empty for the model to add.

  python3 extract_questions.py SUBJECT_DIR [--force]

Works on the noisy OCR text of scanned-then-OCR'd KTU papers: it does not trust printed question numbers.
It uses the marks column "(7)" at the end of a line, "a)/b)/c)" labels and the "Module" / "OR" markers to
rebuild the numbering (Part A: A1..An; Part B: B<n><a|b|c>, starting after the last Part A number).

Every paper gets a check against the KTU pattern:
  2019 scheme: Part A 10 x 3, Part B 5 modules, each question = 14 marks
  2024 scheme: Part A  8 x 3, Part B 4 modules, each question =  9 marks
Papers that fail the check, or have no text layer, are marked "review" in papers.json and listed in
SUBJECT_DIR/pyq/EXTRACTION.md, so they can be corrected from the PDF or the page images in pyq/img/.
"""
import json, os, re, sys

MARK = re.compile(r"^(?P<body>.*?)\s*\(\s*(?P<m>\d{1,2})\s*[)\]}\\|1l]?\s*$")
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
        if STOP_AT.search(raw) and out:
            break
        s = raw.rstrip()
        if not s.strip():
            continue
        if MARKER.match(s):
            out.append(s); continue
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


def collect(lines, part):
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
        m = MARK.match(l)
        if m and 1 <= int(m.group("m")) <= 20:
            body = re.sub(r"\s+\d\s*$", "", m.group("body"))          # drop a CO column ("... 1 (5)")
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
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^[\s'\",.*`|;:J]*\d{0,2}\s+(?=[A-Za-z(])", "", t)         # OCR specks before the text
    t = SUBLBL.sub("", t, count=1)
    t = NUMLEAD.sub("", t, count=1)
    return t.strip()


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


def main():
    D = sys.argv[1]
    force = "--force" in sys.argv
    pj = os.path.join(D, "papers.json")
    P = json.load(open(pj))
    report = ["# Question extraction report", "",
              "Status per paper. `ok` = numbering and marks match the KTU pattern (still skim the text). "
              "`review` = fix from the PDF / pyq/img pages. Topics are empty: tag them from topics.json.", ""]
    for p in P["papers"]:
        if p.get("questions") and not force and not p.get("auto"):
            report.append(f"- {p['label']}: kept (already has {len(p['questions'])} questions)")
            continue
        src = p.get("source")
        txt = os.path.join(D, src[:-4] + ".txt") if src else None
        text = open(txt, errors="replace").read() if txt and os.path.exists(txt) else ""
        if len(text) < 3000:
            p.update({"questions": [], "auto": True, "extract_status": "review: scanned or no text - transcribe from pyq/img"})
            report.append(f"- **{p['label']}**: SCANNED / little text -> transcribe from `pyq/img/` images")
            continue
        a, b = split_parts(clean_lines(text))
        if a is None:
            p.update({"questions": [], "auto": True, "extract_status": "review: PART A/PART B headings not found"})
            report.append(f"- **{p['label']}**: PART A / PART B headings not found -> transcribe by hand")
            continue
        qs = number(collect(a, "A"), collect(b, "B"))
        scheme, probs = check(qs)
        st = "ok" if not probs else "review: " + "; ".join(probs)
        p.update({"questions": qs, "auto": True, "scheme": scheme, "extract_status": st})
        report.append(f"- {'**' if probs else ''}{p['label']}{'**' if probs else ''}: {len(qs)} sub-questions, "
                      f"{scheme} scheme, {st}")
    # duplicate sessions (the same paper saved under two names): mark the later-listed one
    def toks(p):
        return {w for q in p.get("questions", []) for w in re.findall(r"[a-z]{4,}", q["text"].lower())}
    for i, p in enumerate(P["papers"]):
        for o in P["papers"][:i]:
            A_, B_ = toks(p), toks(o)
            if A_ and B_ and len(A_ & B_) / len(A_ | B_) > 0.7 and not o.get("duplicate_of"):
                p["duplicate_of"] = o["id"]
                report.append(f"- **{p['label']}** looks like a duplicate of {o['label']}: marked duplicate_of (skipped by predict.py)")
                break
    json.dump(P, open(pj, "w"), indent=1, ensure_ascii=False)
    open(os.path.join(D, "pyq", "EXTRACTION.md"), "w").write("\n".join(report) + "\n")
    print("\n".join(report))


if __name__ == "__main__":
    main()
