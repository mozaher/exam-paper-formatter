"""In-place injection into .tex: markers, heuristics, escaping, sandbox compile."""
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from formatting import inject_tex, sandbox
from formatting.pdf import PaperData, QuestionData, SectionData

FIXTURES = Path(__file__).parent / "fixtures"

needs_latex = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not installed"
)

MARKED_TEX = r"""\documentclass[12pt]{article}
\usepackage[margin=25mm]{geometry}
\begin{document}
\noindent My Institution \hfill EXAM
%%QUESTIONS_START
\noindent\textbf{1.} Dummy question one? \hfill [2 marks]
\vspace{40mm}

\noindent\textbf{2.} Dummy question two? \hfill [5 marks]
\vspace{85mm}
%%QUESTIONS_END
\noindent End matter stays.
\end{document}
"""


def _paper_data():
    data = PaperData(title="Real Exam")
    data.sections = [
        SectionData(
            title="Part 1",
            questions=[
                QuestionData(
                    item_type="mcq", body="Pick the best answer, ok?",
                    marks=Decimal(2),
                    choices=[("First & best", True), ("Second", False)],
                ),
                QuestionData(
                    item_type="essay", body="Explain 50% of $peculiar_{cases}$.",
                    marks=Decimal(4),
                ),
            ],
        )
    ]
    return data


def test_marker_detection_is_unambiguous():
    detection = inject_tex.detect(MARKED_TEX)
    assert not detection.ambiguous
    assert detection.used_markers


def test_heuristic_detection_on_fixture():
    text = FIXTURES.joinpath("sample.tex").read_text()
    detection = inject_tex.detect(text)
    assert not detection.ambiguous
    assert detection.start > 0


def test_generate_replaces_only_the_region():
    detection = inject_tex.detect(MARKED_TEX)
    out = inject_tex.generate(MARKED_TEX, (detection.start, detection.end), _paper_data())
    assert "Dummy question one" not in out
    assert "Pick the best answer" in out
    assert "My Institution" in out          # before region: untouched
    assert "End matter stays." in out       # after region: untouched
    assert "\\documentclass[12pt]{article}" in out  # preamble untouched


def test_question_line_pattern_is_inferred_from_dummy():
    detection = inject_tex.detect(MARKED_TEX)
    out = inject_tex.generate(MARKED_TEX, (detection.start, detection.end), _paper_data())
    # The dummy pattern \noindent\textbf{N.} ... \hfill [n marks] is reused.
    assert "\\textbf{1.}" in out
    assert "[2 marks]" in out


def test_answer_space_scales_with_marks():
    detection = inject_tex.detect(MARKED_TEX)
    out = inject_tex.generate(MARKED_TEX, (detection.start, detection.end), _paper_data())
    # Dummy rule: 2->40mm, 5->85mm  =>  15mm/mark + 10mm base; 4 marks ≈ 70mm.
    import re

    spaces = [float(v) for v, _ in re.findall(r"\\vspace\{(\d+(?:\.\d+)?)(mm)\}", out)]
    assert spaces, "essay must get a vspace"
    assert 55 <= spaces[-1] <= 85


def test_latex_specials_in_content_are_escaped():
    detection = inject_tex.detect(MARKED_TEX)
    out = inject_tex.generate(MARKED_TEX, (detection.start, detection.end), _paper_data())
    assert r"50\%" in out
    assert r"\$peculiar" in out
    assert r"First \& best" in out


@needs_latex
def test_injected_tex_compiles_in_sandbox():
    detection = inject_tex.detect(MARKED_TEX)
    out = inject_tex.generate(MARKED_TEX, (detection.start, detection.end), _paper_data())
    pdf = sandbox.compile_latex(out.encode())
    assert pdf.startswith(b"%PDF-")
