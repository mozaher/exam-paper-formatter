"""In-place content injection for .docx templates.

The institution's (sanitized) Word file IS the template. We locate the
dummy-question region, then at generation time replace only that region
with real exam content, cloning the dummy paragraphs as style prototypes —
fonts, indents, auto-numbering, answer boxes and everything outside the
region stay byte-identical to what the institution authored.

Region detection is the pluggable "AI-assisted" step: the default is a
deterministic pattern classifier with a confidence verdict; ambiguous
documents fall back to staff pointing out the question area in the UI
(never editing data). An LLM can replace the classifier behind the same
detect() contract later.
"""
import copy
import io
import re
import zipfile
from dataclasses import dataclass, field

from defusedxml import ElementTree as SafeET

import xml.etree.ElementTree as ET

from .patterns import (
    FIELD_LABEL_RE,
    FIELD_STOPLIST,
    OPTION_RE,
    QUESTION_START_RE,
    map_field_label,
    marks_in,
)
from .sanitize import fix_mc_ignorable

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class InjectError(Exception):
    pass


def _register_namespaces(xml_bytes: bytes):
    for prefix, uri in re.findall(rb'xmlns:([\w-]+)="([^"]+)"', xml_bytes):
        ET.register_namespace(prefix.decode(), uri.decode())


def para_text(p) -> str:
    return "".join(t.text or "" for t in p.iter(f"{W}t"))


def _is_blank(el) -> bool:
    return el.tag == f"{W}p" and not para_text(el).strip()


def _has_numpr(el) -> bool:
    return el.tag == f"{W}p" and el.find(f"{W}pPr/{W}numPr") is not None


@dataclass
class BlockInfo:
    index: int
    kind: str      # heading | question | option | blank | table | text
    text: str


@dataclass
class Detection:
    """JSON-friendly result of locating the dummy-question region."""

    start: int = -1
    end: int = -1
    ambiguous: bool = True
    reason: str = ""
    blocks: list = field(default_factory=list)  # [{index, kind, text}] snippets

    def to_json(self):
        return {
            "start": self.start,
            "end": self.end,
            "ambiguous": self.ambiguous,
            "reason": self.reason,
            "blocks": self.blocks,
        }

    @classmethod
    def from_json(cls, data):
        d = cls()
        d.start = data.get("start", -1)
        d.end = data.get("end", -1)
        d.ambiguous = data.get("ambiguous", True)
        d.reason = data.get("reason", "")
        d.blocks = data.get("blocks", [])
        return d


def _load_body(source: bytes):
    zin = zipfile.ZipFile(io.BytesIO(source))
    doc_xml = zin.read("word/document.xml")
    _register_namespaces(doc_xml)
    root = SafeET.fromstring(doc_xml)
    body = root.find(f"{W}body")
    if body is None:
        raise InjectError("The document has no body.")
    return zin, root, body


def _classify(body) -> list:
    """Classify body-level blocks by role."""
    blocks = []
    children = list(body)
    for i, el in enumerate(children):
        tag = el.tag.split("}")[1]
        if tag == "tbl":
            blocks.append(BlockInfo(i, "table", "[table]"))
            continue
        if tag != "p":
            blocks.append(BlockInfo(i, "text", f"[{tag}]"))
            continue
        text = para_text(el).strip()
        if not text:
            blocks.append(BlockInfo(i, "blank", ""))
        elif OPTION_RE.match(text):
            blocks.append(BlockInfo(i, "option", text))
        else:
            blocks.append(BlockInfo(i, "text", text))

    # Question paragraphs: the non-blank paragraph immediately before a run
    # of options (MCQ), or a numbered/numbered-looking paragraph (essay).
    for i, info in enumerate(blocks):
        if info.kind != "text":
            continue
        nxt = _next_nonblank(blocks, i)
        if nxt is not None and blocks[nxt].kind == "option":
            info.kind = "question"

    for i, info in enumerate(blocks):
        if info.kind != "text":
            continue
        el = list(body)[i]
        numberish = _has_numpr(el) or bool(QUESTION_START_RE.match(info.text))
        if not numberish:
            continue
        nxt = _next_nonblank(blocks, i)
        # A numbered paragraph directly preceding a question/heading is a
        # section heading; otherwise it's an (essay) question.
        if nxt is not None and blocks[nxt].kind in ("question", "text") and (
            _has_numpr(list(body)[blocks[nxt].index])
            or QUESTION_START_RE.match(blocks[nxt].text or "")
            or blocks[nxt].kind == "question"
        ):
            info.kind = "heading"
        else:
            info.kind = "question"
    return blocks


def _next_nonblank(blocks, i):
    for j in range(i + 1, len(blocks)):
        if blocks[j].kind != "blank":
            return j
    return None


