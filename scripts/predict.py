#!/usr/bin/env python3
"""
KTU exam predictor: forecast topics and questions for one subject from past papers + module notes.

Usage
  python3 predict.py SUBJECT_DIR [--type regular|supplementary] [--label "Nov 2026"]

SUBJECT_DIR holds (see README.md):
  subject.json  code, name, old_codes, modules, pattern, alpha_history, course_boost, new_topic_questions
  topics.json   topic -> module, syllabus status, model-paper flag, notes pages ("M3:31-35")
  papers.json   every question of every past paper, tagged with topic keys; paper["course"] = course code
Writes SUBJECT_DIR/results.json and SUBJECT_DIR/FORECAST.md. Papers with no questions or with "duplicate_of"
are skipped; questions without topic tags stop the run (tag them first).

Method (v2, learned from the CN Nov 2026 paper; see README.md)
  1. Question families: TF-IDF cosine (numbers kept), average-linkage clustering, one family never
     holds two questions from one paper.
  2. History model: logistic regression "topic appears in the next paper" on frequency, recency,
     last paper, last same-type paper, intensity, gap. Rolling backtest. Needs >= 3 papers.
  3. Notes prior: every section of the current notes is a topic; prior grows with notes pages and the
     model-paper flag, scaled to the expected number of distinct topics in one paper.
  4. Topic probability = alpha x history + (1 - alpha) x notes prior. With no usable history, alpha = 0.
  5. Study order is module-balanced (each module carries the same marks in the 2024 scheme).
  6. Holdout: every current-course paper is re-forecast from earlier papers only and scored.
  7. Questions: times asked, current-course papers weighted course_boost, calibrated to the reuse rate.
"""
import argparse, json, os, re, sys, datetime
import numpy as np

LAMBDA = 0.7          # recency decay per paper
SIM_THRESHOLD = 0.55  # average cosine similarity for two clusters to be the same question
L2 = 0.05
CURVE_K = [10, 15, 20, 25, 30, 35, 40]
ADJUST = {
    "syllabus_weight": {"in": 1.0, "partial": 0.7, "old": 0.5, "new": 1.0},
    "model_paper_boost": 0.30,
    "not_in_notes_prior": 0.03,
}
STOP = set("""a an the of in on for to and or is are be by with from as at this that these those it its what which how why
does do can will you your following given each any all one two three four five six eight their there into
explain describe draw discuss illustrate write give list define state differentiate compare between suitable neat
diagram diagrams example examples short notes about using used use detail details show find also identify summarize
demonstrate mention justify proper appropriate necessary various different working work""".split())


# ------------------------------------------------------------------ data + families
def load(d):
    cfg = json.load(open(os.path.join(d, "subject.json")))
    topics = json.load(open(os.path.join(d, "topics.json")))["topics"]
    pp = os.path.join(d, "papers.json")
    papers = json.load(open(pp))["papers"] if os.path.exists(pp) else []
    papers = [p for p in papers if p.get("questions") and not p.get("duplicate_of")]   # skip empty / duplicate papers
    for p in papers:
        p.setdefault("course", cfg["old_codes"][0] if cfg.get("old_codes") else cfg["code"])
    untagged = [f"{p['id']} {q['q']}" for p in papers for q in p["questions"] if not q.get("topics")]
    if untagged:
        sys.exit(f"{len(untagged)} questions have no topics yet (e.g. {', '.join(untagged[:8])}). "
                 "Tag them in papers.json, or delete papers you will not use.")
    qs = []
    for pi, p in enumerate(papers):
        for q in p["questions"]:
            qs.append({**q, "paper": pi, "pid": p["id"], "part": q["q"][0]})
    return cfg, papers, topics, qs


def tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+(?:\.[0-9]+)*", text.lower()) if t not in STOP]


def numbers(text):
    return sorted(re.findall(r"\d[\d.,/x^]*\d|\d", text))


