"""Load role briefs, strip INTERNAL sections, and guard model output.

Founders drop role brief PDFs (or .txt/.md files) into briefs/. Every brief is
split into a public part and an INTERNAL part the moment it is loaded. Only the
public part is ever sent to the model. The INTERNAL part is kept only as a set
of fingerprints (dollar amounts, percentages, and 5-word phrases) so the output
guard can block a reply that somehow repeats internal details.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from pypdf import PdfReader

REFUSAL_LINE = "That's not something I can share."

BRIEF_SUFFIXES = {".pdf", ".txt", ".md"}
SKIP_FILES = {"readme.md", "founder-answers.md"}

# A line containing the uppercase word INTERNAL starts an internal block.
INTERNAL_START_RE = re.compile(r"\bINTERNAL\b")

# Public headings that end an internal block. If none follows, the internal
# block runs to the end of the document (the safe default).
PUBLIC_HEADINGS = (
    "escalation",
    "about the company",
    "how the company works",
    "why we're hiring",
    "the team you'd join",
    "what you'll do",
    "first 30 / 60 / 90 days",
    "must-haves",
    "nice-to-haves",
    "dealbreakers",
    "ideal candidate profile",
    "candidate targeting",
    "candidate match scorecard",
    "sourcing keywords",
    "interview process",
    "why candidates say yes",
    "recruiter faq",
)
MD_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")

FIELD_KEYS = (
    "Company",
    "Role",
    "Role category",
    "Employment type",
    "Location",
    "Salary",
    "Equity",
    "Visa sponsorship",
    "Contrario hire reward",
    "Bonuses",
    "Openings",
    "Hiring timeline",
)

MONEY_RE = re.compile(r"\$\s?(\d+(?:[.,]\d+)*)\s?([kKmMbB])?(?![a-zA-Z])")
PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s?%")
WORD_RE = re.compile(r"[a-z0-9$%.']+")
SHINGLE_SIZE = 5


# ---------------------------------------------------------------- extraction

def extract_pdf_text(source: Path | bytes) -> str:
    """Return the text of a PDF, keeping its layout where pypdf supports it."""
    data = source.read_bytes() if isinstance(source, Path) else source
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout")
        except TypeError:  # older pypdf without layout mode
            text = page.extract_text()
        pages.append(text or "")
    return "\n".join(pages)


def read_document(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return extract_pdf_text(path)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------- INTERNAL removal

def _is_public_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or INTERNAL_START_RE.search(stripped):
        return False
    md = MD_HEADING_RE.match(stripped)
    if md:
        return True
    lowered = stripped.lower()
    if lowered.strip(" :.-=[]") in {"end internal", "public", "end of internal"}:
        return True
    if line[:1].isspace() or lowered.startswith(("-", "*", "\u2022")):
        return False
    return any(lowered.startswith(heading) for heading in PUBLIC_HEADINGS)


def split_internal(text: str) -> tuple[str, str]:
    """Split a brief into (public_text, internal_text)."""
    public_lines: list[str] = []
    internal_lines: list[str] = []
    in_internal = False
    for line in text.splitlines():
        if INTERNAL_START_RE.search(line):
            in_internal = True
            internal_lines.append(line)
            continue
        if in_internal and _is_public_heading(line):
            in_internal = False
        (internal_lines if in_internal else public_lines).append(line)
    public = "\n".join(public_lines).strip() + "\n"
    internal = "\n".join(internal_lines).strip()
    return public, internal


def strip_internal(text: str) -> str:
    return split_internal(text)[0]


# ------------------------------------------------------------- fingerprints

def _money_tokens(text: str) -> set[str]:
    tokens = set()
    for number, suffix in MONEY_RE.findall(text):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        multiplier = {"k": 1e3, "m": 1e6, "b": 1e9}.get((suffix or "").lower(), 1)
        tokens.add(f"${int(round(value * multiplier))}")
    return tokens


def _percent_tokens(text: str) -> set[str]:
    return {f"{float(n):g}%" for n in PERCENT_RE.findall(text)}


def _shingles(text: str) -> set[str]:
    words = WORD_RE.findall(text.lower().replace("\u2019", "'"))
    return {
        " ".join(words[i : i + SHINGLE_SIZE])
        for i in range(len(words) - SHINGLE_SIZE + 1)
    }


def text_tokens(text: str) -> set[str]:
    return _money_tokens(text) | _percent_tokens(text) | _shingles(text)


def internal_fingerprints(internal_text: str) -> set[str]:
    """Tokens from INTERNAL lines, minus the header line itself."""
    body = "\n".join(
        line for line in internal_text.splitlines()
        if "DO NOT SHARE" not in line.upper()
    )
    body = INTERNAL_START_RE.sub(" ", body)
    return text_tokens(body)


def guard_output(text: str, fingerprints: set[str]) -> tuple[str, bool]:
    """Return (safe_text, leaked). A leak replaces the reply with the refusal."""
    if not fingerprints or not text:
        return text, False
    if text_tokens(text) & fingerprints:
        return REFUSAL_LINE, True
    return text, False


# -------------------------------------------------------------- brief model

def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "role"


def parse_fields(text: str) -> dict[str, str]:
    """Read 'Key    value' or 'Key: value' rows, including wrapped values."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    for key in FIELD_KEYS:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*(?::\s*|\s{{2,}})(\S.*?)\s*$", re.I)
        for i, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1)
            # Wrapped values continue on deeply indented lines.
            for nxt in lines[i + 1 : i + 4]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) >= 20:
                    value += " " + nxt.strip()
                else:
                    break
            fields[key.lower()] = re.sub(r"\s+", " ", value).strip()
            break
    return fields


