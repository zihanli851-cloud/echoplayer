from pathlib import Path
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import app


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


def test_review_route_accepts_docx_upload(monkeypatch, tmp_path) -> None:
    document = tmp_path / "paper.docx"
    _write_minimal_docx(document, ["一、简答题", "1. 请说明栈和队列的区别。"])

    monkeypatch.setenv("ENABLE_ASYNC_AGENT", "false")

    with TestClient(app) as client:
        response = client.post(
            "/review",
            data={"teacher_name": "李老师", "teacher_id": "T001", "subject": "chinese"},
            files={"paper_a": ("paper.docx", document.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

    assert response.status_code != 400
    assert "A 卷必须为 PDF 或 DOCX 文件。" not in response.text