def similarity(qs):
    docs = [tokens(q["text"]) for q in qs]
    vocab = {w: i for i, w in enumerate(sorted({w for d in docs for w in d}))}
    df = np.zeros(len(vocab))
    for d in docs:
        for w in set(d):
            df[vocab[w]] += 1
    idf = np.log((len(docs) + 1) / (df + 1)) + 1
    M = np.zeros((len(docs), len(vocab)))
    for i, d in enumerate(docs):
        for w in d:
            M[i, vocab[w]] += 1
        M[i] *= idf
        M[i] /= np.linalg.norm(M[i]) or 1
    return M @ M.T


def cluster(qs, S):
    clusters = [[i] for i in range(len(qs))]
    while True:
        best, pair = SIM_THRESHOLD, None
        for a in range(len(clusters)):
            pa = {qs[i]["paper"] for i in clusters[a]}
            ta = {t for i in clusters[a] for t in qs[i]["topics"]}
            for b in range(a + 1, len(clusters)):
                if pa & {qs[i]["paper"] for i in clusters[b]}:
                    continue
                if not ta & {t for i in clusters[b] for t in qs[i]["topics"]}:
                    continue
                s = S[np.ix_(clusters[a], clusters[b])].mean()
                if s > best:
                    best, pair = s, (a, b)
        if not pair:
            break
        a, b = pair
        clusters[a] += clusters.pop(b)
    for f, c in enumerate(clusters):
        for i in c:
            qs[i]["fam"] = f
    return len(clusters)


def repeat_behaviour(papers, qs):
    by_fam = {}
    for i, q in enumerate(qs):
        by_fam.setdefault(q["fam"], []).append(i)
    per_paper = []
    for k in range(1, len(papers)):
        pq = [q for q in qs if q["paper"] == k]
        rep = [q for q in pq if any(qs[i]["paper"] < k for i in by_fam[q["fam"]])]
        per_paper.append({"paper": papers[k]["label"], "type": papers[k]["type"], "course": papers[k]["course"],
                          "questions": len(pq), "repeats": len(rep), "rate": len(rep) / max(len(pq), 1)})
    return {"per_paper": per_paper}


# ------------------------------------------------------------------ models
def logreg(F, y, iters=5000, lr=0.5):
    A = np.column_stack([np.ones(len(F)), F])
    w = np.zeros(A.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-A @ w))
        w -= lr * (A.T @ (p - y) / len(y) + L2 * np.r_[0, w[1:]])
    return w


def sigm(w, F):
    return 1 / (1 + np.exp(-(np.column_stack([np.ones(len(F)), F]) @ w)))


def topic_feats(X, C, types, k, ttype):
    x, c = X[:k], C[:k]
    w = LAMBDA ** np.arange(k - 1, -1, -1)
    same = [i for i in range(k) if types[i] == ttype]
    gap = np.array([min(k - 1 - np.nonzero(x[:, t])[0][-1], 4) / 4 if x[:, t].any() else 1.0 for t in range(x.shape[1])])
    return np.column_stack([x.mean(0), (w[:, None] * x).sum(0) / w.sum(), x[-1],
                            x[same[-1]] if same else x.mean(0), np.minimum(c.mean(0), 3) / 3, gap])


def history_probs(X, C, types, k, ttype, keys):
    """Logistic topic model trained on papers 2..k-1, scoring paper k. None if fewer than 3 papers."""
    if k < 3:
        return None
    wt = logreg(np.vstack([topic_feats(X, C, types, j, types[j]) for j in range(2, k)]),
                np.concatenate([X[j] for j in range(2, k)]))
    return dict(zip(keys, sigm(wt, topic_feats(X, C, types, k, ttype))))


def notes_pages(meta):
    n = 0
    for r in meta.get("notes", []):
        a, _, b = r.split(":")[1].partition("-")
        n += int(b or a) - int(a) + 1
    return n


