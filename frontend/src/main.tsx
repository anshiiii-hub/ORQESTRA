import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, BarChart3, BrainCircuit, CheckCircle2, Clock3, Code2, Database, Layers, Play, ShieldCheck, XCircle, RefreshCw, LogOut, Users, Search, Trash2, Lock, AlertTriangle, Check, X, FileText, Download} from 'lucide-react';
import {API, ApiError, request, upload} from './api';
import './style.css';

type Role = 'owner' | 'member' | 'viewer';
type Session = {user: {id: string; email: string}; csrf_token: string};
type Workspace = {id: string; name: string; role: Role};
type Member = {user_id: string; email: string; role: Role};
type Task = {id: string; name: string; agent: string; status: string; attempts: number; execution_attempts: {number: number; status: string; error_code: string | null}[]; routing_decision: {mode: string; score: number; eligible_candidate_count: number; factors: Record<string, number>; candidates: {agent: string; observations?: {average_reported_cost_usd: string; completed_attempts: number}}[]} | null; capability: string; agent_version: string; schema_version: string; error_code: string | null; execution: {request_id: string; schema_version: string; status: string} | null};
type Agent = {id: string; version: string; capabilities: string[]; enabled: boolean; availability: 'ready' | 'offline' | 'disabled'; available_workers: number; last_seen_at: string | null};
type Planning = {provider?: string; model?: string | null; status: string; accounted_tokens?: number; accounted_cost_usd?: string; max_cost_usd?: string; error_code?: string | null; attempts: {number: number; status: string; input_tokens: number | null; output_tokens: number | null}[]};
type PlannerConfiguration = {llm_available: boolean; model: string | null; provider: string | null; max_attempts: number; max_tokens: number; max_cost_usd: number; max_deadline_seconds: number};
type ResearchConfiguration = {available: boolean; max_urls: number; max_response_bytes: number; max_timeout_seconds: number; content_trust: 'untrusted'};
type ResearchSource = {id: string; url: string; title: string; excerpt: string; content_sha256: string | null; fetched_at: string | null; trust_level: 'untrusted'; status: 'fetched' | 'failed'; error_code: string | null};
type ResearchResult = {id: string; status: string; query: string; sources: ResearchSource[]};
type DatasetAnalysis = {id: string; workspace_id: string; filename: string; file_format: string; source_size_bytes: number; content_sha256: string; row_count: number; column_count: number; metadata: {analysis_version: string; formula_cells_treated_as_data: number}; profile: {rows: number; columns: {name: string; inferred_type: string; missing_count: number; distinct_count: number; example?: string; statistics?: {count: number; mean: number; min: number; max: number; sum: number}}[]}; chart_artifacts: {id: string; title: string}[]; csv_export_url: string};
type SandboxConfiguration = {available: boolean; max_files: number; max_patch_files: number; max_input_bytes: number; max_checks: number; checks: string[]; memory_bytes: number; cpu_cores: number; pids: number; network: 'none'; host_mounts: number};
type SandboxResult = {id: string; status: 'completed' | 'failed' | 'timed_out'; diff: string; artifact: {checks: {check: string; status: string; exit_code: number | null; stdout: string; stderr: string}[]; isolation: {network: string; host_mounts: number; memory_bytes?: number; cpu_cores?: number; pids?: number; wall_time_seconds?: number}}};
type KnowledgeConfiguration = {available: boolean; max_file_bytes: number; max_text_chars: number; chunk_size: number; chunk_overlap: number; vector_dimension: number; supported_extensions: string[]; content_trust: 'untrusted'};
type DocumentFileItem = {id: string; workspace_id: string; original_name: string; mime_type: string; sha256: string; size_bytes: number; status: string; chunk_count: number; created_at: string | null};
type ChunkSearchResult = {chunk_id: string; file_id: string; filename: string; chunk_index: number; source_offset: number; score: number; content: string; metadata: {char_count?: number; word_count?: number}};
type KnowledgeSearchResult = {workspace_id: string; query: string; total_matches: number; results: ChunkSearchResult[]};
type Approval = {id: string; workspace_id: string; workflow_id: string | null; task_id: string | null; action_type: string; description: string; details: {arguments_hash?: string; sanitized_parameters?: Record<string, unknown>; parameters?: Record<string, unknown>; [key: string]: unknown}; status: 'pending' | 'approved' | 'rejected' | 'expired'; decision: 'approved' | 'rejected' | null; decision_by: string | null; reason: string | null; requested_by: string; requester_email?: string | null; decider_email?: string | null; created_at: string; expires_at: string; decided_at: string | null};
type RegisteredTool = {tool: string; effect: 'read_only' | 'external_write' | 'isolated_compute'; requires_approval: boolean; required_permissions: string[]; description: string};
type ToolExecutionResponse = {status: 'executed' | 'waiting_approval'; approval_id?: string; tool_call_id?: string; tool_name: string; effect: string; requires_approval?: boolean; arguments_hash?: string; message?: string; result?: unknown};
type Artifact = {id: string; workspace_id: string; workflow_id: string | null; task_id: string | null; name: string; artifact_type: string; mime_type: string; size_bytes: number; content_sha256: string; metadata: Record<string, unknown>; created_at: string | null; download_url: string};
type VerificationCriterion = {id: string; category: string; criterion: string; status: 'satisfied' | 'warning' | 'failed'; is_mandatory: boolean; evidence: string};
type VerificationDefect = {criterion_id: string; severity: 'critical' | 'major' | 'minor' | 'info'; category: string; description: string; evidence_refs: string[]; repairable: boolean; suggested_task_ids: string[]};
type VerificationReport = {id: string; workspace_id: string; workflow_id: string | null; artifact_id: string | null; decision: 'PASS' | 'PASS_WITH_WARNINGS' | 'REVISION_REQUIRED' | 'FAIL' | 'INDETERMINATE'; confidence_score: number; criteria: VerificationCriterion[]; defects: VerificationDefect[]; summary: string; created_at: string | null};
type Workflow = {id: string; workspace_id: string; prompt: string; status: string; routing_mode: 'fixed' | 'adaptive'; tasks: Task[]; output: unknown; error: string | null; planning: Planning | null; plan: {final_task_id: string; tasks: unknown[]} | null};
type Run = Pick<Workflow, 'id' | 'prompt' | 'status'>;
type RunEvent = {id: number; kind: string; details: Record<string, unknown>; created_at: string};
const demoCSV = 'product,sales,units\nFrames,2400,12\nSunglasses,1800,9\nLenses,1200,20';

function Brand() {
  return <div className="brand"><div className="logo"><BrainCircuit size={23}/></div><div><b>ORQESTRA <span>AI</span></b><small>ORCHESTRATION LAB</small></div></div>;
}

