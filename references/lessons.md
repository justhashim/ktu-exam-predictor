# Lessons from checking a forecast against a real paper (CN, PCCST501, Nov 2026)

If the local study folder has one, the full record is in `<study dir>/CNW/FINDINGS.md`.

**What was done:** 8 old-course (CST303) papers → topics built from those papers → a history model. Its backtest on old papers claimed 94% of marks for the top 35 topics.

**What happened:** the first PCCST501 paper gave **83%** for the top 35 topics. The causes:
- **14 of 60 marks fell on topics missing from the list:** PPP, 4B/5B and 8B/10B, PCM, scrambling. They were new-scheme notes content that never appeared in old papers.
- **The setter followed the ktunotes notes and the textbook.** All 26 sub-questions were in the notes, several as the textbook's own worked examples (Stop-and-Wait 1 Mbps / 20 ms / 15 packets; MAC 47:20:1B:2E:08:EE; FDM with guard bands).
- **About half the sub-questions were reworded old-course questions.** Exact wording rarely repeats.
- **The student who used it had one night,** got through about 7 topics in rank order. The paper was hard; results are not out yet.

**What was changed (now the method in `scripts/predict.py`):**
- **Topics come from notes sections**, with page counts.
- **Notes prior:** more pages plus the model-paper flag give a higher prior.
- **The forecast is a blend:** 0.25 × history + 0.75 × notes.
- **The study order is module-balanced.**
- **New-course papers count 3×** when ranking likely questions.
- **Re-scoring the same paper:** notes-heavy blends reach 90% at the top 35 topics; history only reaches 78%; random reaches 66%. The weights were chosen on that one paper, so the real gain is smaller.

**What this means when you predict:**
1. **Notes first, papers second**, for every course that changed scheme.
2. **Teach whole topics with every past wording**, plus the worked examples in the notes, not single "likely questions".
3. **Balance the modules.** Each 2024-scheme module carries 15 of the 60 marks (two Part A questions plus one Part B choice).
4. **State accuracy as measured** ("about 80–90% of marks if you cover the top 30–35 topics; this was tested on one paper"). Never "90% guaranteed".
5. **Time is the main limit.** Help the student start early, and put the topics with the most marks first.

**Later checks (automatic pipeline), scored on hand-checked tags by share of the syllabus:**
- **Ranking by topic count flattered coarse topic lists.** With 17 topics, "top 25" meant everything. Measure by share of the syllabus (notes pages) instead.
- **Scoring a paper with its own automatic tags inflates results.** A mis-tag into a popular topic counts as covered. Score with hand-checked tags.
- **Topics from notes headings alone did no better than random.** The ktunotes topic list, plus notes content it misses, plus splitting coarse topics by groups of past questions, beat random by about 15–24 points (hand-built: about 25).

# Second check: PCEOT501 Microcontrollers and Embedded Systems (Nov 2026), scored blind

**The setup:** the worst case.
- **PCEOT501 belongs to Electrical and Computer Engineering ("EO"),** a branch new in the 2024 scheme, and Nov 2026 was its first exam. A new branch has no past papers anywhere, and ktunotes may not cover it for a while.
- **ktunotes had nothing for PCEOT501:** no catalogue entry, notes, topic list, model paper or past papers. The skill could not resolve the code; `--name` fixes that.
- **Expect this for every new branch.** Predecessors must come from other branches, by course name; sibling notes come from established branches (here ECE and CSE).
- **The only predecessor was RAT206,** a 2019-scheme S4 course from another branch. It had 7 distinct papers, all OCR'd scans hand-transcribed (2 were re-uploads of other sessions).
- **Topics came from clusters of RAT206 questions,** with old modules mapped 5 → 4 by guess.
- **The forecast was frozen** (`PCEOT501/predictor/blind_auto/`) before the paper was opened. The paper was then hand-tagged and scored with `score.py`.

**Results (share of the syllabus studied → share of marks):**

