"""Workspace-scoped document memory, chunk indexing and deterministic retrieval."""

import hashlib
import math
import os
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import DocumentChunk, DocumentFile

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_TEXT_CHARS = 1_000_000
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 100
VECTOR_DIMENSION = 128
MAX_SEARCH_LIMIT = 50

SUPPORTED_EXTENSIONS = (
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".html",
    ".css",
    ".yaml",
    ".yml",
    ".sql",
    ".sh",
    ".pdf",
)

MIME_TYPE_MAP = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".py": "text/x-python",
    ".js": "application/javascript",
    ".ts": "application/typescript",
    ".jsx": "text/jsx",
    ".tsx": "text/tsx",
    ".html": "text/html",
    ".css": "text/css",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".sql": "application/sql",
    ".sh": "application/x-sh",
    ".pdf": "application/pdf",
}


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class KnowledgeConfigResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    available: bool = True
    max_file_bytes: int = MAX_UPLOAD_BYTES
    max_text_chars: int = MAX_TEXT_CHARS
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    vector_dimension: int = VECTOR_DIMENSION
    supported_extensions: list[str] = list(SUPPORTED_EXTENSIONS)
    content_trust: Literal["untrusted"] = "untrusted"


class KnowledgeSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=MAX_SEARCH_LIMIT)
    file_id: str | None = Field(default=None, max_length=36)


class ChunkSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chunk_id: str
    file_id: str
    filename: str
    chunk_index: int
    source_offset: int
    score: float
    content: str
    metadata: dict


class KnowledgeSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    workspace_id: str
    query: str
    total_matches: int
    results: list[ChunkSearchResult]


def knowledge_settings() -> KnowledgeConfigResponse:
    return KnowledgeConfigResponse()


def validate_upload_filename(filename: str) -> str:
    cleaned = filename.strip()
    if not cleaned or len(cleaned) > 255:
        raise KnowledgeError("invalid_filename", "Filename must be between 1 and 255 characters")
    if "/" in cleaned or "\\" in cleaned or ".." in cleaned:
        raise KnowledgeError("invalid_filename", "Path traversal in filename is forbidden")
    base, ext = os.path.splitext(cleaned)
    ext_lower = ext.lower()
    if ext_lower not in SUPPORTED_EXTENSIONS:
        raise KnowledgeError(
            "unsupported_file_type",
            f"Unsupported file format '{ext}'. Supported formats: {', '.join(SUPPORTED_EXTENSIONS)}",
        )
    if not re.match(r"^[A-Za-z0-9_.\- ]+$", cleaned):
        raise KnowledgeError(
            "invalid_filename",
            "Filename may only contain alphanumeric characters, spaces, dots, dashes, and underscores",
        )
    return cleaned


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise KnowledgeError("invalid_pdf", "File header does not match PDF format")
    # Pure-Python bounded stream text extractor for PDF documents
    text_parts = []
    # Extract text from PDF literal strings inside text objects (BT ... ET)
    # Pattern matches (string) Tj or ' or "
    stream_pattern = re.compile(rb"BT\s*(.*?)\s*ET", re.DOTALL)
    text_token_pattern = re.compile(rb"\((.*?)\)\s*T[jJ]")
    found = False
    for bt in stream_pattern.finditer(content):
        for tj in text_token_pattern.finditer(bt.group(1)):
            raw_str = tj.group(1)
            try:
                decoded = raw_str.decode("latin1", errors="ignore")
                # unescape standard PDF escapes
                decoded = decoded.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
                if decoded.strip():
                    text_parts.append(decoded)
                    found = True
            except Exception:
                continue
    if not found:
        # Fallback: scan printable ASCII runs of length >= 4
        printable_runs = re.findall(rb"[A-Za-z0-9 ,.!?;:'\"()\-\n\r]{4,}", content)
        for run in printable_runs:
            s = run.decode("ascii", errors="ignore").strip()
            # Filter PDF syntax keywords
            if not s.startswith(("/Type", "/Font", "/Filter", "endobj", "xref")):
                text_parts.append(s)
    extracted = "\n".join(text_parts).strip()
    if len(extracted) < 5:
        raise KnowledgeError("empty_file", "No readable text content could be extracted from PDF")
    return extracted


