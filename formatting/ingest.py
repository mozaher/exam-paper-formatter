"""Template ingestion & generation orchestration (in-place injection model).

Ingest:   upload -> unconditional sanitize (docx) -> locate dummy-question
          region (AI-assisted step, heuristic default) -> store sanitized
          source + confirmed region on the template.
Generate: inject real paper content into the stored source (everything
          outside the region byte-preserved) -> render to PDF through the
          compile sandbox (pdflatex / LibreOffice, no exceptions).

The region detector is the pluggable AI step: deterministic pattern
classifiers ship as the default; an LLM detector can be swapped in behind
detect_regions() to handle messier documents — it would only ever propose
region boundaries for staff to visually confirm, never emit documents.
"""
from dataclasses import dataclass

from . import inject_docx, inject_tex, sandbox, sanitize


class IngestError(Exception):
    pass


@dataclass
class IngestResult:
    kind: str            # "docx" | "tex"
    sanitized: bytes
    detection_json: dict
    ambiguous: bool
    reason: str


def ingest_upload(filename: str, source: bytes) -> IngestResult:
    name = (filename or "").lower()
    if name.endswith(".docx"):
        try:
            cleaned = sanitize.sanitize_docx(source)
        except ValueError as exc:
            raise IngestError(str(exc)) from exc
        try:
            detection = inject_docx.detect(cleaned)
        except inject_docx.InjectError as exc:
            raise IngestError(str(exc)) from exc
        return IngestResult(
            kind="docx",
            sanitized=cleaned,
            detection_json=detection.to_json(),
            ambiguous=detection.ambiguous,
            reason=detection.reason,
        )
    if name.endswith(".tex"):
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IngestError("The .tex file must be UTF-8 encoded.") from exc
        detection = inject_tex.detect(text)
        return IngestResult(
            kind="tex",
            sanitized=source,
            detection_json=detection.to_json(),
            ambiguous=detection.ambiguous,
            reason=detection.reason,
        )
    raise IngestError("Upload a .docx or .tex template.")


def redetect(kind: str, sanitized: bytes, manual_region) -> dict:
    """Re-run detection with a staff-confirmed region."""
    if kind == "docx":
        return inject_docx.detect(sanitized, manual_region=manual_region).to_json()
    text = sanitized.decode("utf-8")
    return inject_tex.detect(text, manual_region=manual_region).to_json()


def build_document(kind: str, sanitized: bytes, detection_json: dict,
                   paper_data, answers=False) -> bytes:
    """Inject paper content into the stored source. Returns docx or tex bytes."""
    region = (detection_json.get("start", -1), detection_json.get("end", -1))
    if region[0] < 0:
        raise IngestError(
            "This template has no confirmed question region — re-upload it "
            "and confirm where the questions go."
        )
    if kind == "docx":
        try:
            return inject_docx.generate(sanitized, region, paper_data, answers)
        except inject_docx.InjectError as exc:
            raise IngestError(str(exc)) from exc
    try:
        text = sanitized.decode("utf-8")
        return inject_tex.generate(text, region, paper_data, answers).encode("utf-8")
    except inject_tex.TexInjectError as exc:
        raise IngestError(str(exc)) from exc


def render_pdf(kind: str, document: bytes) -> bytes:
    """Render an injected document to PDF via the compile sandbox."""
    if kind == "docx":
        return sandbox.convert_docx(document)
    return sandbox.compile_latex(document)


def generate_paper_pdf(kind, sanitized, detection_json, paper_data, answers=False):
    return render_pdf(
        kind, build_document(kind, sanitized, detection_json, paper_data, answers)
    )
