import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, BrainCircuit, CheckCircle2, Clock3, Layers, Play, ShieldCheck, XCircle, RefreshCw, LogOut, Users} from 'lucide-react';
import {ApiError, request} from './api';
import './style.css';

type Role = 'owner' | 'member' | 'viewer';
type Session = {user: {id: string; email: string}; csrf_token: string};
type Workspace = {id: string; name: string; role: Role};
type Member = {user_id: string; email: string; role: Role};
type Task = {id: string; name: string; agent: string; status: string; attempts: number};
type Workflow = {id: string; workspace_id: string; prompt: string; status: string; tasks: Task[]; output: unknown; error: string | null};
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
        if (details) {setWorkflow(details[0]); setEvents(details[1]);}
      } catch (error) {
        if (!controller.signal.aborted) {
          if (error instanceof ApiError && error.status === 404) {setRuns([]); setWorkflow(null); setEvents([]); setMembers([]);}
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
    if (!workspace || !canWrite) return;
    const data = await mutate<{workflow_id: string}>('/workflows', {workspace_id: workspace.id, prompt, csv_text: csv});
    setWorkflow(null); setEvents([]); setSelected(data.workflow_id);
  });
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
      <div className="sidefoot"><span className="account-email">{session.user.email}</span><button className="secondary signout" disabled={busy} onClick={() => action(async () => {await mutate('/auth/logout'); onSignedOut();})}><LogOut size={14}/> Sign out</button><small>Checkpoint 03 · Local development</small></div>
    </aside>
    <main><header><div><div className="eyebrow">{workspace?.name || 'YOUR WORKSPACE'} / CONTROL CENTER</div><h1>Workflow orchestration</h1><p>{workspace ? `${workspace.role} access · ${workspace.role === 'viewer' ? 'Inspect' : 'Run and inspect'} your team’s workflows.` : 'Create your first workspace to get started.'}</p></div><div className="head-tag"><span className="pulse"/> Signed in</div></header>
      {(message || parentMessage) && <p role="alert" className="error">{message || parentMessage}</p>}
      {!workspace ? <section className="panel"><h2>A place for your work</h2><p className="muted">Name your workspace using the form in the sidebar. Each workspace has its own workflows and members.</p></section> : <>
      <div className="cards">{[
        ['RECENT WORKFLOWS', runs.length, 'Latest 50 in this workspace'],
        ['COMPLETED', runs.filter(w => w.status === 'succeeded').length, 'Verified end-to-end'],
        ['RUNNING', runs.filter(w => ['running', 'queued'].includes(w.status)).length, 'Active pipeline'],
        ['FAILED', runs.filter(w => w.status === 'failed').length, 'Review execution logs']
      ].map(([label, value, description]) => <div className="metric" key={label}><small>{label}</small><b>{value}</b><span>{description}</span></div>)}</div>
      <div className="columns"><section className="panel"><div className="panel-head"><div><div className="eyebrow">01 / INPUT</div><h2>Launch a workflow</h2></div><Play size={18}/></div>
        <form onSubmit={e => {e.preventDefault(); create();}}>
          <label htmlFor="prompt">Task instructions</label><textarea id="prompt" required minLength={3} rows={3} value={prompt} onChange={e => setPrompt(e.target.value)} maxLength={3000} disabled={!canWrite}/>
          <label htmlFor="csv">Dataset (CSV text)</label><textarea id="csv" required minLength={3} maxLength={250000} className="code-input" rows={6} value={csv} onChange={e => setCSV(e.target.value)} disabled={!canWrite}/>
          <div className="helper">{canWrite ? 'Demo pipeline: inspect CSV → summarize → verify. No LLM key required.' : 'Viewer access lets you inspect workflows. Ask the owner for member access to run one.'}</div>
          <button className="primary" disabled={!canWrite || busy}>{busy ? 'Please wait…' : 'Execute workflow'} <Play size={16}/></button>
        </form>
      </section>
      <section className="panel results"><div className="panel-head"><div><div className="eyebrow">02 / EXECUTION</div><h2>Agent execution graph</h2></div><button className="iconbutton" aria-label="Refresh workflows" onClick={() => setRefreshKey(key => key + 1)}><RefreshCw size={17}/></button></div>
        {!workflow ? <div className="empty">Submit or select a workflow to view its execution.</div> : <>
          <div className="statusbar"><span className={`status ${workflow.status}`}>{workflow.status.toUpperCase()}</span><span>{succeeded} / {workflow.tasks.length} tasks completed</span></div>
          <div className="track"><div style={{width: `${succeeded / workflow.tasks.length * 100}%`}}/></div>
          <div className="graph">{workflow.tasks.map((t, index) => <React.Fragment key={t.id}>{index > 0 && <div className="connector">↓</div>}<div className={`node ${t.status}`}><div className="node-icon">{t.status === 'succeeded' ? <CheckCircle2 size={19}/> : t.status === 'failed' ? <XCircle size={19}/> : <Clock3 size={19}/>}</div><div><strong>{t.name.toUpperCase()}</strong><small>{t.agent} · attempts {t.attempts}</small></div><span className="node-status">{t.status}</span></div></React.Fragment>)}</div>
          {workflow.error && <p role="alert" className="error">{workflow.error}</p>}
          {canWrite && ['running', 'queued'].includes(workflow.status) && <button className="danger" disabled={busy} onClick={() => action(async () => {await mutate(`/workflows/${workflow.id}/cancel`);})}>Cancel workflow</button>}
        </>}
      </section></div>
      <div className="columns lower"><section className="panel"><div className="panel-head"><div><div className="eyebrow">03 / OBSERVABILITY</div><h2>Execution event log</h2></div><Activity size={18}/></div><div className="log">{events.length === 0 ? <div className="empty small">No events yet.</div> : events.map(e => <div className="logline" key={e.id}><span>{new Date(e.created_at).toLocaleTimeString()}</span><strong>{e.kind.replaceAll('_', ' ')}</strong><code>{JSON.stringify(e.details)}</code></div>)}</div></section>
      <section className="panel"><div className="panel-head"><div><div className="eyebrow">04 / OUTPUT</div><h2>Verified result</h2></div><ShieldCheck size={18}/></div><pre className="output">{workflow?.output ? JSON.stringify(workflow.output, null, 2) : 'Output will appear after verification.'}</pre></section></div>
      {workspace.role === 'owner' && <section className="panel lower"><div className="panel-head"><div><div className="eyebrow">05 / WORKSPACE</div><h2>Manage members</h2></div><Users size={18}/></div>
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
