"""Build minimal .docx files in-memory for injection tests."""
import io
import zipfile

W_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def para(text, numpr=False, bold=False):
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    ppr = ""
    if numpr:
        ppr = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="2"/></w:numPr></w:pPr>'
    if not text:
        return f"<w:p>{ppr}</w:p>"
    return (
        f"<w:p>{ppr}<w:r>{rpr}<w:t xml:space=\"preserve\">{text}</w:t></w:r></w:p>"
    )


def field_table(rows):
    """rows: [(label, value)] -> a simple 2-column table."""
    trs = ""
    for label, value in rows:
        trs += (
            "<w:tr>"
            f"<w:tc><w:p><w:r><w:t>{label}</w:t></w:r></w:p></w:tc>"
            f"<w:tc><w:p><w:r><w:t>{value}</w:t></w:r></w:p></w:tc>"
            "</w:tr>"
        )
    return f"<w:tbl>{trs}</w:tbl>"


def build_docx(body_xml, extra_entries=None):
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_URI}"><w:body>{body_xml}</w:body></w:document>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/styles.xml", "<styles/>")
        for name, data in (extra_entries or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def standard_exam_docx():
    """A typical template: header, field table, 2 MCQs with A) options, essay."""
    body = (
        para("Kinford University", bold=True)
        + para("EXAM", bold=True)
        + field_table([("Class:", "Introduction to Programming"), ("Date:", "January 15, 2050")])
        + para("")
        + para("Core Concepts", numpr=True, bold=True)
        + para("")
        + para("What is the purpose of a loop?", numpr=True)
        + para("A) To repeat code")
        + para("B) To define functions")
        + para("")
        + para("Which structure implements a queue?", numpr=True)
        + para("A) Linked list")
        + para("B) Tree")
        + para("")
        + para("Explain recursion with an example.", numpr=True)
        + para("")
        + para("")
        + para("End of dummy content note", bold=False)
    )
    return build_docx(body)
