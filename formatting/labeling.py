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
from dataclasses import dataclass, field

from django.conf import settings

from .extract import PT_TO_MM, Measurements

BRACKETED_MARKS_RE = re.compile(
    r"[\[\(]\s*(\d+(?:\.\d+)?)\s*marks?\s*[\]\)]", re.IGNORECASE
)
BARE_MARKS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*marks?\b", re.IGNORECASE)
QUESTION_START_RE = re.compile(r"^\s*(?:q(?:uestion)?\s*)?\d{1,3}\s*[\.\):]", re.IGNORECASE)


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


def _marks_in(text):
    match = BRACKETED_MARKS_RE.search(text) or BARE_MARKS_RE.search(text)
    return float(match.group(1)) if match else None


class HeuristicLabeler:
    """Deterministic labeling from text patterns + geometry."""

    name = "heuristic"

    def label(self, m: Measurements) -> LabelResult:
        result = LabelResult(labeler=self.name)

        line_marks = {}
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
                result.regions.append(LabeledRegion("question", line.page, line.text))

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