| Tags | 40% | 60% | Ceiling (whole list) |
|---|---|---|---|
| Lenient | 58% (random 40%) | 77% (random 57%) | 82% |
| Strict | 45% (random 26%) | 57% (random 38%) | 57% |

The ranking beat random by about 18 points. The ceiling was the problem: what the list lacked, studying could not reach.

**What the old papers could not show:**
- **A whole ARM module (Module 4: 20 of the 96 marks on the paper):** programmer's model, operating modes, conditional execution, ARM programs (GCD, counting 1s). RAT206 had no ARM.
- **About 25 marks of 8051 programs and numericals, in the CO3/CO4 style:**
  - flags after ADD, Timer Mode 1 delay, baud rate with SMOD, IE instructions
  - port toggling with delays, Timer 1 delay programs, serial receive, Timer 0 ISR square wave
  - assembler directives
  - The 2019 RAT206 papers were mostly descriptive.
- **The modules were reshuffled:**
  - M1: embedded basics + 8051 architecture
  - M2: 8051 programming
  - M3: timers, serial, interrupts, interfacing
  - M4: ARM
  - The proportional 5 → 4 guess was wrong, and RAT206's Arduino, embedded-design and RTOS modules were barely asked (2 Part A questions).

**What would have worked:**
- **Notes of similar courses in other branches,** which `fetch.siblings()` finds by name.
- **PBECT404 Microcontrollers (ECE) notes,** based on Mazidi, contain every 8051 numerical and program pattern in the paper.
- **PBCST504 (CSE) notes cover ARM concepts:** programmer's model, privileged/unprivileged modes, RISC. They have no ARM assembly programs.
- **Together** they raise the strict ceiling from 57% to about 87%. This rule was found after seeing the paper, so treat the gain as optimistic.
- **Their automatic topics were junk** (notes-page headings like "Sjmp Done"). Build such topics by hand from `OUTLINE.txt`.

