# Changelog

All notable changes to the KTU exam predictor skill. The format follows Keep a Changelog 1.1.0, and versions follow Semantic Versioning 2.0.0:
- **Major:** a command, a flag or a study-folder file changes in a way that old folders or old instructions break.
- **Minor:** new features, with everything old still working.
- **Patch:** bug fixes only.

When you release, bump `metadata.version` in `SKILL.md` to match the newest heading here; a test checks the two agree.

## [1.3.0] - 2026-11-28

Ranks parts of one topic together, says how deep each module's Part B needs a student to go, and lists the notes sections inside each topic. Built from two real papers scored blind: DAA lost 11 marks to a heavily asked topic split into four small ones, and CN to sections hidden inside broader topics.

### Added
- **Topic families.** Parts of one topic are ranked as one family, by the chance any of them is asked (pieces split from one topic: by their best piece), and sit together in the study order. A family comes from a `family` field in `topics.json`, a split topic's parent, or 2 to 6 topics in a module sharing a "Name:" prefix (not generic ones like "Module 1:"). `"family_order": false` in `subject.json` turns it off.
- **Part B depth line** in `FORECAST.md` and `results.json` (`part_b_depth`): per module, how many of its top topics past papers needed to answer one Part B question in full.
- **Covers lists.** Each topic lists the notes sections it covers (`covers` in `topics.json`, a Covers column in `FORECAST.md`), from page-top and numbered mid-page headings: "Digital line coding" shows Block Coding, Scrambling, PCM.

### Fixed
- Sentence fragments at the top of a notes page ("Two connecting devices joined by one") no longer become topics.

## [1.2.0] - 2026-11-28

Two past papers were "left out, could not read" for a user until they tinkered with them. This release makes the skill retry what failed, read what the OCR lost, and say what it found, read and used before it shows the forecast.

### Added
- **Retries.** Each paper, notes PDF, syllabus and model paper gets up to 3 tries. A download that fails, or arrives cut off or damaged, is downloaded again. A web page cut short of its declared length is retried too.
- **Materials report**, printed by `prepare` before the forecast, at the top of `FORECAST.md`, and in `status`:
  - past papers available, selected, downloaded (and how many needed a retry) and read
  - papers and questions used in the forecast
  - **Read with gaps:** used papers whose drafted questions miss question numbers, or carry numbers the paper doesn't have
  - each paper not used, with its reason (download failed, unreadable, scan not transcribed, duplicate, garbled)
  - notes, syllabus and model paper
  - files that needed more than one try, and how to fill what's still missing
- `materials.json` in the subject folder: every file `prepare` tried, its tries, whether it was read as text or a scan, and why it failed.
- **Page images for every question paper** (past papers, the model paper and the student's own papers) in `pyq/img/` and `model/img/`, even when the PDF has text. ktunotes papers are scans with an OCR text layer, which can drop question numbers, words or a Part B heading; the images are where to read them from.
- **SKILL.md step 2:** if a file still failed after 3 tries, rerun `prepare` once; then open the first message to the student with the materials report, in a fixed order.
- `metadata.version` in `SKILL.md`, and this changelog.

### Changed
- A failure that another try won't change (HTTP 403/404, a blocked link, a private Drive file) is reported after one try instead of being retried.
- A rerun of `prepare` skips papers whose page images are already there and newer than the PDF.
- `ktu.py predict` prints the materials report after the forecast summary; `prepare` prints it once.

### Fixed
- A cut-off or damaged PDF was reported as a scan or as "no text tool", and later runs kept the broken file. It is now reported as unreadable and downloaded again.
- An unreadable file from an earlier run is kept until a new copy arrives, so a failed re-download can't lose the only copy.
- A valid PDF with padding after its end marker is no longer rejected as cut off.
- An unreadable syllabus was reported as "read (0 modules)"; an unreadable model paper as found. Both now say "could not be read".
- Failure messages state the real number of tries.
- `status` counted papers the forecast leaves out as garbled; it now agrees with the forecast.
- Notes kept from an earlier run are counted, and scanned notes are no longer counted as used.

## [1.1.0] - 2026-11-26

### Changed
- `ktu.py prepare` downloads and converts papers and notes in parallel, and renders scanned pages as JPEG instead of PNG (#13, closes #12). PCCST502 on Windows: 128 s → 38 s; PBCST504 on Linux: about 70–80 s → about 30 s.
- The syllabus page and syllabus text are fetched and read once per run instead of several times.
- Scanned pages are saved as `pyq/img/*.jpg` and `notes/img/*.jpg` (previously `.png`).

### Fixed
- Notes files that ktunotes lists twice are added to `notes/OUTLINE.txt` once, so they no longer count double when topics are drafted.
- `test_hostile_file_names_and_symlinks` no longer fails on Windows.

### Docs
- README: how to use the skill in web ChatGPT or Gemini (#11).

## [1.0.0] - 2026-11-25

First tagged release.

### Added
- `ktu.py prepare "<code or name>"` builds a forecast in one command:
  - finds the course and its older-scheme predecessors (e.g. PCCST502 → CST306)
  - downloads about 10 past papers, the ktunotes notes, the official syllabus and the model paper
  - extracts the questions, drafts and auto-tags topics, and splits coarse topics into subtopics
  - writes `FORECAST.md` with a module-balanced study order, likely-repeat questions and an assembled likely paper
- `status`, `predict` and `autotag` subcommands for iterating, and `score.py` for blind scoring after the real exam.
- Measured on four real Nov 2026 papers: studying the top 40% / 60% of topics covered 67–90% / 90–95% of marks (lenient), against 48–54% / 67–73% for random topic choice.

[1.3.0]: https://github.com/justhashim/ktu-exam-predictor/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/justhashim/ktu-exam-predictor/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/justhashim/ktu-exam-predictor/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/justhashim/ktu-exam-predictor/releases/tag/v1.0.0
