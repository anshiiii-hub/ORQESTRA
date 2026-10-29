import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { Presentation, PresentationFile } from "@oai/artifact-tool";

const ROOT = process.cwd();
const BUILD = path.join(ROOT, ".presentation-build");
const OUTPUT_DIR = path.join(ROOT, "deliverables");
const DRAFT = path.join(BUILD, "orqestra-viva-draft.pptx");
const FINAL_PPTX = process.env.ORQESTRA_FINAL_PPTX || path.join(OUTPUT_DIR, "ORQESTRA_AI_Final_Viva_Deck.pptx");
const SKILL_DIR = "/Users/anshita/.codex/plugins/cache/openai-primary-runtime/presentations/26.1025.11240/skills/presentations";
const RUNTIME_PYTHON = "/Users/anshita/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3";
const C = {
  bg: "#0B1720",
  bg2: "#10232D",
  ink: "#F2F0E8",
  muted: "#A8B7BC",
  teal: "#70C9BC",
  amber: "#F0BD76",
  coral: "#E58A72",
  line: "#35505B",
  white: "#FFFFFF",
};
const FONT = "Arial";
const W = 1280;
const H = 720;
const p = Presentation.create({ slideSize: { width: W, height: H } });

function shape(slide, name, geometry, x, y, w, h, fill = "none", stroke = "none", width = 0) {
  return slide.shapes.add({
    geometry,
    name,
    position: { left: x, top: y, width: w, height: h },
    fill,
    line: { style: "solid", fill: stroke, width },
  });
}

function text(slide, name, value, x, y, w, h, size, color = C.ink, bold = false, alignment = "left") {
  const item = shape(slide, name, "textbox", x, y, w, h);
  item.text = value;
  item.text.style = {
    fontSize: size,
    color,
    bold,
    typeface: FONT,
    alignment,
    verticalAlignment: "middle",
    wrap: "square",
    autoFit: "shrinkText",
    insets: 0,
  };
  return item;
}

function line(slide, name, x1, y, x2, color = C.line, width = 1) {
  shape(slide, name, "line", x1, y, x2 - x1, 0, "none", color, width);
}

function vline(slide, name, x, y1, y2, color = C.line, width = 1) {
  shape(slide, name, "line", x, y1, 0, y2 - y1, "none", color, width);
}

function base(title, section, index, notes) {
  const s = p.slides.add();
  s.background.fill = C.bg;
  text(s, `section-${index}`, section.toUpperCase(), 70, 38, 720, 24, 15, C.teal, true);
  text(s, `title-${index}`, title, 70, 74, 1140, 62, 46, C.ink, true);
  line(s, `title-rule-${index}`, 70, 151, 1210, C.line, 1);
  line(s, `footer-rule-${index}`, 70, 674, 1210, C.line, 1);
  text(s, `footer-${index}`, "ORQESTRA AI  |  FINAL PROJECT VIVA", 70, 681, 620, 20, 13, C.muted);
  text(s, `page-${index}`, String(index).padStart(2, "0"), 1165, 681, 45, 20, 13, C.muted, true, "right");
  s.speakerNotes.text = notes;
  return s;
}

function paragraph(s, prefix, heading, body, y, accent = C.teal, bodySize = 23) {
  shape(s, `${prefix}-marker`, "rect", 72, y + 6, 5, 24, accent);
  text(s, `${prefix}-heading`, heading, 96, y, 1080, 34, 25, C.ink, true);
  text(s, `${prefix}-body`, body, 96, y + 40, 1060, 68, bodySize, C.muted);
}

