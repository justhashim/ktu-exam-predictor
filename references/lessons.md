# Lessons from checking a forecast against a real paper (CN, PCCST501, Nov 2026)

If the local study folder has one, the full record is in `<study dir>/CNW/FINDINGS.md`.

**What was done:** 8 old-course (CST303) papers → topics built from those papers → a history model. Its backtest on old papers claimed 94% of marks for the top 35 topics.

**What happened:** the first PCCST501 paper gave **83%** for the top 35 topics. The causes:
- **14 of 60 marks fell on topics missing from the list:** PPP, 4B/5B and 8B/10B, PCM, scrambling. They were new-scheme notes content that never appeared in old papers.
- **The setter followed the ktunotes notes and the textbook.** All 26 sub-questions were in the notes, several as the textbook's own worked examples (Stop-and-Wait 1 Mbps / 20 ms / 15 packets; MAC 47:20:1B:2E:08:EE; FDM with guard bands).
- **About half the sub-questions were reworded old-course questions.** Exact wording rarely repeats.
- **The student who used it had one night,** got through about 7 topics in rank order, and did badly.

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
