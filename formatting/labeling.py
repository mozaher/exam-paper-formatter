"""Semantic labeling of extracted PDF regions.

Takes the geometric Measurements (lines + blank gaps) and decides what each
region *means*: question text, a marks indicator, blank answer space. From
question⇄marks⇄gap pairings it produces the data points for the
marks-to-answer-space rule.

The labeler is a pluggable step behind get_labeler():

- HeuristicLabeler (default): deterministic pattern + geometry rules with
  per-decision confidence. Works offline, costs nothing, and is fully
  auditable.
- LLMLabeler: integration point for an AI model to do the same labeling on
  ambiguous documents. Deliberately NOT wired to an API yet — no key exists
  in this deployment, and per the platform's hard constraint the model would
  only ever emit labels/spec proposals for the deterministic pipeline to
  execute, never a finished template. Enable later by setting
  EXTRACT_LABELER=llm and implementing label() with the Claude API.

Either way the output feeds the same reviewable spec + confirmation flow.
"""
import re
from collections import Counter
from dataclasses import dataclass, field

from django.conf import settings

from .extract import PT_TO_MM, Measurements

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


@dataclass
class LabeledRegion:
    kind: str          # "question" | "marks" | "answer_space" | "other"
    page: int
    text: str = ""
    marks_value: float = 0.0
    height_mm: float = 0.0


@dataclass
class LabelResult:
    labeler: str
    regions: list = field(default_factory=list)
    # (marks_value, blank_height_mm) pairs found under marked questions:
    rule_points: list = field(default_factory=list)
    question_count: int = 0
    marks_count: int = 0
    # Structural proposals from the cover page / question layout:
    heading_text: str = ""
    address_lines: list = field(default_factory=list)
    candidate_labels: list = field(default_factory=list)
    option_style: str = ""
    option_samples: int = 0
    question_x0s_mm: list = field(default_factory=list)


def _marks_in(text):
    match = BRACKETED_MARKS_RE.search(text) or BARE_MARKS_RE.search(text)
    return float(match.group(1)) if match else None


class HeuristicLabeler:
    """Deterministic labeling from text patterns + geometry."""

    name = "heuristic"

    def label(self, m: Measurements) -> LabelResult:
        result = LabelResult(labeler=self.name)

        line_marks = {}
        option_styles = []
        for i, line in enumerate(m.lines):
            marks = _marks_in(line.text)
            is_question = bool(QUESTION_START_RE.match(line.text))
            if marks is not None:
                line_marks[i] = marks
                result.marks_count += 1
                result.regions.append(
                    LabeledRegion("marks", line.page, line.text, marks_value=marks)
                )
            if is_question:
                result.question_count += 1
                result.question_x0s_mm.append(round(line.x0 * PT_TO_MM, 1))
                result.regions.append(LabeledRegion("question", line.page, line.text))
            option = OPTION_RE.match(line.text)
            if option is not None and not is_question:
                paren, letter, punct = option.groups()
                if paren:
                    option_styles.append("(a)")
                elif letter.isupper():
                    option_styles.append("A." if punct == "." else "A)")
                else:
                    option_styles.append("a)")

        if option_styles:
            style, count = Counter(option_styles).most_common(1)[0]
            result.option_style = style
            result.option_samples = count

        self._label_cover(m, result)

        floor = -1  # index of the previous gap's line-above; don't scan past it
        for gap in m.gaps:
            # A blank region belongs to the question above it: scan upward for
            # the nearest marks indicator, but never past the previous blank
            # region (that space belongs to the previous question).
            marks = None
            idx = gap.above_index
            lowest = max(floor + 1, gap.above_index - 12)
            while idx >= lowest:
                if idx in line_marks:
                    marks = line_marks[idx]
                    break
                idx -= 1
            floor = gap.above_index
            height_mm = round(gap.height_pt * PT_TO_MM, 1)
            if marks is not None:
                result.rule_points.append((marks, height_mm))
                result.regions.append(
                    LabeledRegion(
                        "answer_space",
                        m.lines[gap.above_index].page,
                        marks_value=marks,
                        height_mm=height_mm,
                    )
                )
            else:
                result.regions.append(
                    LabeledRegion(
                        "other", m.lines[gap.above_index].page, height_mm=height_mm
                    )
                )
        return result

    def _label_cover(self, m: Measurements, result: LabelResult):
        """Cover-page structure: big heading, corner address, fill-in fields."""
        page1 = [ln for ln in m.lines if ln.page == 0]
        if not page1:
            return
        body_size = m.body_font_size_pt or 11.0
        page_center = m.page_width_pt / 2.0

        # Heading: the largest clearly-oversized, horizontally centered,
        # short text line on page 1.
        best = None
        for line in page1:
            if line.font_size < body_size * 1.25 or len(line.text) > 40:
                continue
            center = (line.x0 + line.x1) / 2.0
            if abs(center - page_center) > 0.12 * m.page_width_pt:
                continue
            if best is None or line.font_size > best.font_size:
                best = line
        if best is not None:
            result.heading_text = best.text.strip()
            result.regions.append(LabeledRegion("heading", 0, best.text))

        # Address block: right-side lines in the top ~30% of page 1, above
        # the heading (fill-in rows below it must not bleed in).
        address_floor = best.y1 if best is not None else 0.7 * m.page_height_pt
        for line in page1:
            if line is best or FIELD_LABEL_RE.match(line.text):
                continue
            if (
                line.y0 >= max(address_floor, 0.7 * m.page_height_pt)
                and line.x0 >= 0.55 * m.page_width_pt
                and len(result.address_lines) < 6
            ):
                result.address_lines.append(line.text.strip())
                result.regions.append(LabeledRegion("address", 0, line.text))

        # Candidate fill-in fields: "Label:" lines above the first question.
        first_q_index = next(
            (i for i, ln in enumerate(m.lines) if QUESTION_START_RE.match(ln.text)),
            len(m.lines),
        )
        seen = set()
        for i, line in enumerate(m.lines[:first_q_index]):
            if line.page != 0:
                break
            match = FIELD_LABEL_RE.match(line.text)
            if match is None:
                continue
            label = match.group(1).strip()
            if label.lower() in FIELD_STOPLIST or label.lower() in seen:
                continue
            if len(result.candidate_labels) >= 8:
                break
            seen.add(label.lower())
            result.candidate_labels.append(label)
            result.regions.append(LabeledRegion("candidate_field", 0, line.text))


class LLMLabeler:
    """Placeholder for AI-assisted labeling of ambiguous layouts.

    Integration point: send the line texts + geometry (never the uploaded
    source file) to a Claude model and ask for the same LabelResult schema.
    The model proposes labels; everything downstream (spec derivation,
    review, rendering) stays deterministic.
    """

    name = "llm"

    def label(self, m: Measurements) -> LabelResult:
        raise NotImplementedError(
            "The LLM labeler is not configured (no API key in this "
            "deployment). Set EXTRACT_LABELER=heuristic, or implement this "
            "with the Claude API."
        )


def get_labeler():
    choice = getattr(settings, "EXTRACT_LABELER", "heuristic")
    if choice == "llm":
        return LLMLabeler()
    return HeuristicLabeler()