// 1. Cover
{
  const s = p.slides.add();
  s.background.fill = C.bg;
  shape(s, "cover-accent", "rect", 72, 92, 8, 500, C.teal);
  text(s, "cover-brand", "ORQESTRA AI", 112, 118, 850, 45, 20, C.teal, true);
  text(s, "cover-title", "Reliable orchestration\nfor agent workflows", 108, 202, 1010, 190, 70, C.ink, true);
  text(s, "cover-subtitle", "System design, controls, evaluation and deployment readiness", 112, 421, 960, 62, 29, C.muted);
  text(s, "cover-date", "Project viva  |  27 October 2026", 112, 577, 680, 32, 18, C.muted);
  s.speakerNotes.text = "Opening: ORQESTRA AI is an academic prototype for durable, workspace-scoped task orchestration. This deck presents implemented behavior and states the remaining acceptance gaps plainly.";
}

// 2. Problem and objective
{
  const s = base("Reliable execution is the core problem", "Problem", 2,
    "The project focuses on reliable task execution around agents: typed plans, durable state, access control and evidence. It is not a claim that a large autonomous coding product is complete. Source: README.md; docs/agent-contracts.md; docs/scheduler.md.");
  paragraph(s, "problem-a", "Plans must be executable", "Reject malformed graphs, incompatible task contracts and missing dependencies before work begins.", 188);
  paragraph(s, "problem-b", "Progress must survive failure", "Persist tasks and attempts; use leases and checkpoint boundaries so workers can resume safely.", 322, C.amber);
  paragraph(s, "problem-c", "Results must be reviewable", "Scope data by workspace, gate sensitive writes and keep verification limits visible.", 456, C.coral);
}

// 3. Architecture diagram
{
  const s = base("The service separates browser, API, worker and state", "Architecture", 3,
    "Walk left to right. Caddy is the only public service in the production overlay. The API and separate polling worker share PostgreSQL state, which also stores atomic login-attempt windows. Mounted password files feed the database and application configuration. Sources: docs/architecture.md; compose.yaml; compose.production.yaml.");
  const browser = shape(s, "browser", "rect", 75, 264, 164, 86, C.bg2, C.line, 2);
  text(s, "browser-label", "Browser\nReact dashboard", 87, 274, 140, 65, 22, C.ink, true, "center");
  const caddy = shape(s, "caddy", "rect", 304, 240, 206, 132, C.bg2, C.teal, 2);
  text(s, "caddy-label", "Caddy\nHTTPS + proxy", 320, 261, 174, 90, 25, C.ink, true, "center");
  const web = shape(s, "frontend", "rect", 602, 178, 220, 88, C.bg2, C.line, 2);
  text(s, "frontend-label", "Static frontend\nNginx", 617, 187, 190, 67, 22, C.ink, true, "center");
  const api = shape(s, "api", "rect", 602, 325, 220, 92, C.bg2, C.teal, 2);
  text(s, "api-label", "FastAPI\nauth + orchestration", 616, 334, 192, 73, 22, C.ink, true, "center");
  const db = shape(s, "postgres", "rect", 928, 203, 235, 92, C.bg2, C.teal, 2);
  text(s, "postgres-label", "PostgreSQL\nworkflow + auth state", 944, 211, 204, 74, 22, C.ink, true, "center");
  const worker = shape(s, "worker", "rect", 928, 342, 235, 92, C.bg2, C.amber, 2);
  text(s, "worker-label", "Polling worker\nleased task execution", 944, 350, 204, 74, 22, C.ink, true, "center");
  s.shapes.connect(browser, caddy, { kind: "straight", fromSide: "right", toSide: "left", line: { style: "solid", fill: C.teal, width: 2 } });
  s.shapes.connect(caddy, web, { kind: "elbow", fromSide: "right", toSide: "left", line: { style: "solid", fill: C.muted, width: 2 } });
  s.shapes.connect(caddy, api, { kind: "elbow", fromSide: "right", toSide: "left", line: { style: "solid", fill: C.teal, width: 2 } });
  s.shapes.connect(api, db, { kind: "elbow", fromSide: "right", toSide: "left", line: { style: "solid", fill: C.teal, width: 2 } });
  s.shapes.connect(worker, db, { kind: "straight", fromSide: "left", toSide: "right", line: { style: "solid", fill: C.amber, width: 2 } });
  text(s, "auth-note", "Login throttling shares the same database: five attempts per normalized email every 15 minutes.", 74, 503, 1080, 48, 21, C.muted);
  text(s, "security-note", "Production ingress: Caddy only. API, database and frontend ports stay private.", 74, 560, 1080, 48, 21, C.teal, true);
}

