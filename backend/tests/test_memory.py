import hashlib

import pytest

from app.knowledge import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    KnowledgeError,
    chunk_document,
    compute_embedding,
    cosine_similarity,
    extract_text_and_validate,
    score_chunk,
    validate_upload_filename,
)


def test_filename_validation_rules():
    assert validate_upload_filename("notes.txt") == "notes.txt"
    assert validate_upload_filename("report.md") == "report.md"
    assert validate_upload_filename("pipeline.py") == "pipeline.py"
    assert validate_upload_filename("data.csv") == "data.csv"
    assert validate_upload_filename("doc.pdf") == "doc.pdf"

    with pytest.raises(KnowledgeError) as err:
        validate_upload_filename("../traversal.txt")
    assert err.value.code == "invalid_filename"

    with pytest.raises(KnowledgeError) as err:
        validate_upload_filename("malicious.exe")
    assert err.value.code == "unsupported_file_type"

    with pytest.raises(KnowledgeError) as err:
        validate_upload_filename("invalid/sub/file.txt")
    assert err.value.code == "invalid_filename"


def test_extract_text_and_validate():
    content = b"Title: Machine Learning\n\nOrchestration of multiple agents."
    text, mime_type, size, sha256 = extract_text_and_validate("paper.txt", content)
    assert mime_type == "text/plain"
    assert size == len(content)
    assert sha256 == hashlib.sha256(content).hexdigest()
    assert "Machine Learning" in text

    # Empty file
    with pytest.raises(KnowledgeError) as err:
        extract_text_and_validate("empty.txt", b"")
    assert err.value.code == "empty_file"

    # Null bytes in text file
    with pytest.raises(KnowledgeError) as err:
        extract_text_and_validate("binary.txt", b"hello\x00world")
    assert err.value.code == "text_decode_failed"

    # PDF extraction
    pdf_content = b"%PDF-1.4\nBT /F1 12 Tf (Autonomous Multi-Agent Task Execution) Tj ET\nxref\n"
    pdf_text, pdf_mime, _, _ = extract_text_and_validate("spec.pdf", pdf_content)
    assert pdf_mime == "application/pdf"
    assert "Autonomous Multi-Agent Task Execution" in pdf_text


def test_chunking_and_source_offsets():
    short_text = "This is a short paragraph for testing single chunk behavior."
    chunks = chunk_document(
        short_text, chunk_size=DEFAULT_CHUNK_SIZE, overlap=DEFAULT_CHUNK_OVERLAP
    )
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["source_offset"] == 0
    assert chunks[0]["content"] == short_text
    assert chunks[0]["metadata_json"]["char_count"] == len(short_text)

    # Multi-chunk text
    sentences = [
        f"Sentence number {i} with detailed information on agent systems." for i in range(40)
    ]
    long_text = "\n\n".join(sentences)
    chunks = chunk_document(long_text, chunk_size=200, overlap=50)
    assert len(chunks) > 1
    for i, chunk in enumerate(chunks):
        assert chunk["chunk_index"] == i
        assert chunk["source_offset"] >= 0
        assert len(chunk["content"]) > 0


def test_deterministic_embedding_and_scoring():
    text_a = "Adaptive multi-agent task execution and fault tolerance"
    vec_1 = compute_embedding(text_a)
    vec_2 = compute_embedding(text_a)
    assert vec_1 == vec_2
    assert len(vec_1) == 128

    sim_self = cosine_similarity(vec_1, vec_2)
    assert abs(sim_self - 1.0) < 1e-4

    text_b = "Cooking recipes and sourdough baking bread"
    vec_b = compute_embedding(text_b)
    sim_diff = cosine_similarity(vec_1, vec_b)
    assert sim_diff < sim_self

    score = score_chunk("adaptive multi-agent", vec_1, text_a, vec_1)
    assert score > 0.6


