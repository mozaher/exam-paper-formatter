"""The formatting spec ("theme schema") and its derivation.

A spec is a small dict of named, bounded layout values — the only thing the
deterministic renderer ever consumes. Uploaded sample files are measured and
labeled to *propose* a spec; after staff confirm it (visually), the spec is
stored on a PaperTemplate and the upload is gone.

Everything here is deterministic and clamped to sane bounds, so a bad
extraction can produce an ugly paper but never a broken or unsafe one.
"""
import statistics
from decimal import Decimal

# field: (default, min, max)
NUMERIC_FIELDS = {
    "font_size_pt": (11.0, 8.0, 16.0),
    "line_spacing": (1.35, 1.0, 2.2),
    "para_spacing_pt": (6.0, 0.0, 30.0),
    "margin_left_mm": (20.0, 8.0, 45.0),
    "margin_right_mm": (20.0, 8.0, 45.0),
    "margin_top_mm": (18.0, 8.0, 45.0),
    "margin_bottom_mm": (20.0, 8.0, 45.0),
    "answer_base_mm": (15.0, 0.0, 60.0),
    "answer_per_mark_mm": (18.0, 2.0, 40.0),
}
CHOICE_FIELDS = {
    "font_family": ("serif", ("serif", "sans")),
    "paper_size": ("A4", ("A4", "LETTER")),
}

CONFIDENCE_FIELDS = [
    "font_family",
    "font_size",
    "line_spacing",
    "para_spacing",
    "margins",
    "paper_size",
    "answer_rule",
]

SANS_HINTS = (
    "helvetica", "arial", "calibri", "carlito", "verdana", "tahoma",
    "dejavusans", "liberationsans", "opensans", "roboto", "lato", "sfss",
)
SERIF_HINTS = (
    "times", "georgia", "cambria", "caladea", "garamond", "palatino",
    "bookman", "liberationserif", "dejavuserif", "nimbusroman", "cmr",
    "sfrm", "lmroman", "p052", "minion",
)

# Standard page sizes in mm.
PAGE_SIZES_MM = {"A4": (210.0, 297.0), "LETTER": (215.9, 279.4)}
PT_TO_MM = 25.4 / 72.0


def default_spec() -> dict:
    spec = {name: default for name, (default, _, _) in NUMERIC_FIELDS.items()}
    spec.update({name: default for name, (default, _) in CHOICE_FIELDS.items()})
    return spec


def clamp_spec(spec: dict) -> dict:
    out = default_spec()
    for name, (_, lo, hi) in NUMERIC_FIELDS.items():
        if name in spec:
            try:
                out[name] = round(min(max(float(spec[name]), lo), hi), 2)
            except (TypeError, ValueError):
                pass
    for name, (_, choices) in CHOICE_FIELDS.items():
        if spec.get(name) in choices:
            out[name] = spec[name]
    return out


def spec_from_template(template) -> dict:
    if template is None:
        return default_spec()
    return clamp_spec(
        {
            "font_family": template.font,
            "paper_size": template.paper_size,
            "font_size_pt": template.font_size_pt,
            "line_spacing": template.line_spacing,
            "para_spacing_pt": template.para_spacing_pt,
            "margin_left_mm": template.margin_left_mm,
            "margin_right_mm": template.margin_right_mm,
            "margin_top_mm": template.margin_top_mm,
            "margin_bottom_mm": template.margin_bottom_mm,
            "answer_base_mm": template.answer_base_mm,
            "answer_per_mark_mm": template.answer_per_mark_mm,
        }
    )


def apply_spec_to_template(template, spec: dict):
    spec = clamp_spec(spec)
    template.font = spec["font_family"]
    template.paper_size = spec["paper_size"]
    template.font_size_pt = Decimal(str(spec["font_size_pt"]))
    template.line_spacing = Decimal(str(spec["line_spacing"]))
    template.para_spacing_pt = Decimal(str(spec["para_spacing_pt"]))
    template.margin_left_mm = Decimal(str(spec["margin_left_mm"]))
    template.margin_right_mm = Decimal(str(spec["margin_right_mm"]))
    template.margin_top_mm = Decimal(str(spec["margin_top_mm"]))
    template.margin_bottom_mm = Decimal(str(spec["margin_bottom_mm"]))
    template.answer_base_mm = Decimal(str(spec["answer_base_mm"]))
    template.answer_per_mark_mm = Decimal(str(spec["answer_per_mark_mm"]))
    return template


