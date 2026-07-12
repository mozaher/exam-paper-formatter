"""Shared text patterns for locating exam structures in documents."""
import re

BRACKETED_MARKS_RE = re.compile(
    r"[\[\(]\s*(\d+(?:\.\d+)?)\s*marks?\s*[\]\)]", re.IGNORECASE
)
BARE_MARKS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*marks?\b", re.IGNORECASE)

# Colon must not be followed by a digit, so times ("4:00 PM") don't count.
QUESTION_START_RE = re.compile(
    r"^\s*(?:q(?:uestion)?\s*)?\d{1,3}\s*(?:[\.\)]|:(?!\d))", re.IGNORECASE
)
OPTION_RE = re.compile(r"^\s*(\()?([A-Ha-h])([\.\)])\s+")
FIELD_LABEL_RE = re.compile(r"^\s*([A-Z][A-Za-z ./#]{0,22})\s*[:：]")

# Labels that look like fill-in fields but aren't candidate details.
FIELD_STOPLIST = {
    "instructions", "instruction", "note", "notes", "warning", "important",
    "answer", "answers", "example", "examples", "marks", "total",
}


def marks_in(text):
    match = BRACKETED_MARKS_RE.search(text) or BARE_MARKS_RE.search(text)
    return float(match.group(1)) if match else None


# How template field labels map onto paper values at generation time.
# Everything else is left exactly as authored.
def map_field_label(label, paper_data):
    """Return the replacement value for a recognized label, else None."""
    key = label.strip().lower()
    if key in ("name", "student", "student name", "candidate", "id",
               "student id", "roll", "roll no", "group", "section",
               "program", "programme"):
        return ""  # fill-in fields: blank for the candidate
    if key in ("subject", "class", "course", "module", "unit", "paper", "exam"):
        return paper_data.title
    if key in ("code", "course code", "subject code"):
        return paper_data.course_code
    if key in ("date", "exam date"):
        return paper_data.exam_date.strftime("%d %B %Y") if paper_data.exam_date else ""
    if key in ("time", "duration", "schedule"):
        return paper_data.duration_text
    return None