// 4. Workflow lifecycle
{
  const s = base("A task moves through a persisted, fenced lifecycle", "Execution", 4,
    "Explain the lifecycle rather than claiming exactly-once execution. Tasks are leased and may be repeated after lease expiry. Results only publish while the attempt owns the lease. Pure deterministic tasks are safer to retry; uncertain provider charges are handled conservatively. Source: docs/scheduler.md; docs/benchmarks-recovery.md.");
  const steps = [
    ["01", "Validate", "Typed DAG and\ncapability checks"],
    ["02", "Persist", "Workflow, tasks\nand dependencies"],
    ["03", "Lease", "Worker claims\na bounded attempt"],
    ["04", "Dispatch", "Agent runs with\nscoped inputs"],
    ["05", "Verify", "Validate output\nand approvals"],
    ["06", "Record", "Persist result,\nevent and artifact"],
  ];
  const xs = [78, 277, 476, 675, 874, 1073];
  line(s, "lifecycle-line", 110, 280, 1165, C.line, 3);
  steps.forEach(([num, heading, body], i) => {
    const x = xs[i];
    shape(s, `step-dot-${i}`, "ellipse", x, 248, 64, 64, i === 2 ? C.amber : C.teal);
    text(s, `step-number-${i}`, num, x, 262, 64, 34, 20, C.bg, true, "center");
    text(s, `step-heading-${i}`, heading, x - 25, 340, 150, 36, 25, C.ink, true, "center");
    text(s, `step-body-${i}`, body, x - 35, 388, 170, 70, 21, C.muted, false, "center");
  });
  text(s, "lifecycle-limit", "Recovery is at least once; pure task idempotency and stale-result fencing matter.", 74, 531, 1090, 46, 22, C.amber, true);
}

// 5. Trust boundaries
{
  const s = base("Trust boundaries shape access, tools and retrieved content", "Controls", 5,
    "The security review maps existing tests to workspace isolation, CSRF/origin enforcement, SSRF controls, sandbox constraints, approval fingerprints and formula-safe exports. Prompt-like fixture text tests deterministic handling; it does not demonstrate resistance of a live LLM. Source: docs/checkpoint15-security-review.md; docs/authentication.md; docs/approvals.md.");
  paragraph(s, "control-a", "Identity and tenancy", "Cookie sessions, CSRF/origin checks and membership checks scope reads and writes.", 184);
  paragraph(s, "control-b", "Sensitive effects", "External writes pause for approval; canonical argument fingerprints detect tampering.", 307, C.amber);
  paragraph(s, "control-c", "Untrusted inputs", "Research and documents are evidence, not instructions. CSV formulas are treated as data and escaped on export.", 430, C.coral);
  paragraph(s, "control-d", "Isolated execution", "The optional code runner is disabled by default and needs a separate TLS-protected Docker Engine.", 553, C.teal);
}

