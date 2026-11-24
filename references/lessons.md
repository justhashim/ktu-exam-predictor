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