**What this means when you predict:**
1. **When ktunotes has no notes or no papers, ask the student first** to download notes, the syllabus or papers into `<subject>/notes/` and `<subject>/papers/`. `prepare` prints `== ASK THE USER`. Only if they decline, carry on with predecessor papers plus `--notes-from` siblings, and say what that cannot cover.
2. **The module headings alone (from the syllabus or the paper's CO list) would have exposed the ARM module.** Ask for them.
3. **For 2024-scheme microcontroller-type courses, expect calculations and programs** with an 11.0592 MHz crystal. Teach worked timer, baud and delay numericals and the standard programs, not only descriptions.
4. **Score a real paper only against a snapshot frozen before opening it** (`score.py`). The `predict` holdout is optimistic once topics are edited after the exam: here it showed 80–95%.

**Later the same day: the official syllabus.** syllabus.ktunotes.live had PCEOT501's syllabus PDF, even though ktunotes had no notes for the course.
- **Every question on the real paper was in it,** ARM module included. The module headings match the paper's modules.
- **Results:** one topic per syllabus item, plus the same 7 RAT206 papers, scored on strict hand tags. Studying 40% / 60% of the syllabus covered **67% / 90%** of marks (random 49% / 68%), with a ceiling of 100%. The papers-only forecast got 45% / 57%, with a ceiling of 57%.
- **Where the gain comes from:** mostly the topic list itself. Random choice from the syllabus beats the papers-only forecast. The ranking is nearly flat with no notes (equal priors), so cover every item.
- **Caveat:** the parser was written after seeing the paper, so this is not a blind result.
- **How to apply:** `prepare` fetches the syllabus for every course (`syllabus/`, `MODULES.md`). With no notes, its items become the topics. Check `MODULES.md`, because a module number in the table can land a few lines off (here the 8051 Timer heading went to module 2; the paper also asked timer questions in module 2).

# Use every source together (PBCST504 setup, 25 Nov 2026)

Setting up PBCST504 Microcontrollers showed what a single source misses:
- **The syllabus said the exam is 40 marks** (8 × 2 + 4 × 6, a project-based course), not the assumed 60. The marks check and the assembled paper were wrong until this was read.
- **The older course was a different subject.** CST307 (8085/8086/8051, 8254/8255) was picked by name, but PBCST504 is ARM Cortex-M / STM32, and its old questions became the "likely questions". Word overlap alone doesn't catch this, because microcontroller courses share "interrupt", "timer" and "serial". Chip names do: RAT206 (8051/Arduino) against the ARM syllabus had 93% of its chip-naming questions about chips the syllabus never names, against 40% for its real successor PCEOT501.
- **The official model paper showed the style:** STM32 HAL code, baud-rate timing, CAN frame IDs and FreeRTOS `xTaskCreate` parameters. That is more programming than the notes suggest.
- **The ktunotes topic list misses syllabus items.** For DAA, the syllabus check found Disjoint Sets / Union-Find, Randomized Algorithms (Monte Carlo, Las Vegas) and Bin Packing missing from the list.

**How to apply:** `prepare` now fetches the syllabus and model paper for every course, applies the syllabus exam pattern, adds uncovered syllabus items as topics, warns when an older course doesn't fit (`--skip-old`), and lists the materials used at the top of `FORECAST.md`. Calibration: the right predecessors scored 69–95% syllabus match; wrong ones scored 10–17%, or showed 87–93% chip mismatch. That's 7 pairs plus drafted (OCR) papers, so it's a guide, not a guarantee.


# DAA PCCST502, first real paper (Nov 2026), scored blind

Snapshot `DAA/predictor/blind_nov2026/`, paper `nov2026_actual.json`.
- **Topic list: 100% ceiling.** Every one of the 24 questions fit an existing topic, with no `NEW_*` tags. 22 of 25 topics were asked, so the paper covered nearly the whole syllabus.
- **Topic ranking: the ranking the student used beat random; the `FORECAST.md` blend did not.** Two rankings were scored on the same paper:
  - **The study page's ranking, which the student studied from** (29 topics by past-paper marks, model paper counted double). Its top 15 covered 49 of 60 marks (choices counted as the exam allows). The same 15 topics picked at random covered 38 on average (20,000 draws), and the first 15 in syllabus order covered 30. Adding the predicted paper brought it to 56 of 60.
  - **The `FORECAST.md` blend** (notes-heavy prior, a = 0.25): worse than random. Studying 40% / 60% of the syllabus covered 37% / 48% of marks, against 53% / 71% for random choice. The prior pushed big-page theory topics up: complexity of code (ranked 3) and algorithm basics (ranked 10) were not asked, while recurrences (recursion tree, substitution/iteration, Master), ranked 18, 22 and 25, carried all of Module 1 Part B (18 marks).
- **Question level was strong.** About 21 of 24 questions had a recognisable past version in the repeat list. Near-verbatim: Las Vegas vs Monte Carlo, vertex cover NP-completeness, recursion tree T(n/a)+T(bn)+cn, linked-list UNION, matrix chain with dimensions 5x4, 4x6, 6x2. The study page's predicted paper plus its backups covered almost every question. The only gap was Best Fit. A6, the backtracking control abstraction, was in the Backtracking topic card's answer but not in the predicted paper, which asked 4-queens in that slot. When a topic's past questions alternate between a definition and an example (control abstraction vs 4-queens), put both in the predicted paper or its backups.
- **How to apply:**
  - **Before grading a forecast, ask which list the student studied from, and score that one.** Here the study page's list and `FORECAST.md` gave opposite verdicts on the same paper.
  - **When the past-paper-marks ranking and the notes-weighted blend disagree, don't trust the blend's order on its own.** On this paper the notes prior ranked long theory sections above the topics the setter asks every time. That's one paper, so check it on the next course rather than treating it as a rule.
  - **For a 2024-scheme course whose papers touch nearly every topic,** tell the student to cover every topic and drill the repeat list. Don't let them skip low-ranked topics. Recurrence topics in a "Module 1 = analysis" course are Part B staples whatever the notes prior says.