def extract_text_and_validate(filename: str, content: bytes) -> tuple[str, str, int, str]:
    size = len(content)
    if size == 0:
        raise KnowledgeError("empty_file", "Uploaded file is empty (0 bytes)")
    if size > MAX_UPLOAD_BYTES:
        raise KnowledgeError("file_too_large", f"File size ({size} bytes) exceeds 5 MiB limit")

    sha256 = hashlib.sha256(content).hexdigest()
    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()
    mime_type = MIME_TYPE_MAP.get(ext_lower, "application/octet-stream")

    if ext_lower == ".pdf":
        text = _extract_pdf_text(content)
    else:
        if b"\x00" in content:
            raise KnowledgeError("text_decode_failed", "Text file contains null bytes")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise KnowledgeError(
                    "text_decode_failed", "File content must be valid UTF-8 text"
                ) from None

    cleaned_text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if not cleaned_text.strip():
        raise KnowledgeError("empty_file", "File contains no readable text")
    if len(cleaned_text) > MAX_TEXT_CHARS:
        raise KnowledgeError(
            "file_too_large",
            f"Extracted text ({len(cleaned_text)} characters) exceeds 1,000,000 limit",
        )

    return cleaned_text, mime_type, size, sha256


def chunk_document(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[dict]:
    if chunk_size <= 0:
        raise KnowledgeError("chunking_error", "chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise KnowledgeError("chunking_error", "overlap must be >= 0 and < chunk_size")

    chunks = []
    text_length = len(text)
    if text_length <= chunk_size:
        return [
            {
                "chunk_index": 0,
                "source_offset": 0,
                "content": text,
                "metadata_json": {
                    "char_count": text_length,
                    "word_count": len(text.split()),
                },
            }
        ]

    step = chunk_size - overlap
    offset = 0
    chunk_index = 0

    while offset < text_length:
        end = min(offset + chunk_size, text_length)
        # If not at the end of text, attempt to find a natural break near the end of the window
        if end < text_length:
            window_slice = text[offset:end]
            # Look for paragraph or sentence break in the last 20% of the window
            break_search_start = int(len(window_slice) * 0.8)
            search_zone = window_slice[break_search_start:]
            # Priority: \n\n, \n, . , ! , ?
            candidates = [
                search_zone.rfind("\n\n"),
                search_zone.rfind("\n"),
                search_zone.rfind(". "),
                search_zone.rfind("! "),
                search_zone.rfind("? "),
            ]
            best_idx = max(candidates)
            if best_idx != -1:
                end = (
                    offset
                    + break_search_start
                    + best_idx
                    + (
                        2
                        if search_zone[best_idx : best_idx + 2] in (". ", "! ", "? ", "\n\n")
                        else 1
                    )
                )

        chunk_content = text[offset:end].strip()
        if chunk_content:
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "source_offset": offset,
                    "content": chunk_content,
                    "metadata_json": {
                        "char_count": len(chunk_content),
                        "word_count": len(chunk_content.split()),
                    },
                }
            )
            chunk_index += 1

        offset += step
        if offset >= text_length:
            break

    return chunks


