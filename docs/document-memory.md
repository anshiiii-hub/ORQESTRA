# Document memory and knowledge retrieval

Checkpoint 11 adds workspace-scoped reference document memory, sliding-window chunk indexing with character source offsets, deterministic 128-dimensional vector embeddings, and search retrieval.

## Workspace isolation and security boundary

- **Tenant isolation**: All documents and indexed chunks are strictly bound to an authorized `workspace_id`. Cross-workspace searches and document operations are blocked by query filters and server-side RBAC checks.
- **Untrusted evidence**: Stored text and retrieved chunk contents are untrusted data. They are never executed or treated as system instructions (FR-O07).
- **Supported formats**: Text documents (`.txt`, `.text`, `.md`, `.markdown`), code and configs (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.html`, `.css`, `.yaml`, `.yml`, `.sql`, `.sh`), structured data (`.csv`, `.tsv`, `.json`), and text-bearing `.pdf` files.
- **Upload constraints**: Bounded to 5 MiB per file and 1,000,000 extracted text characters. Traversal characters (`..`, `/`, `\`), null bytes, and unsupported extensions are rejected before storage.
- **Deletion cascade**: Deleting a file cascades to immediate removal of all derived chunk records from the knowledge index.

## Chunking and indexing

1. **Sliding-window partition**:
   - Default chunk size: 500 characters.
   - Chunk overlap: 100 characters.
   - Natural boundary detection: attempts paragraph (`\n\n`), newline (`\n`), or sentence breaks (`. `, `! `, `? `) near the end of the window to keep semantic units coherent.
2. **Provenance tracking**:
   - Each chunk persists its `chunk_index`, character `source_offset` within the source text, character count, and word count.
3. **Deterministic vector embeddings**:
   - Computes a normalized 128-dimensional feature-hashed term and n-gram frequency vector.
   - Zero external LLM token expenditure or API cost during embedding generation or retrieval.
   - 100% reproducible offline and in containerized test environments.

## Scoring and search ranking

`POST /api/v1/knowledge/search` scores chunks using a hybrid formula:
$$\text{Score} = 0.5 \times \text{CosineSimilarity}(\vec{q}, \vec{c}) + 0.4 \times \text{TokenOverlap} + \text{PhraseBonus}$$

- $\text{CosineSimilarity}$: dot product between normalized unit vectors $\vec{q}$ and $\vec{c}$.
- $\text{TokenOverlap}$: fraction of query tokens present in the chunk.
- $\text{PhraseBonus}$: bonus when the entire query appears verbatim in the text.
- Scores are clamped between `0.0` and `1.0` and sorted in descending order.

## API endpoints

- `GET /api/v1/knowledge/config`: reports upload limits, chunk size, vector dimension, and supported file extensions.
- `POST /api/v1/files/upload?workspace_id=...&filename=...`: streams raw document bytes, verifies sha256, validates encoding, stores file record, and automatically indexes chunks.
- `GET /api/v1/files?workspace_id=...`: lists files in the workspace with chunk counts and statuses.
- `GET /api/v1/files/{file_id}`: returns file metadata and chunk count.
- `POST /api/v1/files/{file_id}/index`: re-partitions and re-indexes file chunks.
- `DELETE /api/v1/files/{file_id}`: removes document file and cascades deletion of its chunks.
- `POST /api/v1/knowledge/search`: accepts `{workspace_id, query, limit, file_id?}` and returns top ranked chunks with filenames, offsets, scores, and text excerpts.
