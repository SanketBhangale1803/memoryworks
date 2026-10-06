"""Universal document text extraction.

Every artifact a workspace injects — office documents, PDFs, spreadsheets,
presentations, web pages, mail exports, code and plain text — is converted to
plain text with light structural markers (page, slide, sheet, and heading
markers) before chunking, so downstream retrieval and provenance behave
identically regardless of source format.

Extraction is deliberately stdlib-first: OOXML and OpenDocument are zip+XML,
mail is the stdlib email parser, HTML uses a strict stdlib parser, and only
PDF needs a third-party reader (pypdf). Hard caps on entry sizes, row counts,
and extracted text length make the ingest path safe against decompression
bombs and pathological files.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any
from xml.etree import ElementTree

# Hard limits. Extraction stays far below the point where a single upload could
# exhaust worker memory or dominate the chunk/embedding pipeline.
MAX_SOURCE_BYTES = 100 * 1024 * 1024
MAX_ZIP_ENTRY_BYTES = 64 * 1024 * 1024
MAX_EXTRACTED_CHARS = 5_000_000
MAX_SHEET_ROWS = 50_000
MAX_SHEET_COLS = 256

TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".text",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".csv",
    ".tsv",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
    ".env",
    ".properties",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".kt",
    ".swift",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".php",
    ".tf",
    ".hcl",
    ".dockerfile",
    ".makefile",
    ".gradle",
    ".gitignore",
}

DOCUMENT_SUFFIXES = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
    ".odt",
    ".rtf",
    ".html",
    ".htm",
    ".xhtml",
    ".eml",
}

SUPPORTED_UPLOAD_SUFFIXES = TEXT_SUFFIXES | DOCUMENT_SUFFIXES

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_OFFICE_DOC = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
_TEXT_DOC = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_DC = "{http://purl.org/dc/elements/1.1/}"


class UnsupportedDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    format: str
    title: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def warnings(self) -> list[str]:
        return list(self.metadata.get("warnings", []))


def _clip(text: str) -> str:
    return text[:MAX_EXTRACTED_CHARS]


def _looks_binary(data: bytes) -> bool:
    """True for images, audio, video, archives, and other non-text bytes.

    Without this, a JPEG fell through to plain text: almost any even-length
    byte string decodes as UTF-16, so a photo became megabytes of noise that
    was chunked, embedded, and scanned for secrets — minutes of CPU per file.
    UTF-16 text is recognised by its byte-order mark; NUL bytes or a high share
    of control bytes mean the data is not text.
    """
    sample = data[:8192]
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False
    if b"\x00" in sample:
        return True
    control = sum(1 for byte in sample if byte < 32 and byte not in (9, 10, 12, 13))
    return control > len(sample) * 0.1


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _zip_read(zip_file: zipfile.ZipFile, name: str) -> bytes:
    with zip_file.open(name) as handle:
        return handle.read(MAX_ZIP_ENTRY_BYTES)


def _core_title(zip_file: zipfile.ZipFile) -> str:
    try:
        root = ElementTree.fromstring(_zip_read(zip_file, "docProps/core.xml"))
    except (KeyError, ElementTree.ParseError):
        return ""
    for child in root:
        if child.tag == f"{_DC}title" and (child.text or "").strip():
            return child.text.strip()[:300]
    return ""


def _office_kind(zip_file: zipfile.ZipFile) -> str:
    try:
        content_types = _zip_read(zip_file, "[Content_Types].xml").decode("utf-8", "replace")
    except (KeyError, ElementTree.ParseError, UnicodeDecodeError):
        content_types = ""
    if "word/" in content_types:
        return "docx"
    if "ppt/" in content_types:
        return "pptx"
    if "xl/" in content_types:
        return "xlsx"
    if "opendocument" in content_types or _zip_namelist_has(zip_file, "content.xml"):
        return "odt"
    return ""


def _zip_namelist_has(zip_file: zipfile.ZipFile, name: str) -> bool:
    try:
        return name in zip_file.namelist()
    except zipfile.BadZipFile:
        return False


# ---------------------------------------------------------------------------
# OOXML: Word, PowerPoint, Excel
# ---------------------------------------------------------------------------


def _docx_text(zip_file: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = ElementTree.fromstring(_zip_read(zip_file, "word/document.xml"))
    body = root.find(f"{_W_NS}body")
    lines: list[str] = []
    warnings: list[str] = []

    def paragraph_text(paragraph: ElementTree.Element) -> str:
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                parts.append("\t")
            elif node.tag in {f"{_W_NS}br", f"{_W_NS}cr"}:
                parts.append("\n")
        return "".join(parts)

    def emit_block(element: ElementTree.Element) -> None:
        if element.tag == f"{_W_NS}p":
            style = element.find(f"{_W_NS}pPr/{_W_NS}pStyle")
            text = paragraph_text(element).strip()
            if not text:
                return
            level = ""
            if style is not None:
                style_id = style.get(f"{_W_NS}val", "")
                if style_id.lower().startswith("heading"):
                    digits = re.sub(r"\D", "", style_id)
                    depth = int(digits) if digits else 1
                    level = "#" * max(1, min(depth, 6)) + " "
            lines.append(f"{level}{text}")
        elif element.tag == f"{_W_NS}tbl":
            for row in element.findall(f"{_W_NS}tr"):
                cells = [
                    " ".join(
                        paragraph_text(cell_paragraph).strip()
                        for cell_paragraph in cell.findall(f"{_W_NS}p")
                    ).strip()
                    for cell in row.findall(f"{_W_NS}tc")
                ]
                if any(cells):
                    lines.append("| " + " | ".join(cells) + " |")
            lines.append("")

    if body is None:
        raise UnsupportedDocumentError("The .docx file has no document body")
    for element in body:
        emit_block(element)
    metadata: dict[str, Any] = {"paragraphs": len(lines)}
    title = _core_title(zip_file)
    if not lines:
        warnings.append("The document contained no extractable text")
    return _clip("\n".join(lines).strip()), {"title": title, "warnings": warnings, **metadata}


def _pptx_text(zip_file: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    def slide_number(name: str) -> int:
        match = re.search(r"slide(\d+)\.xml$", name)
        return int(match.group(1)) if match else 0

    slides = sorted(
        (name for name in zip_file.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)),
        key=slide_number,
    )
    if not slides:
        raise UnsupportedDocumentError("The .pptx file contains no slides")
    lines: list[str] = []
    for name in slides:
        number = slide_number(name)
        root = ElementTree.fromstring(_zip_read(zip_file, name))
        paragraphs = [
            "".join(node.text or "" for node in paragraph.iter(f"{_A_NS}t")).strip()
            for paragraph in root.iter(f"{_A_NS}p")
        ]
        paragraphs = [text for text in paragraphs if text]
        lines.append(f"## Slide {number}")
        if paragraphs:
            lines.append(f"**{paragraphs[0]}**")
            lines.extend(paragraphs[1:])
        notes_name = f"ppt/notesSlides/notesSlide{number}.xml"
        if _zip_namelist_has(zip_file, notes_name):
            notes_root = ElementTree.fromstring(_zip_read(zip_file, notes_name))
            notes = [
                "".join(node.text or "" for node in paragraph.iter(f"{_A_NS}t")).strip()
                for paragraph in notes_root.iter(f"{_A_NS}p")
            ]
            notes = [text for text in notes if text]
            if notes:
                lines.append("Speaker notes: " + " ".join(notes))
        lines.append("")
    title = _core_title(zip_file)
    return _clip("\n".join(lines).strip()), {
        "title": title,
        "slide_count": len(slides),
    }


def _column_index(reference: str) -> int:
    index = 0
    for char in reference:
        if char.isalpha():
            index = index * 26 + (ord(char.upper()) - 64)
        else:
            break
    return index


def _xlsx_text(zip_file: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    shared: list[str] = []
    if _zip_namelist_has(zip_file, "xl/sharedStrings.xml"):
        shared_root = ElementTree.fromstring(_zip_read(zip_file, "xl/sharedStrings.xml"))
        for item in shared_root:
            shared.append("".join(node.text or "" for node in item.iter(f"{_S_NS}t")))
    sheet_names: list[str] = []
    sheet_targets: list[str] = []
    if _zip_namelist_has(zip_file, "xl/workbook.xml") and _zip_namelist_has(
        zip_file, "xl/_rels/workbook.xml.rels"
    ):
        workbook = ElementTree.fromstring(_zip_read(zip_file, "xl/workbook.xml"))
        rels = ElementTree.fromstring(_zip_read(zip_file, "xl/_rels/workbook.xml.rels"))
        rel_targets = {rel.get("Id", ""): rel.get("Target", "") for rel in rels}
        rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        for sheet in workbook.iter(f"{_S_NS}sheet"):
            sheet_names.append(sheet.get("name") or "Sheet")
            target = rel_targets.get(sheet.get(rel_ns, ""))
            if target:
                target = target.lstrip("/")
                sheet_targets.append(target if target.startswith("xl/") else f"xl/{target}")
    if not sheet_targets:
        sheet_targets = sorted(
            name
            for name in zip_file.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        sheet_names = [f"Sheet {index + 1}" for index in range(len(sheet_targets))]
    if not sheet_targets:
        raise UnsupportedDocumentError("The .xlsx file contains no worksheets")
    lines: list[str] = []
    total_rows = 0
    for position, target in enumerate(sheet_targets):
        try:
            sheet_root = ElementTree.fromstring(_zip_read(zip_file, target))
        except (KeyError, ElementTree.ParseError):
            continue
        lines.append(
            f"## Sheet: {sheet_names[position] if position < len(sheet_names) else position + 1}"
        )
        row_count = 0
        for row in sheet_root.iter(f"{_S_NS}row"):
            row_count += 1
            if row_count > MAX_SHEET_ROWS:
                lines.append(f"(truncated after {MAX_SHEET_ROWS} rows)")
                break
            values: dict[int, str] = {}
            for cell in row.findall(f"{_S_NS}c"):
                reference = cell.get("r") or ""
                column = _column_index(reference)
                if column < 1 or column > MAX_SHEET_COLS:
                    continue
                cell_type = cell.get("t", "")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{_S_NS}t")).strip()
                else:
                    value_node = cell.find(f"{_S_NS}v")
                    raw = (value_node.text if value_node is not None else "") or ""
                    if cell_type == "s":
                        try:
                            value = shared[int(raw)]
                        except (ValueError, IndexError):
                            value = raw
                    else:
                        value = raw.strip()
                if value:
                    values[column] = value.replace("\n", " ").strip()
            if values:
                width = max(values)
                lines.append(" | ".join(values.get(column, "") for column in range(1, width + 1)))
        lines.append("")
        total_rows += row_count
    title = _core_title(zip_file)
    return _clip("\n".join(lines).strip()), {
        "title": title,
        "sheet_count": len(sheet_targets),
        "row_count": total_rows,
    }


# ---------------------------------------------------------------------------
# OpenDocument text
# ---------------------------------------------------------------------------


def _odt_text(zip_file: zipfile.ZipFile) -> tuple[str, dict[str, Any]]:
    root = ElementTree.fromstring(_zip_read(zip_file, "content.xml"))
    lines: list[str] = []
    for node in root.iter():
        if node.tag == f"{{{_TEXT_DOC}}}h":
            text = "".join(node.itertext()).strip()
            if text:
                level = min(int(node.get(f"{{{_TEXT_DOC}}}outline-level", "1")) or 1, 6)
                lines.append("#" * level + " " + text)
        elif node.tag == f"{{{_TEXT_DOC}}}p":
            text = "".join(node.itertext()).strip()
            if text:
                lines.append(text)
    if not lines:
        raise UnsupportedDocumentError("The .odt file contained no extractable text")
    title = ""
    try:
        meta_root = ElementTree.fromstring(_zip_read(zip_file, "meta.xml"))
        for child in meta_root.iter(f"{_DC}title"):
            title = (child.text or "").strip()[:300]
    except (KeyError, ElementTree.ParseError):
        pass
    return _clip("\n".join(lines)), {"title": title}


# ---------------------------------------------------------------------------
# RTF
# ---------------------------------------------------------------------------

_RTF_SKIP_DESTINATIONS = {
    "fonttbl",
    "colortbl",
    "stylesheet",
    "info",
    "pict",
    "object",
    "header",
    "footer",
    "*",
}


def _rtf_text(data: bytes) -> str:
    output: list[str] = []
    stack: list[bool] = []
    skip_depth: int | None = None
    index = 0
    text = data.decode("ascii", errors="replace")
    while index < len(text):
        char = text[index]
        if char == "{":
            stack.append(skip_depth is not None)
            index += 1
        elif char == "}":
            if stack:
                stack.pop()
            if skip_depth is not None and not any(stack):
                skip_depth = None
            index += 1
        elif char == "\\":
            match = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", text[index:])
            if match:
                word, parameter = match.group(1), match.group(2)
                if word in _RTF_SKIP_DESTINATIONS and skip_depth is None:
                    skip_depth = len(stack)
                elif skip_depth is None:
                    if word == "par" or word == "line":
                        output.append("\n")
                    elif word == "tab":
                        output.append("\t")
                    elif word in {"emdash", "endash"}:
                        output.append("-")
                    elif word == "bullet":
                        output.append("*")
                    elif word in {"u", "uc"} and parameter:
                        try:
                            code = int(parameter)
                            if code > 0:
                                output.append(chr(code))
                        except ValueError:
                            pass
                index += match.end()
                continue
            hex_match = re.match(r"\\'([0-9a-fA-F]{2})", text[index:])
            if hex_match:
                if skip_depth is None:
                    output.append(chr(int(hex_match.group(1), 16)))
                index += hex_match.end()
                continue
            index += 2
        else:
            if skip_depth is None and char not in "\r\n":
                output.append(char)
            index += 1
    rendered = "".join(output)
    rendered = re.sub(r"[ \t]+\n", "\n", rendered)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return _clip(rendered.strip())


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_HTML_SKIP_TAGS = {"script", "style", "template", "noscript", "svg", "head", "iframe"}
_HTML_IGNORED_TAGS = {"nav", "footer", "aside"}
_HTML_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "table",
    "ul",
    "ol",
    "form",
    "br",
    "hr",
}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._ignore_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _HTML_IGNORED_TAGS:
            self._ignore_depth += 1
        elif tag == "td" or tag == "th":
            self.parts.append(" | ")
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and self._ignore_depth == 0:
            level = int(tag[1])
            self.parts.append("\n" + "#" * level + " ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag in _HTML_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _HTML_IGNORED_TAGS and self._ignore_depth:
            self._ignore_depth -= 1
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = (self.title + data).strip()[:300]
            return
        if self._skip_depth or self._ignore_depth:
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        rendered = "".join(self.parts)
        rendered = re.sub(r"[ \t]+", " ", rendered)
        rendered = re.sub(r" ?\n ?", "\n", rendered)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered)
        return _clip(rendered.strip())


def _html_text(data: bytes) -> tuple[str, str]:
    extractor = _HTMLTextExtractor()
    extractor.feed(_decode_text(data))
    extractor.close()
    return extractor.text(), extractor.title


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


def _eml_text(data: bytes) -> tuple[str, dict[str, Any]]:
    message = BytesParser(policy=policy.default).parsebytes(data)
    headers = []
    for header in ("Subject", "From", "To", "Date", "Cc"):
        value = str(message.get(header, "") or "").strip()
        if value:
            headers.append(f"{header}: {value}")
    lines = list(headers)
    warnings: list[str] = []
    body_written = False
    attachment_names: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            disposition = str(part.get_content_disposition() or "")
            filename = part.get_filename()
            content_type = part.get_content_type()
            if disposition == "attachment" or (
                filename and content_type == "application/octet-stream"
            ):
                if filename:
                    attachment_names.append(filename)
                continue
            if content_type == "text/plain" and not body_written:
                lines.append(str(part.get_content()))
                body_written = True
            elif content_type == "text/html" and not body_written:
                text, _ = _html_text(part.get_payload(decode=True) or b"")
                lines.append(text)
                body_written = True
    else:
        content_type = message.get_content_type()
        payload = message.get_content() if content_type in {"text/plain", "text/html"} else ""
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", "replace")
        lines.append(str(payload))
    text = _clip("\n".join(lines).strip())
    return text, {
        "title": str(message.get("Subject", "") or "")[:300],
        "attachment_count": len(attachment_names),
        "attachments": attachment_names[:20],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------


def _pdf_text(data: bytes) -> tuple[str, dict[str, Any]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - requirements pin pypdf
        raise UnsupportedDocumentError("PDF support is not installed") from exc
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise UnsupportedDocumentError(
                "The PDF is password protected; remove protection before ingesting"
            ) from exc
    pages: list[str] = []
    warnings: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            page_text = (page.extract_text() or "").strip()
        except Exception:
            page_text = ""
            warnings.append(f"Page {number} could not be extracted")
        if page_text:
            pages.append(f"## Page {number}\n{page_text}")
        if sum(len(item) for item in pages) > MAX_EXTRACTED_CHARS:
            warnings.append("PDF text truncated at the extraction limit")
            break
    info_title = ""
    with contextlib.suppress(Exception):
        info_title = str((reader.metadata or {}).get("/Title") or "").strip()[:300]
    if not pages:
        warnings.append("No text layer found; the PDF may be scanned images")
    return _clip("\n\n".join(pages)), {
        "title": info_title,
        "page_count": len(reader.pages),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _sniff_format(filename: str, data: bytes) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    dot_suffix = f".{suffix}" if suffix else ""
    if dot_suffix in {".docx", ".xlsx", ".pptx"}:
        return suffix
    if dot_suffix == ".odt":
        return "odt"
    if dot_suffix == ".pdf":
        return "pdf"
    if dot_suffix in {".html", ".htm", ".xhtml"}:
        return "html"
    if dot_suffix == ".rtf":
        return "rtf"
    if dot_suffix == ".eml":
        return "eml"
    if dot_suffix in TEXT_SUFFIXES:
        return "text"
    # Content sniffing catches wrong or missing extensions.
    if data.startswith(b"%PDF"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
                return _office_kind(zip_file) or "zip"
        except zipfile.BadZipFile:
            return "zip"
    head = data[:4096].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<body" in head:
        return "html"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    return "text"


def extract_document(filename: str, data: bytes) -> ExtractedDocument:
    """Convert any supported source artifact into chunkable plain text."""
    if len(data) > MAX_SOURCE_BYTES:
        raise UnsupportedDocumentError("File exceeds the 100MB ingest limit")
    if not data:
        raise UnsupportedDocumentError("The file is empty")
    fmt = _sniff_format(filename, data)
    title = ""
    metadata: dict[str, Any] = {}

    if fmt in {"docx", "xlsx", "pptx", "odt"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
                if fmt == "docx":
                    text, extra = _docx_text(zip_file)
                elif fmt == "pptx":
                    text, extra = _pptx_text(zip_file)
                elif fmt == "xlsx":
                    text, extra = _xlsx_text(zip_file)
                else:
                    text, extra = _odt_text(zip_file)
        except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise UnsupportedDocumentError(f"The {fmt} file is corrupt: {exc}") from exc
        title = extra.pop("title", "")
        metadata.update(extra)
    elif fmt == "pdf":
        text, extra = _pdf_text(data)
        title = extra.pop("title", "")
        metadata.update(extra)
    elif fmt == "html":
        text, title = _html_text(data)
    elif fmt == "rtf":
        text = _rtf_text(data)
    elif fmt == "eml":
        text, extra = _eml_text(data)
        title = extra.pop("title", "")
        metadata.update(extra)
    elif fmt == "text":
        if _looks_binary(data):
            raise UnsupportedDocumentError(
                "This is not a text document (it looks like an image, audio, video, or "
                "archive), so there is nothing to read"
            )
        text = _clip(_decode_text(data))
    else:
        raise UnsupportedDocumentError(
            f"Unsupported document format {fmt!r}; convert it to PDF, DOCX, XLSX, "
            "PPTX, HTML, or plain text first"
        )

    if not title:
        title = filename.rsplit("/", 1)[-1]
    metadata["format"] = fmt
    metadata["source_bytes"] = len(data)
    return ExtractedDocument(text=text, format=fmt, title=title, metadata=metadata)


def document_metadata_json(document: ExtractedDocument) -> str:
    return json.dumps(document.metadata)