def test_workspace_file_upload_index_and_search_api(api_client, accounts):
    owner = accounts("mem-owner@example.com")
    member = accounts("mem-member@example.com")
    viewer = accounts("mem-viewer@example.com")

    workspace = owner.post("/api/v1/workspaces", json={"name": "Knowledge Workspace"}).json()["id"]
    owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "mem-member@example.com", "role": "member"},
    )
    owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "mem-viewer@example.com", "role": "viewer"},
    )

    # Check config endpoint
    config_resp = viewer.get("/api/v1/knowledge/config")
    assert config_resp.status_code == 200
    assert config_resp.json()["available"] is True
    assert ".md" in config_resp.json()["supported_extensions"]

    # Upload document by member
    doc_content = b"# Multi-Agent Architecture\n\nAgents coordinate through durable task graph scheduling and fault-tolerant retries.\nVerification guarantees output correctness."
    upload_resp = member.post(
        f"/api/v1/files/upload?workspace_id={workspace}&filename=architecture.md",
        content=doc_content,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload_resp.status_code == 201, upload_resp.text
    file_info = upload_resp.json()
    file_id = file_info["id"]
    assert file_info["original_name"] == "architecture.md"
    assert file_info["chunk_count"] >= 1
    assert file_info["status"] == "indexed"

    # List files
    files_list = viewer.get(f"/api/v1/files?workspace_id={workspace}")
    assert files_list.status_code == 200
    assert len(files_list.json()) == 1
    assert files_list.json()[0]["id"] == file_id

    # Retrieve single file metadata
    file_meta = viewer.get(f"/api/v1/files/{file_id}")
    assert file_meta.status_code == 200
    assert file_meta.json()["id"] == file_id

    # Reindex file
    reindex_resp = member.post(f"/api/v1/files/{file_id}/index")
    assert reindex_resp.status_code == 200
    assert reindex_resp.json()["chunk_count"] >= 1

    # Search knowledge
    search_resp = viewer.post(
        "/api/v1/knowledge/search",
        json={"workspace_id": workspace, "query": "durable task graph scheduling", "limit": 5},
    )
    assert search_resp.status_code == 200, search_resp.text
    search_data = search_resp.json()
    assert search_data["total_matches"] >= 1
    top_match = search_data["results"][0]
    assert top_match["file_id"] == file_id
    assert top_match["filename"] == "architecture.md"
    assert top_match["score"] > 0.3
    assert "durable task graph" in top_match["content"]


def test_strict_workspace_isolation_and_cross_tenant_prevention(api_client, accounts):
    owner_a = accounts("tenant-a@example.com")
    owner_b = accounts("tenant-b@example.com")

    ws_a = owner_a.post("/api/v1/workspaces", json={"name": "Workspace A"}).json()["id"]
    ws_b = owner_b.post("/api/v1/workspaces", json={"name": "Workspace B"}).json()["id"]

    # Tenant A uploads confidential doc
    doc_a = b"Confidential Project Falcon details: stealth drone flight telemetry algorithms."
    up_a = owner_a.post(
        f"/api/v1/files/upload?workspace_id={ws_a}&filename=falcon.txt",
        content=doc_a,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert up_a.status_code == 201
    file_a_id = up_a.json()["id"]

    # Tenant B searches for "stealth drone flight telemetry" in Workspace B
    search_b = owner_b.post(
        "/api/v1/knowledge/search",
        json={"workspace_id": ws_b, "query": "stealth drone flight telemetry", "limit": 5},
    )
    assert search_b.status_code == 200
    assert search_b.json()["total_matches"] == 0
    assert len(search_b.json()["results"]) == 0

    # Tenant B attempts direct access to Tenant A's file
    access_attempt = owner_b.get(f"/api/v1/files/{file_a_id}")
    assert access_attempt.status_code in (403, 404)

    # Tenant B attempts reindexing Tenant A's file
    reindex_attempt = owner_b.post(f"/api/v1/files/{file_a_id}/index")
    assert reindex_attempt.status_code in (403, 404)


def test_rbac_and_deletion_cascade(api_client, accounts):
    owner = accounts("cascade-owner@example.com")
    viewer = accounts("cascade-viewer@example.com")

    workspace = owner.post("/api/v1/workspaces", json={"name": "Cascade WS"}).json()["id"]
    owner.post(
        f"/api/v1/workspaces/{workspace}/members",
        json={"email": "cascade-viewer@example.com", "role": "viewer"},
    )

    # Viewer cannot upload
    denied_upload = viewer.post(
        f"/api/v1/files/upload?workspace_id={workspace}&filename=viewer_doc.txt",
        content=b"Unauthorized text",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert denied_upload.status_code == 403

    # Owner uploads
    upload = owner.post(
        f"/api/v1/files/upload?workspace_id={workspace}&filename=deletable.txt",
        content=b"Target document to be removed and cascade deleted from memory index.",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert upload.status_code == 201
    file_id = upload.json()["id"]

    # Verify search returns it
    s1 = owner.post(
        "/api/v1/knowledge/search",
        json={"workspace_id": workspace, "query": "cascade deleted", "limit": 5},
    )
    assert s1.json()["total_matches"] >= 1

    # Viewer cannot delete
    denied_delete = viewer.delete(f"/api/v1/files/{file_id}")
    assert denied_delete.status_code == 403

    # Owner deletes
    delete_resp = owner.delete(f"/api/v1/files/{file_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["deleted"] is True

    # Confirm file is gone
    assert owner.get(f"/api/v1/files/{file_id}").status_code == 404

    # Confirm chunks are completely gone from search index
    s2 = owner.post(
        "/api/v1/knowledge/search",
        json={"workspace_id": workspace, "query": "cascade deleted", "limit": 5},
    )
    assert s2.json()["total_matches"] == 0