function SignIn({onSession, initialMessage}: {onSession: (session: Session) => void; initialMessage: string}) {
  const [register, setRegister] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState(initialMessage);
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage('');
    try {
      const session = await request<Session>(`/auth/${register ? 'register' : 'login'}`, {method: 'POST', body: JSON.stringify({email, password})});
      setPassword(''); onSession(session);
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  };
  return <div className="auth-page"><section className="panel auth-panel"><Brand/>
    <div className="eyebrow">YOUR WORK, YOUR WORKSPACE</div>
    <h1>{register ? 'Create your account' : 'Welcome back'}</h1>
    <p className="muted">Sign in to run and inspect your team’s workflows.</p>
    <form onSubmit={submit}>
      <label htmlFor="email">Email address</label><input id="email" type="email" autoComplete="username" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)}/>
      <label htmlFor="password">Password</label><input id="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={12} maxLength={128} value={password} onChange={e => setPassword(e.target.value)}/>
      {register && <p className="helper">Use at least 12 characters.</p>}
      {message && <p role="alert" className="error">{message}</p>}
      <button className="primary auth-submit" disabled={busy}>{busy ? 'Please wait…' : register ? 'Create account' : 'Sign in'}</button>
    </form>
    <button className="textbutton" onClick={() => {setRegister(!register); setMessage(''); setPassword('');}} disabled={busy}>{register ? 'Already registered? Sign in' : 'New here? Create an account'}</button>
  </section></div>;
}

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    localStorage.removeItem('orchestra_demo_key');
    const controller = new AbortController();
    request<Session>('/auth/me', {signal: controller.signal})
      .then(setSession).catch(error => {
        if (error.name !== 'AbortError' && !(error instanceof ApiError && error.status === 401)) setMessage(error.message);
      }).finally(() => {if (!controller.signal.aborted) setReady(true);});
    return () => controller.abort();
  }, []);
  const expire = () => {setSession(null); setWorkspaces([]); setWorkspaceId(''); setMessage('Your session ended. Please sign in again.');};
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const list = await request<Workspace[]>('/workspaces', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setWorkspaces(list);
        setWorkspaceId(current => list.some(w => w.id === current) ? current : list[0]?.id || '');
        setMessage('');
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {expire(); return;}
        setMessage((error as Error).message);
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
    };
    refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [session]);
  if (!ready) return <div className="auth-page"><p>Loading your workspace…</p></div>;
  if (!session) return <SignIn initialMessage={message} onSession={value => {setMessage(''); setSession(value);}}/>;
  const workspace = workspaces.find(w => w.id === workspaceId) || null;
  return <Dashboard key={`${session.user.id}:${workspaceId}`} session={session} workspace={workspace} workspaces={workspaces} message={message}
    onSwitch={setWorkspaceId} onExpired={expire} onSignedOut={() => {setSession(null); setWorkspaces([]); setWorkspaceId(''); setMessage('');}}
    onCreated={value => {setWorkspaces(current => [...current, value]); setWorkspaceId(value.id);}}/>;
}

type DashboardProps = {session: Session; workspace: Workspace | null; workspaces: Workspace[]; message: string;
  onSwitch: (id: string) => void; onExpired: () => void; onSignedOut: () => void; onCreated: (workspace: Workspace) => void};

