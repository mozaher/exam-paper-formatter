"""The formatting spec ("theme schema") for the BUILT-IN renderer.

Templates created without an uploaded source file use these bounded layout
values with the deterministic ReportLab renderer (also used for all marking
schemes). Source-file templates ignore this: their own document carries the
formatting.
"""

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
    "question_indent_mm": (0.0, 0.0, 30.0),
}
OPTION_LABEL_STYLES = ("A.", "A)", "(a)", "a)")
CHOICE_FIELDS = {
    "font_family": ("serif", ("serif", "sans")),
    "paper_size": ("A4", ("A4", "LETTER")),
    "option_label_style": ("A.", OPTION_LABEL_STYLES),
}
BOOL_FIELDS = {"show_header_rule": True}
TEXT_FIELDS = {"cover_heading": 120, "address_text": 300}
LIST_FIELDS = {"candidate_fields": (8, 40)}  # (max items, max chars each)


def default_spec() -> dict:
    spec = {name: default for name, (default, _, _) in NUMERIC_FIELDS.items()}
    spec.update({name: default for name, (default, _) in CHOICE_FIELDS.items()})
    spec.update(dict(BOOL_FIELDS))
    spec.update({name: "" for name in TEXT_FIELDS})
    spec.update({name: [] for name in LIST_FIELDS})
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
    for name in BOOL_FIELDS:
        if name in spec:
            out[name] = bool(spec[name])
    for name, max_len in TEXT_FIELDS.items():
        value = spec.get(name)
        if isinstance(value, str):
            out[name] = value.strip()[:max_len]
    for name, (max_items, max_chars) in LIST_FIELDS.items():
        value = spec.get(name)
        if isinstance(value, (list, tuple)):
            out[name] = [
                str(v).strip()[:max_chars] for v in value if str(v).strip()
            ][:max_items]
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
            "question_indent_mm": template.question_indent_mm,
            "option_label_style": template.option_label_style,
            "show_header_rule": template.show_header_rule,
            "cover_heading": template.cover_heading,
            "address_text": template.address_text,
            "candidate_fields": template.candidate_fields,
        }
    )