def notes_prior(topics, expected_topics):
    pages = {t: notes_pages(m) for t, m in topics.items()}
    pos = [v for v in pages.values() if v > 0]
    if not pos:
        return {t: 0.5 for t in topics}
    med = float(np.median(pos))
    raw = {t: min(2.5, (v / med) ** 0.5) for t, v in pages.items() if v > 0}
    base = expected_topics / sum(raw.values())
    out = {}
    for t, m in topics.items():
        if pages[t] == 0:
            out[t] = ADJUST["not_in_notes_prior"]
            continue
        p = min(0.9, base * raw[t])
        if m.get("model"):
            p = 1 - (1 - p) * (1 - ADJUST["model_paper_boost"])
        out[t] = float(p)
    return out


def topic_forecast(p_hist, topics, prior, alpha):
    adj = {}
    for t, meta in topics.items():
        h = 0.0
        if p_hist is not None:
            h = p_hist.get(t, 0.0) * ADJUST["syllabus_weight"].get(meta.get("syllabus", "in"), 1.0)
            if meta.get("model"):
                h = 1 - (1 - h) * (1 - ADJUST["model_paper_boost"])
        a = alpha if p_hist is not None else 0.0
        adj[t] = {"p": float(a * h + (1 - a) * prior[t]), "p_history": float(h), "p_notes": float(prior[t])}
    return adj


def order_global(adj):
    return sorted(adj, key=lambda t: -adj[t]["p"])


def order_balanced(adj, topics, modules):
    per = {m: sorted([t for t in adj if topics[t]["module"] == m], key=lambda t: -adj[t]["p"]) for m in modules}
    out = []
    for r in range(max((len(v) for v in per.values()), default=0)):
        out += sorted([v[r] for v in per.values() if r < len(v)], key=lambda t: -adj[t]["p"])
    return out


# ------------------------------------------------------------------ scoring
def b_pairs(paper_qs):
    nums = sorted({int(re.match(r"B(\d+)", q["q"]).group(1)) for q in paper_qs if q["part"] == "B"})
    return [(n, n + 1) for n in nums if (n - nums[0]) % 2 == 0]


def attainable(paper_qs, ok):
    """Share of marks writable if exactly the questions with ok(q) were prepared (Part B: better option per pair)."""
    def total(f):
        got = sum(q["marks"] for q in paper_qs if q["part"] == "A" and f(q))
        b = {}
        for q in paper_qs:
            if q["part"] == "B":
                n = int(re.match(r"B(\d+)", q["q"]).group(1))
                b[n] = b.get(n, 0) + (q["marks"] if f(q) else 0)
        return got + sum(max(b.get(x, 0), b.get(y, 0)) for x, y in b_pairs(paper_qs))
    return total(ok) / max(total(lambda q: True), 1)


def covers(top):
    return lambda q: set(q["topics"]) <= top


def expected_topics(cfg, papers, qs, upto):
    old = [i for i in range(upto) if papers[i]["course"] != cfg["code"]]
    subq = cfg["pattern"]["subq"]
    if not old:
        return 0.85 * subq
    d = np.mean([len({t for q in qs if q["paper"] == i for t in q["topics"]}) for i in old])
    n = np.mean([sum(1 for q in qs if q["paper"] == i) for i in old])
    return float(d * subq / n)


