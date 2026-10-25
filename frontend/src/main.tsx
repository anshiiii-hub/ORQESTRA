import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Activity, BrainCircuit, CheckCircle2, Clock3, Layers, Play, ShieldCheck, XCircle, RefreshCw, KeyRound} from 'lucide-react';
import './style.css';

type Task={id:string;name:string;agent:string;status:string;dependencies:string[];attempts:number;result:unknown;error:string|null};
type Workflow={id:string;prompt:string;status:string;tasks:Task[];output:unknown;error:string|null};
type Event={id:number;kind:string;details:Record<string,unknown>;created_at:string};
const API=import.meta.env.VITE_API_URL || 'http://localhost:8000';
const demoCSV='product,sales,units\nFrames,2400,12\nSunglasses,1800,9\nLenses,1200,20';

function App(){
  const [apiKey,setApiKey]=useState(localStorage.getItem('orchestra_demo_key')||'');
  const [prompt,setPrompt]=useState('Inspect the sales dataset, compute a numeric summary and verify the results.');
  const [csv,setCSV]=useState(demoCSV);
  const [workflows,setWorkflows]=useState<{id:string;prompt:string;status:string}[]>([]);
  const [selected,setSelected]=useState('');
  const [workflow,setWorkflow]=useState<Workflow|null>(null);
  const [events,setEvents]=useState<Event[]>([]);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const request=async(path:string,init:RequestInit={})=>{
    const response=await fetch(`${API}${path}`,{...init,headers:{'Content-Type':'application/json','X-API-Key':apiKey,...init.headers}});
    const json=await response.json(); if(!response.ok)throw new Error(json.detail||`HTTP ${response.status}`); return json;
  };
  const refresh=async()=>{
    if(!apiKey)return;
    try{
      const list=await request('/api/v1/workflows');setWorkflows(list);
      if(selected){ const [w,e]=await Promise.all([request(`/api/v1/workflows/${selected}`),request(`/api/v1/workflows/${selected}/events`)]);setWorkflow(w);setEvents(e); }
      setMessage('');
    }catch(e){setMessage((e as Error).message)}
  };
  useEffect(()=>{refresh();const timer=setInterval(refresh,1800);return()=>clearInterval(timer)},[selected,apiKey]);
  const create=async()=>{setBusy(true);try{const data=await request('/api/v1/workflows',{method:'POST',body:JSON.stringify({prompt,csv_text:csv})});setSelected(data.workflow_id);await refresh();}catch(e){setMessage((e as Error).message)}finally{setBusy(false)}};
  const cancel=async()=>{if(!selected)return;try{await request(`/api/v1/workflows/${selected}/cancel`,{method:'POST'});await refresh()}catch(e){setMessage((e as Error).message)}};
  const saveKey=(key:string)=>{setApiKey(key);localStorage.setItem('orchestra_demo_key',key)};
  const succeeded=workflow?.tasks.filter(t=>t.status==='succeeded').length||0;
  return <div className="shell">
    <aside className="sidebar"><div className="brand"><div className="logo"><BrainCircuit size={23}/></div><div><b>ORCHESTRA <span>AI</span></b><small>ORCHESTRATION LAB</small></div></div>
      <div className="navitem active"><Layers size={18}/> Workflows</div><div className="navitem"><Activity size={18}/> Observability</div><div className="navitem"><ShieldCheck size={18}/> Verification</div>
      <div className="side-label">RECENT WORKFLOWS</div><div className="recent">{workflows.map(w=><button key={w.id} className={selected===w.id?'recent-btn current':'recent-btn'} onClick={()=>setSelected(w.id)}><span className="dot"/><div><strong>{w.prompt.slice(0,35)}</strong><small>{w.status} · {w.id.slice(0,8)}</small></div></button>)}</div><div className="sidefoot">Checkpoint 01 · Local development</div>
    </aside>
    <main><header><div><div className="eyebrow">WORKSPACE / CONTROL CENTER</div><h1>Workflow orchestration</h1><p>Build, execute and inspect multi-agent task pipelines.</p></div><div className="head-tag"><span className="pulse"/> Development environment</div></header>
      <div className="cards"><div className="metric"><small>TOTAL WORKFLOWS</small><b>{workflows.length}</b><span>Submitted executions</span></div><div className="metric"><small>COMPLETED</small><b>{workflows.filter(w=>w.status==='succeeded').length}</b><span>Verified end-to-end</span></div><div className="metric"><small>RUNNING</small><b>{workflows.filter(w=>w.status==='running'||w.status==='queued').length}</b><span>Active pipeline</span></div><div className="metric"><small>FAILED</small><b>{workflows.filter(w=>w.status==='failed').length}</b><span>Review execution logs</span></div></div>
      <div className="columns"><section className="panel"><div className="panel-head"><div><div className="eyebrow">01 / INPUT</div><h2>Launch a workflow</h2></div><Play size={18}/></div>
        <label><KeyRound size={14}/> Development API key</label><input type="password" value={apiKey} onChange={e=>saveKey(e.target.value)} placeholder="Paste API_KEY from .env"/>
        <label>Task instructions</label><textarea rows={3} value={prompt} onChange={e=>setPrompt(e.target.value)} maxLength={3000}/>
        <label>Dataset (CSV text)</label><textarea className="code-input" rows={6} value={csv} onChange={e=>setCSV(e.target.value)}/>
        <div className="helper">Demo pipeline: inspect CSV → summarize → verify. No LLM key required.</div>
        <button className="primary" onClick={create} disabled={!apiKey||busy}>{busy?'Submitting…':'Execute workflow'} <Play size={16}/></button>
        {message&&<p className="error">{message}</p>}
      </section>
      <section className="panel results"><div className="panel-head"><div><div className="eyebrow">02 / EXECUTION</div><h2>Agent execution graph</h2></div><button className="iconbutton" onClick={refresh}><RefreshCw size={17}/></button></div>
      {!workflow?<div className="empty">Submit or select a workflow to view its execution.</div>:<><div className="statusbar"><span className={`status ${workflow.status}`}>{workflow.status.toUpperCase()}</span><span>{succeeded} / {workflow.tasks.length} tasks completed</span></div><div className="track"><div style={{width:`${succeeded/workflow.tasks.length*100}%`}}/></div>
        <div className="graph">{workflow.tasks.map((t,i)=><React.Fragment key={t.id}>{i>0&&<div className="connector">↓</div>}<div className={`node ${t.status}`}><div className="node-icon">{t.status==='succeeded'?<CheckCircle2 size={19}/>:t.status==='failed'?<XCircle size={19}/>:<Clock3 size={19}/>}</div><div><strong>{t.name.toUpperCase()}</strong><small>{t.agent} · attempts {t.attempts}</small></div><span className="node-status">{t.status}</span></div></React.Fragment>)}</div>
        {workflow.status==='running'&&<button className="danger" onClick={cancel}>Cancel workflow</button>}
      </>}
      </section></div>
      <div className="columns lower"><section className="panel"><div className="panel-head"><div><div className="eyebrow">03 / OBSERVABILITY</div><h2>Execution event log</h2></div><Activity size={18}/></div><div className="log">{events.length===0?<div className="empty small">No events yet.</div>:events.map(e=><div className="logline" key={e.id}><span>{new Date(e.created_at).toLocaleTimeString()}</span><strong>{e.kind.replaceAll('_',' ')}</strong><code>{JSON.stringify(e.details)}</code></div>)}</div></section>
      <section className="panel"><div className="panel-head"><div><div className="eyebrow">04 / OUTPUT</div><h2>Verified result</h2></div><ShieldCheck size={18}/></div><pre className="output">{workflow?.output?JSON.stringify(workflow.output,null,2):'Output will appear after verification.'}</pre></section></div>
    </main></div>
}
createRoot(document.getElementById('root')!).render(<App/>);
