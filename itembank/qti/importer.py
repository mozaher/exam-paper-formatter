"""QTI 3.0 import.

Accepts either a content-package zip (imsmanifest.xml + item XML files) or
a single assessment-item XML file. Parsing is namespace-tolerant — elements
are matched by local name — so files from other QTI 3 producers import even
when they use different prefixes or minor namespace variants. Uploaded XML
is parsed with defusedxml (no entity expansion, no external DTD fetches).

Supported interactions: qti-choice-interaction → MCQ,
qti-extended-text-interaction → essay. Anything else is reported as
skipped, never guessed at.
"""
import re
import zipfile
from dataclasses import dataclass, field

from defusedxml import ElementTree as SafeET

from ..models import Choice, Item, Topic

UUID_RE = re.compile(r"itm-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

DIFFICULTIES = {c[0] for c in Item.Difficulty.choices}
COGNITIVE_LEVELS = {c[0] for c in Item.CognitiveLevel.choices}


class QtiImportError(Exception):
    pass


@dataclass
class ParsedItem:
    identifier: str
    title: str
    item_type: str
    body: str
    marks: str | None = None
    model_answer: str = ""
    choices: list = field(default_factory=list)  # [(text, is_correct)]
    # From package metadata:
    topic: str = ""
    difficulty: str = ""
    cognitive_level: str = ""


@dataclass
class ImportResult:
    created: list = field(default_factory=list)   # Item instances
    skipped: list = field(default_factory=list)   # (identifier/name, reason)
    errors: list = field(default_factory=list)    # (filename, message)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(el, name):
    for child in el.iter():
        if isinstance(child.tag, str) and _local(child.tag) == name:
            return child
    return None


def _find_any(el, *names):
    """First descendant matching any local name.

    Note: never use ``_find(...) or _find(...)`` — an XML element with no
    children is falsy in ElementTree, so a valid but empty element (e.g. an
    <extended-text-interaction/>) would be treated as absent. Always test
    ``is not None`` explicitly, which this helper does.
    """
    for name in names:
        found = _find(el, name)
        if found is not None:
            return found
    return None


def _findall(el, name):
    return [child for child in el.iter() if isinstance(child.tag, str) and _local(child.tag) == name]


def _text_content(el):
    """All human-readable text inside an element, paragraphs joined by blank lines."""
    if el is None:
        return ""
    paragraphs = _findall(el, "p")
    if paragraphs:
        return "\n\n".join("".join(p.itertext()).strip() for p in paragraphs).strip()
    return "".join(el.itertext()).strip()


def parse_item_xml(data: bytes) -> ParsedItem:
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:
        raise QtiImportError(f"Not valid XML: {exc}") from exc

    if _local(root.tag) not in ("qti-assessment-item", "assessmentItem"):
        raise QtiImportError(
            f"Expected a QTI assessment item, found <{_local(root.tag)}>."
        )

    identifier = root.get("identifier", "")
    title = root.get("title", "") or identifier or "Imported item"

    body_el = _find_any(root, "qti-item-body", "itemBody")
    if body_el is None:
        raise QtiImportError("Item has no item body.")

    choice_interaction = _find_any(body_el, "qti-choice-interaction", "choiceInteraction")
    text_interaction = _find_any(
        body_el, "qti-extended-text-interaction", "extendedTextInteraction"
    )

    marks = None
    for od in _findall(root, "qti-outcome-declaration") + _findall(root, "outcomeDeclaration"):
        if od.get("identifier") == "SCORE" and od.get("normal-maximum"):
            marks = od.get("normal-maximum")
        elif od.get("identifier") == "SCORE" and od.get("normalMaximum"):
            marks = od.get("normalMaximum")

    if choice_interaction is not None:
        correct_ids = set()
        for rd in _findall(root, "qti-response-declaration") + _findall(
            root, "responseDeclaration"
        ):
            correct_el = _find_any(rd, "qti-correct-response", "correctResponse")
            if correct_el is not None:
                for v in _findall(correct_el, "qti-value") + _findall(correct_el, "value"):
                    if v.text:
                        correct_ids.add(v.text.strip())

        prompt = _find_any(choice_interaction, "qti-prompt", "prompt")
        body_text = _text_content(prompt) if prompt is not None else _text_content(body_el)

        choices = []
        for sc in _findall(choice_interaction, "qti-simple-choice") + _findall(
            choice_interaction, "simpleChoice"
        ):
            text = "".join(sc.itertext()).strip()
            choices.append((text, sc.get("identifier", "") in correct_ids))
        if not choices:
            raise QtiImportError("Choice interaction has no choices.")

        return ParsedItem(
            identifier=identifier,
            title=title,
            item_type="mcq",
            body=body_text,
            marks=marks,
            choices=choices,
        )

    if text_interaction is not None:
        rubric = _find_any(body_el, "qti-rubric-block", "rubricBlock")
        model_answer = _text_content(rubric) if rubric is not None else ""
        # Body text = paragraphs outside the rubric block.
        paragraphs = []
        for child in list(body_el):
            if _local(child.tag) in ("qti-rubric-block", "rubricBlock"):
                continue
            if _local(child.tag) in (
                "qti-extended-text-interaction",
                "extendedTextInteraction",
            ):
                continue
            text = "".join(child.itertext()).strip()
            if text:
                paragraphs.append(text)
        body_text = "\n\n".join(paragraphs).strip() or _text_content(body_el)

        return ParsedItem(
            identifier=identifier,
            title=title,
            item_type="essay",
            body=body_text,
            marks=marks,
            model_answer=model_answer,
        )

    raise QtiImportError(
        "Unsupported interaction type (only choice and extended-text are supported)."
    )


def parse_manifest_metadata(data: bytes) -> dict:
    """Map resource identifier -> {href, topic, difficulty, cognitive_level}."""
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:
        raise QtiImportError(f"imsmanifest.xml is not valid XML: {exc}") from exc

    out = {}
    for resource in _findall(root, "resource"):
        identifier = resource.get("identifier", "")
        entry = {"href": resource.get("href", "")}
        meta = _find(resource, "itemMetadata")
        if meta is not None:
            for name, key in (
                ("topic", "topic"),
                ("difficulty", "difficulty"),
                ("cognitiveLevel", "cognitive_level"),
            ):
                el = _find(meta, name)
                if el is not None and el.text:
                    entry[key] = el.text.strip()
        out[identifier] = entry
    return out


def _is_duplicate(org, identifier: str) -> bool:
    if not identifier:
        return False
    if Item.objects.for_org(org).filter(external_id=identifier).exists():
        return True
    m = UUID_RE.fullmatch(identifier)
    if m and Item.objects.for_org(org).filter(uuid=m.group(1)).exists():
        return True
    return False


def _get_or_create_topic(org, label: str):
    """Resolve a 'Parent › Child' or plain topic label within the org."""
    if not label:
        return None
    parts = [p.strip() for p in label.split("›")]
    parent = None
    topic = None
    for part in parts:
        if not part:
            continue
        topic, _ = Topic.objects.get_or_create(org=org, parent=parent, name=part[:200])
        parent = topic
    return topic


def _create_item(org, user, parsed: ParsedItem) -> Item:
    difficulty = parsed.difficulty if parsed.difficulty in DIFFICULTIES else Item.Difficulty.MEDIUM
    cognitive = (
        parsed.cognitive_level
        if parsed.cognitive_level in COGNITIVE_LEVELS
        else Item.CognitiveLevel.UNDERSTAND
    )
    try:
        marks = float(parsed.marks) if parsed.marks else 1
    except ValueError:
        marks = 1

    item = Item.objects.create(
        org=org,
        item_type=parsed.item_type,
        status=Item.Status.DRAFT,
        title=parsed.title[:255],
        body=parsed.body,
        model_answer=parsed.model_answer,
        topic=_get_or_create_topic(org, parsed.topic),
        difficulty=difficulty,
        cognitive_level=cognitive,
        marks=marks,
        external_id=parsed.identifier[:128],
        created_by=user,
    )
    for i, (text, is_correct) in enumerate(parsed.choices):
        Choice.objects.create(item=item, text=text[:1000], is_correct=is_correct, order=i)
    return item


def import_upload(org, user, filename: str, data: bytes) -> ImportResult:
    """Import a QTI package zip or a single item XML into an organization."""
    result = ImportResult()

    if zipfile.is_zipfile(_Buffer(data)):
        with zipfile.ZipFile(_Buffer(data)) as zf:
            names = zf.namelist()
            manifest_meta = {}
            manifest_name = next(
                (n for n in names if n.split("/")[-1] == "imsmanifest.xml"), None
            )
            if manifest_name:
                try:
                    manifest_meta = parse_manifest_metadata(zf.read(manifest_name))
                except QtiImportError as exc:
                    result.errors.append((manifest_name, str(exc)))

            xml_names = [
                n
                for n in names
                if n.lower().endswith(".xml") and n.split("/")[-1] != "imsmanifest.xml"
            ]
            for name in xml_names:
                try:
                    parsed = parse_item_xml(zf.read(name))
                except QtiImportError as exc:
                    result.errors.append((name, str(exc)))
                    continue
                meta = manifest_meta.get(parsed.identifier, {})
                parsed.topic = meta.get("topic", "")
                parsed.difficulty = meta.get("difficulty", "")
                parsed.cognitive_level = meta.get("cognitive_level", "")
                _import_one(org, user, parsed, result)
        return result

    try:
        parsed = parse_item_xml(data)
    except QtiImportError as exc:
        result.errors.append((filename, str(exc)))
        return result
    _import_one(org, user, parsed, result)
    return result


def _import_one(org, user, parsed: ParsedItem, result: ImportResult):
    if _is_duplicate(org, parsed.identifier):
        result.skipped.append((parsed.identifier or parsed.title, "already in this bank"))
        return
    result.created.append(_create_item(org, user, parsed))


class _Buffer:
    """Minimal seekable wrapper so zipfile can probe raw bytes."""

    def __init__(self, data: bytes):
        import io

        self._bio = io.BytesIO(data)

    def __getattr__(self, name):
        return getattr(self._bio, name)
