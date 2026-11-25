#!/usr/bin/env python3
"""
Guards for untrusted input. Everything this skill downloads (past papers, notes, ktunotes pages, Drive file
names) and every file a student drops in is written by someone else, and an agent reads the text it ends up
in (papers.json, topics.json, OUTLINE.txt, FORECAST.md). So:

  check_url(url)            only https to the hosts the skill needs (also checked on every redirect)
  clean_text(s)             drop invisible / direction-changing / control characters
  looks_like_injection(s)   text that talks to an AI agent instead of a student (prompt injection)
  scrub(s)                  clean_text, and withhold the text entirely if it looks like an injection
  md_safe(s)                text safe to put in a Markdown table: no images, links, HTML or row breaks
  safe_join(base, name)     a file name from outside that cannot escape its folder

Standard library only. Set KTU_NO_NETWORK=1 to make every download fail (the tests do).
"""
import os, re, unicodedata, urllib.parse

# Hosts the skill downloads from, matched exactly or as a parent domain ("x.googleusercontent.com").
ALLOWED_HOSTS = (
    "ktunotes.live",                  # view., pyq., topics., model. (the catalogue, paper index, topic lists)
    "drive.google.com", "docs.google.com", "drive.usercontent.google.com",
    "googleusercontent.com",          # Drive download redirects
    "mbcetlibrary.wordpress.com",     # the MBCET past-paper index
    "files.wordpress.com",
)
MAX_DOWNLOAD = 60 * 1024 * 1024       # bytes; a past paper or notes PDF is a few MB

WITHHELD = "[text withheld: it contained instructions aimed at an AI agent; read the original PDF]"

_INVISIBLE = re.compile("[\u00ad\u034f\u061c\u115f\u1160\u17b4\u17b5\u180b-\u180e\u200b-\u200f\u202a-\u202e"
                        "\u2060-\u2064\u2066-\u206f\u3164\ufe00-\ufe0f\ufeff\uffa0\ufff9-\ufffb]"
                        "|[\U000e0000-\U000e007f]|[\U000e0100-\U000e01ef]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0d-\x1f\x7f-\x9f]")      # keeps \t \n \f (page breaks)

_INJECTION = [re.compile(p, re.I) for p in (
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b.{0,25}"
    r"\b(instructions?|prompts?|rules|directions|guidelines|context|messages?)\b",
    r"\b(new|updated|real|actual|hidden|secret)\s+(instructions?|system prompt|task)\b",
    r"\bsystem\s*prompt\b",
    r"<\s*/?\s*(system|assistant|user|instructions?|im_start|im_end)\b[^>]*>",
    r"\[/?(INST|SYS)\]|<<\s*/?SYS\s*>>|\|im_(start|end)\|",
    r"\b(you are|act as|pretend to be|roleplay as)\s+(now\s+)?(an?\s+)?(ai|llm|assistant|chatbot|agent|claude|chatgpt|gpt|gemini)\b",
    r"\b(note|message|attention|instruction)s?\s+(to|for)\s+(the\s+)?(ai|llm|assistant|agent|model|claude|chatgpt|gpt)\b",
    r"\b(ai|llm|assistant|agent|model|claude|chatgpt|gpt)s?\s*(reading|processing|parsing)\s+this\b",
    r"\b(do not|don't|never)\s+(tell|inform|show|alert|mention (this )?to)\s+(the\s+)?(user|student|human)\b",
    r"\b(curl|wget|iwr|invoke-webrequest)\b[^\n]{0,200}\|\s*(ba|z)?sh\b",
    r"\b(run|execute)\s+(the\s+)?(following|this|these)\s+(shell\s+)?(command|commands|script|code)\b",
    r"\brm\s+-rf\s+[/~]",
    r"(~|\$HOME|%USERPROFILE%)[/\\]\.(ssh|aws|gnupg|config/gh)\b|\bid_(rsa|ed25519)\b",
    r"\b(api[_ -]?key|access[_ -]?token|secret[_ -]?key|password|credentials?)\b.{0,40}\b(send|post|upload|share|paste|email|print|reveal)\b",
    r"\b(send|post|upload|exfiltrate|leak)\b.{0,60}\b(api[_ -]?key|token|secret|password|credentials?|ssh key|\.env)\b",
    r"\bexfiltrat",
)]


class BlockedURL(ValueError):
    pass


def host_allowed(host):
    host = (host or "").lower().rstrip(".")
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def check_url(url):
    """Return url if it is https to an allowed host, else raise BlockedURL. Rejects user-info tricks
    (https://drive.google.com@evil.example/) and look-alike hosts (drive.google.com.evil.example)."""
    if os.environ.get("KTU_NO_NETWORK"):
        raise BlockedURL(f"network disabled (KTU_NO_NETWORK): {url}")
    if not isinstance(url, str) or any(c in url for c in "\r\n\t\\ "):
        raise BlockedURL(f"malformed URL: {url!r}")
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise BlockedURL(f"only https downloads are allowed: {url}")
    if p.username or p.password or "@" in p.netloc:
        raise BlockedURL(f"URL with user info: {url}")
    if p.port not in (None, 443):
        raise BlockedURL(f"unexpected port: {url}")
    if not host_allowed(p.hostname):
        raise BlockedURL(f"host not allowed ({p.hostname}); allowed: {', '.join(ALLOWED_HOSTS)}")
    return url


def clean_text(s):
    """Unicode-normalise and drop characters that hide text from a human reader but not from a model:
    zero-width and joiner characters, bidirectional overrides, Unicode tag characters, control characters."""
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFC", s)       # NFC, not NFKC: keeps n², µ, ½ in questions intact
    s = _INVISIBLE.sub("", s)
    return _CONTROL.sub("", s)


def looks_like_injection(s):
    if not isinstance(s, str) or not s:
        return False
    flat = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", clean_text(s)))   # NFKC folds ｆｕｌｌｗｉｄｔｈ tricks
    return any(p.search(flat) for p in _INJECTION)


def scrub(s):
    """Text from outside, ready to store: cleaned, or WITHHELD if it reads as instructions to an AI."""
    s = clean_text(s)
    return WITHHELD if looks_like_injection(s) else s


def md_safe(s, limit=None):
    """Text for a Markdown table cell or list item. Images and links become plain text (an image URL in a
    rendered page can leak data), HTML is escaped, and line breaks and pipes cannot break the table."""
    s = scrub(s if isinstance(s, str) else str(s))
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)            # ![alt](url) -> alt
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)             # [text](url) -> text
    s = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", s)            # [text][ref] -> text
    s = re.sub(r"(?i)\b(https?|ftp|file|data|javascript):/*", lambda m: m.group(1) + "[:]//", s)
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = s.replace("|", "/").replace("`", "'")
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1] + "…"
    return s


def safe_name(name, default="file"):
    """A file name from outside, reduced to letters, digits, '.', '_', '-' and spaces, never '.' or '..'."""
    name = clean_text(os.path.basename(str(name).replace("\\", "/")))
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:150] or default


def safe_join(base, name):
    """os.path.join(base, safe_name(name)), checked to stay inside base."""
    p = os.path.join(base, safe_name(name))
    rb, rp = os.path.realpath(base), os.path.realpath(p)
    if os.path.commonpath([rb, rp]) != rb or rp == rb:
        raise ValueError(f"unsafe file name: {name!r}")
    return p
