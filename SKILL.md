---
name: ktu-exam-predictor
description: Predict the important topics and likely questions for any KTU B.Tech exam from a course code or name (e.g. "PCCST502", "DAA", "machine learning", "microcontrollers"). It downloads about 10 past papers (current course + older-scheme versions) and the ktunotes module notes, extracts the questions, forecasts with a method checked against a real paper, and teaches the result. Use this whenever the user mentions a KTU subject together with exams, PYQs or previous-year questions, important topics, "what will come", a study plan before an exam, a supplementary/supply exam, or exam prediction, even if they don't say "predict".
---

# KTU exam predictor

You get a course code or name, and sometimes the exam date or a number of papers. Turn it into a forecast the student can study from: which topics matter, in what order, with every past version of each question and worked answers.

**Scripts:** the `scripts/` folder next to this SKILL.md. Call it `$S`. Its location depends on how the skill was installed, e.g. `~/.claude/skills/ktu-exam-predictor/scripts` (global) or `.claude/skills/ktu-exam-predictor/scripts` (project). Use the base directory you were given when this skill loaded. Needs Python 3 with numpy, and poppler-utils.
- `fetch.py`: finds the course, downloads papers and notes, sets up the folder.
- `extract_questions.py`: drafts the questions from the downloaded papers.
- `predict.py`: produces the forecast.

**Subject data:** `~/Documents/Study Materials/<SHORT>/predictor/` (change the root with the `KTU_STUDY_DIR` environment variable).

**Before you start, read:**
- `references/student.md`, if it exists: who the student is, how they study, their exam calendar and what is already set up. It is a private, local file; `references/student.example.md` shows its format. If it is missing, ask the student for their course, exam date and how they like to study.
- `references/lessons.md`: why notes come first. A forecast built only from old papers missed 14 of 60 marks on a real paper.

## Choose the mode by time left

- **Full mode (more than 3 days, or no date given):** steps 1–5 below, then teach.
- **Quick mode (3 days or less):** the student needs to start studying within the hour.
  1. Run step 1 (setup) and step 2 (extraction).
  2. Give a first study order straight away from the notes and the extracted questions:
     - list each module's sections
     - mark the ones whose wording recurs across papers, using `pyq/*.txt`
     - add the notes' worked examples and numericals
  3. Start teaching module by module.
  4. Hand steps 3–5 to a background subagent, if your agent can run one, with the subject folder path and this skill's path. Otherwise do them between teaching sessions. When its `FORECAST.md` arrives, adjust the remaining order and say what changed.

Covering every module matters more than perfect ranking, because each module carries the same marks.

## Step 1: set up (resolve, fetch, create folder)

```bash
python3 $S/fetch.py resolve "<code or name>"      # check the course and predecessors it picked
python3 $S/fetch.py setup "<code or name>" --papers 10 [--old CODE] [--short NAME]
```

`setup` does the following:
- matches the input to the ktunotes catalogue by code, short name or name
- adds older-scheme predecessors (similar name and next to it in the catalogue, e.g. PCCST502 → CST306)
- downloads the N most recent distinct exams: current course first, then 2019-scheme papers, then 2015-scheme ones only if needed
- downloads the typed notes and writes `notes/OUTLINE.txt` (scans go to `notes/img/`)
- writes `subject.json`, a `papers.json` skeleton and `STATUS.md`

**Check the predecessors in the resolve output.** A course with a similar name but different content (an ECE version, say) adds noise. Rerun with `--old` or drop those papers. If no notes folder exists, use the student's own PDFs or `https://topics.ktunotes.live/<code>`.

## Step 2: extract questions (script drafts, you finish)

```bash
python3 $S/extract_questions.py ~/Documents/"Study Materials"/<SHORT>/predictor          # draft
python3 $S/extract_questions.py ~/Documents/"Study Materials"/<SHORT>/predictor --check  # validate after fixing
```

The script splits each paper's OCR text into sub-questions (`A1`, `B11a`, …) with marks and text. It rebuilds the numbering from the marks column, the a)/b) labels and the Module/OR markers, because OCR mangles printed numbers. It also marks the same exam saved under two names as `duplicate_of`; `predict.py` skips those.

**Expect most KTU papers to need finishing by you.** In testing, about 1 in 10 real papers came out fully clean, and most drafts missed or merged one or two questions. The script saves time on layout; reading noisy OCR is something you do better.

For each paper marked `review` or `SCANNED` in `pyq/EXTRACTION.md`:
1. Open its `.txt`, or its pages in `pyq/img/`.
2. Correct or write `questions[]` in `papers.json`, keeping the paper's own numbering.
3. Run `--check` until every paper passes. It checks the KTU pattern (2019 scheme: 10 × 3 + 14-mark Part B questions; 2024 scheme: 8 × 3 + 9-mark ones), duplicate ids and empty text, so a mis-transcribed paper can't slip through.

With several papers to fix, give 2–3 papers each to parallel subagents. Pass each one the paper paths, the question format and the `--check` command.

2015-scheme papers (CSxxx codes) have a different layout. Skip them if there are enough 2019 and 2024 papers.

## Step 3: topics.json, built from the notes

Make one topic per section of the current notes, using `notes/OUTLINE.txt` and the images for scanned modules. This is where the CN forecast went wrong: topics built from old papers can't see new-syllabus material.

```json
{"topics": {"MERGE_SORT": {"name": "Merge sort and its analysis", "module": 2, "syllabus": "in",
                           "model": true, "notes": ["M2:14-18"]}}}
```

- **`model: true`:** the topic is in the official model paper (`https://model.ktunotes.live/<code>`, or a MODEL folder from `fetch.py list-notes <CODE>`).
- **`syllabus: old`:** the topic is predecessor-only and absent from the notes.
- **The paper pattern:** if the model paper shows a pattern other than 8 × 3 + 4 × 9, update `pattern` in `subject.json`. PBCST courses may differ.

## Step 4: tag topics, then predict

1. In `papers.json`, give every sub-question all the topic keys it needs. `predict.py` refuses untagged questions, so a silent gap can't distort the forecast.
2. Run:

```bash
python3 $S/predict.py ~/Documents/"Study Materials"/<SHORT>/predictor --type regular --label "<Mon YYYY>"
```

Use `--type supplementary` for supply exams. Their reuse rates differ.

## Step 5: deliver and teach

Read `FORECAST.md`, then give, in order of priority:
1. **The module-balanced study order**, with notes page numbers. Each module's top topics come first.
2. **Every past version of each top topic's question.** Exact wording rarely repeats, but the topic and its textbook example do.
3. **Worked answers** for numericals and the notes' own examples, checked by computing them where possible.
4. **The assembled likely paper**, labelled as a guess.

If the student wants a page to study from and your agent can publish pages (e.g. Claude's Artifact tool), build one: forecast, likely questions, and an answer under each question.

Then teach topic by topic. The student says "next" to move on.

Report accuracy honestly, for example: "on the one real paper checked, the top 30–35 topics covered about 80–90% of marks". A student who trusts an inflated number and stops studying early can fail, so overclaiming does real harm.

## After the real exam

1. Add the paper to `papers.json` with `"course": "<current code>"`.
2. Rerun `predict.py`. A holdout table appears, comparing history only, notes only and the blend.
3. Write `<SHORT>/FINDINGS.md`, and update `references/lessons.md` if something new was learned.