// 6. Evaluation design
{
  const s = base("Checkpoint 15 uses a frozen 78-case evaluation", "Evaluation", 6,
    "There are 78 cases: 71 expected successful cases and 7 expected invalid-input rejections. Each execution defaults to three replicates, yielding 234 case-replicates per routing mode. Single means direct registry dispatch; fixed and adaptive use their policies. Expected outputs are compared recursively with a small numeric tolerance. Source: backend/app/fixtures/benchmark_v2.json; docs/checkpoint15-security-review.md.");
  text(s, "eval-count", "78", 84, 204, 236, 145, 112, C.teal, true);
  text(s, "eval-label", "frozen cases", 95, 350, 230, 40, 25, C.muted, true);
  vline(s, "eval-divider", 370, 205, 500, C.line, 1);
  text(s, "eval-valid", "71", 410, 213, 180, 76, 62, C.ink, true);
  text(s, "eval-valid-caption", "expected success", 412, 291, 230, 38, 21, C.muted);
  text(s, "eval-invalid", "7", 410, 354, 180, 76, 62, C.amber, true);
  text(s, "eval-invalid-caption", "expected rejection", 412, 432, 230, 38, 21, C.muted);
  text(s, "eval-modes-title", "Three baselines", 720, 205, 400, 42, 31, C.ink, true);
  text(s, "eval-modes", "Direct registry\nFixed routing\nAdaptive routing", 720, 261, 400, 126, 24, C.muted);
  text(s, "eval-replicates", "3 replicates by default  =  234 observations per mode", 720, 429, 460, 70, 22, C.teal, true);
  text(s, "eval-hash", "Raw fixture bytes are SHA-256 hashed and recorded with every result.", 80, 558, 1100, 42, 20, C.muted);
}

// 7. Honest findings
{
  const s = base("The evaluation checks behavior, not superiority", "Findings", 7,
    "Tests verify expected outcomes for all cases across the three modes. That means the deterministic implementation meets its fixtures; it is not evidence that one routing policy is better. Only one built-in registered agent exists per capability. The suite is local, sequential, provider-free, and does not measure speedup or provider cost. Sources: docs/checkpoint15-security-review.md; backend/tests/test_checkpoint_recovery_benchmarks.py.");
  text(s, "findings-one", "Fixture agreement", 78, 203, 365, 44, 31, C.teal, true);
  text(s, "findings-one-body", "All 78 cases are checked against predeclared outcomes in each baseline test.", 78, 260, 360, 98, 23, C.muted);
  vline(s, "findings-separator-1", 475, 206, 438, C.line, 1);
  text(s, "findings-two", "No policy comparison", 498, 203, 355, 44, 31, C.amber, true);
  text(s, "findings-two-body", "Fixed and adaptive currently select the same agents because the registry has one per capability.", 498, 260, 350, 118, 23, C.muted);
  vline(s, "findings-separator-2", 873, 206, 438, C.line, 1);
  text(s, "findings-three", "No live-model claim", 896, 203, 304, 44, 31, C.coral, true);
  text(s, "findings-three-body", "No provider calls, quality scores, API costs or parallel speedup are measured.", 896, 260, 292, 116, 23, C.muted);
  text(s, "findings-interval", "Reported confidence intervals describe case-replicate outcomes, not independent user samples.", 80, 507, 1120, 60, 22, C.ink, true);
}

// 8. Demo walkthroughs
{
  const s = base("The showcase runs four bounded scenarios", "Demonstrations", 8,
    "A, B and C satisfy the handbook's three core demonstrations as guided scenarios. A and B are fixture-backed and disclose that status; C invokes the real bounded CSV analysis code. Demo D is only a checkpoint-state snapshot; do not describe it as live worker termination. Sources: docs/checkpoint16-operations.md; backend/app/demos.py; backend/tests/test_checkpoint16_demos.py.");
  const rows = [
    ["A", "Research report", "Synthetic local source cards; no live retrieval or current market claims.", C.teal],
    ["B", "Coding validation", "Example diff and real approval record; no sandbox execution or repo write.", C.amber],
    ["C", "Dataset intelligence", "Real bounded CSV profile, computed totals/Z-scores, report and SVG artifact.", C.teal],
    ["D", "Recovery walkthrough", "Persisted pause/checkpoint snapshot; no worker-kill test.", C.coral],
  ];
  rows.forEach(([letter, title, detail, color], i) => {
    const y = 184 + i * 105;
    text(s, `demo-letter-${i}`, letter, 78, y, 55, 52, 38, color, true);
    text(s, `demo-title-${i}`, title, 151, y + 1, 315, 40, 26, C.ink, true);
    text(s, `demo-detail-${i}`, detail, 482, y + 1, 688, 64, 22, C.muted);
    if (i < rows.length - 1) line(s, `demo-rule-${i}`, 150, y + 78, 1174, C.line, 1);
  });
}