def _classify_font(font_name: str):
    """Map a PDF font name to a supported family. Returns (family, matched)."""
    normalized = font_name.split("+")[-1].replace("-", "").replace(" ", "").lower()
    for hint in SANS_HINTS:
        if hint in normalized:
            return "sans", True
    for hint in SERIF_HINTS:
        if hint in normalized:
            return "serif", True
    return "serif", False


def _fit_rule(points):
    """Least-squares fit marks -> blank height (mm). Returns (base, per_mark) or None."""
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if len(set(xs)) >= 2:
        n = len(xs)
        mean_x, mean_y = sum(xs) / n, sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        intercept = mean_y - slope * mean_x
        if slope <= 0:
            return None
        return (intercept, slope)
    # One distinct marks value: assume proportionality through a small base.
    marks = xs[0]
    height = statistics.median(ys)
    if marks <= 0:
        return None
    base = min(10.0, height * 0.2)
    return (base, (height - base) / marks)


def derive_spec(measurements, labels):
    """Measurements + labels -> (spec, confidence, notes).

    confidence maps CONFIDENCE_FIELDS to True (unambiguous) / False.
    notes are plain-language explanations of anything ambiguous, written for
    the staff review page — never raw values.
    """
    spec = default_spec()
    confidence = {name: False for name in CONFIDENCE_FIELDS}
    notes = []

    # Paper size
    page_w_mm = measurements.page_width_pt * PT_TO_MM
    page_h_mm = measurements.page_height_pt * PT_TO_MM
    for name, (w, h) in PAGE_SIZES_MM.items():
        if abs(page_w_mm - w) <= 3 and abs(page_h_mm - h) <= 3:
            spec["paper_size"] = name
            confidence["paper_size"] = True
            break
    if not confidence["paper_size"]:
        spec["paper_size"] = "A4" if abs(page_h_mm - 297) < abs(page_h_mm - 279.4) else "LETTER"
        notes.append(
            "The page size didn't exactly match A4 or US Letter, so the closest one was used."
        )

    # Font family + size
    family, matched = _classify_font(measurements.body_font_name)
    spec["font_family"] = family
    confidence["font_family"] = matched
    if not matched:
        notes.append(
            "The document's font isn't one of the supported ones, so the closest "
            "supported style was chosen. Use the font control below if it looks wrong."
        )
    if measurements.body_font_size_pt and measurements.body_font_char_share >= 0.45:
        spec["font_size_pt"] = measurements.body_font_size_pt
        confidence["font_size"] = True
    elif measurements.body_font_size_pt:
        spec["font_size_pt"] = measurements.body_font_size_pt
        notes.append(
            "The document mixes several text sizes; the most common one was used."
        )

    # Margins
    spec["margin_left_mm"] = measurements.margin_left_mm
    spec["margin_right_mm"] = measurements.margin_right_mm
    spec["margin_top_mm"] = measurements.margin_top_mm
    spec["margin_bottom_mm"] = measurements.margin_bottom_mm
    all_measured = all(measurements.margins_measured.values())
    if len(measurements.lines) >= 5 and all_measured:
        confidence["margins"] = True
    elif len(measurements.lines) < 5:
        notes.append(
            "There wasn't much text to measure the page margins from, so they may be off."
        )
    else:
        unmeasured = [k for k, v in measurements.margins_measured.items() if not v]
        notes.append(
            f"The sample's text didn't reach the {' and '.join(unmeasured)} of the "
            "page, so that margin was assumed — check it in the preview."
        )

    # Line spacing
    if measurements.leading_samples >= 3 and measurements.body_font_size_pt:
        ratio = measurements.leading_pt / measurements.body_font_size_pt
        spec["line_spacing"] = ratio
        spread_ok = (
            measurements.leading_samples < 2
            or measurements.leading_spread_pt <= 0.25 * measurements.leading_pt
        )
        confidence["line_spacing"] = spread_ok
        if not spread_ok:
            notes.append("Line spacing varied through the document; an average was used.")
    else:
        notes.append(
            "Line spacing couldn't be measured reliably (too few multi-line "
            "paragraphs in the sample), so a standard value was used."
        )

    # Paragraph spacing
    if measurements.para_gap_samples >= 2:
        spec["para_spacing_pt"] = measurements.para_gap_pt
        confidence["para_spacing"] = True
    elif measurements.para_gap_samples == 1:
        spec["para_spacing_pt"] = measurements.para_gap_pt
        notes.append("Paragraph spacing was measured from a single example.")
    else:
        notes.append("No clear paragraph spacing was found; a standard value was used.")

    # Marks -> answer-space rule (from the dummy questions)
    rule = _fit_rule(labels.rule_points)
    if rule is not None:
        spec["answer_base_mm"], spec["answer_per_mark_mm"] = rule
        distinct = len({p[0] for p in labels.rule_points})
        if distinct >= 2:
            # Check the fit actually explains the data.
            residuals = [
                abs(rule[0] + rule[1] * marks - height)
                for marks, height in labels.rule_points
            ]
            confidence["answer_rule"] = max(residuals) <= 12.0
            if not confidence["answer_rule"]:
                notes.append(
                    "The blank answer space in the sample didn't grow consistently "
                    "with the marks, so the rule is a best fit — check question "
                    "spacing in the preview."
                )
        else:
            notes.append(
                "Only one marks value had blank space under it, so the "
                "answer-space rule is estimated from that single example."
            )
    else:
        notes.append(
            "No blank answer space linked to marks (like “[5 marks]”) was found "
            "in the sample, so a standard answer-space rule was used."
        )

    return clamp_spec(spec), confidence, notes


