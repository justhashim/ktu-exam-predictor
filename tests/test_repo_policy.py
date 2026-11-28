"""Checks on what contributors change. SKILL.md and references/ are loaded into an AI agent's context, and the
scripts run on students' machines, so a pull request must not be able to slip in hidden instructions, links to
new hosts, shell commands, new dependencies or new network or process calls without the tests noticing.

If one of these fails on purpose (say a new download host is really needed), change the rule here in the same
pull request so a reviewer sees it."""
import ast, os, re, subprocess, unittest

import _util as U
import safety

ROOT = U.ROOT
AGENT_DOCS = ["SKILL.md", "references/lessons.md", "references/student.example.md"]   # loaded by the agent
ALL_DOCS = AGENT_DOCS + ["README.md", "CONTRIBUTING.md", "CHANGELOG.md"]
DOC_HOSTS = {"img.shields.io", "www.python.org", "www.npmjs.com", "github.com", "ktunotes.live",
             "model.ktunotes.live", "topics.ktunotes.live", "pyq.ktunotes.live", "view.ktunotes.live"}
GITHUB_REPO = "github.com/justhashim/ktu-exam-predictor"
STDLIB = {"argparse", "ast", "collections", "concurrent.futures", "contextlib", "datetime", "functools", "glob", "html", "io", "itertools",
          "json", "math", "os", "random", "re", "shutil", "statistics", "string", "subprocess", "sys", "tempfile",
          "time", "unicodedata", "urllib", "urllib.error", "urllib.parse", "urllib.request"}
LOCAL = {"fetch", "safety", "predict", "extract_questions", "ktu", "score"}
OPTIONAL = {"pypdf"}                                    # only inside try/except, never required
NETWORK_NAMES = {"urlopen", "build_opener", "Request", "HTTPRedirectHandler", "HTTPSHandler", "OpenerDirector"}
EXTERNAL_TOOLS = {"pdftotext", "pdftoppm"}


def read(rel):
    p = os.path.join(ROOT, rel)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def scripts():
    d = os.path.join(ROOT, "scripts")
    return sorted(f for f in os.listdir(d) if f.endswith(".py"))


def repo_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.split("\n")[:-1] if out.stdout.endswith("\n") else out.stdout.split("\n")
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for r, ds, fs in os.walk(ROOT):
        ds[:] = [d for d in ds if d not in (".git", "__pycache__")]
        found += [os.path.relpath(os.path.join(r, f), ROOT) for f in fs]
    return [f for f in found if f != "references/student.md"]