def detect(source: bytes, manual_region=None) -> Detection:
    """Locate the dummy-question region. The AI-assisted step."""
    _, _, body = _load_body(source)
    blocks = _classify(body)

    detection = Detection()
    detection.blocks = [
        {"index": b.index, "kind": b.kind, "text": b.text[:90]} for b in blocks
    ]

    if manual_region is not None:
        start, end = manual_region
        if 0 <= start <= end < len(blocks):
            detection.start, detection.end = start, end
            detection.ambiguous = False
            detection.reason = "Region confirmed by staff."
            return detection

    q_idx = [b.index for b in blocks if b.kind in ("question", "heading", "option")]
    if not q_idx:
        detection.reason = (
            "No dummy questions were recognized in the document (numbered "
            "questions or A)/B)/C) options)."
        )
        return detection

    start, end = min(q_idx), max(q_idx)
    # Extend the region through an essay question's trailing answer blocks
    # (blank paragraphs / answer-box tables) up to the next real content.
    while end + 1 < len(blocks) and blocks[end + 1].kind in ("blank", "table"):
        end += 1
    while end > start and blocks[end].kind == "blank":
        end -= 1

    questions = sum(1 for b in blocks[start:end + 1] if b.kind == "question")
    options = sum(1 for b in blocks[start:end + 1] if b.kind == "option")

    detection.start, detection.end = start, end
    if questions >= 2 and (options >= 2 or questions >= 3):
        detection.ambiguous = False
        detection.reason = f"Found {questions} dummy questions."
    else:
        detection.ambiguous = True
        detection.reason = (
            f"Only {questions} question-like paragraph(s) were recognized — "
            "please confirm where the questions area starts and ends."
        )
    return detection


# ---------------------------------------------------------------------------
# Generation: splice real content into the detected region.
# ---------------------------------------------------------------------------

@dataclass
class Prototypes:
    heading: object = None
    mcq_question: object = None
    option: object = None
    essay_question: object = None
    essay_tail: list = field(default_factory=list)
    blank: object = None
    question_has_marks: bool = False
    question_literal_number: bool = False
    option_style: tuple = ("A", ")")  # (case sample, punctuation)


def _harvest_prototypes(body, blocks, start, end) -> Prototypes:
    protos = Prototypes()
    children = list(body)
    region = blocks[start:end + 1]

    for b in region:
        el = children[b.index]
        if b.kind == "heading" and protos.heading is None:
            protos.heading = el
        elif b.kind == "blank" and protos.blank is None:
            protos.blank = el
        elif b.kind == "option" and protos.option is None:
            protos.option = el
            match = OPTION_RE.match(b.text)
            if match:
                paren, letter, punct = match.groups()
                protos.option_style = ("(" if paren else letter, punct)

    for i, b in enumerate(region):
        if b.kind != "question":
            continue
        el = children[b.index]
        nxt = _next_nonblank(blocks, b.index)
        is_mcq = nxt is not None and blocks[nxt].kind == "option"
        if is_mcq and protos.mcq_question is None:
            protos.mcq_question = el
        if not is_mcq and protos.essay_question is None:
            protos.essay_question = el
            # Tail: everything after the essay question until the next
            # question/heading — typically blank paragraphs + answer table.
            tail = []
            for j in range(b.index + 1, end + 1):
                if blocks[j].kind in ("question", "heading", "option"):
                    break
                tail.append(children[j])
            protos.essay_tail = tail
        if marks_in(b.text):
            protos.question_has_marks = True
        if QUESTION_START_RE.match(b.text):
            protos.question_literal_number = True

    if protos.mcq_question is None:
        protos.mcq_question = protos.essay_question
    if protos.essay_question is None:
        protos.essay_question = protos.mcq_question
    if protos.blank is None:
        protos.blank = ET.fromstring(f"<w:p xmlns:w='{W[1:-1]}'/>")
    return protos


