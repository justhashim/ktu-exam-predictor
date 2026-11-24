---
name: ktu-exam-predictor
description: Predict the important topics and likely questions for any KTU B.Tech exam from a course code or name (e.g. "PCCST502", "DAA", "machine learning", "microcontrollers"). One command downloads about 10 past papers (current course + older-scheme versions) and the ktunotes module notes, extracts and tags the questions, and writes a forecast checked against a real paper; then teach the result. Use this whenever the user mentions a KTU subject together with exams, PYQs or previous-year questions, important topics, "what will come", a study plan before an exam, a supplementary/supply exam, or exam prediction, even if they don't say "predict".
---

# KTU exam predictor

You get a course code or name, and sometimes an exam date or a paper count. Produce a forecast the student can study from: which topics matter, in what order, with every past version of each question. Then teach it.

**Scripts:** the `scripts/` folder next to this SKILL.md. Call it `$S`; use the base directory you were given when the skill loaded.
- **No installs needed:** they use only the Python 3 standard library.
- **poppler-utils** (`pdftotext`, `pdftoppm`) is optional. It improves PDF text; without it, read PDFs with your own file-reading tool.

## 1. Run the pipeline (one command, about a minute)

```bash
python3 $S/ktu.py prepare "<code or name>" --label "<Mon YYYY>"     # add --type supplementary for supply exams
```

It does the following, printing what it did at each step:
1. Finds the course and its older-scheme versions (e.g. PCCST502 → CST306).
2. Downloads the 10 most recent distinct papers and the ktunotes notes.
3. Drafts every question with its marks.
4. Drafts topics from the notes' sections (from the ktunotes topic list for modules whose notes are scans).
5. Auto-tags questions to topics.
6. Writes `FORECAST.md` in the subject folder (`<study dir>/<SHORT>/predictor/`).
   - The study dir is `$KTU_STUDY_DIR`, else `~/Documents/Study Materials` if it exists, else `~/KTU Exams`.

**Check the course and predecessors it printed.** A similarly named course with different content (an ECE version, say) adds noise. Rerun with `--old CODE` to force a predecessor, then delete the wrong papers from `papers.json` and `pyq/`.

## 2. Read the forecast and the status

```bash
python3 $S/ktu.py status "<code or name>"
```

Open `FORECAST.md`. It contains:
- the module-balanced study order, with notes pages
- the likely-repeat questions
- an assembled likely paper

`status` lists what is still automatic-quality:
- papers whose drafts failed the KTU marks check, or that are scans with no questions
- low-confidence tags
- modules whose notes are scans

## 3. Improve it if there is time (optional, each fix helps)

**What is accurate as-is.** On the one real paper checked, notes-section topics with automatic tags covered **about 85% of the marks** when studying the top 57% of topics (random: 63%). Hand-checked topics and tags reached 90%.

**What to fix, highest value first.** Re-run `python3 $S/ktu.py predict "<course>"` after any of these:
1. **Scanned papers** (no questions):
   - read `pyq/img/*.png` or the PDF
   - write `questions[]` in `papers.json`: `{"q": "A1"|"B11a", "marks": 3, "topics": [], "text": "..."}`
   - run `python3 $S/ktu.py autotag "<course>"`
2. **Papers that failed the marks check** (`pyq/EXTRACTION.md`):
   - correct them in `papers.json`
   - validate with `python3 $S/extract_questions.py <subject dir> --check`
   - The check uses the KTU pattern. 2019 scheme: 10 × 3 marks, plus 14-mark Part B questions. 2024 scheme: 8 × 3 marks, plus 9-mark questions.
3. **Topics:**
   - Merge or rename odd notes-section topics in `topics.json`.
   - Add topics for scanned-notes modules from `notes/img/`.
   - Set `"model": true` for topics in the official model paper (`https://model.ktunotes.live/<code>`).
   - Fine-grained topics (one per notes section) are what make the ranking accurate.
4. **Low-confidence tags:** fix `topics` on questions with a low `tag_score`.

With several papers to fix, use parallel subagents if your agent supports them: 2–3 papers each, with the paper paths, the question format and the `--check` command.

## 4. Teach

In order of priority:
1. **The study order, module by module.** Each 2024-scheme module carries the same marks (15 of 60), so cover every module.
2. **Every past version of each top topic's questions.** Exact wording rarely repeats; topics and textbook examples do.
3. **Worked answers for numericals and the notes' own examples.** Compute them where possible.
4. **The likely paper**, labelled as a guess.

Go topic by topic, and let the student say "next" to move on. If your agent can publish pages (e.g. Claude's Artifact tool), offer a study page with the forecast and an answer under each question.

**Report accuracy as measured** ("on one real paper, the top 30–35 topics covered about 80–90% of marks"), never as a promise. A student who trusts an inflated number and stops studying early can fail.

## Time left decides the order

**3 days or less:**
1. Run step 1.
2. Start teaching from `FORECAST.md` within minutes.
3. Do step 3's fixes between topics, or hand them to a background subagent.

**More time:** do step 3 first, then teach.

## Before you start

- **Read `references/lessons.md`.** A forecast built only from old papers missed 14 of 60 marks; notes-first fixed it.
- **Read `references/student.md` if it exists.** It is private and local: the student's courses, exam dates and how they like to study (`references/student.example.md` shows the format). If it is missing, ask for the exam date and how much time they have.

**After the real exam:**
1. Add it to `papers.json` with `"course": "<current code>"`.
2. Run `ktu.py predict`. A holdout table shows how each method would have done.
3. Note what you learned in `references/lessons.md`.
