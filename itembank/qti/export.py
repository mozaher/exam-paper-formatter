"""QTI 3.0 export.

Items are serialized as individual QTI 3.0 assessment-item XML files and
bundled into an IMS content package (zip with an imsmanifest.xml).

Interoperable parts (stem, choices, correct response, max score, marking
rubric) use standard QTI 3.0 markup. Bank-specific tags that QTI has no
standard slot for (topic, difficulty, cognitive level) travel in the
manifest's per-resource <metadata> block under our own XML namespace, so
any QTI-3 consumer can ignore them while our importer round-trips them.
"""
import io
import xml.etree.ElementTree as ET
import zipfile

QTI_NS = "http://www.imsglobal.org/xsd/imsqtiasi_v3p0"
CP_NS = "http://www.imsglobal.org/xsd/imscp_v1p1"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
EPF_NS = "https://exam-platform.example/xmlns/item-metadata/v1"
XML_NS = "http://www.w3.org/XML/1998/namespace"

QTI_SCHEMA_LOCATION = (
    f"{QTI_NS} https://purl.imsglobal.org/spec/qti/v3p0/schema/xsd/imsqti_asiv3p0.xsd"
)
MATCH_CORRECT_TEMPLATE = (
    "https://purl.imsglobal.org/spec/qti/v3p0/rptemplates/match_correct.xml"
)
QTI_ITEM_RESOURCE_TYPE = "imsqti_item_xmlv3p0"


def _q(tag):
    return f"{{{QTI_NS}}}{tag}"


def _paragraphs(parent, text):
    """Append the stem text as <p> elements (blank line = new paragraph)."""
    parts = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    if not parts:
        parts = [""]
    for part in parts:
        p = ET.SubElement(parent, _q("p"))
        p.text = part


def choice_identifier(index):
    return f"CHOICE_{index + 1}"


def item_to_qti_xml(item) -> bytes:
    """Serialize one Item to a QTI 3.0 assessment-item XML document."""
    root = ET.Element(
        _q("qti-assessment-item"),
        {
            "identifier": item.qti_identifier,
            "title": item.title,
            "adaptive": "false",
            "time-dependent": "false",
            f"{{{XSI_NS}}}schemaLocation": QTI_SCHEMA_LOCATION,
            f"{{{XML_NS}}}lang": "en",
        },
    )

    choices = list(item.choices.all()) if item.item_type == "mcq" else []

    if item.item_type == "mcq":
        rd = ET.SubElement(
            root,
            _q("qti-response-declaration"),
            {"identifier": "RESPONSE", "cardinality": "single", "base-type": "identifier"},
        )
        correct = ET.SubElement(rd, _q("qti-correct-response"))
        for i, choice in enumerate(choices):
            if choice.is_correct:
                value = ET.SubElement(correct, _q("qti-value"))
                value.text = choice_identifier(i)
    else:
        ET.SubElement(
            root,
            _q("qti-response-declaration"),
            {"identifier": "RESPONSE", "cardinality": "single", "base-type": "string"},
        )

    outcome_attrs = {
        "identifier": "SCORE",
        "cardinality": "single",
        "base-type": "float",
        "normal-maximum": f"{item.marks:g}" if hasattr(item.marks, "__float__") else str(item.marks),
    }
    if item.item_type == "essay":
        outcome_attrs["external-scored"] = "human"
    ET.SubElement(root, _q("qti-outcome-declaration"), outcome_attrs)

    body = ET.SubElement(root, _q("qti-item-body"))

    if item.item_type == "mcq":
        interaction = ET.SubElement(
            body,
            _q("qti-choice-interaction"),
            {"response-identifier": "RESPONSE", "shuffle": "true", "max-choices": "1"},
        )
        prompt = ET.SubElement(interaction, _q("qti-prompt"))
        _paragraphs(prompt, item.body)
        for i, choice in enumerate(choices):
            sc = ET.SubElement(
                interaction, _q("qti-simple-choice"), {"identifier": choice_identifier(i)}
            )
            sc.text = choice.text
    else:
        if item.model_answer:
            rubric = ET.SubElement(body, _q("qti-rubric-block"), {"view": "scorer"})
            _paragraphs(rubric, item.model_answer)
        _paragraphs(body, item.body)
        ET.SubElement(
            body,
            _q("qti-extended-text-interaction"),
            {"response-identifier": "RESPONSE"},
        )

    if item.item_type == "mcq":
        ET.SubElement(root, _q("qti-response-processing"), {"template": MATCH_CORRECT_TEMPLATE})

    ET.register_namespace("", QTI_NS)
    ET.register_namespace("xsi", XSI_NS)
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue()


def _c(tag):
    return f"{{{CP_NS}}}{tag}"


def _e(tag):
    return f"{{{EPF_NS}}}{tag}"


def build_manifest(items_with_hrefs) -> bytes:
    manifest = ET.Element(
        _c("manifest"), {"identifier": "exam-platform-itembank-export"}
    )
    metadata = ET.SubElement(manifest, _c("metadata"))
    schema = ET.SubElement(metadata, _c("schema"))
    schema.text = "QTI Package"
    schemaversion = ET.SubElement(metadata, _c("schemaversion"))
    schemaversion.text = "3.0.0"
    ET.SubElement(manifest, _c("organizations"))
    resources = ET.SubElement(manifest, _c("resources"))

    for item, href in items_with_hrefs:
        resource = ET.SubElement(
            resources,
            _c("resource"),
            {
                "identifier": item.qti_identifier,
                "type": QTI_ITEM_RESOURCE_TYPE,
                "href": href,
            },
        )
        res_meta = ET.SubElement(resource, _c("metadata"))
        epf = ET.SubElement(res_meta, _e("itemMetadata"))
        if item.topic is not None:
            topic = ET.SubElement(epf, _e("topic"))
            topic.text = str(item.topic)
        difficulty = ET.SubElement(epf, _e("difficulty"))
        difficulty.text = item.difficulty
        cognitive = ET.SubElement(epf, _e("cognitiveLevel"))
        cognitive.text = item.cognitive_level
        marks = ET.SubElement(epf, _e("marks"))
        marks.text = str(item.marks)
        item_type = ET.SubElement(epf, _e("itemType"))
        item_type.text = item.item_type
        ET.SubElement(resource, _c("file"), {"href": href})

    ET.register_namespace("", CP_NS)
    ET.register_namespace("epf", EPF_NS)
    buf = io.BytesIO()
    ET.ElementTree(manifest).write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue()


def build_package(items) -> bytes:
    """Bundle items into a QTI 3.0 content-package zip."""
    entries = []
    for item in items:
        href = f"items/{item.qti_identifier}.xml"
        entries.append((item, href))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("imsmanifest.xml", build_manifest(entries))
        for item, href in entries:
            zf.writestr(href, item_to_qti_xml(item))
    return buf.getvalue()
