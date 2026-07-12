"""Geometry extraction from a rendered sample PDF.

Measures fonts, margins, line spacing, paragraph spacing and blank vertical
gaps from the PDF's rendered layout (glyph boxes), per the design rule:
formatting is inferred from what the document *looks like*, never by parsing
the semantics of the uploaded LaTeX/Word markup.
"""
import io
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTChar, LTTextContainer, LTTextLine

PT_TO_MM = 25.4 / 72.0
MM_TO_PT = 72.0 / 25.4

# Page furniture ("1", "iv", "- 2 -", "Page 1 of 3") near a page edge is not
# content; left in, it poisons margin and blank-gap measurements.
BARE_NUMBER_RE = re.compile(r"^(?:\d{1,3}|[ivxlc]{1,6})$", re.IGNORECASE)
PAGE_LABEL_RE = re.compile(r"^page\b", re.IGNORECASE)
FURNITURE_EDGE_MM = 30.0


class ExtractError(Exception):
    """The PDF had no measurable text layout (e.g. scanned images only)."""


@dataclass
class TextLine:
    text: str
    page: int
    x0: float
    x1: float
    y0: float
    y1: float
    font_size: float   # dominant size on the line, pt
    font_name: str     # dominant font on the line


@dataclass
class Gap:
    """A blank vertical region between two text lines on the same page."""

    page: int
    above_index: int   # index into Measurements.lines of the line above
    height_pt: float


@dataclass
class Measurements:
    page_width_pt: float
    page_height_pt: float
    lines: list = field(default_factory=list)   # TextLine, reading order
    gaps: list = field(default_factory=list)    # Gap
    body_font_name: str = ""
    body_font_size_pt: float = 0.0
    body_font_char_share: float = 0.0  # fraction of chars in the dominant font+size
    leading_pt: float = 0.0            # baseline-to-baseline, 0 = not measurable
    leading_samples: int = 0
    leading_spread_pt: float = 0.0
    para_gap_pt: float = 0.0           # extra space between paragraphs, 0 = none seen
    para_gap_samples: int = 0
    margin_left_mm: float = 0.0
    margin_right_mm: float = 0.0
    margin_top_mm: float = 0.0
    margin_bottom_mm: float = 0.0
    # Which margins were actually measurable from the sample's text reach.
    margins_measured: dict = field(
        default_factory=lambda: {"left": True, "right": True, "top": True, "bottom": True}
    )


def extract_measurements(pdf_bytes: bytes) -> Measurements:
    laparams = LAParams(line_margin=0.4)
    try:
        pages = list(extract_pages(io.BytesIO(pdf_bytes), laparams=laparams))
    except Exception as exc:
        raise ExtractError(f"Could not read the PDF layout: {exc}") from exc
    if not pages:
        raise ExtractError("The PDF has no pages.")

    m = Measurements(page_width_pt=pages[0].width, page_height_pt=pages[0].height)

    font_counter = Counter()
    for pageno, page in enumerate(pages):
        for element in page:
            if not isinstance(element, LTTextContainer):
                continue
            for obj in element:
                if not isinstance(obj, LTTextLine):
                    continue
                chars = [c for c in obj if isinstance(c, LTChar)]
                text = obj.get_text().strip()
                if not chars or not text:
                    continue
                sizes = Counter(round(c.size, 1) for c in chars)
                fonts = Counter(c.fontname for c in chars)
                line = TextLine(
                    text=text,
                    page=pageno,
                    x0=obj.x0,
                    x1=obj.x1,
                    y0=obj.y0,
                    y1=obj.y1,
                    font_size=sizes.most_common(1)[0][0],
                    font_name=fonts.most_common(1)[0][0],
                )
                m.lines.append(line)
                for c in chars:
                    font_counter[(c.fontname, round(c.size, 1))] += 1

    if not m.lines:
        raise ExtractError(
            "No selectable text found in the rendered document — is it a "
            "scanned image? Upload a text-based sample."
        )

    m.lines = [ln for ln in m.lines if not _is_furniture(ln, m.page_height_pt)]
    if not m.lines:
        raise ExtractError("The document contains no measurable body text.")

    # Reading order: page, top-to-bottom, left-to-right.
    m.lines.sort(key=lambda ln: (ln.page, -round(ln.y1, 1), ln.x0))

    (body_font, body_size), body_chars = font_counter.most_common(1)[0]
    m.body_font_name = body_font
    m.body_font_size_pt = body_size
    m.body_font_char_share = body_chars / sum(font_counter.values())

    _measure_margins(m)
    _measure_spacing(m)
    return m