# ---------------------------------------------------------------------------
# Plain-language adjustment controls for the review page. Staff never see or
# edit raw spec values; each control nudges bounded fields and re-renders.
# ---------------------------------------------------------------------------

ADJUSTMENTS = {
    "text_size": {
        "label": "Text size",
        "less": "Smaller",
        "more": "Larger",
    },
    "line_spacing": {
        "label": "Line spacing",
        "less": "Tighter",
        "more": "Looser",
    },
    "para_spacing": {
        "label": "Space between questions",
        "less": "Less",
        "more": "More",
    },
    "margins": {
        "label": "Page margins",
        "less": "Narrower",
        "more": "Wider",
    },
    "answer_space": {
        "label": "Answer space",
        "less": "Less",
        "more": "More",
    },
    "font_family": {
        "label": "Font style",
        "less": "Serif",
        "more": "Sans-serif",
    },
}


def apply_adjustment(spec: dict, control: str, direction: str) -> dict:
    """One click of a plain-language control. Returns a new clamped spec."""
    spec = dict(spec)
    sign = 1 if direction == "more" else -1
    if control == "text_size":
        spec["font_size_pt"] = spec.get("font_size_pt", 11.0) + 0.5 * sign
    elif control == "line_spacing":
        spec["line_spacing"] = spec.get("line_spacing", 1.35) + 0.08 * sign
    elif control == "para_spacing":
        spec["para_spacing_pt"] = spec.get("para_spacing_pt", 6.0) + 2.0 * sign
    elif control == "margins":
        for name in ("margin_left_mm", "margin_right_mm", "margin_top_mm", "margin_bottom_mm"):
            spec[name] = spec.get(name, 20.0) + 3.0 * sign
    elif control == "answer_space":
        factor = 1.15 if sign > 0 else 0.87
        spec["answer_base_mm"] = spec.get("answer_base_mm", 15.0) * factor
        spec["answer_per_mark_mm"] = spec.get("answer_per_mark_mm", 18.0) * factor
    elif control == "font_family":
        spec["font_family"] = "sans" if direction == "more" else "serif"
    return clamp_spec(spec)
