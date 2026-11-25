# Contributing

Thanks for helping. Fixes for new courses, better extraction from scanned papers, and real exam results all make the forecasts better for the next student.

## Before you open a pull request

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline and in temporary folders. They take about a second and need nothing beyond Python 3.8+.

## Ground rules

This skill runs on students' machines, and its text is loaded into an AI agent's context. So a few rules are enforced by `tests/test_repo_policy.py`:

- **Standard library only.** No new dependencies. `pypdf` stays optional, inside a `try`.
- **One network call site.** All downloads go through `fetch.get`, which allows https to the hosts in `safety.ALLOWED_HOSTS` and checks every redirect. A new host means a change to that list and to its test, in the same pull request, with a reason.
- **No shell or dynamic code.** `subprocess.run` is used only for `pdftotext`, `pdftoppm` and the skill's own scripts, with a literal argument list. No `eval`, `exec` or `shell=True`.
- **Text from outside is data.** Anything downloaded, or dropped in by the student, goes through `safety.scrub` before it is stored and `safety.md_safe` before it is written to `FORECAST.md`. File names go through `safety.safe_join`.
- **Docs the agent reads stay plain.** `SKILL.md` and `references/` have no hidden characters, HTML comments, shell commands or links outside the known sites. Their frontmatter keys don't change without review.
- **Private data stays out.** `references/student.md` is git-ignored. Never commit a student's calendar, papers or notes.

If a rule blocks something you really need, change the rule in the test, and say why in the pull request.

## Reporting a real exam

The most useful contribution is a forecast checked against a real paper. Follow "After the real exam" in `SKILL.md`: save the forecast before you look at the paper, then score it with `scripts/score.py`. Add what you learned to `references/lessons.md`.

## Security

If you find a way around these checks (for example, a paper or notes file that gets instructions through to the agent), please open an issue with a minimal example file, or a pull request with a failing test.
