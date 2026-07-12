"""In-place content injection for .tex templates.

The uploaded LaTeX source IS the template: preamble, packages, fonts,
spacing and everything outside the dummy-question block are preserved
byte-for-byte. Only the question block is replaced, and the result is
compiled by the existing sandbox (no shell-escape, no network, resource
limits) — arbitrary institutional LaTeX never runs outside it.

Region selection, in order of preference:
1. Explicit markers (documented convention, always unambiguous):
       %%QUESTIONS_START ... %%QUESTIONS_END
2. Heuristic: the contiguous line range spanning recognizable dummy
   questions/options/marks/vspace lines. Ambiguous detections fall back to
   the staff pointing out the start/end lines in the UI.

New question lines are built from the DUMMY question's own line as a
pattern (its number, text and marks are substituted), so the institution's
commands (\\textbf, \\hfill [n marks], \\vspace...) are reused as-is. All
injected content is LaTeX-escaped.
"""
import re
import statistics
from dataclasses import dataclass, field

from .patterns import BRACKETED_MARKS_RE, OPTION_RE, QUESTION_START_RE, marks_in

MARKER_START = "%%QUESTIONS_START"
MARKER_END = "%%QUESTIONS_END"

VSPACE_RE = re.compile(r"\\vspace\*?\{(\d+(?:\.\d+)?)\s*(mm|cm|pt|in)\}")
ITEM_RE = re.compile(r"^\s*\\item\b")
SECTIONISH_RE = re.compile(r"\\(section|subsection|part)\*?\{")
QUESTION_TEXT_RE = re.compile(
    r"^(?P<prefix>.*?)(?P<number>\d{1,3})(?P<mid>\s*[\.\)]\s*\}?\s*)"
    r"(?P<text>.+?)(?P<suffix>\s*(?:\\hfill\s*)?(?:[\[\(]\s*\d+(?:\.\d+)?\s*marks?\s*[\]\)])?\s*)$"
)

TEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
    "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


class TexInjectError(Exception):
    pass


def escape_tex(text: str) -> str:
    return "".join(TEX_SPECIALS.get(ch, ch) for ch in text or "")


@dataclass
class TexDetection:
    start: int = -1          # line index, inclusive
    end: int = -1            # line index, inclusive
    ambiguous: bool = True
    reason: str = ""
    used_markers: bool = False
    blocks: list = field(default_factory=list)  # [{index, kind, text}]

    def to_json(self):
        return {
            "start": self.start, "end": self.end, "ambiguous": self.ambiguous,
            "reason": self.reason, "used_markers": self.used_markers,
            "blocks": self.blocks,
        }

    @classmethod
    def from_json(cls, data):
        d = cls()
        for key in ("start", "end", "ambiguous", "reason", "used_markers", "blocks"):
            setattr(d, key, data.get(key, getattr(d, key)))
        return d