// 9. Deployment model
{
  const s = base("The staging profile narrows the public surface", "Operations", 9,
    "The overlay routes browser traffic through Caddy, uses the static production frontend, keeps backend service ports private and injects the DB password from a mounted file. Public registration and optional LLM/research/sandbox features are disabled. Bootstrap admin refuses to create an account if any user already exists. Sources: compose.production.yaml; deploy/Caddyfile; docs/checkpoint16-operations.md.");
  text(s, "deploy-entry", "Public ingress", 84, 206, 270, 45, 30, C.teal, true);
  text(s, "deploy-entry-body", "Caddy on 80/443\nAutomatic TLS\nSecurity headers", 84, 266, 275, 140, 24, C.muted);
  vline(s, "deploy-divider-1", 400, 207, 422, C.line, 1);
  text(s, "deploy-state", "Private services", 443, 206, 300, 45, 30, C.ink, true);
  text(s, "deploy-state-body", "FastAPI + worker\nPostgreSQL\nNo host port mappings", 443, 266, 300, 140, 24, C.muted);
  vline(s, "deploy-divider-2", 792, 207, 422, C.line, 1);
  text(s, "deploy-secret", "Operator controls", 834, 206, 340, 45, 30, C.amber, true);
  text(s, "deploy-secret-body", "Mounted DB secret\nRegistration disabled\nFirst account via CLI bootstrap", 834, 266, 340, 140, 24, C.muted);
  text(s, "deploy-safety", "Staging only: restrict access by VPN or source IP until auth hardening and independent review are complete.", 84, 480, 1090, 78, 24, C.coral, true);
}

// 10. Backup and restore
{
  const s = base("Backup and restore are designed as a verifiable pair", "Recovery", 10,
    "Backup requires GPG and a passphrase file, produces a custom-format PostgreSQL dump, records schema and key row counts, and checksums the dump and manifest. Restore checks archive paths and checksums before creating a new database, applies migrations and compares counts. The encrypted backup and isolated restore passed against the local Docker/PostgreSQL stack; public hosting and off-host retention remain open. Sources: scripts/backup.sh; scripts/restore.sh; scripts/health_drill.sh; docs/checkpoint16-evidence.md.");
  const chain = [
    ["1", "Dump", "PostgreSQL custom format"],
    ["2", "Protect", "GPG AES-256 + SHA-256"],
    ["3", "Restore", "New isolated DB only"],
    ["4", "Verify", "Migrations + row counts"],
  ];
  const positions = [86, 375, 664, 953];
  chain.forEach(([n, heading, sub], i) => {
    const x = positions[i];
    text(s, `backup-number-${i}`, n, x, 224, 58, 58, 42, i === 3 ? C.amber : C.teal, true, "center");
    text(s, `backup-title-${i}`, heading, x - 54, 313, 166, 40, 25, C.ink, true, "center");
    text(s, `backup-sub-${i}`, sub, x - 70, 363, 198, 74, 21, C.muted, false, "center");
    if (i < chain.length - 1) line(s, `backup-line-${i}`, x + 88, 253, positions[i + 1] - 18, C.line, 2);
  });
  text(s, "backup-limit", "Off-host retention and a restore on the staging host are still required for disaster recovery.", 83, 514, 1100, 56, 23, C.amber, true);
}