def holdout(cfg, papers, topics, qs, X, C, types, keys, h, modules):
    pq = [q for q in qs if q["paper"] == h]
    p_hist = history_probs(X, C, types, h, types[h], keys)
    prior = notes_prior(topics, expected_topics(cfg, papers, qs, h))
    alpha = cfg.get("alpha_history", 0.25)
    variants = {
        "history only": order_balanced(topic_forecast(p_hist, topics, prior, 1.0), topics, modules),
        "notes only": order_balanced(topic_forecast(p_hist, topics, prior, 0.0), topics, modules),
        f"blend a={alpha}": order_balanced(topic_forecast(p_hist, topics, prior, alpha), topics, modules),
    }
    grid = [10, 15, 20, 25, 30, 35, 40]
    out = {"paper": papers[h]["label"], "curves": {}}
    for name, order in variants.items():
        out["curves"][name] = {str(K): attainable(pq, covers(set(order[:K]))) for K in grid}
    rng = np.random.default_rng(0)
    out["curves"]["random"] = {str(K): float(np.mean([attainable(pq, covers(set(rng.choice(list(topics), min(K, len(topics)), replace=False))))
                                                      for _ in range(300)])) for K in grid}
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject_dir")
    ap.add_argument("--type", default="regular", choices=["regular", "supplementary"])
    ap.add_argument("--label", default=None)
    args = ap.parse_args()
    D = os.path.abspath(args.subject_dir)
    cfg, papers, topics, qs = load(D)
    modules = list(range(1, cfg.get("modules", 4) + 1))
    target = {"label": args.label or f"next {cfg['code']} exam ({args.type})", "type": args.type, "course": cfg["code"]}
    keys = sorted(topics)
    bad = {t for q in qs for t in q["topics"]} - set(keys)
    assert not bad, f"papers.json uses topic keys missing from topics.json: {sorted(bad)}"
    P = len(papers)
    types = [p["type"] for p in papers]
    X = np.zeros((P, len(keys))); C = np.zeros((P, len(keys)))
    for q in qs:
        for t in q["topics"]:
            X[q["paper"], keys.index(t)] = 1; C[q["paper"], keys.index(t)] += 1
    n_fam = 0
    if qs:
        n_fam = cluster(qs, similarity(qs))
    behaviour = repeat_behaviour(papers, qs) if qs else {"per_paper": []}

    # rolling backtest of the history model on old-course papers
    rows = []
    for k in range(3, P):
        pq = [q for q in qs if q["paper"] == k]
        p_k = history_probs(X, C, types, k, types[k], keys)
        order = sorted(keys, key=lambda t: -p_k[t])
        rng = np.random.default_rng(k)
        rows.append({"paper": papers[k]["label"], "course": papers[k]["course"],
                     "curve": {str(K): attainable(pq, covers(set(order[:K]))) for K in CURVE_K},
                     "random": {str(K): float(np.mean([attainable(pq, covers(set(rng.choice(keys, min(K, len(keys)), replace=False))))
                                                       for _ in range(200)])) for K in CURVE_K}})
    holdouts = [holdout(cfg, papers, topics, qs, X, C, types, keys, h, modules)
                for h in range(3, P) if papers[h]["course"] == cfg["code"]]

    # final forecast
    p_hist = history_probs(X, C, types, P, target["type"], keys)
    prior = notes_prior(topics, expected_topics(cfg, papers, qs, P))
    adj = topic_forecast(p_hist, topics, prior, cfg.get("alpha_history", 0.25))
    order = order_balanced(adj, topics, modules)

    boost = cfg.get("course_boost", 3.0)
    fam_mem = {}
    for q in qs:
        fam_mem.setdefault(q["fam"], []).append(q)
    fam_topics = {f: {t for q in m for t in q["topics"]} for f, m in fam_mem.items()}
    wtimes = {f: sum(boost if papers[pi]["course"] == cfg["code"] else 1.0 for pi in {q["paper"] for q in m}) for f, m in fam_mem.items()}
    tot = {}
    for f, ts in fam_topics.items():
        for t in ts:
            tot[t] = tot.get(t, 0) + wtimes[f]
    fam_out = []
    for f, mem in fam_mem.items():
        mem = sorted(mem, key=lambda q: q["paper"])
        best, bt = -1.0, None
        for t in fam_topics[f]:
            i = keys.index(t)
            qpp = C[:, i].sum() / max(X[:, i].sum(), 1)
            v = adj[t]["p"] * qpp * wtimes[f] / tot[t]
            if v > best:
                best, bt = v, t
        fam_out.append({"family": int(f), "topic": bt, "module": topics[bt]["module"], "p": best,
                        "times": len({q["paper"] for q in mem}),
                        "current_course": any(papers[q["paper"]]["course"] == cfg["code"] for q in mem),
                        "sessions": [papers[q["paper"]]["label"] for q in mem], "latest": mem[-1]["text"],
                        "wordings": [q["text"] for q in mem]})
    same_type = [r for r in behaviour["per_paper"] if r["type"] == target["type"]][-3:] or behaviour["per_paper"][-3:]
    reuse = (sum(r["repeats"] for r in same_type) / sum(r["questions"] for r in same_type)) if same_type else 0.4
    scale = reuse * cfg["pattern"]["subq"] / max(sum(x["p"] for x in fam_out), 1e-9)
    for x in fam_out:
        x["p"] = float(min(0.95, x["p"] * scale))
    fam_out.sort(key=lambda x: (-x["p"], -x["times"]))

    a_share = {}
    for t in topics:
        mem = [q for q in qs if t in q["topics"]]
        a_share[t] = (sum(q["part"] == "A" for q in mem) + 1) / (len(mem) + 3) if mem else 0.35
    used = set()

    def best_q(t):
        cand = [x for x in fam_out if t in fam_topics[x["family"]] and x["family"] not in used]
        if cand:
            x = max(cand, key=lambda x: (x["p"], x["times"]))
            used.add(x["family"])
            return {"topic": t, "p": adj[t]["p"], "q_p": x["p"], "sessions": x["sessions"], "latest": x["latest"], "new": False}
        return {"topic": t, "p": adj[t]["p"], "q_p": None, "sessions": [],
                "latest": cfg.get("new_topic_questions", {}).get(t, "Explain: " + topics[t]["name"]), "new": True}
    pa = cfg["pattern"]
    a_per = max(1, round(pa["part_a"] / len(modules)))
    paper = {}
    for m in modules:
        mt = [t for t in order if topics[t]["module"] == m][:a_per + 4]
        A = sorted(mt, key=lambda t: -a_share[t])[:a_per]
        B = [t for t in mt if t not in A][:4]
        paper[str(m)] = {"A": [best_q(t) for t in A], "B": [[best_q(t) for t in B[0:2]], [best_q(t) for t in B[2:4]]]}

    bt_curve = {}
    if rows:
        bt_curve = {str(K): {"mean": float(np.mean([r["curve"][str(K)] for r in rows])),
                             "worst": float(np.min([r["curve"][str(K)] for r in rows])),
                             "random": float(np.mean([r["random"][str(K)] for r in rows]))} for K in CURVE_K}
    out = {"generated": datetime.date.today().isoformat(), "subject": {k: cfg[k] for k in ("code", "name") if k in cfg},
           "target": target, "papers": [{"id": p["id"], "label": p["label"], "type": p["type"], "course": p["course"]} for p in papers],
           "n_questions": len(qs), "n_families": n_fam, "alpha_history": cfg.get("alpha_history", 0.25) if p_hist is not None else 0.0,
           "behaviour": behaviour, "backtest": bt_curve, "holdout": holdouts,
           "topics": [{"rank": i + 1, "key": t, "name": topics[t]["name"], "module": topics[t]["module"],
                       "notes": topics[t].get("notes", []), "asked": int(X[:, keys.index(t)].sum()) if P else 0, **adj[t]}
                      for i, t in enumerate(order)],
           "questions": fam_out, "expected_repeats": float(sum(x["p"] for x in fam_out)), "reuse_rate": reuse, "paper": paper}
    json.dump(out, open(os.path.join(D, "results.json"), "w"), indent=1, ensure_ascii=False)
    write_markdown(out, cfg, os.path.join(D, "FORECAST.md"))
    report(out)