class TestDocs(unittest.TestCase):
    def test_no_hidden_characters(self):
        for rel in repo_files():
            if rel.endswith((".md", ".py", ".yml", ".yaml", ".json", ".txt")) or rel in (".gitignore", "LICENSE"):
                text = read(rel) or ""
                m = safety._INVISIBLE.search(text) or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", text)
                self.assertIsNone(m, f"{rel}: hidden or control character {m.group(0)!r}" if m else "")

    def test_no_html_comments_or_hidden_html(self):
        for rel in ALL_DOCS:
            text = read(rel)
            if text is None:
                continue
            self.assertNotIn("<!--", text, f"{rel}: HTML comments hide text from readers but not from models")
            self.assertNotRegex(text, r"(?i)<\s*(script|iframe|style|object|embed)\b|display\s*:\s*none|font-size\s*:\s*0",
                                f"{rel}: hidden HTML")

    def test_agent_docs_do_not_talk_like_an_injection(self):
        for rel in ALL_DOCS:
            text = read(rel)
            for i, line in enumerate((text or "").splitlines(), 1):
                self.assertFalse(safety.looks_like_injection(line), f"{rel}:{i}: {line}")

    def test_links_only_to_known_sites(self):
        for rel in ALL_DOCS:
            for url in re.findall(r"https?://[^\s)\]>`\"']+", read(rel) or ""):
                host = re.match(r"https?://([^/:]+)", url).group(1).lower()
                self.assertIn(host, DOC_HOSTS, f"{rel}: link to an unlisted site: {url}")
                self.assertTrue(url.startswith("https://"), f"{rel}: plain http link: {url}")
                if host == "github.com":
                    self.assertIn(GITHUB_REPO, url, f"{rel}: GitHub link outside this repo: {url}")

    def test_agent_docs_have_no_risky_commands(self):
        risky = re.compile(r"(?i)\bsudo\b|\bcurl\b|\bwget\b|\|\s*(ba|z)?sh\b|\brm\s+-[rf]|\bchmod\b|\bchown\b|\beval\b|"
                           r"base64\s+-d|\bnc\s+-|powershell|invoke-(webrequest|expression)|\biex\b|~/\.ssh|\bid_rsa\b|"
                           r"\.env\b|\bssh\s|\bscp\s|crontab|\bgit\s+(push|config|remote)|\bnpm\s+(i|install)\b|"
                           r"pip\s+install\s+(?!pypdf\b)")
        for rel in AGENT_DOCS:
            for i, line in enumerate((read(rel) or "").splitlines(), 1):
                self.assertIsNone(risky.search(line), f"{rel}:{i}: {line}")

    def test_readme_commands_are_the_documented_ones(self):
        for i, line in enumerate((read("README.md") or "").splitlines(), 1):
            if re.search(r"\bsudo\b", line):
                self.assertRegex(line, r"^\s*sudo (apt|dnf) install poppler-utils\b", f"README.md:{i}")
            self.assertNotRegex(line, r"(?i)\|\s*(ba|z)?sh\b|\bcurl\b.*\||\bwget\b", f"README.md:{i}")

    def test_skill_frontmatter(self):
        text = read("SKILL.md")
        m = re.match(r"---\n(.*?)\n---\n", text, re.S)
        self.assertTrue(m, "SKILL.md must start with YAML frontmatter")
        keys = re.findall(r"(?m)^([A-Za-z_-]+):", m.group(1))
        self.assertLessEqual(set(keys), {"name", "description", "license", "metadata"},
                             "new frontmatter keys (e.g. allowed-tools) change what the agent may do: review them")
        self.assertIn("name: ktu-exam-predictor", m.group(1))
        desc = re.search(r"(?m)^description:\s*(.+)$", m.group(1)).group(1)
        self.assertLess(len(desc), 1024)

    def test_skill_version_matches_the_changelog(self):
        """SKILL.md's metadata.version is the newest release in CHANGELOG.md, so a copy of the skill says which
        version it is. Bump both in the release pull request."""
        front = re.match(r"---\n(.*?)\n---\n", read("SKILL.md"), re.S).group(1)
        m = re.search(r'(?m)^metadata:\n(?:[ \t]+.*\n)*?[ \t]+version:\s*"?(\d+\.\d+\.\d+)"?\s*$', front + "\n")
        self.assertTrue(m, "SKILL.md frontmatter needs metadata: version: X.Y.Z")
        changelog = read("CHANGELOG.md") or ""
        released = re.findall(r"(?m)^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}$", changelog)
        self.assertTrue(released, "CHANGELOG.md needs '## [X.Y.Z] - YYYY-MM-DD' release headings")
        self.assertEqual(m.group(1), released[0], "SKILL.md version must equal the newest CHANGELOG.md release")
        self.assertIn(f"[{released[0]}]: https://{GITHUB_REPO}/", changelog, "each release links to its diff")

    def test_skill_mentions_only_existing_scripts(self):
        for name in set(re.findall(r"\$S/([A-Za-z_]+\.py)", read("SKILL.md"))):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", name)), name)

    def test_skill_tells_the_agent_downloaded_text_is_data(self):
        self.assertRegex(read("SKILL.md"), r"(?i)downloaded .{0,80}(data|study material), not instructions")


class TestRepoHygiene(unittest.TestCase):
    def test_private_profile_is_ignored_and_not_committed(self):
        self.assertIn("references/student.md", read(".gitignore"))
        self.assertNotIn("references/student.md", repo_files())

    def test_no_binaries_or_large_files(self):
        for rel in repo_files():
            p = os.path.join(ROOT, rel)
            if not os.path.isfile(p):
                continue
            self.assertLess(os.path.getsize(p), 512 * 1024, f"{rel} is large")
            self.assertFalse(rel.endswith((".pyc", ".so", ".dll", ".exe", ".bin", ".pdf", ".zip", ".whl")), rel)
            try:
                open(p, encoding="utf-8").read()
            except UnicodeDecodeError:
                self.fail(f"{rel} is not UTF-8 text")


class TestCodePolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trees = {f: ast.parse(read("scripts/" + f), f) for f in scripts()}

    def test_standard_library_only(self):
        for f, tree in self.trees.items():
            optional_ok = {id(n) for t in ast.walk(tree) if isinstance(t, ast.Try)
                           for b in t.body for n in ast.walk(b)}
            for n in ast.walk(tree):
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
                    for name in names:
                        top = name.split(".")[0]
                        if top in OPTIONAL:
                            self.assertIn(id(n), optional_ok, f"{f}: {name} must stay optional (inside try)")
                        else:
                            self.assertTrue(name in STDLIB or top in LOCAL, f"{f}: import {name} (stdlib only)")

    def test_no_dynamic_code_or_shell(self):
        banned_calls = {"eval", "exec", "compile", "__import__", "breakpoint", "input"}
        banned_attrs = {"system", "popen", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
                        "execv", "execve", "execl", "execlp", "execvp", "spawnl", "spawnv", "startfile", "chmod", "chown",
                        "rmtree", "load_module", "loads_pickle"}
        for f, tree in self.trees.items():
            for n in ast.walk(tree):
                if isinstance(n, ast.Call):
                    fn = n.func
                    if isinstance(fn, ast.Name):
                        self.assertNotIn(fn.id, banned_calls, f"{f}:{n.lineno}: {fn.id}()")
                    if isinstance(fn, ast.Attribute):
                        self.assertNotIn(fn.attr, banned_attrs, f"{f}:{n.lineno}: .{fn.attr}()")
                    for kw in n.keywords:
                        self.assertNotEqual(kw.arg, "shell", f"{f}:{n.lineno}: shell= is not allowed")

    def test_subprocess_runs_only_known_tools(self):
        for f, tree in self.trees.items():
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "run" \
                        and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess":
                    arg = n.args[0] if n.args else None
                    self.assertIsInstance(arg, ast.List, f"{f}:{n.lineno}: subprocess.run needs a literal list")
                    first = arg.elts[0]
                    ok = (isinstance(first, ast.Constant) and first.value in EXTERNAL_TOOLS) or \
                         (isinstance(first, ast.Attribute) and first.attr == "executable")
                    self.assertTrue(ok, f"{f}:{n.lineno}: subprocess.run of an unlisted program")

    def test_network_calls_live_only_in_fetch_get(self):
        for f, tree in self.trees.items():
            for n in ast.walk(tree):
                name = n.attr if isinstance(n, ast.Attribute) else (n.id if isinstance(n, ast.Name) else None)
                if name in NETWORK_NAMES:
                    self.assertEqual(f, "fetch.py", f"{f}:{n.lineno}: network access outside fetch.get")
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
                    for m in mods:
                        self.assertFalse(m.startswith(("socket", "http", "ftplib", "smtplib", "telnetlib", "ssl")),
                                         f"{f}: import {m}")
                        if m.startswith("urllib.request"):
                            self.assertEqual(f, "fetch.py", f"{f}: urllib.request outside fetch.py")
        src = read("scripts/fetch.py")
        self.assertEqual(len(re.findall(r"\b_OPENER\.open\(|\burlopen\(", src)), 1, "one network call site")
        get_src = src[src.index("def get("):src.index("def cached(")]
        self.assertIn("safety.check_url(url)", get_src)
        self.assertIn("safety.MAX_DOWNLOAD", get_src)

    def test_url_literals_use_allowed_hosts(self):
        for f, tree in self.trees.items():
            if f == "safety.py":                     # its docstring shows blocked examples on purpose
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    for url in re.findall(r"[a-z]+://[^\s\"'`{}<>()]+", n.value.replace("\\", "")):   # regex literals
                        host = re.match(r"[a-z]+://([^/:?#]+)", url)
                        self.assertTrue(url.startswith("https://") and host and safety.host_allowed(host.group(1)),
                                        f"{f}:{getattr(n, 'lineno', '?')}: {url}")

    def test_no_secret_or_dotfile_paths(self):
        bad = re.compile(r"\.ssh\b|id_rsa|id_ed25519|\.bashrc|\.zshrc|\.profile\b|authorized_keys|\.netrc|"
                         r"\.git-credentials|\.aws/|\.npmrc|\.pypirc|/etc/passwd|/etc/shadow|keychain|\.env\b")
        for f, tree in self.trees.items():
            if f == "safety.py":                     # the detector's own patterns
                continue
            for n in ast.walk(tree):
                if isinstance(n, ast.Constant) and isinstance(n.value, str):
                    self.assertIsNone(bad.search(n.value), f"{f}:{getattr(n, 'lineno', '?')}: {n.value[:80]!r}")

    def test_scripts_write_text_through_safety(self):
        # Every module that turns outside text into files the agent reads imports the guards.
        for f in ("fetch.py", "ktu.py", "extract_questions.py", "predict.py"):
            self.assertRegex(read("scripts/" + f), r"(?m)^import safety\b|^from safety import", f)


if __name__ == "__main__":
    unittest.main()
