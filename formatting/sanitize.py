"""Unconditional, category-based sanitization of uploaded .docx files.

Strips macros, DDE fields, OLE/ActiveX objects and external references by
CATEGORY — no scanning of content to decide, no allowlisting of "clean"
macros. Applied once at ingest; only the sanitized bytes are ever stored,
converted, or offered for download.

.docx never executes on the server (LibreOffice only converts it, inside
the compile sandbox), so this stripping primarily protects the humans who
later open generated papers in Word.
"""
import io
import re
import zipfile

from defusedxml import ElementTree as SafeET

import xml.etree.ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

# Zip entries removed wholesale (macros, embedded programs/objects).
STRIP_PATH_RES = [
    re.compile(r"(^|/)vbaProject\.bin$", re.IGNORECASE),
    re.compile(r"(^|/)vbaData\.xml$", re.IGNORECASE),
    re.compile(r"(^|/)embeddings/", re.IGNORECASE),
    re.compile(r"(^|/)activeX/", re.IGNORECASE),
    re.compile(r"\.bin$", re.IGNORECASE),
]

# Field instructions that can trigger external fetches / command execution.
DANGEROUS_FIELD_RE = re.compile(
    r"\b(DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|IMPORT|LINK|RD)\b", re.IGNORECASE
)


def _strip_path(name: str) -> bool:
    return any(rx.search(name) for rx in STRIP_PATH_RES)


def _register_namespaces(xml_bytes: bytes):
    for prefix, uri in re.findall(rb'xmlns:([\w-]+)="([^"]+)"', xml_bytes):
        ET.register_namespace(prefix.decode(), uri.decode())
    # .rels and [Content_Types].xml use a DEFAULT namespace; without
    # registering it, ElementTree would re-emit every element as ns0:*,
    # which OPC readers refuse to load.
    default = re.search(rb'<[^>]*\sxmlns="([^"]+)"', xml_bytes)
    if default:
        ET.register_namespace("", default.group(1).decode())


def fix_mc_ignorable(xml_bytes: bytes) -> bytes:
    """Keep mc:Ignorable consistent after reserialization.

    ElementTree only declares namespaces that are actually used, but
    mc:Ignorable may still list prefixes (e.g. w15) whose declarations were
    dropped with the last element using them. Strict OOXML readers
    (LibreOffice, Word) reject an Ignorable entry with no matching xmlns —
    so filter the list down to declared prefixes.
    """
    declared = {p.decode() for p in re.findall(rb"xmlns:([\w-]+)=", xml_bytes)}

    def _filter(match):
        kept = [p for p in match.group(1).decode().split() if p in declared]
        return b'mc:Ignorable="' + " ".join(kept).encode() + b'"'

    return re.sub(rb'mc:Ignorable="([^"]*)"', _filter, xml_bytes)


def _clean_document_xml(data: bytes) -> bytes:
    """Remove OLE objects, ActiveX controls and dangerous field codes."""
    _register_namespaces(data)
    root = SafeET.fromstring(data)
    w = f"{{{W_NS}}}"

    def clean(parent):
        for child in list(parent):
            tag = child.tag
            if tag in (f"{w}object", f"{w}control"):
                parent.remove(child)
                continue
            if tag == f"{w}fldSimple":
                instr = child.get(f"{w}instr", "")
                if DANGEROUS_FIELD_RE.search(instr):
                    parent.remove(child)
                    continue
            if tag == f"{w}instrText":
                if DANGEROUS_FIELD_RE.search(child.text or ""):
                    child.text = ""
            clean(child)

    clean(root)
    return fix_mc_ignorable(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _clean_rels(data: bytes) -> bytes:
    """Drop relationships to stripped parts and non-hyperlink external targets."""
    _register_namespaces(data)
    root = SafeET.fromstring(data)
    rel_tag = f"{{{REL_NS}}}Relationship"
    for rel in list(root):
        if rel.tag != rel_tag:
            continue
        target = rel.get("Target", "")
        rtype = rel.get("Type", "")
        external = rel.get("TargetMode", "") == "External"
        if _strip_path(target):
            root.remove(rel)
        elif external and not rtype.endswith("/hyperlink"):
            root.remove(rel)
    return fix_mc_ignorable(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _clean_settings(data: bytes) -> bytes:
    """Remove attached-template references (auto template fetch on open)."""
    _register_namespaces(data)
    root = SafeET.fromstring(data)
    w = f"{{{W_NS}}}"
    for child in list(root):
        if child.tag == f"{w}attachedTemplate":
            root.remove(child)
    return fix_mc_ignorable(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))


def _clean_content_types(data: bytes) -> bytes:
    _register_namespaces(data)
    root = SafeET.fromstring(data)
    for child in list(root):
        part = child.get("PartName", "")
        ctype = child.get("ContentType", "")
        if _strip_path(part) or "vbaProject" in ctype or "ms-office.activeX" in ctype:
            root.remove(child)
    return fix_mc_ignorable(ET.tostring(root, xml_declaration=True, encoding="UTF-8"))


def sanitize_docx(source: bytes) -> bytes:
    """Return a rebuilt .docx with macro/DDE/OLE categories removed."""
    try:
        zin = zipfile.ZipFile(io.BytesIO(source))
    except zipfile.BadZipFile as exc:
        raise ValueError("Not a valid .docx (zip) file.") from exc

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            name = info.filename
            if _strip_path(name):
                continue
            data = zin.read(name)
            if name == "word/document.xml":
                data = _clean_document_xml(data)
            elif name.endswith(".rels"):
                data = _clean_rels(data)
            elif name == "word/settings.xml":
                data = _clean_settings(data)
            elif name == "[Content_Types].xml":
                data = _clean_content_types(data)
            zout.writestr(name, data)
    return out.getvalue()
