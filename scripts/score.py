#!/usr/bin/env python3
"""
Score a real exam paper against a forecast that was frozen BEFORE the paper was seen.

  python3 score.py SNAPSHOT_DIR PAPER.json [--tags topics|strict]

SNAPSHOT_DIR holds the frozen topics.json and results.json (e.g. <subject>/predictor/blind_auto/, a copy made
right after `ktu.py predict`). PAPER.json is one paper: {"questions": [{"q": "A1", "marks": 3, "topics": [...],
"strict": [...]}]}, hand-tagged against the snapshot's topic keys. Tags not in the snapshot (e.g. NEW_ARM for
content no past paper had) can never be covered, which is the point: they measure what the forecast missed.

Prints, per share of the syllabus studied (by study size, as in predict.py): marks covered by the forecast's
module-balanced order, by random topic choice, and the ceiling (studying every topic in the snapshot).
"""
import json, os, random, statistics, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import predict  # noqa: E402

SHARES = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]


def score(snap, paper, field="topics"):
    topics = json.load(open(os.path.join(snap, "topics.json"), encoding="utf-8"))["topics"]
    res = json.load(open(os.path.join(snap, "results.json"), encoding="utf-8"))
    order = [t["key"] for t in res["topics"]]
    eff = predict.effort_map({k: v for k, v in topics.items() if k in order})
    qs = [{**q, "part": q["q"][0], "topics": q.get(field) or q.get("topics", [])} for q in paper["questions"]]
    total = sum(q["marks"] for q in qs)
    unseen = sorted({t for q in qs for t in q["topics"] if t not in topics})
    rng = random.Random(0)
    rows = []
    for f in SHARES:
        top = predict.take_share(order, eff, f)
        rnd = statistics.mean(predict.attainable(qs, predict.covers(predict.random_share(rng, order, eff, f)))
                              for _ in range(500))
        rows.append((f, predict.attainable(qs, predict.covers(top)), rnd))
    lost = {}
    for q in qs:
        for t in q["topics"]:
            if t not in topics:
                lost[t] = lost.get(t, 0) + q["marks"]
    return {"rows": rows, "unseen": unseen, "lost_marks": lost, "total": total,
            "ranks": {q["q"]: [order.index(t) + 1 if t in order else None for t in q["topics"]] for q in qs},
            "n_topics": len(order)}


def main():
    a = sys.argv[1:]
    if len(a) < 2:
        sys.exit(__doc__)
    field = a[a.index("--tags") + 1] if "--tags" in a else "topics"
    paper = json.load(open(a[1], encoding="utf-8"))
    o = score(a[0], paper, field)
    print(f"{paper.get('label', a[1])}: {o['total']} marks offered (choices included), tags = {field}, "
          f"forecast of {o['n_topics']} topics; coverage = share of the marks a student can answer")
    print("| Syllabus studied | forecast | random |")
    print("|---|---|---|")
    for f, c, r in o["rows"]:
        print(f"| {f:.0%} | {c:.0%} | {r:.0%} |")
    if o["lost_marks"]:
        print("Marks on content the forecast never listed:",
              ", ".join(f"{t} {m}" for t, m in sorted(o["lost_marks"].items(), key=lambda x: -x[1])))
    print("Rank of each question's topics:", "  ".join(f"{q}:{r}" for q, r in o["ranks"].items()))


if __name__ == "__main__":
    main()