def compute_embedding(text: str, dimension: int = VECTOR_DIMENSION) -> list[float]:
    """Compute a deterministic, normalized feature-hashed vector of dimension D."""
    vector = [0.0] * dimension
    normalized = text.lower()
    # Tokenize words and character 3-grams
    tokens = re.findall(r"\b[a-z0-9_]{2,}\b", normalized)
    for token in tokens:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        idx = h % dimension
        sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        vector[idx] += sign

    # Add character n-grams for typo and subword resilience
    for i in range(len(normalized) - 2):
        trigram = normalized[i : i + 3]
        h = int(hashlib.md5(trigram.encode("utf-8")).hexdigest(), 16)
        idx = h % dimension
        sign = 1.0 if ((h >> 8) & 1) == 0 else -1.0
        vector[idx] += 0.5 * sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0.0:
        vector = [round(v / norm, 6) for v in vector]
    return vector


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    return max(0.0, min(1.0, dot))


def score_chunk(
    query: str,
    query_vec: list[float],
    chunk_content: str,
    chunk_vec: list[float] | None,
) -> float:
    cos_sim = cosine_similarity(query_vec, chunk_vec) if chunk_vec else 0.0

    # Keyword overlap scoring
    query_tokens = set(re.findall(r"\b[a-z0-9_]{2,}\b", query.lower()))
    if not query_tokens:
        return round(cos_sim, 4)

    chunk_lower = chunk_content.lower()
    matches = sum(1 for tok in query_tokens if tok in chunk_lower)
    token_overlap = matches / len(query_tokens)

    # Phrase match bonus
    phrase_bonus = 0.2 if query.lower().strip() in chunk_lower else 0.0

    hybrid = 0.5 * cos_sim + 0.4 * token_overlap + phrase_bonus
    return round(min(1.0, max(0.0, hybrid)), 4)


def index_file_chunks(
    db: Session,
    document_file: DocumentFile,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> int:
    """Delete existing chunks for file and create new indexed chunks."""
    db.execute(delete(DocumentChunk).where(DocumentChunk.file_id == document_file.id))
    raw_chunks = chunk_document(document_file.stored_text, chunk_size, overlap)

    for item in raw_chunks:
        emb = compute_embedding(item["content"], VECTOR_DIMENSION)
        chunk = DocumentChunk(
            workspace_id=document_file.workspace_id,
            file_id=document_file.id,
            chunk_index=item["chunk_index"],
            content=item["content"],
            source_offset=item["source_offset"],
            embedding=emb,
            metadata_json=item["metadata_json"],
        )
        db.add(chunk)

    document_file.status = "indexed"
    db.flush()
    return len(raw_chunks)


def search_workspace_chunks(
    db: Session,
    workspace_id: str,
    query: str,
    limit: int = 5,
    file_id: str | None = None,
) -> list[ChunkSearchResult]:
    query_vec = compute_embedding(query, VECTOR_DIMENSION)

    stmt = (
        select(DocumentChunk, DocumentFile.original_name)
        .join(DocumentFile, DocumentChunk.file_id == DocumentFile.id)
        .where(DocumentChunk.workspace_id == workspace_id)
    )
    if file_id:
        stmt = stmt.where(DocumentChunk.file_id == file_id)

    rows = db.execute(stmt).all()
    scored: list[ChunkSearchResult] = []

    for chunk, original_name in rows:
        embedding = chunk.embedding if isinstance(chunk.embedding, list) else None
        score = score_chunk(query, query_vec, chunk.content, embedding)
        if score > 0.01:
            scored.append(
                ChunkSearchResult(
                    chunk_id=chunk.id,
                    file_id=chunk.file_id,
                    filename=original_name,
                    chunk_index=chunk.chunk_index,
                    source_offset=chunk.source_offset,
                    score=score,
                    content=chunk.content,
                    metadata=chunk.metadata_json or {},
                )
            )

    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:limit]


def serialize_document_file(file: DocumentFile, chunk_count: int | None = None) -> dict:
    return {
        "id": file.id,
        "workspace_id": file.workspace_id,
        "original_name": file.original_name,
        "mime_type": file.mime_type,
        "sha256": file.sha256,
        "size_bytes": file.size_bytes,
        "status": file.status,
        "chunk_count": chunk_count,
        "created_at": file.created_at.isoformat() if file.created_at else None,
    }