def _clone_with_text(proto, text: str):
    """Deep-copy a paragraph, keep pPr + first run's rPr, set new text."""
    clone = copy.deepcopy(proto)
    first_rpr = None
    for run in clone.iter(f"{W}r"):
        rpr = run.find(f"{W}rPr")
        if rpr is not None:
            first_rpr = copy.deepcopy(rpr)
            break
    for child in list(clone):
        if child.tag != f"{W}pPr":
            clone.remove(child)
    for j, line in enumerate(text.split("\n")):
        run = ET.SubElement(clone, f"{W}r")
        if first_rpr is not None:
            run.append(copy.deepcopy(first_rpr))
        if j > 0:
            ET.SubElement(run, f"{W}br")
        t = ET.SubElement(run, f"{W}t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = line
    return clone


def _option_label(index, style) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    letter = letters[index] if index < 26 else str(index + 1)
    lead, punct = style
    if lead == "(":
        return f"({letter.lower()})"
    if lead.islower():
        return f"{letter.lower()}{punct}"
    return f"{letter}{punct}"


def _question_blocks(protos, number, q, answers=False):
    """Build the block sequence for one question from the prototypes."""
    out = []
    is_mcq = q.item_type == "mcq"
    proto = protos.mcq_question if is_mcq else protos.essay_question
    stem = q.body.replace("\n\n", "\n")
    if protos.question_literal_number:
        stem = f"{number}. {stem}"
    if protos.question_has_marks:
        marks = f"{q.marks:g}" if hasattr(q.marks, "__float__") else str(q.marks)
        unit = "mark" if float(q.marks) == 1 else "marks"
        stem = f"{stem} [{marks} {unit}]"
    out.append(_clone_with_text(proto, stem))

    if is_mcq:
        option_proto = protos.option or proto
        for i, (text, _correct) in enumerate(q.choices):
            label = _option_label(i, protos.option_style)
            out.append(_clone_with_text(option_proto, f"{label} {text}"))
    else:
        if protos.essay_tail:
            out.extend(copy.deepcopy(el) for el in protos.essay_tail)
        else:
            for _ in range(6):
                out.append(copy.deepcopy(protos.blank))
    out.append(copy.deepcopy(protos.blank))
    return out


def _replace_fields(body, region_range, paper_data):
    """Update recognized 'Label: value' fields outside the question region."""
    start, end = region_range
    children = list(body)
    for i, el in enumerate(children):
        if start <= i <= end:
            continue
        if el.tag == f"{W}tbl":
            _replace_fields_in_table(el, paper_data)
        elif el.tag == f"{W}p":
            _replace_field_in_paragraph(el, paper_data)


def _replace_fields_in_table(tbl, paper_data):
    for row in tbl.iter(f"{W}tr"):
        cells = row.findall(f"{W}tc")
        for c, cell in enumerate(cells):
            text = " ".join(para_text(p) for p in cell.findall(f"{W}p")).strip()
            match = FIELD_LABEL_RE.match(text)
            if not match or match.group(1).lower() in FIELD_STOPLIST:
                continue
            remainder = text[match.end():].strip()
            value = map_field_label(match.group(1), paper_data)
            if value is None:
                continue
            if remainder:
                # Label and value share the cell: rewrite the cell text.
                _set_cell_text(cell, f"{match.group(0)} {value}".strip())
            elif c + 1 < len(cells):
                _set_cell_text(cells[c + 1], value)


def _set_cell_text(cell, text):
    paras = cell.findall(f"{W}p")
    if not paras:
        return
    new_p = _clone_with_text(paras[0], text)
    for p in paras:
        cell.remove(p)
    cell.append(new_p)


def _replace_field_in_paragraph(p, paper_data):
    text = para_text(p).strip()
    match = FIELD_LABEL_RE.match(text)
    if not match or match.group(1).lower() in FIELD_STOPLIST:
        return
    if not text[match.end():].strip():
        return  # bare label with no value: leave for handwriting
    value = map_field_label(match.group(1), paper_data)
    if value is None:
        return
    replacement = _clone_with_text(p, f"{match.group(0)} {value}".strip())
    p.clear()
    p.tag = replacement.tag
    p.attrib.update(replacement.attrib)
    for child in list(replacement):
        p.append(child)


def generate(source: bytes, region, paper_data, answers=False) -> bytes:
    """Inject paper content into the sanitized source. Returns .docx bytes."""
    zin, root, body = _load_body(source)
    blocks = _classify(body)
    start, end = region
    if not (0 <= start <= end < len(blocks)):
        raise InjectError("The stored question region no longer matches the document.")

    protos = _harvest_prototypes(body, blocks, start, end)
    if protos.mcq_question is None and protos.essay_question is None:
        raise InjectError(
            "No question paragraph could be used as a formatting prototype "
            "in the selected region."
        )

    new_blocks = []
    number = 0
    for section in paper_data.sections:
        if protos.heading is not None:
            new_blocks.append(_clone_with_text(protos.heading, section.title))
            new_blocks.append(copy.deepcopy(protos.blank))
        for q in section.questions:
            number += 1
            new_blocks.extend(_question_blocks(protos, number, q, answers))

    children = list(body)
    for el in children[start:end + 1]:
        body.remove(el)
    for offset, el in enumerate(new_blocks):
        body.insert(start + offset, el)

    _replace_fields(body, (start, start + len(new_blocks) - 1), paper_data)

    doc_xml = fix_mc_ignorable(
        ET.tostring(root, xml_declaration=True, encoding="UTF-8")
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            if info.filename == "word/document.xml":
                zout.writestr(info.filename, doc_xml)
            else:
                zout.writestr(info.filename, zin.read(info.filename))
    return out.getvalue()
