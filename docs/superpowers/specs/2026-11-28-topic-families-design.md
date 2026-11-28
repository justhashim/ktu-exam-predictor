# Topic families, Part B depth, and what each topic covers

## Why
Two real papers, scored against forecasts frozen before the exam, lost marks in three ways (`references/lessons.md`):

1. **A heavily asked topic split into small ones.** DAA (PCCST502, Nov 2026): recurrences carried all 18 marks of Module 1 Part B and were in every past paper, but as four topics (recursion tree, Master, substitution, iteration) they ranked 11, 17, 25 and 27 on the study page and 18, 22 and 25 in `FORECAST.md`. Together they averaged about 5.8 marks a paper, more than the #1 topic.
2. **Part B questions come in pairs.** A 9-mark question has two parts, usually from two topics, and the student answers one question per module. DAA lost 4 marks in Module 3 (knapsack #8 with Dijkstra #19; matrix chain #9 with Floyd–Warshall #16). CN (PCCST501, Nov 2026, v1.2.0 run blind) lost 3 in Module 1 and 5 in Module 4 at 40% of the syllabus: in each module both questions needed that module's 4th topic, one step past the cut.
3. **Topics too coarse, sections invisible.** CN's syllabus is written by chapter ("Digital transmission", "Bandwidth utilization", "Data link control"). Notes pages on PPP, 4B/5B block coding, scrambling, PCM and FDM were attached to broader topics (HDLC, line coding, transmission media) as reading pages, so nothing told the student those sections were part of the topic. 28 of the 96 marks on offer fell on them. The notes do have those sections, but as numbered headings mid-page ("2. Block Coding", "3. Scrambling", "A. Frequency-Division Multiplexing (FDM)"), and `ktu.heading()` only reads a page's first two lines. Meanwhile sentence fragments at the top of a page pass as headings and become junk topics ("Two connecting devices joined by one", whose next line is "transmission medium.").

## Goals
- Parts of one topic are ranked as one family and sit together in the study order.
- `FORECAST.md` tells the student, per module, how many of that module's topics past papers needed to answer one Part B question in full.
- Each topic lists the notes sections it covers, so a student studying "Digital line coding" sees "Block coding, Scrambling, PCM".
- Page-top sentence fragments no longer become topics.

## Non-goals
- Predicting each topic's exact Part B partner: in DAA's 9 past papers, 15 of 39 topic pairs inside Part B questions appeared once, and none of the real paper's pairs had been set twice.
- Turning notes sections into ranked topics of their own (considered and rejected below).
- Changing the history or notes models, or their weights.

## Anti-goals
- A family so large it swallows a module (PCEOT501 topic names such as "Module 1: ..." would make a 15-topic family).
- Junk in the topic list: a heading filter that lets list items or sentence pieces through must not create topics.
- Lower measured accuracy than v1.2.0.

## Approaches considered
**Ranking.**
1. Rank families as one unit (chosen): the order is where the DAA marks were lost.
2. Show families without reordering: safer, but recurrences would have stayed at 18, 22 and 25.
3. Exact Part B partners from past pairings: dropped, past pairings don't repeat reliably.

**Topic coverage.**
1. A "covers" list inside each topic, from the section headings on its notes pages (chosen): the student sees what is inside the topic; the ranking is unchanged; a stray list item is one extra word in a list, not a fake topic.
2. Sections as ranked subtopics under the topic that absorbed them: names and ranks everything, but CN would grow from 35 to about 80 topics, and every noisy heading that slipped through would become a fake topic (CN's notes have 83 distinct numbered headings, DAA 90, ML 132, many of them list items such as "INPUT" or sentence pieces such as "The client opens a TCP connection and sends").
3. Only fix heading detection: removes junk topics, but adds nothing on CN, whose missing sections were already attached to listed topics. Its fragment fix is taken (below).

## Design

### 1. Families (`predict.py`)
- **Family of a topic**, first match wins:
  1. a `family` field in `topics.json` (any string, shared by its members; set by hand or by an agent);
  2. its `parent` (subtopics made by `split_topics`; the parent is in the family too);
  3. a shared name prefix before ":" ("Recurrences: Master theorem"), when 2 to 6 topics in the same module share it. Prefixes that are generic are ignored: "Module N", "Unit N", "Part X", "Chapter N", "Section N", "M1". A prefix shared by 7 or more topics makes no family: those topics stay single. *(Builder's call: the 2-to-6 cap applies only to prefix families; an explicit `family` field and `parent` families are taken as meant, whatever their size.)*
  4. otherwise the topic is its own family.
- Members of one family are always in one module (a family whose members span modules is split by module).
- **Family score**: the chance at least one member is asked, 1 − Π(1 − pᵢ), from each topic's existing blended p.
- **Order**: `order_balanced` sorts each module's families by family score, interleaves modules as today (round by round), and expands each family in place into its members, highest p first. A family of k members takes k consecutive places in its module's sequence. `order_global` is unchanged.
- **Switch**: `subject.json` `"family_order": true` (the default when absent). `false` gives today's order, so old and new can be compared in one codebase. The backtests and holdouts call `order_balanced`, so they use whichever order is switched on; `take_share` may cut a family at the 40% or 60% mark (no special handling).
- **Output**: `results.json` topics gain `family` (the family label, absent for single topics). `FORECAST.md`'s study-order table gains a "Family" column, blank for single topics.

### 2. Part B depth line (`predict.py`, `FORECAST.md`)
- For each past paper with questions (the same papers `load()` uses), and each pair of Part B options (`b_pairs`):
  - an option's **need** is the deepest position of any topic it is tagged with, each topic's position counted within its own module's part of the final study order (position 1 = the module's first topic). Tags not in the order (e.g. `OFF_SYLLABUS`) are ignored; an option with no tag left is skipped. *(Builder's call: ignoring the off-syllabus part reports how deep the studyable part needs; a mixed-module option is rare and is attributed by the rule below.)*
  - the pair's need is the smaller of its two options' needs, attributed to the module of that option's topics (the most common module among them);
  - pairs with no coverable option are skipped.
- Per module, report the median need over papers, and the number of papers it is based on: "Module 3: in 7 past papers, answering one Part B question in full needed the module's top 5 topics (median)." Modules with no data get no line.
- Shown under the study-order table in `FORECAST.md`, and in `results.json` as `part_b_depth: {module: {"median": n, "papers": k}}`. It does not change the order.

### 3. What each topic covers (`ktu.py`)
- **Section headings on a notes page**: the page's existing `heading()` result, plus any line anywhere on the page that is a numbered or lettered heading: `2. Block Coding`, `4.1 Framing`, `A. Frequency-Division Multiplexing (FDM)`, `3) Scrambling`. A candidate is kept only if it:
  - has 1 to 8 words, starts with a capital letter, and has balanced parentheses;
  - is not mostly lowercase words (more than half of its words of 4+ letters lowercase means it is a sentence, e.g. "The client opens a TCP connection and sends");
  - does not end with ",", "-", ":" or a word such as "by", "of", "the", "a", "an", "to", "and", "or", "in", "on", "with", "for", "not";
  - is not followed by a line that starts with a lowercase letter (the sentence continues);
  - passes `safety.looks_like_injection` and `HEADING_SKIP`.
- **covers**: each topic gets `covers`: the distinct kept headings on its notes pages (`notes` and `notes_see`), in page order, without headings that repeat the topic's own name (`fetch.name_sim` ≥ 0.5) or each other (case-insensitive), at most 12. Written to `topics.json` by `draft_topics`, scrubbed with `safety.scrub`, and shown in `FORECAST.md`'s study-order table as a "Covers" column (truncated, with `md_safe`).
- **Study size**: a topic with no `notes` pages of its own but with `notes_see` pages takes the `notes_see` page count as its study size in `effort_map` (today it counts as a median-sized topic). The notes prior is unchanged.
- **Fragment fix**: `heading()` applies the same "sentence continues" and "ends with a stopword or unbalanced parenthesis" rules, so page-top fragments no longer become gap topics or notes-section topics.
- Existing `topics.json` files are kept (as today, `draft_topics` doesn't redraft unless `--redo`). `covers` for a kept file is filled in on the next `ktu.py topics --redo`; `predict.py` works without it.

### 4. SKILL.md
- Step 3 (topics): `family` can be set in `topics.json` to group topics that belong together.
- Step 4 (teach): teach every section in a topic's `covers` list, and use the Part B depth line to decide how far down each module to go.

## Accuracy check before keeping it
Run old (v1.2.0) and new code side by side and report the numbers in the pull request:
- **Real papers, frozen forecasts:** DAA (`S5/DAA/predictor/blind_nov2026/` against `nov2026_actual.json`) and CN (the v1.2.0 run frozen on 28 Nov 2026, before the paper was tagged, and the real paper tagged to its keys with lenient `topics` and `strict` fields) with `score.py`, lenient and strict, at 40% and 60% of the syllabus. *(Builder's call: the CN snapshot and tagged paper exist in the session scratchpad; the first build step copies them to `S5/CNW/predictor/blind_v120_nov2026/` (`topics.json`, `results.json`, `papers.json`, `FORECAST.md`, `real_paper_v120_tags.json`), next to DAA's, so they outlast the session.)* To score a frozen snapshot with the new code, re-run `predict.py` on a copy of the snapshot folder (it holds `subject.json`-equivalent settings from the original run) with `family_order` on and off. For CN also report a "covers" score: a question counts if a studied topic's name or its covers list names its content. Both checks are after the fact (these papers showed the problems), so they confirm the fixes, not their general value.
- **History backtests** (`predict.py` output) for DAA, ML, AI, CNW and PCEOT501.
- **Topic lists:** count of topics per subject before and after, and the list of topics the fragment fix removed (to check no real topic was lost).
- Keep family ordering on by default only if no subject is worse at 40% or 60%. *(Builder's call: "worse" = any drop in a real paper's strict score, or a drop of more than 2 percentage points in a subject's mean history backtest.)* If one is, report it and ask before keeping it on. The other parts (Family column, depth line, covers, study size, fragment fix) ship either way.

## Left to the builder
- Function names and where the code sits within `predict.py` and `ktu.py`.
- Tests: family detection (explicit, parent, prefix, generic prefix ignored, size cap, single module); family score; order keeps members together; depth line on a hand-made paper set; heading candidates (kept: "2. Block Coding", "A. Frequency-Division Multiplexing (FDM)"; rejected: "The client opens a TCP connection and sends", "Left to right", "Two connecting devices joined by one" followed by a lowercase line, "ACK (client's rwnd 2000 not"); covers built from `notes_see` pages and capped at 12; study size from `notes_see`.
- `CHANGELOG.md`: an `## [Unreleased]` section listing the changes.
