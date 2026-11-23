"""Step 2: turn a messy job description + intake notes into a clean role brief,
and split it into chunks for embedding (Step 3)."""
import re

from . import llm

SECTIONS = [
    "Role Summary",
    "Location & Work Setup",
    "Compensation",
    "Must-Haves",
    "Nice-to-Haves",
    "Dealbreakers",
    "Interview Process",
    "Selling Points",
    "Confidential (do not share with recruiters)",
]

BRIEF_SYSTEM = f"""You are a recruiting operations assistant at Contrario.
Rewrite the founder's job description and intake notes into a clean role brief
in Markdown. Use exactly these '## ' headings, in this order:
{chr(10).join('- ' + s for s in SECTIONS)}

Rules:
- Use only facts found in the input. Never invent comp, location, or process details.
- If a section has no information, write: "Not specified yet."
- Put anything the founder says is private (equity details, internal concerns,
  names of other candidates, etc.) under the Confidential heading.
- Use short bullet points."""


def build_brief(title: str, job_description: str, intake_notes: str = "") -> str:
    user = (
        f"Role title: {title}\n\n"
        f"JOB DESCRIPTION:\n{job_description}\n\n"
        f"FOUNDER INTAKE NOTES:\n{intake_notes or '(none)'}"
    )
    body = llm.chat(BRIEF_SYSTEM, user)
    return f"# {title}\n\n{body}"


def chunk_brief(markdown: str) -> list[dict]:
    """One chunk per '## ' section. Confidential sections are flagged so they
    are never retrieved for recruiter answers."""
    chunks = []
    parts = re.split(r"^## +", markdown, flags=re.MULTILINE)
    for part in parts[1:]:
        heading, _, text = part.partition("\n")
        text = text.strip()
        if not text:
            continue
        chunks.append({
            "section": heading.strip(),
            "text": f"{heading.strip()}:\n{text}",
            "confidential": "confidential" in heading.lower(),
        })
    return chunks
