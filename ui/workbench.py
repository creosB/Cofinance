"""Cofinance v2 read-only workbench. Run: python -S -E -B -m ui.workbench

Linear-inspired native CSS, not an official Linear component package.
Preserves v1. No external assets, credentials, model calls, writes or remote binding.
Trace producers: core.trace.run_traced_team / core.trace.run_traced_clock1.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.trace import TRACE_FORMAT, safe

ROOT = Path(__file__).resolve().parents[1]
MAX_TRACE_BYTES = 16 * 1024 * 1024

PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Cofinance | Execution</title>
<style>
/* Plan: real-codebase additive workbench, preserve dark identity. Native CSS inspired by
   Linear's calmer navigation and progressive disclosure. No official package is implied.
   Signature: one execution spine, with the policy result beside its measured model calls.
   DESIGN_VARIANCE=4; MOTION_INTENSITY=2; VISUAL_DENSITY=6. Marketing rules N/A. */
:root{--bg:oklch(0.192 0.004 90);--surface:oklch(0.216 0.004 90);--nav:oklch(0.172 0.004 90);--raised:oklch(0.247 0.005 90);--hover:oklch(0.28 0.005 90);--line:oklch(0.32 0.006 90);--text:oklch(0.925 0.005 90);--muted:oklch(0.72 0.009 90);--subtle:oklch(0.65 0.009 90);--accent:oklch(0.78 0.115 158);--on-accent:oklch(0.18 0.022 158);--warn:oklch(0.82 0.12 80);--error:oklch(0.77 0.12 25);--focus:oklch(0.84 0.09 158);--radius:6px;--font:system-ui,-apple-system,"Segoe UI",sans-serif;--mono:ui-monospace,"Cascadia Code",Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 var(--font)}button,input,select{font:inherit}button{color:inherit;cursor:pointer}button,input,select{border-radius:var(--radius)}button{border:1px solid transparent;background:transparent;padding:8px 12px;min-height:36px;text-align:left}button:hover{background:var(--hover)}button:active{transform:translateY(1px)}button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid var(--focus);outline-offset:3px}button[disabled]{opacity:.55;cursor:default}input,select{background:var(--surface);color:var(--text);border:1px solid var(--line);padding:8px 10px;min-height:38px}input::placeholder{color:var(--muted)}a{color:var(--accent)}h1,h2,h3,p{margin:0}h1{font-size:24px;line-height:1.3;font-weight:600;letter-spacing:-.035em}h2{font-size:18px;letter-spacing:-.02em;font-weight:600}h3{font-size:16px;font-weight:600}.muted{color:var(--muted)}.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}.small{font-size:14px}.hidden{display:none!important}.primary{background:var(--accent);color:var(--on-accent);font-weight:600}.primary:hover{filter:brightness(1.07);background:var(--accent)}.outline{border-color:var(--line)}.shell{display:grid;grid-template-columns:214px minmax(0,1fr);min-height:100dvh}.sidebar{background:var(--nav);padding:22px 14px;border-right:1px solid var(--surface);display:flex;flex-direction:column;position:sticky;top:0;height:100dvh}.brand{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:600;padding:0 8px 25px;letter-spacing:-.03em}.monogram{display:grid;place-content:center;width:27px;height:27px;background:var(--raised);border:1px solid var(--line);border-radius:var(--radius);color:var(--accent);font:600 17px var(--font)}.nav{display:grid;gap:4px}.nav button{color:var(--muted);width:100%;display:flex;justify-content:space-between}.nav button[aria-current=page]{color:var(--text);background:var(--raised)}.nav .key{font:14px var(--mono);color:var(--subtle)}.side-label{margin:30px 9px 10px;color:var(--subtle)}.run-list{display:grid;gap:5px;overflow:auto;max-height:55vh}.run-button{padding:10px 9px;width:100%;border:1px solid transparent}.run-button.selected{background:var(--surface);border-color:var(--line)}.run-button .title{display:block;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.run-button .meta{display:flex;gap:8px;margin-top:3px;color:var(--muted);font-size:14px}.side-foot{margin-top:auto;padding:22px 9px 0;color:var(--muted)}.connection{display:flex;align-items:center;gap:7px}.connection:before{content:"";width:6px;height:6px;background:var(--accent);border-radius:50%}.connection.error:before{background:var(--error)}.topbar{height:63px;padding:0 28px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--surface);gap:12px}.breadcrumbs{display:flex;gap:12px;align-items:center}.breadcrumbs span{color:var(--muted)}.top-actions{display:flex;align-items:center;gap:8px}.top-actions button{color:var(--muted)}.workspace{padding:30px 28px 24px;max-width:1800px;margin:auto}.page-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:24px;margin-bottom:23px}.page-heading p{margin-top:7px;color:var(--muted);max-width:70ch}.tag{display:inline-flex;align-items:center;gap:6px;padding:3px 7px;background:var(--raised);border-radius:4px;font-size:14px;white-space:nowrap;color:var(--muted)}.status-complete{color:var(--accent)}.status-error,.status-degraded,.status-fallback,.status-deferred{color:var(--warn)}.status-error{color:var(--error)}.status-running{color:var(--warn)}.summary{display:flex;gap:24px;align-items:center;flex-wrap:wrap;padding:15px 0 20px;margin-bottom:8px;color:var(--muted)}.summary strong{font-weight:500;color:var(--text);margin-right:5px}.toolbar{display:flex;align-items:end;gap:12px;padding:12px 0 18px}.field{display:grid;gap:6px}.field label{font-size:14px;color:var(--muted)}.search{flex:1;max-width:430px}.search input{width:100%}.filters{display:flex;flex-wrap:wrap;gap:5px}.filters button{color:var(--muted);padding:6px 10px}.filters button.active{background:var(--raised);color:var(--text);border-color:var(--line)}.execution{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:24px;align-items:start}.timeline-wrap{min-width:0}.timeline-header{display:grid;grid-template-columns:minmax(200px,1fr) 105px 100px;gap:12px;color:var(--subtle);padding:11px 12px 12px;border-bottom:1px solid var(--line)}.timeline-header span:last-child{text-align:right}.trace-row{width:100%;display:grid;grid-template-columns:minmax(200px,1fr) 105px 100px;gap:12px;align-items:center;min-height:61px;border-radius:0;border-bottom:1px solid var(--surface);padding:10px 12px}.trace-row:hover{background:var(--surface)}.trace-row[aria-pressed=true]{background:var(--raised);box-shadow:inset 0 0 0 1px var(--line);border-radius:var(--radius)}.step-main{display:flex;gap:10px;min-width:0;padding-left:calc(var(--depth)*17px)}.step-mark{margin-top:5px;flex:0 0 16px;height:16px;display:grid;place-items:center;border:1px solid var(--line);border-radius:4px;color:var(--muted);font-size:14px}.kind-agent .step-mark,.kind-run .step-mark{background:var(--raised);color:var(--text)}.kind-gate .step-mark{color:var(--accent)}.step-copy{min-width:0}.step-title{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:500}.step-meta{display:block;color:var(--muted);margin-top:2px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.step-time{text-align:right;color:var(--muted);font:14px var(--mono)}.inspector{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);position:sticky;top:18px;min-width:0;overflow:hidden}.inspector-head{padding:18px 18px 16px;display:flex;gap:10px;align-items:start;justify-content:space-between}.inspector-head button{padding:0 7px;min-height:28px;color:var(--muted)}.inspector-body{padding:0 18px 18px}.inspector .description{color:var(--muted);margin:12px 0 20px}.property{display:grid;grid-template-columns:90px minmax(0,1fr);gap:10px;padding:6px 0}.property .label{color:var(--muted)}.property .value{overflow-wrap:anywhere}.inspector details{margin-top:14px;border-top:1px solid var(--line);padding-top:13px}summary{cursor:pointer;color:var(--text);font-weight:500;padding:4px 0}.data-block{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.6 var(--mono);max-height:330px;overflow:auto;background:var(--nav);border-radius:var(--radius);padding:12px;margin:12px 0 0;color:var(--text)}.empty{padding:64px 24px;text-align:center;max-width:620px;margin:auto}.empty h2{margin-bottom:9px}.empty p{color:var(--muted);margin:0 auto 16px;max-width:55ch}.empty code{display:block;padding:14px;background:var(--surface);text-align:left;overflow:auto;border-radius:var(--radius)}.inline-notice{padding:12px 14px;background:var(--raised);border:1px solid var(--line);border-radius:var(--radius);color:var(--muted);margin-bottom:18px}.inline-notice strong{color:var(--text);font-weight:500}.notice-error{color:var(--error)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}th{font-weight:500;color:var(--muted);padding:14px 12px;border-bottom:1px solid var(--line)}td{padding:16px 12px;border-bottom:1px solid var(--surface);vertical-align:top}td .muted{display:block;margin-top:3px}td:first-child{max-width:450px;overflow-wrap:anywhere}.section{margin-top:26px}.section h2{margin-bottom:12px}.provenance{max-width:75ch;color:var(--muted)}.provenance p{margin:10px 0}.link-button{color:var(--accent)}.skeleton{height:60px;border-bottom:1px solid var(--surface);position:relative}.skeleton:before{content:"";position:absolute;left:14px;top:20px;width:55%;height:13px;background:var(--raised);border-radius:3px}.error-bar{margin-bottom:15px;padding:10px 14px;border:1px solid var(--error);border-radius:var(--radius);color:var(--error)}.footer-note{padding-top:20px;color:var(--subtle)}dialog{background:var(--surface);color:var(--text);border:1px solid var(--line);border-radius:var(--radius);width:min(600px,90vw);padding:18px}dialog::backdrop{background:oklch(0.1 0.002 90 / .65)}dialog input{width:100%;margin:12px 0}dialog button{display:block;width:100%;margin-top:5px}.dialog-head{display:flex;align-items:center;justify-content:space-between}.dialog-head button{width:auto;margin:0}.palette-results{max-height:350px;overflow:auto}.mobile-title{display:none}
@media(min-width:1700px){.execution{grid-template-columns:minmax(0,1fr) 420px}}@media(max-width:1170px){.execution{grid-template-columns:minmax(0,1fr) 310px;gap:16px}.workspace{padding:24px 20px}.shell{grid-template-columns:184px minmax(0,1fr)}.sidebar{padding:20px 10px}.trace-row,.timeline-header{grid-template-columns:minmax(140px,1fr) 90px 76px;gap:8px}.toolbar{align-items:start;flex-direction:column}.search{width:100%;max-width:none}}@media(max-width:900px){.execution{grid-template-columns:1fr}.inspector{position:static}.sidebar{position:static;height:auto;min-height:100dvh}.timeline-header,.trace-row{grid-template-columns:minmax(140px,1fr) 90px 76px}.topbar{padding:0 20px}.breadcrumbs span{display:none}.summary{gap:15px}}/* Product UI: restrained dark palette, no decorative motion. Marketing checks N/A. */
main,.workspace,.execution,.page-heading>div,.topbar,.top-actions{min-width:0}
.inspector-head h2{min-width:0;overflow-wrap:anywhere}.inspector-head button{flex-shrink:0}
.decision-summary{margin:18px 0}.decision-summary h3{margin-bottom:10px}.decision-headline{margin-bottom:12px;overflow-wrap:anywhere;color:var(--muted)}
.score-table{width:100%;font-size:14px}.score-table th,.score-table td{padding:8px 4px;overflow-wrap:anywhere}.score-table th:first-child{width:42%}.score-table td:not(:first-child){font-family:var(--mono)}
.decision-outcome{display:flex;gap:10px;align-items:baseline;margin-bottom:10px}.decision-outcome strong{color:var(--accent)}.decision-explainer{color:var(--muted);margin:10px 0}.evidence-link{overflow-wrap:anywhere;display:inline-block;min-height:32px;margin-top:8px}
.refresh-state{min-height:24px;color:var(--muted)}.refresh-state button{padding:4px 8px}.mobile-title{white-space:nowrap}
@media(max-width:620px){
 .shell{display:block;width:100%}.sidebar{display:none}.mobile-title{display:inline}
 .topbar{height:auto;min-height:58px;display:grid;grid-template-columns:minmax(0,1fr);padding:12px 16px;gap:10px}
 .breadcrumbs #view-name{display:none}.top-actions{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;width:100%}
 .top-actions .mono,.top-actions .refresh-label{display:none}.top-actions button,.top-actions select{min-height:44px;max-width:100%}
 .workspace{padding:22px 16px}.page-heading{gap:10px;flex-wrap:wrap}.page-heading>div{flex:1 1 210px}.page-heading h1{font-size:21px;overflow-wrap:anywhere}.page-heading p{max-width:36ch}
 .timeline-header,.trace-row{grid-template-columns:minmax(0,1fr) 77px 66px;gap:6px;padding-left:6px;padding-right:6px}
 .step-main{padding-left:calc(min(var(--depth),2)*8px);gap:6px}.step-mark{display:none}.step-meta{max-width:100%}
 .filters{gap:2px}.filters button{padding:7px 9px;min-height:44px}.summary{gap:12px}.toolbar{min-width:0}.search{min-width:0}.search input{min-width:0}
 .inspector{scroll-margin-top:12px;max-width:100%}.inspector-head button,summary{min-height:44px}.data-block{max-width:100%}.tag{padding:3px 6px}.page-heading .tag{white-space:normal}
 .trace-row{min-height:64px}.table-wrap{max-width:100%;min-width:0}.table-wrap table{min-width:560px}dialog{width:calc(100% - 24px);max-width:calc(100% - 24px)}
 .evidence-link{min-height:44px}.refresh-state button{min-height:44px}
}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important;scroll-behavior:auto!important}button:active{transform:none}}
</style></head><body>
<div class="shell"><aside class="sidebar" aria-label="Workspace navigation"><div class="brand"><span class="monogram" aria-hidden="true">C</span>Cofinance</div><nav class="nav"><button data-view="execution" aria-current="page">Execution <span class="key">1</span></button><button data-view="models">Models <span class="key">2</span></button><button data-view="dataset">Dataset <span class="key">3</span></button></nav><div class="side-label">Recorded runs</div><div id="run-list" class="run-list"><p class="muted">Loading local runs...</p></div><div class="side-foot"><div id="connection" class="connection">Local viewer</div><div class="small muted" style="margin-top:8px">Read-only. No remote services.</div></div></aside>
<main><header class="topbar"><div class="breadcrumbs"><strong class="mobile-title">Cofinance</strong><span>Workspace</span><span aria-hidden="true">/</span><strong id="view-name">Execution</strong></div><div class="top-actions"><select id="mobile-nav" class="hidden" aria-label="Navigate"><option value="execution">Execution</option><option value="models">Models</option><option value="dataset">Dataset</option></select><button id="refresh"><span class="refresh-label">Refresh</span><span class="mobile-title">Refresh</span></button><button id="palette-open" class="outline" aria-keyshortcuts="Control+k Meta+k">Find <span class="mono">Ctrl K</span></button></div></header>
<div class="workspace"><div id="errors" role="alert"></div><section id="execution-view"><div class="page-heading"><div><h1 id="run-title">Execution</h1><p id="run-description">Every model call, handoff, and policy decision in one place.</p></div><span id="run-badge" class="tag">Read-only trace</span></div><div id="run-summary" class="summary"></div><div id="mode-notice"></div><div class="toolbar"><div class="field search"><label for="search">Search this run</label><input id="search" type="search" placeholder="Agent, model, headline, or reason" autocomplete="off" aria-keyshortcuts="/"></div><div class="filters" role="group" aria-label="Filter execution steps"><button data-filter="all" class="active" aria-pressed="true">All steps</button><button data-filter="model" aria-pressed="false">Models</button><button data-filter="decision" aria-pressed="false">Decisions</button><button data-filter="agent" aria-pressed="false">Agents</button><button data-filter="tool" aria-pressed="false">Tools</button><button data-filter="gate" aria-pressed="false">Gates</button></div></div>
<div class="execution"><div class="timeline-wrap"><div class="timeline-header"><span id="steps-heading">Execution steps</span><span>State</span><span>Duration</span></div><div id="timeline" aria-label="Recorded execution steps"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div></div><aside id="inspector" class="inspector" aria-label="Step inspector" aria-live="polite"><div class="inspector-head"><h2>Inspect a step</h2></div><div class="inspector-body"><p class="description">Select a row to see its model, evidence, output, or gate outcome.</p><p class="muted">Use J and K to move between steps.</p></div></aside></div><p class="footer-note">Observed execution only. Timings include wrapper overhead. No private chain-of-thought.</p></section>
<section id="models-view" class="hidden"><div class="page-heading"><div><h1>Models in this run</h1><p>Observed calls only. Roles do not imply a particular model.</p></div><button id="back-execution" class="outline">View execution</button></div><div id="model-content"></div></section>
<section id="dataset-view" class="hidden"><div class="page-heading"><div><h1>Qualification dataset</h1><p>Evidence collection is not model evaluation.</p></div><span class="tag">Local corpus</span></div><div id="dataset-content"></div></section>
</div></main></div>
<dialog id="palette" aria-labelledby="palette-title"><div class="dialog-head"><h2 id="palette-title">Find a run</h2><button id="palette-close" aria-label="Close finder">Close</button></div><label for="palette-search" class="muted">Run title or status</label><input id="palette-search" type="search" autocomplete="off" placeholder="Search recorded runs"><div id="palette-results" class="palette-results"></div></dialog>
<script>
'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={runs:[],run:null,selected:null,filter:'all',query:'',view:'execution',visible:[],corpus:null,runListKey:'',generation:0,polling:false};
const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const word=s=>String(s||'unknown').replaceAll('_',' ');
const ms=v=>v===null||v===undefined?'Not recorded':v>=1000?(v/1000).toFixed(2)+' s':v.toFixed(1)+' ms';
const when=s=>{const d=new Date(s);return isNaN(d)?'Unknown time':d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});};
const status=s=>`<span class="status-${['complete','error','degraded','fallback','running','deferred'].includes(s)?s:'unknown'}">${e(word(s))}</span>`;
const json=v=>JSON.stringify(v,null,2)??'Not recorded';
function err(message){$('#errors').innerHTML=message?`<div class="error-bar">${e(message)} <button id="retry" class="outline">Retry</button></div>`:'';if($('#retry'))$('#retry').onclick=()=>refresh(true);$('#connection').classList.toggle('error',!!message);$('#connection').textContent=message?'Connection interrupted':'Local viewer';}
async function api(path){const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),8000);try{const r=await fetch(path,{cache:'no-store',credentials:'omit',signal:abort.signal});if(!r.ok)throw new Error(r.status===404?'Recorded run no longer exists.':'Local viewer request failed.');return await r.json();}finally{clearTimeout(timer);}}
function setView(view){state.view=view;['execution','models','dataset'].forEach(v=>$('#'+v+'-view').classList.toggle('hidden',v!==view));$$('[data-view]').forEach(b=>b.setAttribute('aria-current',b.dataset.view===view?'page':'false'));$('#view-name').textContent=word(view).replace(/^./,c=>c.toUpperCase());$('#mobile-nav').value=view;if(view==='models')renderModels();if(view==='dataset')renderDataset();}
function renderRuns(){const list=$('#run-list'),key=json([state.run?.id,state.runs.map(r=>[r.id,r.title,r.status,r.mode])]);if(key===state.runListKey)return;state.runListKey=key;const focused=document.activeElement?.dataset.run,top=list.scrollTop;list.innerHTML=state.runs.length?state.runs.map(r=>`<button class="run-button ${state.run?.id===r.id?'selected':''}" data-run="${e(r.id)}" title="${e(r.title)}"><span class="title">${e(r.title)}</span><span class="meta">${status(r.status)}<span>${e(r.mode)}</span></span><span class="muted">${e(when(r.started_at))}</span></button>`).join(''):'<p class="muted">No recorded runs yet.</p>';$$('[data-run]').forEach(b=>b.onclick=()=>loadRun(b.dataset.run));list.scrollTop=top;if(focused)$('[data-run="'+focused+'"]')?.focus({preventScroll:true});}
function flatten(spans){const byParent=new Map(),ids=new Set(spans.map(s=>s.id));for(const s of spans){const p=ids.has(s.parent_id)?s.parent_id:null;if(!byParent.has(p))byParent.set(p,[]);byParent.get(p).push(s);}const out=[],seen=new Set();function walk(parent,depth){for(const s of byParent.get(parent)||[]){if(seen.has(s.id))continue;seen.add(s.id);out.push({...s,depth});if(depth<30)walk(s.id,depth+1);}}walk(null,0);for(const s of spans)if(!seen.has(s.id))out.push({...s,depth:0});return out;}
function renderRun(){const r=state.run;if(!r){$('#run-title').textContent='Execution';$('#run-summary').innerHTML='';$('#mode-notice').innerHTML='';$('#timeline').innerHTML='<div class="empty"><h2>No recorded runs</h2><p>Start a traced backend run on your PC. This viewer never starts a model or changes a watchlist.</p><code>python -S -E -B -m ui.workbench</code></div>';return;}$('#run-title').textContent=r.title;$('#run-description').textContent='Trace the evidence, model calls, and rules behind this run.';$('#run-badge').innerHTML=status(r.status);const models=r.spans.filter(s=>s.kind==='model'),gates=r.spans.filter(s=>s.kind==='gate'),issues=r.spans.filter(s=>['error','fallback'].includes(s.status));$('#run-summary').innerHTML=`<span><strong>${e(ms(r.duration_ms))}</strong> total</span><span><strong>${models.length}</strong> model calls</span><span><strong>${gates.length}</strong> gate checks</span><span><strong>${issues.length}</strong> error / fallback steps</span><span>${e(when(r.started_at))}</span>`;let notice=r.mode==='fixture'?'<strong>Fixture run.</strong> Model and tool outputs are test stubs, not live measurements.':r.mode==='replay'?'<strong>Diagnostic replay.</strong> Recorded source data, real calls where identified. Fresh in-memory signal store; no alerts or watchlist writes.':'<strong>Live trace.</strong> Actual executed calls; this viewer is read-only.';if(r.storage_errors||r.dropped_spans)notice+=` Trace incomplete: ${r.storage_errors||0} storage errors, ${r.dropped_spans||0} omitted steps.`;$('#mode-notice').innerHTML=`<div class="inline-notice">${notice}</div>`;renderTimeline();renderInspector();if(state.view==='models')renderModels();}
function renderTimeline(){if(!state.run)return;const focused=document.activeElement?.dataset.span;const q=state.query.toLowerCase();const rows=flatten(state.run.spans).filter(s=>(state.filter==='all'||s.kind===state.filter||(state.filter==='decision'&&s.kind==='route'))&&(!q||json(s).toLowerCase().includes(q)));state.visible=rows;$('#steps-heading').textContent=`Execution steps (${rows.length})`;$('#timeline').innerHTML=rows.length?rows.map(s=>`<button class="trace-row kind-${e(s.kind)}" data-span="${e(s.id)}" style="--depth:${Math.min(s.depth,6)}" aria-pressed="${s.id===state.selected}"><span class="step-main"><span class="step-mark" aria-hidden="true">${s.kind==='gate'?'=':s.kind==='model'?'M':s.kind==='decision'?'D':s.kind==='tool'?'T':s.kind==='agent'?'A':s.kind==='route'?'R':'·'}</span><span class="step-copy"><span class="step-title">${e(s.name)}</span><span class="step-meta">${e(s.model||s.actor)}${s.inputs?.title?' / '+e(s.inputs.title):''}</span></span></span><span>${status(s.status)}</span><span class="step-time mono">${e(ms(s.duration_ms))}</span></button>`).join(''):'<div class="empty"><h2>No matching steps</h2><p>Try a different term or choose All steps.</p><button id="clear-filters" class="outline">Clear filters</button></div>';$$('[data-span]').forEach(b=>b.onclick=()=>selectSpan(b.dataset.span));if(focused)$('[data-span="'+focused+'"]')?.focus({preventScroll:true});if($('#clear-filters'))$('#clear-filters').onclick=()=>{$('#search').value='';state.query='';setFilter('all');};}
function setFilter(f){state.filter=f;$$('[data-filter]').forEach(b=>{b.classList.toggle('active',b.dataset.filter===f);b.setAttribute('aria-pressed',String(b.dataset.filter===f));});renderTimeline();}
function selectSpan(id){state.selected=id;renderTimeline();renderInspector();if(mobile.matches)$('#inspector').scrollIntoView({block:'start',behavior:'instant'});}
function evidenceUrl(value){try{const u=new URL(value);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password?u.href:null;}catch{return null;}}
function decisionSummary(s){
 const out=s.outputs||{},input=s.inputs||{},j=out.judgment||(typeof out.material==='number'?out:null),limits=out.thresholds||{};
 const explanations={below_digest_threshold:'The materiality score stayed below the recorded digest threshold.',material_not_urgent:'Eligible for a digest, but did not meet both alert thresholds.',material_and_urgent:'Met both alert thresholds; any alert is only a diagnostic outcome in replay.',stale:'Skipped because the item exceeded the age limit.',duplicate:'Skipped because this item was already seen.',duplicate_title:'Skipped because the headline was already judged.',primary_only:'Only the primary judge ran; the cascade did not call its adjudicator.',adjudicator_down:'The adjudicator failed. Alert eligibility was capped; a single judge cannot alert.',adjudicated:'The adjudicator supplied the final judgment for this gray-zone item.',alert_agreement:'Both judges were required. The lower materiality and urgency scores were used.'};
 const reason=out.reason||out.route,gate=!!out.action,rows=[];
 const number=value=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(4):'Not recorded';
 if(j){rows.push(['Materiality',number(j.material),typeof limits.digest_p==='number'?'Digest ≥ '+number(limits.digest_p):'Not recorded']);rows.push(['Urgency',number(j.urgent),typeof limits.alert_urgent_p==='number'?'Alert ≥ '+number(limits.alert_urgent_p):'Not recorded']);if(typeof limits.alert_p==='number')rows.push(['Alert materiality',number(j.material),'Alert ≥ '+number(limits.alert_p)]);}
 let text=explanations[reason]||'';
 if(String(reason).startsWith('alert_downgraded:'))text='The model met alert lines, but Python downgraded it: '+word(String(reason).split(':')[1])+'.';
 if(String(reason).startsWith('classifier_error:'))text='Classification failed. The item was deferred, never promoted to an alert.';
 const passed=typeof out.passed==='boolean'?out.passed:typeof out.faithful_synthesis==='boolean'?out.faithful_synthesis:null;
 const title=out.title||input.title,link=evidenceUrl(out.url||input.url);
 if(!j&&!reason&&passed===null&&!title)return '';
 return `<section class="decision-summary" aria-label="Decision explanation">${title?`<p class="decision-headline">${e(title)}</p>`:''}${gate?`<div class="decision-outcome"><strong>${e(String(out.action).toUpperCase())}</strong><span class="muted">${e(out.ticker||input.ticker||'')}</span></div>`:''}${passed!==null?`<h3>${passed?'Number check passed':'Number check failed'}</h3><p class="decision-explainer">${passed?'The recorded number-faithfulness check passed. This does not verify every factual claim.':'The recorded number check failed; inspect the fallback and accepted output.'}</p>`:''}${reason?`<h3>${e(word(reason))}</h3>`:''}${text?`<p class="decision-explainer">${e(text)}</p>`:''}${rows.length?`<table class="score-table" aria-label="Recorded decision scores"><thead><tr><th>Check</th><th>Score</th><th>Policy line</th></tr></thead><tbody>${rows.map(r=>`<tr>${r.map(v=>`<td>${e(v)}</td>`).join('')}</tr>`).join('')}</tbody></table><p class="decision-explainer">Model scores are not calibrated probabilities.</p>`:''}${j?.direction?`<div class="property"><span class="label">Direction</span><span>${e(j.direction)}</span></div>`:''}${link?`<a class="evidence-link" href="${e(link)}" target="_blank" rel="noreferrer noopener">Open source evidence</a>`:''}</section>`;
}
function renderInspector(){
 const box=$('#inspector'),s=state.run?.spans.find(s=>s.id===state.selected);
 const keep=box.dataset.inspectedSpan===s?.id,opened=keep?[...box.querySelectorAll('details')].map(d=>d.open):null;
 const scrolls=keep?[...box.querySelectorAll('.data-block')].map(d=>d.scrollTop):[];
 const focused=keep&&box.contains(document.activeElement)?document.activeElement.id||document.activeElement.closest('details')?.dataset.section:null;
 if(!s){box.dataset.inspectedSpan='';box.innerHTML='<div class="inspector-head"><h2>Inspect a step</h2></div><div class="inspector-body"><p class="description">Select a row to see its model, evidence, output, or gate outcome.</p><p class="muted">Use J and K to move between steps.</p></div>';return;}
 box.dataset.inspectedSpan=s.id;
 const props=[['Actor',s.actor],['Kind',word(s.kind)],['Model',s.model||'Not a model call'],['State',word(s.status)],['Started','+'+ms(s.start_ms)],['Duration',ms(s.duration_ms)]];
 if(s.error_type)props.push(['Error',s.error_type]);const parent=state.run.spans.find(p=>p.id===s.parent_id);if(parent)props.push(['Parent',parent.name]);
 const summary=decisionSummary(s);
 box.innerHTML=`<div class="inspector-head"><h2>${e(s.name)}</h2><button id="inspector-close" aria-label="Close step inspector">${mobile.matches?'Back to steps':'Close'}</button></div><div class="inspector-body">${props.map(([k,v])=>`<div class="property"><span class="label">${e(k)}</span><span class="value ${k==='Model'?'mono':''}">${e(v)}</span></div>`).join('')}${summary}<details data-section="output" ${!summary?'open':''}><summary>Observed output</summary><pre id="output-data" class="data-block"></pre></details><details data-section="input"><summary>Input / policy</summary><pre id="input-data" class="data-block"></pre></details><p class="description">Bounded diagnostic excerpts. Secret-key fields and tagged private thinking are omitted; this is not a comprehensive data-loss filter.</p></div>`;
 $('#output-data').textContent=json(s.outputs);$('#input-data').textContent=json(s.inputs);
 if(opened)[...box.querySelectorAll('details')].forEach((d,i)=>d.open=opened[i]);
 [...box.querySelectorAll('.data-block')].forEach((d,i)=>d.scrollTop=scrolls[i]||0);
 if(focused){const node=box.querySelector('#'+CSS.escape(focused))||box.querySelector('details[data-section="'+focused+'"] summary');node?.focus({preventScroll:true});}
 $('#inspector-close').onclick=()=>{const id=state.selected;state.selected=null;renderTimeline();renderInspector();const row=$('[data-span="'+id+'"]');row?.focus({preventScroll:true});if(mobile.matches)row?.scrollIntoView({block:'center'});};
}
function renderModels(){const calls=state.run?.spans.filter(s=>s.kind==='model')||[];if(!calls.length){$('#model-content').innerHTML='<div class="empty"><h2>No observed model calls</h2><p>Select a run with recorded model spans. Planned or downloaded models are not counted as executed.</p></div>';return;}const groups=new Map();for(const s of calls){const key=s.model||'Identity not supplied';if(!groups.has(key))groups.set(key,[]);groups.get(key).push(s);}$('#model-content').innerHTML=`<div class="inline-notice">${e(state.run.title)} <strong>${e(state.run.mode)}</strong>. Timing is observed wall time, not a throughput benchmark.</div><div class="table-wrap"><table><thead><tr><th>Model / backend</th><th>Roles</th><th>Calls</th><th>Total time</th><th>Errors</th></tr></thead><tbody>${[...groups].map(([name,items])=>`<tr><td><span class="mono">${e(name)}</span></td><td>${e([...new Set(items.map(s=>s.actor))].join(', '))}</td><td class="mono">${items.length}</td><td class="mono">${e(ms(items.reduce((t,s)=>t+(s.duration_ms||0),0)))}</td><td>${items.filter(s=>s.status==='error').length}</td></tr>`).join('')}</tbody></table></div><div class="section"><h2>Identity is explicit</h2><p class="provenance">Narrator backends report their own model_id. A missing identity stays unknown. Unused adjudicators do not appear as executed calls.</p></div>`;}
function renderDataset(){const c=state.corpus;if(!c){$('#dataset-content').innerHTML='<div class="empty"><h2>No local corpus found</h2><p>Collect source evidence with qualify.news_dataset before labelling.</p></div>';return;}$('#dataset-content').innerHTML=`<div class="summary"><span><strong>${e(c.rows)}</strong> unlabelled candidates</span><span><strong>${e(c.window_days)}</strong> day window</span><span>${e(when(c.collected_at))}</span></div><div class="inline-notice"><strong>Not benchmark-ready.</strong> Human labels and sufficient class, trap, and source coverage are still required.</div><div class="table-wrap"><table><thead><tr><th>Evidence source</th><th>Rows</th><th>Timestamp meaning</th></tr></thead><tbody>${Object.entries(c.by_source||{}).map(([name,n])=>`<tr><td>${e(word(name))}</td><td class="mono">${e(n)}</td><td>${name==='apple_newsroom'?'Feed update, publication unknown':name==='hackernews'?'HN submission, not article publication':'Source publication'}</td></tr>`).join('')}</tbody></table></div><div class="section"><h2>Human review</h2><p class="provenance">Open the local label.csv inside data/news_corpus_20261107. Labels remain blank until reviewed. Finalization keeps shared events together and refuses incomplete evidence or labels.</p><pre class="data-block">data/news_corpus_20261107/label.csv</pre></div><div class="section"><h2>Coverage limits</h2><div class="provenance"><p>Company announcements are issuer claims. HN is supplementary discussion. Regulatory coverage is not connected, and Bitcoin currently has HN only.</p><p>Signal thresholds have not been calibrated on this corpus.</p></div></div>`;}
async function loadRun(id){const generation=++state.generation;try{const run=await api('/api/run?id='+encodeURIComponent(id));if(generation!==state.generation)return false;const same=state.run?.id===run.id;if(same&&state.run.revision===run.revision){err('');return true;}state.run=run;if(!same){state.selected=null;state.query='';$('#search').value='';state.filter='all';setFilter('all');}renderRuns();renderRun();err('');return true;}catch(ex){if(generation===state.generation)err(ex.name==='AbortError'?'Local viewer timed out.':ex.message);return false;}}
async function refresh(force=false){if(state.polling)return;state.polling=true;try{const [runs,corpus]=await Promise.all([api('/api/runs'),api('/api/corpus')]);const changed=json(corpus)!==json(state.corpus);state.runs=runs;state.corpus=corpus;renderRuns();if(state.view==='dataset'&&changed)renderDataset();let ok=true;if(!state.run&&runs.length)ok=await loadRun(runs[0].id);else if(state.run){const meta=runs.find(r=>r.id===state.run.id);if(!meta){err('Selected run is no longer available. Choose another recorded run.');return;}if(force||meta.revision!==state.run.revision)ok=await loadRun(state.run.id);}else renderRun();if(ok)err('');}catch(ex){err(ex.name==='AbortError'?'Local viewer timed out.':ex.message);}finally{state.polling=false;}}
function openPalette(){$('#palette-search').value='';renderPalette();$('#palette').showModal();$('#palette-search').focus();}
function renderPalette(){const q=$('#palette-search').value.toLowerCase(),runs=state.runs.filter(r=>(r.title+' '+r.status).toLowerCase().includes(q));$('#palette-results').innerHTML=runs.length?runs.map(r=>`<button class="outline" data-find="${e(r.id)}">${e(r.title)} <span class="muted">${e(r.mode)} / ${e(word(r.status))}</span></button>`).join(''):'<p class="muted">No matching runs.</p>';$$('[data-find]').forEach(b=>b.onclick=()=>{$('#palette').close();setView('execution');loadRun(b.dataset.find);});}
$$('[data-view]').forEach(b=>b.onclick=()=>setView(b.dataset.view));$$('[data-filter]').forEach(b=>b.onclick=()=>setFilter(b.dataset.filter));$('#search').oninput=ev=>{state.query=ev.target.value;renderTimeline();};$('#refresh').onclick=()=>refresh(true);$('#back-execution').onclick=()=>setView('execution');$('#mobile-nav').onchange=ev=>setView(ev.target.value);$('#palette-open').onclick=openPalette;$('#palette-close').onclick=()=>$('#palette').close();$('#palette-search').oninput=renderPalette;$('#palette-search').onkeydown=ev=>{if(ev.key==='Enter'){$('[data-find]')?.click();}};
document.addEventListener('keydown',ev=>{if((ev.ctrlKey||ev.metaKey)&&ev.key.toLowerCase()==='k'){ev.preventDefault();if(!$('#palette').open)openPalette();return;}if(ev.key==='Escape'&&$('#palette').open){ev.preventDefault();$('#palette').close();return;}const typing=/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)||$('#palette').open;if(typing)return;if(ev.key==='/'){ev.preventDefault();setView('execution');$('#search').focus();}else if(['1','2','3'].includes(ev.key))setView(['execution','models','dataset'][Number(ev.key)-1]);else if(['j','k'].includes(ev.key)&&state.visible.length){ev.preventDefault();const i=state.visible.findIndex(s=>s.id===state.selected),next=Math.max(0,Math.min(state.visible.length-1,i+(ev.key==='j'?1:-1)));selectSpan(state.visible[next].id);$(`[data-span="${state.visible[next].id}"]`)?.scrollIntoView({block:'nearest'});}else if(ev.key==='Escape'){state.selected=null;renderTimeline();renderInspector();}});
const mobile=matchMedia('(max-width:620px)');function mobileNav(){$('#mobile-nav').classList.toggle('hidden',!mobile.matches);}mobile.addEventListener('change',mobileNav);mobileNav();refresh();setInterval(()=>{if(!document.hidden)refresh();},2500);
</script></body></html>'''


def read_trace(directory: Path, run_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("invalid run")
    path = directory / (run_id + ".json")
    if path.is_symlink() or path.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("invalid trace size or link")
    run = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(run, dict) or run.get("format") != TRACE_FORMAT or run.get("id") != run_id
            or not isinstance(run.get("spans"), list) or not isinstance(run.get("title"), str)
            or run.get("mode") not in {"live", "fixture", "replay"}
            or run.get("status") not in {"running", "complete", "degraded", "error"}):
        raise ValueError("invalid trace")
    def duration(value):
        return value is None or (type(value) in (int, float) and 0 <= value < 1e12)
    if not duration(run.get("duration_ms")) or len(run["spans"]) > 2000:
        raise ValueError("invalid trace duration or span count")
    ids = set()
    for span in run["spans"]:
        if (not isinstance(span, dict) or not re.fullmatch(r"[0-9a-f]{32}", str(span.get("id", "")))
                or span["id"] in ids or not isinstance(span.get("name"), str)
                or span.get("kind") not in {"run", "route", "agent", "model", "decision", "tool", "gate", "handoff"}
                or span.get("status") not in {"running", "complete", "fallback", "error"}
                or not duration(span.get("start_ms")) or not duration(span.get("duration_ms"))):
            raise ValueError("invalid span")
        ids.add(span["id"])
    # Sanitize again at the read boundary; a file is data, never trusted HTML.
    result = {key: safe(value) for key, value in run.items() if key != "spans"}
    result["spans"] = [safe(span) for span in run["spans"][:2000] if isinstance(span, dict)]
    result["revision"] = path.stat().st_mtime_ns
    return result


def list_runs(directory: Path) -> list[dict]:
    runs = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)[:100]:
        try:
            run = read_trace(directory, path.stem)
        except (OSError, ValueError, TypeError):
            continue
        runs.append({key: run.get(key) for key in ("id", "title", "status", "mode", "started_at", "duration_ms", "revision")})
    return runs


def make_handler(trace_dir: Path, corpus_dir: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, status: int, payload, *, html: bool = False):
            body = payload.encode("utf-8") if html else json.dumps(payload, ensure_ascii=True, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if html:
                self.send_header("X-Cofinance-UI", hashlib.sha256(body).hexdigest())
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            parsed = urlsplit(self.path)
            # A clicked link from ClickUp is cross-site navigation, not an API read.
            # Permit only user-activated, top-level GET navigation to the UI shell.
            # Cross-site fetches, frames, and all API paths remain blocked.
            shell_navigation = (self.path == "/" and self.headers.get("Sec-Fetch-Mode") == "navigate"
                                and self.headers.get("Sec-Fetch-Dest") == "document"
                                and self.headers.get("Sec-Fetch-User") == "?1")
            cross_site = self.headers.get("Sec-Fetch-Site") == "cross-site"
            if host not in allowed or (cross_site and not shell_navigation):
                self.reply(403, {"error": "local origin required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + h for h in allowed}:
                self.reply(403, {"error": "local origin required"})
                return
            try:
                if parsed.path == "/" and not parsed.query:
                    self.reply(200, PAGE, html=True)
                elif parsed.path == "/api/runs" and not parsed.query:
                    self.reply(200, list_runs(trace_dir))
                elif parsed.path == "/api/run":
                    query = parse_qs(parsed.query)
                    if set(query) != {"id"} or len(query["id"]) != 1:
                        raise ValueError("invalid query")
                    self.reply(200, read_trace(trace_dir, query["id"][0]))
                elif parsed.path == "/api/corpus" and not parsed.query:
                    path = corpus_dir / "manifest.json"
                    if not path.exists():
                        self.reply(200, None)
                    else:
                        if path.is_symlink() or path.stat().st_size > 100_000:
                            raise ValueError("invalid manifest")
                        manifest = json.loads(path.read_text(encoding="utf-8"))
                        self.reply(200, {k: safe(manifest.get(k)) for k in ("rows", "window_days", "collected_at", "by_source", "by_ticker")})
                else:
                    self.reply(404, {"error": "not found"})
            except (OSError, ValueError, TypeError, KeyError):
                self.reply(404, {"error": "local record unavailable"})
    return Handler


def create_server(*, port: int = 8876, trace_dir: Path | None = None, corpus_dir: Path | None = None):
    return ThreadingHTTPServer(("127.0.0.1", port), make_handler(trace_dir or ROOT / "data/traces",
                                                              corpus_dir or ROOT / "data/news_corpus_20261107"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876)
    args = parser.parse_args()
    server = create_server(port=args.port)
    print(f"Cofinance local workbench: http://127.0.0.1:{server.server_port}", flush=True)
    print("UI fingerprint: " + hashlib.sha256(PAGE.encode("utf-8")).hexdigest()[:12], flush=True)
    print("Read-only viewer. Models run separately; no public binding.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