def _is_furniture(line: TextLine, page_height_pt: float) -> bool:
    edge = FURNITURE_EDGE_MM * MM_TO_PT
    near_edge = line.y0 < edge or line.y1 > page_height_pt - edge
    if not near_edge:
        return False
    text = line.text.strip()
    compact = re.sub(r"[^0-9A-Za-z]", "", text)
    if BARE_NUMBER_RE.match(compact):
        return True  # "1", "iv", "- 2 -"
    return bool(PAGE_LABEL_RE.match(text) and re.search(r"\d", text))  # "Page 1 of 3"


def _measure_margins(m: Measurements):
    xs0 = [ln.x0 for ln in m.lines]
    xs1 = [ln.x1 for ln in m.lines]
    m.margin_left_mm = round(min(xs0) * PT_TO_MM, 1)
    first_page_tops = [ln.y1 for ln in m.lines if ln.page == 0]
    m.margin_top_mm = round((m.page_height_pt - max(first_page_tops)) * PT_TO_MM, 1)

    # Right margin is only measurable when some line actually reaches toward
    # the right edge (ragged short lines say nothing about the margin).
    raw_right = round((m.page_width_pt - max(xs1)) * PT_TO_MM, 1)
    if raw_right > max(1.6 * m.margin_left_mm, m.margin_left_mm + 15):
        m.margin_right_mm = m.margin_left_mm  # assume symmetric
        m.margins_measured["right"] = False
    else:
        m.margin_right_mm = raw_right

    # Bottom margin is only measurable when text reaches the lower page half.
    raw_bottom = round(min(ln.y0 for ln in m.lines) * PT_TO_MM, 1)
    if raw_bottom > 60.0:
        m.margin_bottom_mm = 20.0
        m.margins_measured["bottom"] = False
    else:
        m.margin_bottom_mm = raw_bottom


def _measure_spacing(m: Measurements):
    """Classify vertical deltas between successive lines.

    Deltas near the font size are line leading; slightly larger ones are
    paragraph breaks; much larger ones are blank regions (candidate answer
    space, handed to the labeler).
    """
    size = m.body_font_size_pt or 11.0
    deltas = []  # (above_index, dy)
    for i in range(1, len(m.lines)):
        prev, cur = m.lines[i - 1], m.lines[i]
        if prev.page != cur.page:
            continue
        dy = prev.y1 - cur.y1  # top-to-top ≈ baseline-to-baseline for same size
        if dy > 0.5:
            deltas.append((i - 1, dy))

    leading_candidates = [dy for _, dy in deltas if size * 0.9 <= dy <= size * 2.4]
    if leading_candidates:
        m.leading_pt = statistics.median(leading_candidates)
        m.leading_samples = len(leading_candidates)
        if len(leading_candidates) >= 2:
            m.leading_spread_pt = statistics.pstdev(leading_candidates)
    leading = m.leading_pt or size * 1.35

    para_extras = [
        dy - leading
        for _, dy in deltas
        if leading * 1.15 < dy <= leading * 2.6 and (dy - leading) > 1
    ]
    if para_extras:
        m.para_gap_pt = statistics.median(para_extras)
        m.para_gap_samples = len(para_extras)

    for above_index, dy in deltas:
        if dy > leading * 2.6:
            m.gaps.append(
                Gap(
                    page=m.lines[above_index].page,
                    above_index=above_index,
                    height_pt=dy - leading,
                )
            )