def write_markdown(o, cfg, path):
    L = [f"# {cfg['name']} ({cfg['code']}) forecast: {o['target']['label']}",
         f"Generated {o['generated']} by the ktu-exam-predictor skill from {len(o['papers'])} papers"
         + (f" ({', '.join(p['label'] + ' ' + p['course'] for p in o['papers'])})" if o["papers"] else "") + ".", "",
         f"Topic probability = history x {o['alpha_history']:.2f} + notes prior x {1 - o['alpha_history']:.2f}. "
         "Study order is module-balanced. Notes pages refer to the PDFs in `notes/`.", ""]
    if o["backtest"]:
        L += ["## History-model backtest (past papers predicted from earlier ones)", "",
              "| K topics | mean | worst | random |", "|---|---|---|---|"]
        L += [f"| {K} | {v['mean']:.0%} | {v['worst']:.0%} | {v['random']:.0%} |" for K, v in o["backtest"].items()]
        L.append("")
    for h in o["holdout"]:
        L += [f"## Holdout: {h['paper']} forecast from earlier papers only", "",
              "| K | " + " | ".join(h["curves"]) + " |", "|---|" + "---|" * len(h["curves"])]
        for K in ("15", "20", "25", "30", "35"):
            L.append(f"| {K} | " + " | ".join(f"{c[K]:.0%}" for c in h["curves"].values()) + " |")
        L.append("")
    L += ["## Study order (module-balanced)", "", "| # | Topic | Module | P(asked) | history | notes | Notes pages | Times asked |",
          "|---|---|---|---|---|---|---|---|"]
    for t in o["topics"]:
        L.append(f"| {t['rank']} | {t['name']} | M{t['module']} | {t['p']:.2f} | {t['p_history']:.2f} | {t['p_notes']:.2f} | "
                 f"{', '.join(t['notes']) or 'not in notes'} | {t['asked']} |")
    if o["questions"]:
        L += ["", f"## Questions most likely to be reused (about {o['expected_repeats']:.0f} expected)", "",
              "| P | Question (latest wording) | Module | Asked in |", "|---|---|---|---|"]
        for x in o["questions"][:40]:
            L.append(f"| {x['p']:.2f} | {x['latest'].replace('|', '/')} | M{x['module']} | {', '.join(x['sessions'])} |")
    L += ["", "## Paper assembled by the algorithm", ""]
    for m, v in o["paper"].items():
        L.append(f"**Module {m}**")
        for q in v["A"]:
            L.append(f"- Part A: {q['latest']}" + (" *(not asked before)*" if q["new"] else f" *({', '.join(q['sessions'])})*"))
        for i, b in enumerate(v["B"], 1):
            for q in b:
                L.append(f"- Part B option {i}: {q['latest']}" + (" *(not asked before)*" if q["new"] else f" *({', '.join(q['sessions'])})*"))
        L.append("")
    open(path, "w").write("\n".join(L) + "\n")