function Dashboard({session, workspace, workspaces, message: parentMessage, onSwitch, onExpired, onSignedOut, onCreated}: DashboardProps) {
  const [prompt, setPrompt] = useState('Inspect the sales dataset, compute a numeric summary and verify the results.');
  const [csv, setCSV] = useState(demoCSV);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState('');
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [plannerConfiguration, setPlannerConfiguration] = useState<PlannerConfiguration | null>(null);
  const [researchConfiguration, setResearchConfiguration] = useState<ResearchConfiguration | null>(null);
  const [researchQuery, setResearchQuery] = useState('');
  const [researchUrls, setResearchUrls] = useState('');
  const [researchResult, setResearchResult] = useState<ResearchResult | null>(null);
  const [datasetFile, setDatasetFile] = useState<File | null>(null);
  const [datasetAnalysis, setDatasetAnalysis] = useState<DatasetAnalysis | null>(null);
  const [sandboxConfiguration, setSandboxConfiguration] = useState<SandboxConfiguration | null>(null);
  const [sandboxFiles, setSandboxFiles] = useState('{\n  "main.py": "def add(a, b):\\n    return a - b\\n",\n  "test_main.py": "from main import add\\n\\ndef test_add():\\n    assert add(2, 3) == 5\\n"\n}');
  const [sandboxPatch, setSandboxPatch] = useState('{\n  "main.py": "def add(a, b):\\n    return a + b\\n"\n}');
  const [sandboxCheck, setSandboxCheck] = useState('pytest');
  const [sandboxResult, setSandboxResult] = useState<SandboxResult | null>(null);
  const [knowledgeConfiguration, setKnowledgeConfiguration] = useState<KnowledgeConfiguration | null>(null);
  const [documents, setDocuments] = useState<DocumentFileItem[]>([]);
  const [docUploadFile, setDocUploadFile] = useState<File | null>(null);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [knowledgeResult, setKnowledgeResult] = useState<KnowledgeSearchResult | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [tools, setTools] = useState<RegisteredTool[]>([]);
  const [decisionReasons, setDecisionReasons] = useState<Record<string, string>>({});
  const [testToolName, setTestToolName] = useState('file.export_csv');
  const [testToolParams, setTestToolParams] = useState('{\n  "filename": "quarterly_summary.csv",\n  "content": "region,revenue\\nAPAC,54000\\nEMEA,48000"\n}');
  const [testApprovalId, setTestApprovalId] = useState('');
  const [testToolResult, setTestToolResult] = useState<ToolExecutionResponse | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [reportTitle, setReportTitle] = useState('Workspace Synthesized Deliverable');
  const [verificationReport, setVerificationReport] = useState<VerificationReport | null>(null);
  const [plannerMode, setPlannerMode] = useState<'offline' | 'llm'>('offline');
  const [routingMode, setRoutingMode] = useState<'fixed' | 'adaptive'>('fixed');
  const [planningCostLimit, setPlanningCostLimit] = useState('0.05');
  const [planningTokenLimit, setPlanningTokenLimit] = useState('20000');
  const [planningDeadline, setPlanningDeadline] = useState('60');
  const [workspaceName, setWorkspaceName] = useState('');
  const [memberEmail, setMemberEmail] = useState('');
  const [memberRole, setMemberRole] = useState<'member' | 'viewer'>('viewer');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const actionController = useRef(new AbortController());
  useEffect(() => () => actionController.current.abort(), []);
  const canWrite = !!workspace && workspace.role !== 'viewer';
  const fail = (error: unknown) => {
    if ((error as Error).name === 'AbortError') return;
    if (error instanceof ApiError && error.status === 401) onExpired();
    else setMessage((error as Error).message);
  };
  useEffect(() => {
    if (!workspace) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const list = await request<Run[]>(`/workflows?workspace_id=${encodeURIComponent(workspace.id)}`, {signal: controller.signal});
        const details = selected ? await Promise.all([
          request<Workflow>(`/workflows/${selected}`, {signal: controller.signal}),
          request<RunEvent[]>(`/workflows/${selected}/events`, {signal: controller.signal})
        ]) : null;
        const people = workspace.role === 'owner' ? await request<Member[]>(`/workspaces/${workspace.id}/members`, {signal: controller.signal}) : [];
        if (controller.signal.aborted) return;
        setRuns(list); setMembers(people);
        const roster = await request<Agent[]>('/agents', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setAgents(roster);
        const policy = await request<PlannerConfiguration>('/planner/config', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setPlannerConfiguration(policy);
        const researchPolicy = await request<ResearchConfiguration>('/research/config', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setResearchConfiguration(researchPolicy);
        const sandboxPolicy = await request<SandboxConfiguration>('/sandbox/config', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setSandboxConfiguration(sandboxPolicy);
        const knowledgePolicy = await request<KnowledgeConfiguration>('/knowledge/config', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setKnowledgeConfiguration(knowledgePolicy);
        const fileList = await request<DocumentFileItem[]>(`/files?workspace_id=${encodeURIComponent(workspace.id)}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        setDocuments(fileList);
        const approvalList = await request<Approval[]>(`/approvals?workspace_id=${encodeURIComponent(workspace.id)}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        setApprovals(approvalList);
        const toolList = await request<RegisteredTool[]>('/tools', {signal: controller.signal});
        if (controller.signal.aborted) return;
        setTools(toolList);
        const artifactList = await request<Artifact[]>(`/artifacts?workspace_id=${encodeURIComponent(workspace.id)}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        setArtifacts(artifactList);
        if (details) {setWorkflow(details[0]); setEvents(details[1]);}
      } catch (error) {
        if (!controller.signal.aborted) {
          if (error instanceof ApiError && error.status === 404) {setRuns([]); setWorkflow(null); setEvents([]); setMembers([]); setDocuments([]); setApprovals([]); setArtifacts([]);}
          fail(error);
        }
      }
      if (!controller.signal.aborted) timer = setTimeout(refresh, 1800);
    };
    refresh();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [workspace?.id, workspace?.role, selected, refreshKey]);
  const action = async (operation: () => Promise<void>) => {
    setBusy(true); setMessage('');
    try {await operation(); if (!actionController.current.signal.aborted) setRefreshKey(key => key + 1);}
    catch (error) {fail(error);}
    finally {setBusy(false);}
  };
  const mutate = <T,>(path: string, body?: unknown, method = 'POST') => request<T>(path, {method, signal: actionController.current.signal, ...(body ? {body: JSON.stringify(body)} : {})}, session.csrf_token);
  const create = () => action(async () => {
    if (!workspace || !canWrite || (plannerMode === 'llm' && !plannerConfiguration?.llm_available)) return;
    const data = await mutate<{workflow_id: string}>('/workflows', {workspace_id: workspace.id, prompt, csv_text: csv, planner_mode: plannerMode, routing_mode: routingMode, ...(plannerMode === 'llm' ? {planning_max_tokens: Number(planningTokenLimit), planning_max_cost_usd: Number(planningCostLimit), planning_deadline_seconds: Number(planningDeadline)} : {})});
    setWorkflow(null); setEvents([]); setSelected(data.workflow_id);
  });
  const searchResearch = () => action(async () => {
    if (!workspace || !canWrite || !researchConfiguration?.available) return;
    const urls = researchUrls.split(/\r?\n/).map(url => url.trim()).filter(Boolean);
    const result = await mutate<ResearchResult>('/research/search', {workspace_id: workspace.id, query: researchQuery, urls});
    setResearchResult(result);
  });
  const analyzeDataset = () => action(async () => {
    if (!workspace || !canWrite || !datasetFile) return;
    const query = new URLSearchParams({workspace_id: workspace.id, filename: datasetFile.name});
    const result = await upload<DatasetAnalysis>(`/data-analysis/import?${query.toString()}`, datasetFile, session.csrf_token);
    setDatasetAnalysis(result);
  });
  const runSandbox = () => action(async () => {
    if (!workspace || !canWrite || !sandboxConfiguration?.available) return;
    const files = JSON.parse(sandboxFiles) as Record<string, string>;
    const patch = JSON.parse(sandboxPatch) as Record<string, string>;
    const result = await mutate<SandboxResult>('/sandbox/run', {
      workspace_id: workspace.id,
      files,
      patch,
      checks: [sandboxCheck],
    });
    setSandboxResult(result);
  });
  const uploadDocument = () => action(async () => {
    if (!workspace || !canWrite || !docUploadFile) return;
    const query = new URLSearchParams({workspace_id: workspace.id, filename: docUploadFile.name});
    await upload<DocumentFileItem>(`/files/upload?${query.toString()}`, docUploadFile, session.csrf_token);
    setDocUploadFile(null);
  });
  const reindexDoc = (id: string) => action(async () => {
    if (!workspace || !canWrite) return;
    await mutate(`/files/${id}/index`);
  });
  const deleteDoc = (id: string) => action(async () => {
    if (!workspace || !canWrite) return;
    await mutate(`/files/${id}`, undefined, 'DELETE');
    if (knowledgeResult) {
      setKnowledgeResult(prev => prev ? {...prev, results: prev.results.filter(r => r.file_id !== id)} : null);
    }
  });
  const searchKnowledge = () => action(async () => {
    if (!workspace || !knowledgeQuery.trim()) return;
    const res = await mutate<KnowledgeSearchResult>('/knowledge/search', {
      workspace_id: workspace.id,
      query: knowledgeQuery,
      limit: 5,
    });
    setKnowledgeResult(res);
  });
  const submitDecision = (approvalId: string, decision: 'approved' | 'rejected') => action(async () => {
    if (!workspace || !canWrite) return;
    const reason = decisionReasons[approvalId] || undefined;
    await mutate(`/approvals/${approvalId}/decision`, {decision, reason});
    setDecisionReasons(prev => {
      const next = {...prev};
      delete next[approvalId];
      return next;
    });
  });
  const runToolTest = () => action(async () => {
    if (!workspace || !canWrite) return;
    let params: Record<string, unknown> = {};
    try {
      params = JSON.parse(testToolParams);
    } catch {
      throw new Error('Tool parameters must be valid JSON');
    }
    const body: Record<string, unknown> = {
      workspace_id: workspace.id,
      tool_name: testToolName,
      parameters: params,
    };
    if (testApprovalId.trim()) {
      body.approval_id = testApprovalId.trim();
    }
    const result = await mutate<ToolExecutionResponse>('/tools/execute', body);
    setTestToolResult(result);
    if (result.status === 'waiting_approval' && result.approval_id) {
      setTestApprovalId(result.approval_id);
    }
  });
  const synthesizeReport = () => action(async () => {
    if (!workspace || !canWrite) return;
    const res = await mutate<{status: string; artifacts: Artifact[]}>('/reports/synthesize', {
      workspace_id: workspace.id,
      workflow_id: selected || undefined,
      title: reportTitle,
    });
    if (res.artifacts.length > 0) {
      const md = res.artifacts.find(a => a.artifact_type === 'report_markdown') || res.artifacts[0];
      const ver = await mutate<VerificationReport>('/verification/verify', {
        workspace_id: workspace.id,
        workflow_id: selected || undefined,
        artifact_id: md.id,
      });
      setVerificationReport(ver);
    }
  });
  const runVerification = (artifactId?: string) => action(async () => {
    if (!workspace || !canWrite) return;
    const ver = await mutate<VerificationReport>('/verification/verify', {
      workspace_id: workspace.id,
      workflow_id: selected || undefined,
      artifact_id: artifactId || undefined,
    });
    setVerificationReport(ver);
  });
  const pendingApprovals = approvals.filter(a => a.status === 'pending');
  const pastApprovals = approvals.filter(a => a.status !== 'pending');
  const hasDisabledAgent = agents.some(agent => !agent.enabled);
  const succeeded = workflow?.tasks.filter(t => t.status === 'succeeded').length || 0;
  return <div className="shell">
    <aside className="sidebar"><Brand/>
      <label htmlFor="workspace">Workspace</label><select id="workspace" value={workspace?.id || ''} onChange={e => onSwitch(e.target.value)}>
        {!workspace && <option value="">Create a workspace</option>}{workspaces.map(w => <option key={w.id} value={w.id}>{w.name} · {w.role}</option>)}
      </select>
      <form className="workspace-create" onSubmit={e => {e.preventDefault(); action(async () => {const value = await mutate<Workspace>('/workspaces', {name: workspaceName}); onCreated(value);});}}>
        <label htmlFor="workspace-name">New workspace</label><input id="workspace-name" required maxLength={100} value={workspaceName} onChange={e => setWorkspaceName(e.target.value)} placeholder="Workspace name"/>
        <button className="secondary" disabled={busy}>Create workspace</button>
      </form>
      <div className="navitem active"><Layers size={18}/> Workflows</div>
      <div className="side-label">RECENT WORKFLOWS</div><div className="recent">{runs.map(w => <button key={w.id} className={selected === w.id ? 'recent-btn current' : 'recent-btn'} onClick={() => {setWorkflow(null); setEvents([]); setSelected(w.id);}}><span className="dot"/><div><strong>{w.prompt.slice(0, 35)}</strong><small>{w.status} · {w.id.slice(0, 8)}</small></div></button>)}</div>
      <div className="sidefoot"><span className="account-email">{session.user.email}</span><button className="secondary signout" disabled={busy} onClick={() => action(async () => {await mutate('/auth/logout'); onSignedOut();})}><LogOut size={14}/> Sign out</button><small>Checkpoint 13 · Synthesis, reporting, and verifiable artifacts</small></div>
    </aside>
    <main><header><div><div className="eyebrow">{workspace?.name || 'YOUR WORKSPACE'} / CONTROL CENTER</div><h1>Workflow orchestration</h1><p>{workspace ? `${workspace.role} access · ${workspace.role === 'viewer' ? 'Inspect' : 'Run and inspect'} your team’s workflows.` : 'Create your first workspace to get started.'}</p></div><div className="head-tag"><span className="pulse"/> Signed in</div></header>
      {(message || parentMessage) && <p role="alert" className="error">{message || parentMessage}</p>}
      {!workspace ? <section className="panel"><h2>A place for your work</h2><p className="muted">Name your workspace using the form in the sidebar. Each workspace has its own workflows and members.</p></section> : <>
      <div className="cards">{[
        ['RECENT WORKFLOWS', runs.length, 'Latest 50 in this workspace'],
        ['COMPLETED', runs.filter(w => w.status === 'succeeded').length, 'Verified end-to-end'],
        ['RUNNING', runs.filter(w => ['running', 'queued', 'planning'].includes(w.status)).length, 'Active pipeline'],
        ['FAILED', runs.filter(w => w.status === 'failed').length, 'Review execution logs']
      ].map(([label, value, description]) => <div className="metric" key={label}><small>{label}</small><b>{value}</b><span>{description}</span></div>)}</div>
      <div className="columns"><section className="panel"><div className="panel-head"><div><div className="eyebrow">01 / INPUT</div><h2>Launch a workflow</h2></div><Play size={18}/></div>
        <form onSubmit={e => {e.preventDefault(); create();}}>
          <label htmlFor="planner-mode">Planning mode</label><select id="planner-mode" value={plannerMode} onChange={e => setPlannerMode(e.target.value as 'offline' | 'llm')} disabled={!canWrite || busy}>
            <option value="offline">Offline pipeline · No provider calls</option><option value="llm" disabled={!plannerConfiguration?.llm_available}>{plannerConfiguration?.llm_available ? `OpenAI planner · ${plannerConfiguration.model}` : 'OpenAI planner · Not configured'}</option>
          </select>
          <label htmlFor="routing-mode">Agent routing baseline</label><select id="routing-mode" value={routingMode} onChange={e => setRoutingMode(e.target.value as 'fixed' | 'adaptive')} disabled={!canWrite || busy}>
            <option value="fixed">Fixed · Use each task’s requested agent</option><option value="adaptive">Adaptive · Score eligible agents</option>
          </select>
          <p className="helper">Adaptive routing filters by capability, protocol, input contract, tool policy, and live worker support. It scores reliability, latency, availability, and observed cost. The current registry has one compatible agent per capability, so the baselines select the same agents until alternatives are registered.</p>
          {plannerMode === 'llm' && <><p className="helper">Sends task instructions and CSV column names to OpenAI. CSV rows stay local. Only registered CSV agents run after plan validation.</p><div className="planning-budget">
            <div><label htmlFor="planning-cost">Max planning cost (USD)</label><input id="planning-cost" type="number" required min="0.000001" max={plannerConfiguration?.max_cost_usd || 1} step="any" value={planningCostLimit} onChange={e => setPlanningCostLimit(e.target.value)} disabled={busy || !canWrite}/></div>
            <div><label htmlFor="planning-tokens">Planning token budget</label><input id="planning-tokens" type="number" required min="512" max={plannerConfiguration?.max_tokens || 64000} step="1" value={planningTokenLimit} onChange={e => setPlanningTokenLimit(e.target.value)} disabled={busy || !canWrite}/></div>
            <div><label htmlFor="planning-deadline">Planning deadline (seconds)</label><input id="planning-deadline" type="number" required min="5" max={plannerConfiguration?.max_deadline_seconds || 180} step="1" value={planningDeadline} onChange={e => setPlanningDeadline(e.target.value)} disabled={busy || !canWrite}/></div>
          </div><p className="helper">At most two provider calls, including one repair. Cost estimates use operator-configured prices. An in-flight call may still be charged if you cancel.</p></>}
          <label htmlFor="prompt">Task instructions</label><textarea id="prompt" required minLength={3} rows={3} value={prompt} onChange={e => setPrompt(e.target.value)} maxLength={3000} disabled={!canWrite}/>
          <label htmlFor="csv">Dataset (CSV text)</label><textarea id="csv" required minLength={3} maxLength={250000} className="code-input" rows={6} value={csv} onChange={e => setCSV(e.target.value)} disabled={!canWrite}/>
          <div className="helper">{canWrite ? hasDisabledAgent ? 'A required agent is disabled. Ask your operator to enable it before starting a workflow.' : plannerMode === 'offline' ? 'Demo pipeline: inspect CSV → summarize → verify. No LLM key required.' : 'The planner proposes a DAG; registered CSV agents execute and verify it locally.' : 'Viewer access lets you inspect workflows. Ask the owner for member access to run one.'}</div>
          <button className="primary" disabled={!canWrite || busy || hasDisabledAgent || (plannerMode === 'llm' && !plannerConfiguration?.llm_available)}>{busy ? 'Please wait…' : plannerMode === 'llm' ? 'Plan and execute' : 'Execute workflow'} <Play size={16}/></button>
        </form>
      </section>
      <section className="panel results"><div className="panel-head"><div><div className="eyebrow">02 / EXECUTION</div><h2>Agent execution graph</h2></div><button className="iconbutton" aria-label="Refresh workflows" onClick={() => setRefreshKey(key => key + 1)}><RefreshCw size={17}/></button></div>
        {!workflow ? <div className="empty">Submit or select a workflow to view its execution.</div> : <>
          <div className="statusbar"><span className={`status ${workflow.status}`}>{workflow.status.toUpperCase()}</span><span>{workflow.tasks.length ? `${succeeded} / ${workflow.tasks.length} tasks completed` : workflow.status === 'waiting_approval' ? 'Suspended for human review' : 'Awaiting validated plan'}</span></div>
          <div className="track"><div style={{width: `${workflow.tasks.length ? succeeded / workflow.tasks.length * 100 : 0}%`}}/></div>
          {workflow.status === 'waiting_approval' && <div className="approval-banner"><AlertTriangle size={18}/><div><strong>Execution suspended for human review</strong><span>A sensitive external write action requires approval before this workflow can proceed. Inspect and authorize the request below.</span></div></div>}
          {workflow.tasks.length === 0 ? <div className="empty">{['queued', 'planning'].includes(workflow.status) ? 'Preparing a validated execution plan…' : workflow.status === 'waiting_approval' ? 'Workflow paused awaiting human authorization.' : 'No executable plan was accepted.'}</div> : <div className="graph">{workflow.tasks.map((t, index) => <React.Fragment key={t.id}>{index > 0 && <div className="connector">↓</div>}<div className={`node ${t.status}`}><div className="node-icon">{t.status === 'succeeded' ? <CheckCircle2 size={19}/> : t.status === 'failed' ? <XCircle size={19}/> : <Clock3 size={19}/>}</div><div><strong>{t.name.toUpperCase()}</strong><small>{t.agent} · v{t.agent_version} · {t.routing_decision ? `${t.routing_decision.mode} score ${t.routing_decision.score.toFixed(3)}` : 'fixed route'} · {t.execution_attempts.length ? `attempt ${t.execution_attempts.length} ${t.execution_attempts[t.execution_attempts.length - 1].status}` : `${t.attempts} attempts`}</small></div><span className="node-status">{t.status}</span></div></React.Fragment>)}</div>}
          {workflow.planning && <div className="planning-summary"><strong>{workflow.planning.provider === 'offline' ? 'Offline pipeline' : `OpenAI planning · ${workflow.planning.model || 'configured model'}`}</strong><span>Planning: {workflow.planning.status} · {workflow.planning.attempts.length} provider {workflow.planning.attempts.length === 1 ? 'call' : 'calls'}</span><span>{workflow.planning.accounted_tokens || 0} accounted tokens · Estimated cost ${Number(workflow.planning.accounted_cost_usd || 0).toFixed(6)}</span>{workflow.planning.attempts.some(attempt => ['reserved', 'indeterminate'].includes(attempt.status)) && <span className="helper">A reservation is retained because usage is pending or uncertain.</span>}{workflow.planning.error_code && <span className="error">{workflow.planning.error_code}</span>}</div>}
          {workflow.error && <p role="alert" className="error">{workflow.error}</p>}
          {canWrite && ['running', 'queued', 'planning', 'waiting_approval'].includes(workflow.status) && <button className="danger" disabled={busy} onClick={() => action(async () => {await mutate(`/workflows/${workflow.id}/cancel`);})}>Cancel workflow</button>}
        </>}
      </section></div>
      <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">RESTRICTED TOOLS / RESEARCH</div><h2>Search approved sources</h2></div><Activity size={18}/></div>
        <p className="helper">Enter up to {researchConfiguration?.max_urls || 3} HTTPS URLs on your operator’s exact domain allowlist. Redirects and private IPs are blocked. Retrieved page text is untrusted data, never an instruction.</p>
        {!researchConfiguration?.available && <p className="helper">Research is off. An operator must enable it and configure approved domains before any fetch can run.</p>}
        <form className="research-form" onSubmit={event => {event.preventDefault(); searchResearch();}}>
          <div><label htmlFor="research-query">Search terms</label><input id="research-query" required maxLength={200} value={researchQuery} onChange={event => setResearchQuery(event.target.value)} disabled={!canWrite || busy || !researchConfiguration?.available}/></div>
          <div><label htmlFor="research-urls">Approved source URLs, one per line</label><textarea id="research-urls" className="code-input" rows={3} required value={researchUrls} onChange={event => setResearchUrls(event.target.value)} disabled={!canWrite || busy || !researchConfiguration?.available}/></div>
          <button className="secondary" disabled={!canWrite || busy || !researchConfiguration?.available}>Search sources</button>
        </form>
        {researchResult && <div className="agent-catalog">{researchResult.sources.map(source => <article className="agent-card" key={source.id}>
          <div className="agent-card-head"><a href={source.url} target="_blank" rel="noreferrer noopener">{source.title || source.url}</a><span className={`availability ${source.status === 'fetched' ? 'ready' : 'offline'}`}>{source.status}</span></div>
          <small>{source.trust_level} · {source.fetched_at ? new Date(source.fetched_at).toLocaleString() : source.error_code} · SHA-256 {source.content_sha256 || 'unavailable'}</small>
          {source.excerpt && <p>{source.excerpt}</p>}
        </article>)}</div>}
      </section>
      <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">DATA ANALYSIS / CSV + XLSX</div><h2>Profile a dataset</h2></div><BarChart3 size={18}/></div>
        <p className="helper">Upload a CSV or XLSX file up to 5 MiB. Analysis is capped at 5,000 rows and 100 columns. Formulas are treated as text; macros are not supported.</p>
        <div className="dataset-upload"><label htmlFor="dataset-file">Dataset file</label><input id="dataset-file" type="file" accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={event => setDatasetFile(event.target.files?.[0] || null)} disabled={!canWrite || busy}/><button className="secondary" type="button" onClick={analyzeDataset} disabled={!canWrite || busy || !datasetFile}>Analyze dataset</button></div>
        {datasetAnalysis && <div className="dataset-result"><div className="dataset-summary"><strong>{datasetAnalysis.filename}</strong><span>{datasetAnalysis.row_count.toLocaleString()} rows · {datasetAnalysis.column_count} columns · {datasetAnalysis.file_format.toUpperCase()}</span><code>SHA-256 {datasetAnalysis.content_sha256}</code><span>{datasetAnalysis.metadata.formula_cells_treated_as_data} formula-like cells were kept as text.</span><a href={`${API}${datasetAnalysis.csv_export_url}`} download={`${datasetAnalysis.id}.csv`}>Download reproducible CSV</a></div>
          {datasetAnalysis.chart_artifacts.map(chart => <figure className="dataset-chart" key={chart.id}><figcaption>{chart.title}</figcaption><img src={`${API}/api/v1/data-analysis/${datasetAnalysis.id}/charts/${chart.id}.svg`} alt={chart.title}/></figure>)}
          <div className="dataset-columns">{datasetAnalysis.profile.columns.map(column => <article className="agent-card" key={column.name}><strong>{column.name}</strong><small>{column.inferred_type} · {column.missing_count} missing · {column.distinct_count} distinct</small>{column.statistics && <small>Mean {column.statistics.mean} · Min {column.statistics.min} · Max {column.statistics.max} · Sum {column.statistics.sum}</small>}{column.example && <small>Example: {column.example}</small>}</article>)}</div>
        </div>}
      </section>
      <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">CODE PROPOSAL / ISOLATED TEST</div><h2>Review a patch in the sandbox</h2></div><Code2 size={18}/></div>
        <p className="helper">Submit baseline files and a proposed replacement patch as JSON. Only the fixed Python checks below can run. The temporary container has no network or host mounts and is removed after the run.</p>
        {!sandboxConfiguration?.available && <p className="helper">Sandbox execution is off. Enable it only after configuring a dedicated Docker Engine with TLS and building the pinned runtime image.</p>}
        <div className="sandbox-form"><div><label htmlFor="sandbox-files">Baseline project files (JSON)</label><textarea id="sandbox-files" className="code-input" rows={9} value={sandboxFiles} onChange={event => setSandboxFiles(event.target.value)} disabled={!canWrite || busy}/></div><div><label htmlFor="sandbox-patch">Proposed patch files (JSON)</label><textarea id="sandbox-patch" className="code-input" rows={7} value={sandboxPatch} onChange={event => setSandboxPatch(event.target.value)} disabled={!canWrite || busy}/></div><div className="sandbox-actions"><label htmlFor="sandbox-check">Check to run</label><select id="sandbox-check" value={sandboxCheck} onChange={event => setSandboxCheck(event.target.value)} disabled={!canWrite || busy}><option value="pytest">pytest</option><option value="unittest">unittest</option><option value="compileall">compileall</option></select><button className="secondary" type="button" onClick={runSandbox} disabled={!canWrite || busy || !sandboxConfiguration?.available}>Run isolated check</button></div></div>
        {sandboxResult && <div className="sandbox-result"><div className="agent-card-head"><strong>Run {sandboxResult.id.slice(0, 8)}</strong><span className={`status ${sandboxResult.status}`}>{sandboxResult.status.toUpperCase()}</span></div><p className="helper">Network: {sandboxResult.artifact.isolation.network} · Host mounts: {sandboxResult.artifact.isolation.host_mounts}</p><h3>Proposed diff</h3><pre className="output">{sandboxResult.diff || 'No changes in the proposed patch.'}</pre>{sandboxResult.artifact.checks.map(check => <article className="agent-card" key={check.check}><strong>{check.check} · {check.status}{check.exit_code === null ? '' : ` · exit ${check.exit_code}`}</strong>{check.stdout && <pre className="output">{check.stdout}</pre>}{check.stderr && <pre className="output error">{check.stderr}</pre>}</article>)}</div>}
      </section>
      <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">DOCUMENT MEMORY / KNOWLEDGE RETRIEVAL</div><h2>Workspace document index</h2></div><Database size={18}/></div>
        <p className="helper">Upload reference documents (.txt, .md, .py, .csv, .pdf) up to 5 MiB. Files are partitioned into chunks with source offsets and deterministic vector embeddings. Content is strictly scoped to this workspace and treated as untrusted evidence.</p>
        <div className="dataset-upload">
          <label htmlFor="doc-file">Add reference file</label>
          <input id="doc-file" type="file" accept=".txt,.text,.md,.markdown,.csv,.tsv,.json,.py,.js,.ts,.jsx,.tsx,.html,.css,.yaml,.yml,.sql,.sh,.pdf" onChange={event => setDocUploadFile(event.target.files?.[0] || null)} disabled={!canWrite || busy}/>
          <button className="secondary" type="button" onClick={uploadDocument} disabled={!canWrite || busy || !docUploadFile}>Upload & index</button>
        </div>
        {documents.length > 0 && <table className="document-table">
          <thead><tr><th>DOCUMENT</th><th>FORMAT</th><th>SIZE</th><th>CHUNKS</th><th>STATUS</th><th>ACTIONS</th></tr></thead>
          <tbody>{documents.map(doc => <tr key={doc.id}>
            <td><strong>{doc.original_name}</strong><br/><code>SHA-256 {doc.sha256.slice(0, 12)}…</code></td>
            <td><code>{doc.mime_type}</code></td>
            <td>{(doc.size_bytes / 1024).toFixed(1)} KiB</td>
            <td><span className="score-pill">{doc.chunk_count} chunk{doc.chunk_count === 1 ? '' : 's'}</span></td>
            <td><span className={`availability ${doc.status === 'indexed' ? 'ready' : 'offline'}`}>{doc.status}</span></td>
            <td><div className="document-actions">{canWrite && <><button className="secondary" type="button" title="Re-index document" onClick={() => reindexDoc(doc.id)} disabled={busy}><RefreshCw size={13}/></button><button className="danger" style={{marginTop:0,padding:'7px 10px'}} type="button" title="Delete document" onClick={() => deleteDoc(doc.id)} disabled={busy}><Trash2 size={13}/></button></>}</div></td>
          </tr>)}</tbody>
        </table>}
        <form className="knowledge-search-form" onSubmit={event => {event.preventDefault(); searchKnowledge();}}>
          <div>
            <label htmlFor="knowledge-search">Query document memory</label>
            <input id="knowledge-search" required maxLength={500} placeholder="Search workspace documents by terms or semantic topic…" value={knowledgeQuery} onChange={event => setKnowledgeQuery(event.target.value)} disabled={busy}/>
          </div>
          <button className="secondary" disabled={busy || !knowledgeQuery.trim()}><Search size={14}/> Search memory</button>
        </form>
        {knowledgeResult && <div className="knowledge-results">
          <div className="panel-head" style={{marginBottom:10}}><strong>{knowledgeResult.total_matches} matched {knowledgeResult.total_matches === 1 ? 'chunk' : 'chunks'} for &ldquo;{knowledgeResult.query}&rdquo;</strong></div>
          {knowledgeResult.results.length === 0 ? <p className="muted">No relevant chunks found in this workspace’s indexed documents.</p> : knowledgeResult.results.map(chunk => <article className="agent-card" key={chunk.chunk_id}>
            <div className="agent-card-head">
              <div>
                <strong>{chunk.filename}</strong> <small style={{marginLeft:8}}>Chunk #{chunk.chunk_index} · Source char offset {chunk.source_offset}</small>
              </div>
              <div style={{display:'flex',gap:6,alignItems:'center'}}>
                <span className="untrusted-tag">untrusted evidence</span>
                <span className="score-pill">Relevance {(chunk.score * 100).toFixed(1)}%</span>
              </div>
            </div>
            <pre className="output" style={{marginTop:12,maxHeight:160}}>{chunk.content}</pre>
          </article>)}
        </div>}
      </section>
      <section className="panel lower">
        <div className="panel-head">
          <div>
            <div className="eyebrow">HUMAN-IN-THE-LOOP / WRITE GATING</div>
            <h2>Write tool policy & approvals</h2>
          </div>
          <div style={{display:'flex',gap:10,alignItems:'center'}}>
            {pendingApprovals.length > 0 && <span className="pending-badge">{pendingApprovals.length} pending review</span>}
            <Lock size={18}/>
          </div>
        </div>
        <p className="helper">
          PRD NFR-08: All external write tools (file export, patch application, configuration changes) require explicit human review. Tool parameters are fingerprinted using SHA-256 to guarantee tamper-resistance post-review. Approvals auto-expire in 24 hours.
        </p>

        <h3>Pending human approvals ({pendingApprovals.length})</h3>
        {pendingApprovals.length === 0 ? (
          <p className="muted">No write actions currently awaiting human review in this workspace.</p>
        ) : (
          <div className="approval-grid">
            {pendingApprovals.map(app => (
              <article className="approval-card pending" key={app.id}>
                <div className="approval-card-head">
                  <div className="approval-title">
                    <strong>{app.action_type}</strong>
                    <span className="effect-pill external_write">EXTERNAL WRITE</span>
                    <span className="status waiting_approval">AWAITING DECISION</span>
                  </div>
                  <small style={{color:'#f0b265'}}>Expires in ~{Math.max(0, Math.round((new Date(app.expires_at).getTime() - Date.now()) / 3600000))}h</small>
                </div>
                <p style={{margin:'4px 0',fontSize:12,color:'#cddbe8'}}>{app.description}</p>
                <div className="approval-meta">
                  <div>
                    <span>REQUESTED BY</span>
                    <code>{app.requester_email || app.requested_by}</code>
                  </div>
                  <div>
                    <span>SHA-256 FINGERPRINT</span>
                    <code>{app.details.arguments_hash ? `${app.details.arguments_hash.slice(0, 16)}…` : '—'}</code>
                  </div>
                  <div>
                    <span>APPROVAL ID</span>
                    <code>{app.id}</code>
                  </div>
                </div>
                <div>
                  <label style={{marginTop:4,marginBottom:6}}>SANITIZED PARAMETERS (VERIFIED)</label>
                  <pre className="output" style={{maxHeight:120}}>{JSON.stringify(app.details.sanitized_parameters || app.details.parameters || {}, null, 2)}</pre>
                </div>
                {canWrite ? (
                  <form className="approval-decision-form" onSubmit={e => e.preventDefault()}>
                    <input
                      placeholder="Optional decision reason or audit note…"
                      value={decisionReasons[app.id] || ''}
                      onChange={e => setDecisionReasons(prev => ({...prev, [app.id]: e.target.value}))}
                      disabled={busy}
                    />
                    <button
                      className="approve-btn"
                      type="button"
                      disabled={busy}
                      onClick={() => submitDecision(app.id, 'approved')}
                    >
                      <Check size={14}/> Approve write
                    </button>
                    <button
                      className="reject-btn"
                      type="button"
                      disabled={busy}
                      onClick={() => submitDecision(app.id, 'rejected')}
                    >
                      <X size={14}/> Reject write
                    </button>
                  </form>
                ) : (
                  <p className="helper" style={{margin:0}}>Viewer access cannot approve or reject actions. Member or owner role required.</p>
                )}
              </article>
            ))}
          </div>
        )}

        {pastApprovals.length > 0 && (
          <details className="execution-contracts" style={{marginTop:20}}>
            <summary>Approval decision history ({pastApprovals.length} recorded)</summary>
            <div className="approval-grid" style={{marginTop:12}}>
              {pastApprovals.slice(0, 10).map(app => (
                <article className={`approval-card ${app.status}`} key={app.id}>
                  <div className="approval-card-head">
                    <div className="approval-title">
                      <strong>{app.action_type}</strong>
                      <span className={`status ${app.status}`}>{app.status.toUpperCase()}</span>
                    </div>
                    <small>{app.decided_at ? new Date(app.decided_at).toLocaleString() : new Date(app.created_at).toLocaleString()}</small>
                  </div>
                  <div className="approval-meta">
                    <div>
                      <span>DECISION BY</span>
                      <code>{app.decider_email || app.decision_by || 'system (expired)'}</code>
                    </div>
                    <div>
                      <span>REASON</span>
                      <span>{app.reason || 'None specified'}</span>
                    </div>
                    <div>
                      <span>FINGERPRINT</span>
                      <code>{app.details.arguments_hash ? `${app.details.arguments_hash.slice(0, 16)}…` : '—'}</code>
                    </div>
                  </div>
                </article>
              ))}
            </div>
          </details>
        )}

        <div style={{marginTop:24,borderTop:'1px solid #233142',paddingTop:18}}>
          <div className="eyebrow">INTERACTIVE POLICY TESTER</div>
          <h3>Test gated tool execution</h3>
          <p className="helper">Demonstrates write gating: running an external write without prior approval immediately halts execution with HTTP 202 Waiting Approval and creates an approval record with a SHA-256 fingerprint.</p>
          <div className="tool-tester-grid">
            <div>
              <label htmlFor="test-tool-select">Registered tool</label>
              <select
                id="test-tool-select"
                value={testToolName}
                onChange={e => {
                  setTestToolName(e.target.value);
                  if (e.target.value === 'file.export_csv') {
                    setTestToolParams('{\n  "filename": "quarterly_summary.csv",\n  "content": "region,revenue\\nAPAC,54000\\nEMEA,48000"\n}');
                  } else if (e.target.value === 'code.apply_patch') {
                    setTestToolParams('{\n  "filepath": "main.py",\n  "patch": "def run():\\n    return True\\n"\n}');
                  } else {
                    setTestToolParams('{\n  "query": "orchestration systems architecture"\n}');
                  }
                }}
                disabled={!canWrite || busy}
              >
                {tools.map(t => (
                  <option key={t.tool} value={t.tool}>
                    {t.tool} · {t.effect.toUpperCase()} {t.requires_approval ? '(APPROVAL REQUIRED)' : '(UNRESTRICTED)'}
                  </option>
                ))}
              </select>
              <label htmlFor="test-tool-approval">Approval ID (leave blank to request new approval)</label>
              <input
                id="test-tool-approval"
                placeholder="Paste approval ID to execute authorized action…"
                value={testApprovalId}
                onChange={e => setTestApprovalId(e.target.value)}
                disabled={!canWrite || busy}
              />
            </div>
            <div>
              <label htmlFor="test-tool-params">Tool parameters (JSON)</label>
              <textarea
                id="test-tool-params"
                className="code-input"
                rows={6}
                value={testToolParams}
                onChange={e => setTestToolParams(e.target.value)}
                disabled={!canWrite || busy}
              />
              <button
                className="secondary"
                type="button"
                style={{marginTop:10,width:'100%'}}
                onClick={runToolTest}
                disabled={!canWrite || busy}
              >
                Execute gated tool
              </button>
            </div>
          </div>
          {testToolResult && (
            <div style={{marginTop:16}} className={testToolResult.status === 'waiting_approval' ? 'approval-banner' : 'dataset-summary'}>
              {testToolResult.status === 'waiting_approval' ? <AlertTriangle size={20}/> : <CheckCircle2 size={20}/>}
              <div>
                <strong>
                  {testToolResult.status === 'waiting_approval'
                    ? 'HTTP 202 WAITING_APPROVAL · Action Gated'
                    : 'HTTP 200 EXECUTED · Action Successful'}
                </strong>
                <span>{testToolResult.message || 'Execution completed.'}</span>
                {testToolResult.arguments_hash && <code>SHA-256: {testToolResult.arguments_hash}</code>}
                {testToolResult.approval_id && <span>Created Approval ID: <code>{testToolResult.approval_id}</code></span>}
                {testToolResult.result !== undefined && <pre className="output" style={{maxHeight:140,marginTop:8}}>{JSON.stringify(testToolResult.result, null, 2)}</pre>}
              </div>
            </div>
          )}
        </div>
      </section>
      <section className="panel lower">
        <div className="panel-head">
          <div>
            <div className="eyebrow">REPORTING & VERIFIABLE ARTIFACTS / US-07, FR-G05, FR-G06</div>
            <h2>Deliverable synthesis & 5-tier verification</h2>
          </div>
          <FileText size={18}/>
        </div>
        <p className="helper">
          PRD Section 10.3 & 10.5: Multi-format synthesis (Markdown, styled printable HTML, JSON) aggregating execution provenance, data profiling, external research, and document evidence. All deliverables undergo automated calibrated scoring against the 5-Tier Verification Rubric (PASS, PASS_WITH_WARNINGS, REVISION_REQUIRED, FAIL, INDETERMINATE) with explicit defect attribution and mandatory limitations disclosure (FR-V05).
        </p>

        <form className="synth-form" onSubmit={e => {e.preventDefault(); synthesizeReport();}}>
          <div>
            <label htmlFor="report-title">Deliverable title</label>
            <input
              id="report-title"
              value={reportTitle}
              onChange={e => setReportTitle(e.target.value)}
              placeholder="e.g. Executive Summary & Verification Report"
              disabled={!canWrite || busy}
            />
          </div>
          <button className="primary" type="submit" disabled={!canWrite || busy}>
            <FileText size={14}/> Synthesize deliverable
          </button>
        </form>

        <h3 style={{marginTop:24}}>Workspace artifacts ({artifacts.length})</h3>
        {artifacts.length === 0 ? (
          <p className="muted">No durable artifacts synthesized yet in this workspace.</p>
        ) : (
          <table className="document-table">
            <thead>
              <tr>
                <th>Artifact</th>
                <th>Type</th>
                <th>Size</th>
                <th>SHA-256 Fingerprint</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {artifacts.map(art => (
                <tr key={art.id}>
                  <td>
                    <strong>{art.name}</strong>
                    <br/>
                    <small className="muted">{art.mime_type}</small>
                  </td>
                  <td><code>{art.artifact_type}</code></td>
                  <td>{(art.size_bytes / 1024).toFixed(1)} KB</td>
                  <td><code title={art.content_sha256}>{art.content_sha256.slice(0, 16)}…</code></td>
                  <td>
                    <div className="document-actions">
                      <a
                        className="textbutton"
                        href={`${API}/api/v1${art.download_url}`}
                        target="_blank"
                        rel="noreferrer"
                        style={{display:'inline-flex',alignItems:'center',gap:4,textDecoration:'none'}}
                      >
                        <Download size={13}/> Download
                      </a>
                      {canWrite && (
                        <button
                          className="textbutton"
                          type="button"
                          disabled={busy}
                          onClick={() => runVerification(art.id)}
                        >
                          Verify rubric
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {verificationReport && (
          <div className="rubric-card">
            <div className="rubric-header">
              <div>
                <span className="eyebrow">5-TIER VERIFICATION RESULT</span>
                <h3 style={{margin:'4px 0'}}>Deliverable verification audit</h3>
              </div>
              <div style={{display:'flex',gap:12,alignItems:'center'}}>
                <span className={`rubric-badge ${verificationReport.decision}`}>
                  {verificationReport.decision.replaceAll('_', ' ')}
                </span>
                <div className="confidence-meter">
                  <small>CALIBRATED CONFIDENCE</small>
                  <b>{(verificationReport.confidence_score * 100).toFixed(0)}%</b>
                </div>
              </div>
            </div>

            <p style={{margin:0,fontSize:13,color:'#cddbe8'}}>{verificationReport.summary}</p>

            <div style={{marginTop:8}}>
              <span className="eyebrow">CALIBRATED CRITERIA EVALUATION</span>
              <div className="criteria-grid">
                {verificationReport.criteria.map(crit => (
                  <div className="criterion-card" key={crit.id}>
                    <div className="criterion-top">
                      <strong>{crit.criterion}</strong>
                      <span className={`rubric-badge ${crit.status === 'satisfied' ? 'PASS' : crit.status === 'warning' ? 'PASS_WITH_WARNINGS' : 'FAIL'}`}>
                        {crit.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="criterion-evidence">
                      <b>Evidence:</b> {crit.evidence}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {verificationReport.defects.length > 0 && (
              <div style={{marginTop:8}}>
                <span className="eyebrow" style={{color:'#f29a9a'}}>DEFECT ATTRIBUTION ({verificationReport.defects.length})</span>
                <div className="defects-list">
                  {verificationReport.defects.map((def, idx) => (
                    <div className="defect-card" key={idx}>
                      <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                        <strong>{def.category.toUpperCase()} · {def.description}</strong>
                        <span className={`severity-pill ${def.severity}`}>{def.severity}</span>
                      </div>
                      <small style={{color:'#bfa6aa'}}>
                        {def.repairable ? 'Repairable' : 'Irreparable defect'} · Recommended tasks: {def.suggested_task_ids.length > 0 ? def.suggested_task_ids.join(', ') : 'None'}
                      </small>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
      <div className="columns lower"><section className="panel"><div className="panel-head"><div><div className="eyebrow">03 / OBSERVABILITY</div><h2>Execution event log</h2></div><Activity size={18}/></div><div className="log">{events.length === 0 ? <div className="empty small">No events yet.</div> : events.map(e => <div className="logline" key={e.id}><span>{new Date(e.created_at).toLocaleTimeString()}</span><strong>{e.kind.replaceAll('_', ' ')}</strong><code>{JSON.stringify(e.details)}</code></div>)}</div></section>
      <section className="panel"><div className="panel-head"><div><div className="eyebrow">04 / OUTPUT</div><h2>Verified result</h2></div><ShieldCheck size={18}/></div><pre className="output">{workflow?.output ? JSON.stringify(workflow.output, null, 2) : 'Output will appear after verification.'}</pre></section></div>
      <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">05 / CAPABILITIES</div><h2>Agent registry</h2></div><BrainCircuit size={18}/></div>
        <p className="helper">Built-in CSV agents · Protocol 1.0 · Worker status updates automatically.</p>
        {agents.length === 0 ? <p className="muted">Loading agent availability…</p> : <div className="agent-catalog">{agents.map(agent => <article className="agent-card" key={agent.id}>
          <div className="agent-card-head"><strong>{({'csv_inspector': 'CSV inspection', 'csv_summarizer': 'Numeric summary', 'result_verifier': 'Result verification'} as Record<string, string>)[agent.id] || agent.id}</strong><span className={`availability ${agent.availability}`}>{agent.availability}</span></div>
          <code>{agent.capabilities.join(', ')}</code><small>Version {agent.version} · {agent.available_workers} compatible {agent.available_workers === 1 ? 'worker' : 'workers'}</small>
        </article>)}</div>}
        {agents.some(agent => agent.availability === 'offline') && <p className="helper">Offline agents have no recent compatible worker heartbeat. Submitted workflows wait for a worker.</p>}
      </section>
      {workflow?.plan && <details className="panel lower execution-contracts"><summary>Validated execution plan</summary><p className="helper">Final task: {workflow.plan.final_task_id}. Every task contributes to this verified result.</p><pre className="output">{JSON.stringify(workflow.plan, null, 2)}</pre></details>}
      {workflow && <details className="panel lower execution-contracts"><summary>Execution contracts</summary><p className="helper">Versions and request IDs for the selected workflow.</p>{workflow.tasks.map(task => <div className="protocol-row" key={task.id}>
        <strong>{task.name}</strong><code>{task.capability || 'Legacy capability'} · agent {task.agent_version} · protocol {task.schema_version}</code>
        {task.routing_decision && <small>Route {task.routing_decision.mode} · score {task.routing_decision.score.toFixed(3)} · {task.routing_decision.eligible_candidate_count} eligible candidate(s) · {Object.entries(task.routing_decision.factors).map(([name, score]) => `${name} ${score.toFixed(2)}`).join(' · ')} · reported cost ${task.routing_decision.candidates.find(candidate => candidate.agent === task.agent)?.observations?.average_reported_cost_usd || '0'} USD across {task.routing_decision.candidates.find(candidate => candidate.agent === task.agent)?.observations?.completed_attempts || 0} attempts</small>}
        <small>{task.execution ? `Request ${task.execution.request_id}` : 'No versioned result recorded'}</small>{task.error_code && <span className="error">{task.error_code}</span>}
      </div>)}</details>}
      {workspace.role === 'owner' && <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">06 / WORKSPACE</div><h2>Manage members</h2></div><Users size={18}/></div>
        <form className="member-form" onSubmit={e => {e.preventDefault(); action(async () => {await mutate(`/workspaces/${workspace.id}/members`, {email: memberEmail, role: memberRole}); setMemberEmail('');});}}>
          <div><label htmlFor="member-email">Registered user’s email</label><input id="member-email" type="email" required value={memberEmail} onChange={e => setMemberEmail(e.target.value)}/></div>
          <div><label htmlFor="member-role">Role</label><select id="member-role" value={memberRole} onChange={e => setMemberRole(e.target.value as 'member' | 'viewer')}><option value="viewer">Viewer</option><option value="member">Member</option></select></div>
          <button className="secondary" disabled={busy}>Add or update member</button>
        </form><p className="helper">Members can launch and cancel workflows. Viewers can inspect results. Only the owner can manage access.</p>
        <div className="member-list">{members.map(member => <div className="member-row" key={member.user_id}><span>{member.email}</span><span className="role-label">{member.role}</span>{member.role !== 'owner' && <button className="textbutton" disabled={busy} onClick={() => action(async () => {await mutate(`/workspaces/${workspace.id}/members/${member.user_id}`, undefined, 'DELETE');})}>Remove</button>}</div>)}</div>
      </section>}
      </>}
    </main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<App/>);
