"""The sandboxed compiler: hard limits, no escapes, no side effects."""
import shutil
from pathlib import Path

import pytest

from formatting import sandbox

FIXTURES = Path(__file__).parent / "fixtures"

needs_latex = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="pdflatex not installed"
)
needs_soffice = pytest.mark.skipif(
    shutil.which("soffice") is None and shutil.which("libreoffice") is None,
    reason="LibreOffice not installed",
)


@needs_latex
def test_legitimate_latex_compiles():
    pdf = sandbox.compile_latex(FIXTURES.joinpath("sample.tex").read_bytes())
    assert pdf.startswith(b"%PDF-")


@needs_latex
def test_shell_escape_produces_no_side_effects(tmp_path):
    marker = tmp_path / "pwned"
    evil = (
        rb"\documentclass{article}\begin{document}"
        rb"\immediate\write18{touch %s}x\end{document}" % str(marker).encode()
    )
    try:
        sandbox.compile_latex(evil)
    except sandbox.CompileError:
        pass  # rejection is fine too
    assert not marker.exists()  # the critical assertion: nothing executed


@needs_latex
def test_reading_outside_job_dir_fails():
    snoop = rb"\documentclass{article}\begin{document}\input{/etc/passwd}\end{document}"
    with pytest.raises(sandbox.CompileError):
        sandbox.compile_latex(snoop)


@needs_latex
def test_runaway_loop_is_killed():
    loop = rb"\documentclass{article}\begin{document}\def\x{\x}\x\end{document}"
    with pytest.raises(sandbox.CompileError):
        sandbox.compile_latex(loop)


def test_oversized_source_rejected():
    with pytest.raises(sandbox.CompileError):
        sandbox.compile_latex(b"x" * (sandbox.MAX_SOURCE_BYTES + 1))


def test_pdf_upload_passthrough_validates_header():
    assert sandbox.render_upload("a.pdf", b"%PDF-1.4 stub").startswith(b"%PDF-")
    with pytest.raises(sandbox.CompileError):
        sandbox.render_upload("a.pdf", b"MZ not a pdf")


def test_unknown_extension_rejected():
    with pytest.raises(sandbox.CompileError):
        sandbox.render_upload("a.exe", b"whatever")


@needs_soffice
def test_docx_converts_to_pdf():
    pdf = sandbox.convert_docx(FIXTURES.joinpath("sample.docx").read_bytes())
    assert pdf.startswith(b"%PDF-")