def _title_and_company(text: str, fields: dict[str, str], fallback: str) -> tuple[str, str]:
    if fields.get("role") and fields.get("company"):
        return fields["role"], fields["company"]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if line.upper().startswith("ROLE BRIEF") and i + 2 < len(lines):
            return lines[i + 1], lines[i + 2]
    return fields.get("role", fallback), fields.get("company", "Unknown company")


@dataclass
class RoleBrief:
    role_id: str
    company: str
    title: str
    source_file: str
    public_text: str
    fields: dict[str, str]
    fingerprints: set[str] = field(default_factory=set)

    @property
    def company_short(self) -> str:
        return re.sub(r"\s*\(.*?\)", "", self.company).strip()

    @property
    def label(self) -> str:
        return f"{self.company_short} - {self.title}"

    @property
    def source_name(self) -> str:
        return f"{self.company_short} brief"

    @property
    def category(self) -> str:
        return self.fields.get("role category", "").lower()

    def get(self, key: str, default: str = "not in the brief") -> str:
        return self.fields.get(key.lower(), default)


def parse_brief(raw_text: str, source_file: str) -> RoleBrief:
    public, internal = split_internal(raw_text)
    fields = parse_fields(public)
    title, company = _title_and_company(public, fields, Path(source_file).stem)
    short_company = re.sub(r"\s*\(.*?\)", "", company).strip()
    return RoleBrief(
        role_id=slugify(f"{short_company}-{title}"),
        company=company,
        title=title,
        source_file=source_file,
        public_text=public,
        fields=fields,
        fingerprints=internal_fingerprints(internal) if internal else set(),
    )


class KnowledgeBase:
    """All role briefs in a folder, reloaded when files change."""

    def __init__(self, briefs_dir: Path):
        self.briefs_dir = Path(briefs_dir)
        self._cache: dict[Path, tuple[float, RoleBrief]] = {}

    def _brief_paths(self) -> list[Path]:
        if not self.briefs_dir.exists():
            return []
        return sorted(
            p for p in self.briefs_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in BRIEF_SUFFIXES
            and p.name.lower() not in SKIP_FILES
            and not p.name.startswith(".")
        )

    def briefs(self) -> list[RoleBrief]:
        result = []
        seen = set()
        for path in self._brief_paths():
            mtime = path.stat().st_mtime
            cached = self._cache.get(path)
            if not cached or cached[0] != mtime:
                brief = parse_brief(read_document(path), path.name)
                self._cache[path] = (mtime, brief)
            brief = self._cache[path][1]
            if brief.role_id not in seen:
                seen.add(brief.role_id)
                result.append(brief)
        return result

    def get(self, role_id: str) -> RoleBrief | None:
        return next((b for b in self.briefs() if b.role_id == role_id), None)

    def resolve(self, text: str) -> RoleBrief | None:
        """Best guess at which role a message is about, or None."""
        lowered = text.lower()
        best, best_score = None, 0
        for brief in self.briefs():
            score = 0
            company_words = [w for w in re.findall(r"[a-z0-9]+", brief.company_short.lower()) if len(w) > 2]
            if company_words and company_words[0] in lowered:
                score += 3
            if brief.title.lower() in lowered:
                score += 3
            title_words = [w for w in re.findall(r"[a-z0-9]+", brief.title.lower()) if len(w) > 2]
            score += sum(1 for w in title_words if re.search(rf"\b{re.escape(w)}\b", lowered))
            if score > best_score:
                best, best_score = brief, score
        return best if best_score >= 2 else None

    def fingerprints(self) -> set[str]:
        """Internal tokens from every brief that never appear in public text."""
        briefs = self.briefs()
        internal: set[str] = set()
        public: set[str] = set()
        for brief in briefs:
            internal |= brief.fingerprints
            public |= text_tokens(brief.public_text)
        if self.founder_answers_text():
            public |= text_tokens(self.founder_answers_text())
        return internal - public

    def founder_answers_text(self) -> str:
        path = self.briefs_dir / "founder-answers.md"
        if not path.exists():
            return ""
        return strip_internal(path.read_text(encoding="utf-8", errors="replace"))

    def context_text(self, extra_docs: dict[str, str] | None = None) -> str:
        """Public knowledge for the model, each block labeled with its source."""
        blocks = []
        for brief in self.briefs():
            blocks.append(f"=== SOURCE: {brief.source_name} ({brief.label}) ===\n{brief.public_text}")
        answers = self.founder_answers_text()
        if answers.strip():
            blocks.append(f"=== SOURCE: Founder answers ===\n{answers}")
        for name, text in (extra_docs or {}).items():
            blocks.append(f"=== SOURCE: {name} ===\n{strip_internal(text)}")
        return "\n\n".join(blocks)