def report(o):
    print(f"{o['subject'].get('name')} ({o['subject'].get('code')}): {o['n_questions']} questions, "
          f"{len(o['papers'])} papers, {o['n_families']} question families")
    for r in o["behaviour"]["per_paper"]:
        print(f"  {r['paper']:9s} {r['course']:9s} ({r['type'][:4]})  {r['repeats']:2d}/{r['questions']} reused ({r['rate']:.0%})")
    if o["backtest"]:
        print("history backtest (K: mean / worst / random): " +
              "  ".join(f"{K}: {v['mean']:.0%}/{v['worst']:.0%}/{v['random']:.0%}" for K, v in o["backtest"].items()))
    for h in o["holdout"]:
        print(f"holdout {h['paper']}: " + " | ".join(f"{n}: K25 {c['25']:.0%} K35 {c['35']:.0%}" for n, c in h["curves"].items()))
    print(f"\nFORECAST {o['target']['label']}  (alpha history {o['alpha_history']:.2f})")
    for t in o["topics"][:20]:
        print(f"  {t['rank']:2d}. M{t['module']} {t['p']:.2f} (hist {t['p_history']:.2f} notes {t['p_notes']:.2f})  {t['name']}")
    for x in o["questions"][:12]:
        print(f"    {x['p']:.2f} {'*' if x['current_course'] else ' '} M{x['module']} {x['latest'][:90]}")


if __name__ == "__main__":
    main()
