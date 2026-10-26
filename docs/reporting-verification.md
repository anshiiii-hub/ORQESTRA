# Synthesis, reporting, and verifiable summary artifacts

Checkpoint 13 implements durable multi-format deliverable synthesis (Markdown, styled printable HTML, structured JSON), SHA-256 content fingerprinting, artifact download endpoints, and the deterministic 5-Tier Verification Rubric engine with defect attribution and mandatory limitations disclosure (PRD Sections 10.3, 10.5, US-07, FR-G05, FR-G06, FR-V01, FR-V04, FR-V05).

## Architectural policy and security boundary

1. **Durable artifact storage and downloads (US-07, FR-O05)**:
   - Artifacts are stored as durable records in PostgreSQL (`artifacts` table) with content stored inline or referenced on disk, tagged with MIME type, byte size, and SHA-256 fingerprint:
     $$\text{Fingerprint} = \text{SHA-256}(\text{RawContentBytes})$$
   - Endpoints `/api/v1/artifacts/{id}` and `/api/v1/artifacts/{id}/download` serve deliverables with explicit `Content-Type`, `Content-Length`, `Content-Disposition: attachment`, and cache control.
   - Multi-tenant isolation strictly enforces that artifacts belonging to workspace A can never be listed, downloaded, or verified by members of workspace B.

2. **Multi-format report synthesis (FR-G05, FR-G06)**:
   - When a deliverable is synthesized from workflow execution, the engine generates three complementary formats:
     - **Markdown (`report_markdown`)**: Human-readable GitHub-flavored markdown with structured sections: Executive Summary, Execution Provenance & Tasks, Dataset Profiling, External Research Citations, Document Evidence, and Mandatory Limitations & Confidence.
     - **Printable HTML (`report_html`)**: Standalone, styled document matching the ORQESTRA design system, fully self-contained and formatted for PDF export or paper printing (`@media print`).
     - **Structured JSON (`report_json`)**: Machine-readable payload containing structured sections, raw citations, task metadata, and verification metadata.

3. **Provenance citations and offsets (PRD Section 10.3)**:
   - Reports cite exact upstream artifacts, tasks, datasets, research URLs, and document memory chunks.
   - Research citations include URL, fetch timestamp, trust level (`untrusted`), and source content SHA-256.
   - Document memory citations include file name, chunk index, source character offset, and relevance score.

4. **Calibrated 5-tier verification rubric (PRD Section 10.5, FR-V01)**:
   - PRD Section 10.2 strictly mandates that LLM self-assessment must be segregated from externally verified correctness.
   - The engine evaluates deliverables against four calibrated criteria categories:
     - `task_completion`: Workflow completion, task statuses, error presence (Weight: 0.35, Mandatory).
     - `result_consistency`: Structural validity, deliverable payload presence, consistency (Weight: 0.25, Mandatory).
     - `provenance_citations`: Grounding in upstream tasks, external research, or document chunks (Weight: 0.20).
     - `limitations_disclosure`: Explicit boundaries, missing data flags, and assumptions (Weight: 0.20, Mandatory per FR-V05).
   - **Calibrated confidence score**:
     $$\text{Score} = \frac{\sum_{i} w_i \cdot \mathbb{I}(\text{criterion}_i = \text{satisfied}) + 0.5 \sum_{j} w_j \cdot \mathbb{I}(\text{criterion}_j = \text{warning})}{\sum_k w_k}$$

5. **Deterministic decision tiers**:
   - `PASS`: All mandatory criteria satisfied, confidence score $\ge 0.85$, zero critical or major defects.
   - `PASS_WITH_WARNINGS`: Mandatory criteria satisfied, confidence score $\ge 0.70$, minor defects present.
   - `REVISION_REQUIRED`: Non-mandatory criterion failed or minor/major defects suggest repairable remediation ($0.50 \le \text{Score} < 0.70$).
   - `FAIL`: Mandatory criterion failed, workflow failed, or confidence score $< 0.50$ (FR-V04: Never silently report success).
   - `INDETERMINATE`: Insufficient evidence or no workflow/artifact provided.

6. **Defect attribution (FR-V01, FR-V04)**:
   - Every failure or warning generates structured defect records:
     - `category`: `task_failure`, `schema_violation`, `missing_artifact`, `unverified_claim`.
     - `severity`: `critical`, `major`, `minor`, `info`.
     - `repairable`: Boolean indicator whether automated re-execution or human revision can fix the defect.
     - `suggested_task_ids`: Explicit upstream task references attributed to the defect.

7. **Mandatory limitations disclosure (FR-V05)**:
   - If a synthesized deliverable omits an explicit limitations section or boundary disclosure, the rubric automatically flags a warning or defect, ensuring deliverables are never presented as unconditionally authoritative.

---

## REST API reference

### Synthesize deliverable
`POST /api/v1/reports/synthesize`
- **Request body**:
  ```json
  {
    "workspace_id": "ws-1234",
    "workflow_id": "wf-5678",
    "title": "Quarterly Financial Analysis",
    "format": "all"
  }
  ```
- **Response**: HTTP 200 OK with synthesized `artifacts` list and `report_id`.

### List and download artifacts
`GET /api/v1/artifacts?workspace_id=ws-1234`
- Lists durable artifacts for the workspace.

`GET /api/v1/artifacts/{id}`
- Returns artifact metadata, size, MIME type, and SHA-256 fingerprint.

`GET /api/v1/artifacts/{id}/download`
- Streams binary/text content with `Content-Disposition: attachment`.

### Evaluate verification rubric
`POST /api/v1/verification/verify`
- **Request body**:
  ```json
  {
    "workspace_id": "ws-1234",
    "workflow_id": "wf-5678",
    "artifact_id": "art-9012"
  }
  ```
- **Response**: HTTP 200 OK with `VerificationReport` (decision, confidence score, evaluated criteria, defect list).

`GET /api/v1/verification/{id}`
- Retrieves a previously evaluated verification report by ID.
