from pathlib import Path
from zipfile import ZipFile

from app.services.document_parser import DocxParser, DocumentParseError, RoutedDocumentParser


def _write_minimal_docx(path: Path, paragraphs: list[str]) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    document_body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>"
        for paragraph in paragraphs
    )
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {document_body}
  </w:body>
</w:document>"""

    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def test_docx_parser_extracts_text(tmp_path) -> None:
    file_path = tmp_path / "paper.docx"
    _write_minimal_docx(file_path, ["课程名称：数据结构", "1. 请说明栈和队列的区别。"])

    text, paragraph_count = DocxParser().extract(file_path)

    assert "课程名称：数据结构" in text
    assert "1. 请说明栈和队列的区别。" in text
    assert paragraph_count >= 1


def test_routed_document_parser_rejects_doc(tmp_path) -> None:
    file_path = tmp_path / "paper.doc"
    file_path.write_bytes(b"fake")

    try:
        RoutedDocumentParser().extract(file_path)
    except DocumentParseError as exc:
        assert ".doc" in str(exc)
    else:
        raise AssertionError("expected .doc to be rejected")