// 11. Gaps and acceptance
{
  const s = base("Local checks pass; hosted release needs verification", "Release status", 11,
    "The latest backend suite passed 281 tests, with two PostgreSQL-only tests skipped locally. Ruff, Mypy, formatting and the frontend build pass. The loopback-only Docker/PostgreSQL stack passed all four demos and the encrypted backup/isolated restore drill. The managed host has a free builder queue; verify the release built from PostgreSQL-backed throttling before repeating hosted health and demo checks. Public TLS details and off-host retention remain unverified. Checkpoints 13/14 retain documented gaps: live PostgreSQL worker kill/restart, browser SSE reconnect and older-workflow search. Sources: README.md; docs/checkpoint16-operations.md; docs/dashboard-observability.md; docs/benchmarks-recovery.md; docs/checkpoint16-evidence.md.");
  text(s, "status-local", "Verified locally", 82, 197, 395, 43, 31, C.teal, true);
  text(s, "status-local-body", "Backend suite: 281 passed\nRuff, Mypy and format checks\nFrontend production build\nFour demos + backup/restore\nCompose overlay syntax", 82, 256, 425, 182, 23, C.muted);
  vline(s, "status-divider", 586, 204, 494, C.line, 1);
  text(s, "status-open", "Hosted checks still open", 626, 197, 540, 43, 31, C.amber, true);
  text(s, "status-open-body", "Successful free-host build + health check\nCore demos on the host\nOff-host backup retention\nExternal security and dependency review", 626, 256, 542, 188, 23, C.muted);
  text(s, "status-hold", "Checkpoint 16 is prepared, not signed off.", 82, 514, 1080, 48, 26, C.coral, true);
}

// 12. Viva close
{
  const s = base("Three questions worth defending", "Discussion", 12,
    "Use these prompts to close. First: why at-least-once and stale-result fencing are more accurate than exactly-once. Second: why PostgreSQL-backed login throttling fits the managed hosting limits. Third: what additional competing agents, held-out tasks and independent observations would be needed to compare routing policies. Invite questions without implying production readiness.");
  paragraph(s, "viva-a", "Why at-least-once recovery?", "Workers can repeat a pure task after lease expiry; fencing prevents stale attempts from overwriting newer work.", 204);
  paragraph(s, "viva-b", "Why use PostgreSQL for login throttling?", "The managed deployment provides PostgreSQL, so an atomic counter table avoids another service while sharing limits across API instances.", 342, C.amber);
  paragraph(s, "viva-c", "What would prove adaptive routing helps?", "A held-out task set, competing agents per capability, controlled repeated runs and uncertainty-aware comparison.", 480, C.coral);
}

await fs.mkdir(BUILD, { recursive: true });
await fs.mkdir(OUTPUT_DIR, { recursive: true });
for (const [index, slide] of p.slides.items.entries()) {
  const blob = await p.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(path.join(BUILD, `slide-${String(index + 1).padStart(2, "0")}.png`), new Uint8Array(await blob.arrayBuffer()));
}
const preview = await p.export({ format: "webp", montage: true, scale: 0.45 });
await fs.writeFile(path.join(BUILD, "montage.webp"), new Uint8Array(await preview.arrayBuffer()));
const draft = await PresentationFile.exportPptx(p);
await draft.save(DRAFT);
const { finalizePresentation } = await import(pathToFileURL(path.join(SKILL_DIR, "container_tools/artifact_tool_utils.mjs")).href);
const receiptPath = path.join(BUILD, `${path.basename(FINAL_PPTX)}.validation.json`);
const finalized = await finalizePresentation({
  explicitTotalSlideCount: p.slides.items.length,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
  workspaceDir: ROOT,
  candidatePath: DRAFT,
  finalPath: FINAL_PPTX,
  pythonExecutable: RUNTIME_PYTHON,
  integrityValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_package_integrity.py"),
  layoutValidatorPath: path.join(SKILL_DIR, "container_tools/inspect_presentation_layout_geometry.py"),
  layoutArgs: ["--expected-slide-size-emu", "12192000,6858000", "--validate-bullet-geometry", "--validate-heading-fit"],
  fontPolicy: { basis: "design", families: [FONT] },
  verifyArtifactToolImport: true,
  receiptPath,
});
console.log(JSON.stringify({ slideCount: p.slides.items.length, draft: DRAFT, final: FINAL_PPTX, preview: path.join(BUILD, "montage.webp"), finalized }));