def _line_kind(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return "blank"
    if MARKER_START in stripped or MARKER_END in stripped:
        return "marker"
    plain = re.sub(r"\\[a-zA-Z]+\*?|[{}]", " ", stripped)
    plain = " ".join(plain.split())
    if OPTION_RE.match(plain):
        return "option"
    if ITEM_RE.match(stripped):
        return "item"
    if VSPACE_RE.search(stripped):
        return "vspace"
    if QUESTION_START_RE.match(plain) or (
        re.search(r"\{\s*\d{1,3}\s*[\.\)]\s*\}", stripped)
    ):
        return "question"
    return "text"


def detect(source_text: str, manual_region=None) -> TexDetection:
    lines = source_text.splitlines()
    detection = TexDetection()
    detection.blocks = [
        {"index": i, "kind": _line_kind(ln), "text": ln.strip()[:90]}
        for i, ln in enumerate(lines)
    ]

    if manual_region is not None:
        start, end = manual_region
        if 0 <= start <= end < len(lines):
            detection.start, detection.end = start, end
            detection.ambiguous = False
            detection.reason = "Region confirmed by staff."
            return detection

    starts = [i for i, ln in enumerate(lines) if MARKER_START in ln]
    ends = [i for i, ln in enumerate(lines) if MARKER_END in ln]
    if starts and ends and starts[0] < ends[0]:
        detection.start, detection.end = starts[0] + 1, ends[0] - 1
        detection.ambiguous = False
        detection.used_markers = True
        detection.reason = "Explicit %%QUESTIONS_START/%%QUESTIONS_END markers."
        return detection

    q_lines = [
        i for i, b in enumerate(detection.blocks)
        if b["kind"] in ("question", "option", "item", "vspace")
    ]
    if not q_lines:
        detection.reason = (
            "No dummy questions were recognized. Add %%QUESTIONS_START and "
            "%%QUESTIONS_END comment markers around them, or point out the "
            "region below."
        )
        return detection

    start, end = min(q_lines), max(q_lines)
    questions = sum(
        1 for b in detection.blocks[start:end + 1] if b["kind"] in ("question", "item")
    )
    detection.start, detection.end = start, end
    if questions >= 2:
        detection.ambiguous = False
        detection.reason = f"Found {questions} dummy question lines."
    else:
        detection.reason = (
            "Couldn't confidently find the dummy questions — please confirm "
            "the region (or add %%QUESTIONS_START/%%QUESTIONS_END markers)."
        )
    return detection


@dataclass
class TexPrototypes:
    question_pattern: str = ""      # with {number} {text} placeholders, marks kept
    question_has_marks: bool = False
    option_pattern: str = ""        # with {label} {text}
    heading_pattern: str = ""       # with {text}
    vspace_unit: str = "mm"
    vspace_base: float = 15.0
    vspace_per_mark: float = 15.0
    has_vspace: bool = False


def _harvest(lines, start, end) -> TexPrototypes:
    protos = TexPrototypes()
    rule_points = []
    last_marks = None
    for i in range(start, end + 1):
        line = lines[i]
        kind = _line_kind(line)
        if kind == "question" and not protos.question_pattern:
            match = QUESTION_TEXT_RE.match(line.strip())
            if match:
                marks = marks_in(line)
                protos.question_has_marks = marks is not None
                suffix = match.group("suffix")
                if marks is not None:
                    suffix = BRACKETED_MARKS_RE.sub("[{marks} marks]", suffix)
                protos.question_pattern = (
                    match.group("prefix") + "{number}" + match.group("mid")
                    + "{text}" + suffix
                )
        if kind == "question":
            last_marks = marks_in(line)
        elif kind == "option" and not protos.option_pattern:
            plain = line.strip()
            m = re.search(r"\(?[A-Ha-h][\.\)]\s*", plain)
            if m:
                protos.option_pattern = plain[:m.start()] + "{label} {text}"
        elif kind == "vspace":
            m = VSPACE_RE.search(line)
            protos.has_vspace = True
            protos.vspace_unit = m.group(2)
            if last_marks is not None:
                rule_points.append((last_marks, float(m.group(1))))
                last_marks = None
    if len({p[0] for p in rule_points}) >= 2:
        xs = [p[0] for p in rule_points]
        ys = [p[1] for p in rule_points]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        denom = sum((x - mean_x) ** 2 for x in xs) or 1.0
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        if slope > 0:
            protos.vspace_per_mark = slope
            protos.vspace_base = max(0.0, mean_y - slope * mean_x)
    elif rule_points:
        marks, space = rule_points[0]
        protos.vspace_per_mark = space / max(marks, 1.0)
        protos.vspace_base = 0.0

    if not protos.question_pattern:
        protos.question_pattern = r"\noindent\textbf{{number}.} {text}" + (
            r" \hfill [{marks} marks]" if protos.question_has_marks else ""
        )
    if not protos.option_pattern:
        protos.option_pattern = r"\hspace*{8mm} {label} {text}"
    return protos


def _fill(pattern: str, **values) -> str:
    out = pattern
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _marks_text(marks) -> str:
    try:
        return f"{float(marks):g}"
    except (TypeError, ValueError):
        return str(marks)


def generate(source_text: str, region, paper_data, answers=False) -> str:
    """Replace the question region with real content. Returns .tex source."""
    lines = source_text.splitlines()
    start, end = region
    if not (0 <= start <= end < len(lines)):
        raise TexInjectError("The stored question region no longer matches the file.")
    protos = _harvest(lines, start, end)

    body = []
    number = 0
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for section in paper_data.sections:
        if len(paper_data.sections) > 1:
            body.append(r"\medskip\noindent\textbf{%s}" % escape_tex(section.title))
            body.append("")
        for q in section.questions:
            number += 1
            stem = escape_tex(q.body.replace("\n\n", " ").replace("\n", " "))
            line = _fill(
                protos.question_pattern,
                number=str(number),
                text=stem,
                marks=_marks_text(q.marks),
            )
            body.append(line)
            body.append("")
            if q.item_type == "mcq":
                for i, (text, _correct) in enumerate(q.choices):
                    label = f"{letters[i] if i < 26 else i + 1})"
                    body.append(
                        _fill(protos.option_pattern, label=label,
                              text=escape_tex(text)) + r"\\"
                    )
                body.append("")
            else:
                height = protos.vspace_base + float(q.marks) * protos.vspace_per_mark
                height = max(10.0, min(height, 220.0))
                body.append(r"\vspace{%.1f%s}" % (height, protos.vspace_unit))
                body.append("")

    return "\n".join(lines[:start] + body + lines[end + 1:]) + "\n"
