"""Interactive v2 vertical slice. One bounded job at a time, explicit user writes,
real local routing and optional qualified narrator. No legacy Streamlit imports.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import dataclasses
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

from .chat_store import ChatStore, ACTIVE, valid_id
from .citations import cite
from .documents import select_excerpts
from .entities import extract_tickers
from .gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter, GlinerOutputError
from .local_models import LocalModels, MODEL_ID, ModelError, Cancelled, narrators
from .market import MarketTools
from .news_service import coverage_note, feed_tickers, news_health
from .memory import HybridMemory, LocalKeywordMemory, MemoryHit, context_from
# Aliased: chat_store.ACTIVE (job statuses) is a different thing; shadowing it broke Stop once (caught by tests).
from .memory_items import ACTIVE as ITEM_ACTIVE, FORGOTTEN as ITEM_FORGOTTEN, NOTE_LIMIT, PENDING as ITEM_PENDING, MemoryLedger
from .orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator, plain_text
from .signals import Item, Outcome, Judgment, Action
from .run_trace import TraceRecorder, run_traced_team

GLINER_REVISION="9d1bfb848cd16d93ee56ccbc59cd92bf0f284d90"
CHAT_FLOWS=(
    "open_conversation",
    "clarification",
    "single_pass_data_answer",
    "python_news_answer",
    "multi_agent_research",
    "document_research",
    "watchlist_add",
    "cancelled",
    "error",
)
UNIVERSE=set(DEFAULT_ALIASES.values())|{"SPY","QQQ","NFLX","INTC","COIN","PLTR","SOFI","F","BABA","RIVN","ADBE","ORCL","AVAX","ADA","DOGE","XRP","BNB","DOT","LINK"}


def _is_live_chat_trace(data):
    if not isinstance(data,dict) or data.get('mode')!='live':
        return False
    summary=data.get('summary')
    if isinstance(summary,dict) and summary.get('origin')=='chat':
        return True
    spans=data.get('spans') if isinstance(data.get('spans'),list) else []
    if any(isinstance(s,dict) and (str(s.get('name') or '').startswith('Clock 1') or s.get('name') in ('Judge headline','Judge served headlines')) for s in spans):
        return False
    return any(isinstance(s,dict) and (s.get('kind')=='route' or s.get('actor') in ('Local assistant','Routing executive','Data Analyst','Market Team Lead')) for s in spans)


def _research_flow(routed,result,trace):
    if routed.intent=='watchlist_add':
        return 'watchlist_add'
    if getattr(result,'document_report',None) is not None:
        return 'document_research'
    gates=[s for s in trace.data.get('spans',[]) if s.get('name')=='Synthesis faithfulness']
    out=gates[-1].get('outputs') if gates and isinstance(gates[-1].get('outputs'),dict) else {}
    method=out.get('method') or out.get('validation_reason')
    if method=='single_pass_data_answer':
        return 'single_pass_data_answer'
    if method in ('python_news_answer','python_digest','no_news') and getattr(result,'data_report',None) is None:
        return 'python_news_answer'
    return 'multi_agent_research'
OPEN_CHAT_SYSTEM=(
    "You are Cofinance, a concise, helpful local assistant running on the user's machine. "
    "Answer greetings, general questions, finance concepts, strategy ideas, and portfolio or tracker questions naturally in the user's language. "
    "Never invent live market prices, current quotes, dates, or recent breaking news. "
    "When the user wants live verified market data, charts, fundamentals, or news for a specific asset, tell them to name the company, ticker, ETF, index, commodity, or crypto and Cofinance will fetch it with live tools. "
    "Write plain concise prose with no markdown bloat, no reasoning tags, and no em dashes or en dashes."
)


class FactsOnly:
    disabled=True
    def complete(self,*args,**kwargs):return ""


class NewsReader:
    def __init__(self,path):self.path=Path(path);self.evidence=[]
    def get_gated_signals(self,ticker,limit=5):
        if not self.path.is_file():return []
        from .pregate import quarantine_reason
        try:
            db=sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",uri=True,timeout=2)
            try:
                rows=db.execute("SELECT ticker,source,url,title,body,published_at,action,reason,material,urgent,direction,direction_p FROM signals WHERE namespace='news' AND ticker=? AND expires_at>? ORDER BY created_at DESC LIMIT ?",(ticker,time.time(),min(5,limit))).fetchall()
            finally:db.close()
        except sqlite3.Error:return []
        out=[]
        for r in rows:
            item=Item(r[0],r[1] or '',r[2] or '',r[3] or '',r[4] or '',r[5] or 0)
            if quarantine_reason(item):continue
            if r[6] not in {'digest','alert'}:continue
            out.append(Outcome(item,Action(r[6]),r[7] or '',Judgment(r[8],r[9],r[10],r[11])))
            self.evidence.append({'title':item.title,'url':item.url,'source':item.source,'as_of':datetime.fromtimestamp(item.published_at,timezone.utc).isoformat() if item.published_at else 'Unknown'})
        return out


class LiveTrace(TraceRecorder):
    def __init__(self,*args,progress=None,**kwargs):
        self.progress_fn=progress;self.last_progress=0
        super().__init__(*args,**kwargs)
    def flush(self):
        super().flush()
        now=time.monotonic()
        if self.progress_fn and now-self.last_progress>.15 and self.data.get('spans'):
            running=[s for s in self.data['spans'] if s['status']=='running']
            if running:
                self.last_progress=now
                self.progress_fn(running[-1]['actor']+': '+running[-1]['name'])


class _NoTrace:
    """Trace stand-in for work that belongs to no chat run (the startup router warm-up)."""
    def span(self,*args,**kwargs):
        import contextlib
        return contextlib.nullcontext()


class ChatService:
    def __init__(self,repo,root=None,models=None,market_factory=MarketTools,router_factory=None,news_backend_factory=None,feed_fetch=None,
                 crowd_odds=None):
        self.repo=Path(repo);self.root=Path(root or self.repo/'data')
        # Crowd odds (Polymarket, read-only, approved by Baris 2026-11-10). Live only with the live market tools, so a test
        # with fake tools never touches the network unless it passes its own lookup. COFINANCE_CROWD_ODDS=off disables it.
        if crowd_odds is None and market_factory is MarketTools and os.environ.get('COFINANCE_CROWD_ODDS','on').strip().lower()!='off':
            from .crowd_odds import load_calibration,lookup
            calibration=load_calibration(self.repo/'docs'/'benchmarks'/'polymarket-calibration.json')
            crowd_odds=lambda tickers:lookup(tickers,calibration=calibration)
        self.crowd_odds=crowd_odds
        self._change_cond=threading.Condition();self._change_seq=0
        self.store=ChatStore(self.root/'chat_v2.db',on_change=self.notify_change);self.store.recover()
        self.models=models or LocalModels(self.root/'local_models',self.repo,on_change=self.notify_change)
        if models is not None and hasattr(self.models,'_on_change'):
            self.models._on_change=self.notify_change
        self.market_factory=market_factory;self.router_factory=router_factory
        self.news_backend_factory=news_backend_factory;self.feed_fetch=feed_fetch
        # MemoryPort. Hybrid (keyword + Hy-Memory lite, rank fusion) is the default since 2026-11-08: it passed the
        # pre-registered rule on a new set (22/24 vs keyword 17/24, data/_memory_compare_v2.py), approved by Baris.
        # COFINANCE_MEMORY=keyword or =hymemory overrides. A missing Hy-Memory install falls back to keyword.
        self.keyword_memory=LocalKeywordMemory(self.root/'memory_v2.db');self.memory=self.keyword_memory
        choice=os.environ.get('COFINANCE_MEMORY','hybrid').strip().lower()
        if choice in {'hymemory','hybrid'}:
            from .hymemory_port import HyMemoryLite
            hy=HyMemoryLite(self.repo,self.root)
            if hy.available():self.memory=HybridMemory(self.keyword_memory,hy) if choice=='hybrid' else hy
        # One identity and state per remembered item; every memory mutation takes self.ledger.lock.
        self.ledger=MemoryLedger(self.root/'memory_v2.db');self.semantic_retry_after=0.0
        self._semantic_inflight=set()  # run ids whose vector copy is being written outside the ledger lock
        self.lock=threading.RLock();self.active=None;self.events={};self.threads={};self.extractor=None;self.closed=False
        self.think_runs=set()  # run ids that asked for deep think; the flag is a per-answer choice, not stored on the job
        # One router start at a time: the startup warm-up and a first chat must never launch two GLiNER processes.
        self.router_lock=threading.Lock();self.warm_thread=None;self.warm_cancel=threading.Event();self.warm={'state':'idle','seconds':None,'error':''}
        self.narrator_lock=threading.Lock();self.narrator_warm_thread=None;self.narrator_warm={'state':'idle','seconds':None,'error':''}
        self.deleting=set()  # conversations being deleted: no run, note or forget may start in them
        self._clearing=False
        # One explicit Clock 1 collection at a time. No scheduler: a person starts every run.
        self.news={'phase':'idle','stage':'','run_id':None,'error':'','started':None,'finished':None,'request_id':None}
        self.news_cancel=None;self.news_thread=None
        self.models.state['router']='cached' if self.gliner_path().exists() else 'missing'

    @property
    def change_seq(self):
        with self._change_cond:
            return self._change_seq

    def notify_change(self):
        with self._change_cond:
            self._change_seq+=1
            self._change_cond.notify_all()

    def wait_change(self,since_seq,timeout):
        with self._change_cond:
            if self._change_seq==since_seq and not self.closed and timeout>0:
                self._change_cond.wait(timeout)
            return self._change_seq

    def gliner_path(self):
        return Path.home()/'.cache/huggingface/hub/models--fastino--GLiNER2.5-Decide/snapshots'/GLINER_REVISION

    def recall_memory(self,session,query,trace,exclude_run=None,tickers=()):
        """Recall earlier accepted turns of this conversation about the same assets. Context only, never evidence;
        failures never block research. If the selected backend fails, keyword memory answers instead (it is always
        written too) and the trace shows both steps."""
        hits=[]
        def run(memory):
            def go():
                nonlocal hits
                synced=self.sync_session(session)  # register and repair this conversation's turns; tombstones skipped
                raw=memory.recall('chat',session['id'],query,limit=5,exclude_run=exclude_run)
                found=self.ledger.resolve(session['id'],raw)  # the one tombstone predicate: forgotten or unknown never pass
                turns=self.select_hits(found,tickers)
                shadow=self.shadow_keyword(session,query,exclude_run,tickers,turns) if isinstance(memory,HybridMemory) else None
                hits=turns+self.pinned_notes(session['id'],tickers)  # notes are pinned: from the ledger, not from search
                return {'backend':memory.backend,'synced':synced,'excluded_latest_run':bool(exclude_run),
                        **({'shadow_keyword':shadow} if shadow else {}),
                        'dropped_forgotten_or_unknown':len(raw)-len(found),
                        'dropped_other_assets':len(found)-len([h for h in found if self.same_asset(h,tickers)]),
                        'recalled':[{'question':h.question[:160],'run_id':h.run_id,'item_id':h.item_id,'kind':h.kind,'score':h.score,**({'found_by':h.sources} if h.sources else {})} for h in hits],
                        'note':'Recalled text is context for the narrator. Its numbers are not added to any allowlist.'}
            return go
        try:
            trace.call('Recall conversation memory','tool',run(self.memory),actor='Conversation memory',
                       inputs={'session_id':session['id'],'query':query[:300],'backend':self.memory.backend})
        except (sqlite3.Error,ValueError,RuntimeError):
            hits=[]
            if self.memory is not self.keyword_memory:
                try:
                    trace.call('Recall conversation memory (keyword fallback)','tool',run(self.keyword_memory),actor='Conversation memory',
                               inputs={'session_id':session['id'],'query':query[:300],'reason':'selected memory backend failed'})
                except (sqlite3.Error,ValueError):
                    hits=[]
        if not any(h.kind=='note' for h in hits):
            try:hits=hits+self.pinned_notes(session['id'],tickers)  # pinned notes never depend on a search backend
            except sqlite3.Error:pass
        return hits

    def shadow_keyword(self,session,query,exclude_run,tickers,hits):
        """Real-chat comparison (approved 2026-11-08): what keyword memory alone would have recalled for this turn.
        Recorded in the trace only, never used as context. data/_memory_shadow_report.py judges disagreements blind."""
        try:
            found=self.ledger.resolve(session['id'],self.keyword_memory.recall('chat',session['id'],query,limit=5,exclude_run=exclude_run))
        except (sqlite3.Error,ValueError):
            return {'error':'keyword shadow failed'}
        kw=self.select_hits(found,tickers)
        return {'keyword_only':[{'run_id':h.run_id,'kind':h.kind,'question':h.question[:160]} for h in kw],
                'agrees':{h.item_id for h in kw}=={h.item_id for h in hits},
                'note':'Shadow comparison only. Keyword-only results are never passed to the narrator.'}

    def local_extractor(self,trace,cancel):
        """Shared offline GLiNER subprocess for routing and headline judging."""
        from .router_worker import RouterProcess
        with self.router_lock:
            if self.extractor is None:
                if not self.gliner_path().exists():raise ModelError('The pinned GLiNER cache is missing. No unapproved download was attempted.')
                self.extractor=RouterProcess(self.repo,self.root/'local_models',cancel)
            self.extractor.cancel=cancel
            if not self.extractor.process or self.extractor.process.poll() is not None:
                self.models.update(router='loading')
                try:
                    with trace.span('Load cached routing model','tool',actor='Local runtime',inputs={'model':'fastino/GLiNER2.5-Decide','revision':GLINER_REVISION,'network':False}):
                        self.extractor.start()
                except BaseException:
                    self.models.update(router='cached');raise  # never left showing 'loading' after a failed start
                self.models.update(router='loaded')
            return self.extractor

    def warm_router(self):
        """Start the GLiNER routing process in the background at dashboard startup (live 2026-11-17: the first question
        after a restart spent 51 s of 57 loading it). Offline cache only, never a download; a later chat waits on
        router_lock instead of starting a second process. COFINANCE_ROUTER_WARMUP=off disables it."""
        if (os.environ.get('COFINANCE_ROUTER_WARMUP','on').lower()=='off' or self.router_factory or self.closed
                or not self.gliner_path().exists() or (self.warm_thread and self.warm_thread.is_alive())):
            self.warm['state']='skipped';return None
        def run():
            started=time.monotonic();self.warm['state']='loading'
            try:
                self.local_extractor(_NoTrace(),self.warm_cancel)
                self.warm.update(state='loaded',seconds=round(time.monotonic()-started,1))
            except BaseException as error:  # a failed warm-up only means the first chat loads it, as before
                self.warm.update(state='failed',error=type(error).__name__,seconds=round(time.monotonic()-started,1))
        self.warm_thread=threading.Thread(target=run,name='router-warmup',daemon=True);self.warm_thread.start()
        return self.warm_thread

    def _load_narrator_locked(self):
        with self.narrator_lock:
            if self.closed:
                return
            self.models.load()
            if self.closed:
                self.models.close()

    def warm_narrator(self):
        if (os.environ.get('COFINANCE_NARRATOR_WARMUP','on').lower()=='off' or self.closed
                or self.models.state.get('selected')=='facts' or not self.models.qualified()
                or self.models.is_loaded() or (self.narrator_warm_thread and self.narrator_warm_thread.is_alive())):
            self.narrator_warm['state']='skipped';return None
        def run():
            started=time.monotonic();self.narrator_warm['state']='loading'
            try:
                self._load_narrator_locked()
                self.narrator_warm.update(state='loaded',seconds=round(time.monotonic()-started,1))
            except BaseException as error:
                self.narrator_warm.update(state='failed',error=type(error).__name__,seconds=round(time.monotonic()-started,1))
        self.narrator_warm_thread=threading.Thread(target=run,name='narrator-warmup',daemon=True);self.narrator_warm_thread.start()
        return self.narrator_warm_thread

    def _query_universe(self,query):
        cashtags=set(re.findall(r'(?<![A-Za-z0-9])\$([A-Za-z][A-Za-z0-9.\-]{0,11})(?![A-Za-z0-9])',query))
        watchlist={r['ticker'].upper() for r in self.store.watchlist() if r.get('ticker')}
        return UNIVERSE|watchlist|{c.upper() for c in cashtags}

    @staticmethod
    def _open_chat_messages(session,query,max_turns=6,max_chars=2400,inactive_runs=()):
        blocked=set(inactive_runs or ())
        history=[]
        for msg in (session.get('messages') or []):
            if blocked and msg.get('run_id') in blocked:
                continue
            role=msg.get('role')
            content=(msg.get('content') or '').strip()
            if role in {'user','assistant'} and content:
                history.append({'role':role,'content':content[:600]})
        if history and history[-1]['role']=='user' and history[-1]['content']==query.strip()[:600]:
            history=history[:-1]
        trimmed=[]
        used=0
        for msg in reversed(history[-max_turns:]):
            if used+len(msg['content'])>max_chars and trimmed:
                break
            trimmed.append(msg)
            used+=len(msg['content'])
        trimmed.reverse()
        trimmed.append({'role':'user','content':query})
        return trimmed

    def router(self,query,trace,cancel):
        if self.router_factory:return self.router_factory()
        self.local_extractor(trace,cancel)
        return GlinerQueryRouter(self.extractor,self._query_universe(query))

    def submit(self,session_id,query,request_id,model,think=False):
        valid_id(session_id);valid_id(request_id)
        if not isinstance(think,bool):raise ValueError('Deep think must be on or off.')
        if think and model=='facts':raise ValueError('Deep think needs a local narrator.')
        if not isinstance(query,str) or not 1<=len(query.strip())<=3000:raise ValueError('Enter a question of up to 3000 characters.')
        query=query.strip()
        if any(ord(c)<32 and c not in '\n\t' for c in query):raise ValueError('Control characters are not allowed.')
        if model not in {'facts',*narrators()}:raise ValueError('Select a supported local model.')
        with self.lock,self.models.lock:
            existing=self.store.by_request(request_id,session_id,query,model)
            if existing:return existing
            if self.closed:raise ModelError('Server is stopping.')
            if self._clearing or session_id in self.deleting:raise ModelError('This conversation is being deleted.')
            if self.active:raise ModelError('One research run is already active. Wait or stop it first.')
            if self.news['phase']=='running':raise ModelError('News collection is running. Wait for it to finish, then ask again.')
            if self.models.thread and self.models.thread.is_alive():raise ModelError('Wait for the model operation to finish.')
            if model!=self.models.state['selected']:raise ModelError('The selected model changed. Refresh and choose it again.')
            if model!= 'facts' and not self.models.qualified():raise ModelError('The narrator must pass qualification before use.')
            if think and not self.models.think_qualified():raise ModelError('Deep think has not passed qualification. Test it in Models first.')
            session=self.store.session(session_id)
            trace=LiveTrace(query[:100],directory=self.root/'traces',progress=lambda stage:self.store.progress(trace.run_id,stage))
            job=self.store.create_job(trace.run_id,request_id,session_id,query,model)
            event=threading.Event();self.events[job['id']]=event;self.active=job['id'];self.models.busy=True
            if think:self.think_runs.add(job['id'])
            thread=threading.Thread(target=self.execute,args=(job,session,trace,event),daemon=True)
            self.threads[job['id']]=thread;thread.start()
            return job

    def execute(self,job,session,trace,cancel):
        started=time.monotonic();remember=None
        def check():
            if cancel.is_set():raise Cancelled('Run stopped by user.')
            if time.monotonic()-started>240:raise ModelError('Research time budget exceeded.')
        self.store.progress(job['id'],'Preparing local research','running')
        try:
            check()
            think=job['id'] in self.think_runs;deep={'requested':think,'used':False}
            session_tickers=list(session.get('tickers',[]))
            router=self.router(job['query'],trace,cancel)
            from .run_trace import TracedExtractor
            if isinstance(router,GlinerQueryRouter):
                router=GlinerQueryRouter(TracedExtractor(router.backend,trace,'fastino/GLiNER2.5-Decide','Routing executive'),router.universe,router.aliases)
            routed=trace.call('Route conversational request','route',lambda:router.route(job['query']),actor='Routing executive',inputs={'query':job['query']})
            check()
            if routed.intent not in {'price_snapshot','news_catalysts','compare_assets','watchlist_add','thesis_review','open_conversation'}:
                raise GlinerOutputError('Unsupported router intent.')
            tools=self.market_factory(cancel=cancel)
            resolved_tickers=list(dict.fromkeys(routed.tickers))
            resolution_gaps=[]
            candidates=tuple(getattr(routed,'entity_candidates',()) or ())
            if routed.intent!='open_conversation' and candidates and (not resolved_tickers or routed.intent=='compare_assets'):
                clauses=re.split(r'\s+(?:and|versus|vs\.?)\s+',job['query'],flags=re.I) if routed.intent=='compare_assets' else [job['query']]
                for term in candidates:
                    if len(resolved_tickers)>=6:raise ValueError('Limit a research run to six assets.')
                    matching=[c for c in clauses if re.search(r'(?<!\w)'+re.escape(term)+r'(?!\w)',c,re.I)]
                    context=matching[0] if len(matching)==1 else job['query'] if len(clauses)==1 else ''
                    self.store.progress(job['id'],f'Resolving {term}')
                    def lookup():
                        resolver=getattr(tools,'resolve_symbol',None)
                        if resolver is None:return None
                        try:return resolver(term,context_query=context)
                        except TypeError:return resolver(term)
                    found=trace.call('Resolve asset symbol','tool',lookup,actor='Yahoo Finance search',inputs={'term':term,'context_query':context[:200]})
                    check()
                    if isinstance(found,dict) and found.get('ambiguous'):
                        resolution_gaps.append((term,found.get('alternatives',[])))
                    elif isinstance(found,dict) and found.get('symbol'):
                        if found['symbol'] not in resolved_tickers:resolved_tickers.append(found['symbol'])
                    else:
                        resolution_gaps.append((term,[]))
            documents=self.store.documents(session['id'])
            doc_match=bool(documents) and (routed.intent!='open_conversation' or any(e.get('matched') for e in select_excerpts(documents,job['query'],top=1)))
            if resolution_gaps:
                term,alts=resolution_gaps[0]
                if alts:
                    choices=', '.join(f"{a['symbol']} ({a['name']}, {a.get('exchange') or a.get('quote_type')})" for a in alts[:3])
                    answer=f'Multiple assets matched "{term}": {choices}. Reply with the exact ticker symbol or market you want to research.'
                else:
                    answer=f'I could not match "{term}" to an asset for this request. What is its exact ticker or exchange?'
                trace.event('Asset clarification required','decision',{'reason':'ambiguous_symbol' if alts else 'unresolved_symbol','term':term,'alternatives':alts[:3],
                            'unresolved_mentions':[name for name,_ in resolution_gaps]},actor='Python')
                trace.finish(summary={'origin':'chat','conversation_id':job['session_id'],'flow':'clarification','answer_kind':'clarification','answer':answer,'risk_mode':'not_resolved','model_choice':job['model']})
                with self.lock:
                    check()
                    self.store.finish(job['id'],answer,{'kind':'clarification','charts':[],'evidence':[],'tickers':session_tickers,'mode':job['model'],'usage':trace.data.get('usage')},session_tickers,'complete')
                return
            if routed.intent=='open_conversation':
                tickers=[]
            else:
                tickers=list(dict.fromkeys(resolved_tickers or session_tickers))
                if not resolved_tickers and tickers:
                    trace.event('Reuse conversation assets','decision',{'reason':'no_asset_in_query','tickers':tickers,
                                'note':'The question named no asset, so the assets of this conversation are reused.'},actor='Python')
                if routed.intent=='compare_assets' and len(tickers)==1:
                    tickers=list(dict.fromkeys(session_tickers+tickers))
                if 'watchlist' in job['query'].lower() and not resolved_tickers:
                    tickers=[r['ticker'] for r in self.store.watchlist()]
                    if not tickers and not doc_match:
                        answer='Your watchlist is empty. Name an asset (such as NVDA, Apple, or BTC) to research it, or click Track on any answer to add one.'
                        trace.event('Asset clarification required','decision',{'reason':'empty_watchlist'},actor='Python')
                        trace.finish(summary={'origin':'chat','conversation_id':job['session_id'],'flow':'clarification','answer_kind':'clarification','answer':answer,'risk_mode':'not_resolved','model_choice':job['model']})
                        with self.lock:
                            check()
                            self.store.finish(job['id'],answer,{'kind':'clarification','charts':[],'evidence':[],'tickers':session_tickers,'mode':job['model'],'usage':trace.data.get('usage')},session_tickers,'complete')
                        return
            if len(tickers)>6:raise ValueError('Limit a research run to six assets.')
            is_open_chat=(routed.intent=='open_conversation' and not doc_match) or (not tickers and not doc_match)
            if is_open_chat:
                if job['model']=='facts':
                    answer='Tool-only mode needs a stock, ETF, crypto asset, or attached PDF. Name an asset (such as NVDA, Palantir, or BTC), or select a local model in Models for general chat.'
                    trace.event('Asset clarification required','decision',{'reason':'facts_only_no_asset_or_document'},actor='Python')
                    trace.finish(summary={'origin':'chat','conversation_id':job['session_id'],'flow':'clarification','answer_kind':'clarification','answer':answer,'risk_mode':'not_resolved','model_choice':job['model']})
                    with self.lock:
                        check()
                        self.store.finish(job['id'],answer,{'kind':'clarification','charts':[],'evidence':[],'tickers':session_tickers,'mode':job['model'],'usage':trace.data.get('usage')},session_tickers,'complete')
                    return
                if not self.models.is_loaded():
                    self.store.progress(job['id'],'Loading the selected narrator')
                self._load_narrator_locked()
                if think:
                    deep['note']='Deep think is used for Market Team Lead research synthesis, not general chat.'
                    trace.event('Deep think skipped','decision',{'reason':'open_conversation','note':deep['note']},actor='Python')
                elif self.models.thinker_loaded():self.models.stop_thinker()
                self.store.progress(job['id'],'Answering locally')
                inactive=self.ledger.inactive_runs(session['id'])
                chat_messages=self._open_chat_messages(session,job['query'],inactive_runs=inactive)
                from .usage import capture_usage
                with trace.span('Answer open conversation','model',actor='Local assistant',model=self.models.model_id,
                                inputs={'intent':routed.intent,'history_messages':len(chat_messages),'live_tools':False}) as span_res:
                    with capture_usage() as receipts:
                        check()
                        raw=self.models.complete(OPEN_CHAT_SYSTEM,job['query'],max_tokens=280,think=False,
                                                 messages=chat_messages,temperature=0.2,allow_truncated=True)
                    span_res['usage']=receipts[-1] if receipts else None
                    check()
                    answer=plain_text(raw).strip()
                    if not answer:
                        raise ModelError('Empty conversational output.')
                    span_res['output']=answer
                trace.finish(summary={'origin':'chat','conversation_id':job['session_id'],'flow':'open_conversation','answer_kind':'conversation','answer':answer[:2500],'risk_mode':'open_conversation','model_choice':job['model']})
                with self.lock:
                    check()
                    self.store.finish(job['id'],answer,{'kind':'conversation','unverified_chat':True,'charts':[],'evidence':[],
                                      'tickers':session_tickers,'mode':job['model'],'usage':trace.data.get('usage'),'deep_think':deep},session_tickers,'complete')
                return
            if documents and not tickers:
                trace.event('Answer from attached files','decision',{'reason':'no_asset_but_documents','documents':len(documents),
                            'note':'The question named no asset and the conversation has attached PDFs, so they are the evidence.'},actor='Python')
            needs_narrator=job['model']!='facts' and not (routed.intent=='news_catalysts' and not documents)
            if needs_narrator:
                if not self.models.is_loaded():
                    self.store.progress(job['id'],'Loading the selected narrator')
                self._load_narrator_locked()
                if think:
                    self.store.progress(job['id'],'Starting deep think')
                    try:self.models.load_thinker();deep['used']=True
                    except (ModelError,OSError) as exc:
                        deep['note']='Deep think could not start ('+str(exc)[:120]+'), so this answer was written without it.'
                        trace.event('Deep think unavailable','decision',{'reason':'start_failed','note':deep['note']},actor='Python')
                    check()
                elif self.models.thinker_loaded():self.models.stop_thinker()
            elif think:
                if job['model']=='facts':
                    deep['note']='Tool-only mode does not use Deep think.'
                    trace.event('Deep think skipped','decision',{'reason':'facts_only','note':deep['note']},actor='Python')
                else:
                    deep['note']='News-only answers use the verified Python digest, so Deep think was not needed.'
                    trace.event('Deep think skipped','decision',{'reason':'python_news_only','note':deep['note']},actor='Python')
            news=NewsReader(self.root/'news.db')
            narrator=self.models if job['model']!= 'facts' else FactsOnly()
            # Routing is already recorded. Reuse the validated result without a second inference.
            routed=dataclasses.replace(routed,tickers=tickers)
            class FixedRouter:
                def route(self,text):check();return routed
            class CheckedNarrator:
                model_id=getattr(narrator,'model_id','tool-only')
                disabled=getattr(narrator,'disabled',False)
                def complete(self,*args,**kwargs):
                    check();result=narrator.complete(*args,**kwargs);check();return result
            class ThinkingNarrator(CheckedNarrator):
                """Deep think: the Market Team Lead's final answer only. Specialists and headline judging never think."""
                is_thinking=True
                def complete(self,*args,**kwargs):
                    check();result=narrator.complete(*args,think=True,**kwargs);check();return result
            llm=CheckedNarrator()
            orch=TeamLeadOrchestrator(FixedRouter(),DataAnalystAgent(llm,tools),NewsResearcherAgent(llm,news),ThinkingNarrator() if deep['used'] else llm,tools)
            # Conversation context: the most recent accepted answer plus recalled earlier turns, this session only.
            # Forgotten turns are tombstoned in the ledger and are never context, not even as the previous answer.
            # Checked here, at execution time, not from the submit-time snapshot.
            inactive=self.ledger.inactive_runs(session['id'])
            research=[m for m in session.get('messages',[]) if m.get('role')=='assistant' and (m.get('payload') or {}).get('kind')=='research'
                      and m.get('run_id') not in inactive]
            # Context only from turns about the same assets: an NVDA risk must not leak into an Apple answer.
            same=lambda ts:bool(set(ts or [])&set(tickers))
            previous=research[-1]['content'] if research and same((research[-1].get('payload') or {}).get('tickers')) else ''
            hits=self.recall_memory(session,job['query'],trace,exclude_run=research[-1].get('run_id') if research else None,tickers=tickers)
            odds_pool=ThreadPoolExecutor(max_workers=1) if self.crowd_odds else None
            odds_future=odds_pool.submit(self.crowd_odds,tickers) if odds_pool else None
            try:
                result=run_traced_team(orch,job['query'],trace,session_id=job['session_id'],context=context_from(previous,hits),
                                       documents=documents or None)
                check()
                answer=result.final_synthesis or 'No accepted evidence is available for this request.'
                if routed.intent=='watchlist_add':answer+=' Use the Add to watchlist button to confirm tracking.'
                odds=None
                if odds_future is not None:
                    def await_odds():
                        while True:
                            check()
                            try:
                                return odds_future.result(timeout=0.05)
                            except TimeoutError:
                                continue
                    # Deterministic and outside the narrator: these values are shown as recorded, never narrated or guarded.
                    try:odds=trace.call('Look up crowd odds','tool',await_odds,actor='Prediction markets',
                                        inputs={'tickers':tickers,'source':'Polymarket public API, read-only','trading':False})
                    except Exception:odds=None  # an aid, never a blocker
                    check()
            finally:
                if odds_pool is not None:
                    odds_pool.shutdown(wait=False,cancel_futures=True)
            headline=None
            if result.news_report and os.environ.get('COFINANCE_HEADLINE_SIGNAL','on').strip().lower()!='off':
                # headline-v1, experimental. Python sums the stored judgments of exactly the headlines the News Researcher
                # was served, as in the screen. Shown as computed, like crowd odds: never narrated or guarded.
                from .headline_signal import card as headline_card,version_for
                served={t:[r['title'] for r in rows] for t,rows in result.news_report.raw_facts.items()}
                version=version_for(self.models.narrator_id)  # only the current judge's judgments, never a mix
                try:headline=trace.call('Score served headlines (headline-v1, experimental)','tool',lambda:headline_card(self.root/'news.db',served,version=version),
                                        actor='Python',inputs={'served':{t:len(v) for t,v in served.items()}})
                except Exception:headline=None  # an aid, never a blocker
            health=news_health(self.root/'news.db')
            payload={'kind':'research','mode':job['model'],'tickers':tickers,'charts':list(tools.charts.values()),
                     'usage':trace.data.get('usage'),
                     'crowd_odds':odds if odds and (odds.get('markets') or odds.get('errors')) else None,
                     'headline_signal':headline,
                     'snapshots':tools.snapshots,'evidence':tools.evidence+news.evidence,
                     'citations':cite(answer[:16000],tools.snapshots,tools.evidence+news.evidence),
                     'faithful_synthesis':result.faithful_synthesis,'risk_mode':result.risk_mode,'deep_think':deep,
                     'coverage_note':coverage_note(health,tickers),
                     'news_collected':bool(health.get('collected')),
                     # Accepted specialist outputs, so the UI can show structure without re-parsing the answer.
                     'reports':{'data':result.data_report.summary if result.data_report else None,
                                'news':result.news_report.summary if result.news_report else None,
                                'news_items':sum(len(v) for v in result.news_report.raw_facts.values()) if result.news_report else 0,
                                'document':result.document_report.summary if result.document_report else None},
                     # The pages the Document Reader quoted, so the answer can show where it came from.
                     'document_sources':[{'document_id':e['document_id'],'name':e['name'],'page':e['page'],'matched':e['matched']}
                                         for e in result.document_report.raw_facts.get('excerpts',[])] if result.document_report else [],
                     'memory':{'backend':self.memory.backend,'used_previous':bool(previous),
                               'recalled':[{'question':h.question[:160],'run_id':h.run_id,'item_id':h.item_id,'kind':h.kind,'score':h.score,**({'found_by':h.sources} if h.sources else {})} for h in hits]}}
            flow=_research_flow(routed,result,trace)
            trace.data['summary'].update({'origin':'chat','conversation_id':job['session_id'],'flow':flow,'answer_kind':'research','answer':answer[:2500],'model_choice':job['model']});trace.flush()
            with self.lock:
                check()
                self.store.finish(job['id'],answer,payload,tickers,trace.data['status'])
            remember=(job['session_id'],job['query'],answer,job['id'],tickers)
        except Cancelled:
            trace.event('Run cancelled','decision',{'reason':'user_cancelled'},fallback=True)
            trace.finish(error=True,summary={'origin':'chat','conversation_id':job['session_id'],'flow':'cancelled','answer_kind':'none'})
            self.store.progress(job['id'],'Stopped','cancelled','Stopped by user. Partial tool observations remain in the trace; no final answer was accepted.')
        except Exception as exc:
            trace.event('Run failed','decision',{'error_type':type(exc).__name__},fallback=True)
            trace.finish(error=True,summary={'origin':'chat','conversation_id':job['session_id'],'flow':'error','answer_kind':'none','error_type':type(exc).__name__})
            self.store.progress(job['id'],'Could not complete research','error',f'{type(exc).__name__}: {str(exc)[:200]}')
        finally:
            with self.lock,self.models.lock:
                self.models.busy=False
                if self.active==job['id']:self.active=None
                self.events.pop(job['id'],None);self.think_runs.discard(job['id'])
                if remember is None:self.threads.pop(job['id'],None)  # clarification, cancel and error paths
            self.notify_change()
        if remember is None:return
        try:
            if not self.closed:self.remember(*remember)
        finally:
            with self.lock:self.threads.pop(job['id'],None)  # tracked until memory is written, so close() waits for it
            self.notify_change()

    # -- memory items (spec: docs/designs/remember_forget.md, review round 1) ---------------------------------
    def backends(self):
        """(keyword, semantic or None). Keyword is always written, so the visible fallback has data."""
        m=self.memory
        if isinstance(m,HybridMemory):return self.keyword_memory,m.semantic
        return self.keyword_memory,(None if m is self.keyword_memory else m)

    @staticmethod
    def same_asset(hit,tickers):
        return not tickers or bool(set(hit.tickers)&set(tickers))

    def select_hits(self,found,tickers):
        """Up to 2 earlier turns about the question's assets. Notes found by search (copies written before notes were
        pinned) are skipped here: pinned_notes() supplies notes from the ledger, so a note is never counted twice."""
        return [h for h in found if h.kind!='note' and self.same_asset(h,tickers)][:2]

    NOTE_CONTEXT_CHARS=600

    def pinned_notes(self,session_id,tickers):
        """User notes are pinned, not searched: every active note of this conversation that names one of the question's
        assets, or names none (conversation-wide), newest first, while they fit in 600 characters. Deterministic, so a
        note the user saved is never silently missed by a ranking. Context only, never evidence."""
        out,used=[],0
        for row in reversed([r for r in self.ledger.items(session_id) if r['kind']=='note' and r['state']==ITEM_ACTIVE]):
            note_tickers=json.loads(row['tickers'] or '[]')
            if note_tickers and tickers and not set(note_tickers)&set(tickers):continue
            if used+len(row['text'])>self.NOTE_CONTEXT_CHARS:continue
            used+=len(row['text'])
            out.append(MemoryHit(row['item_id'],session_id,'User note',row['text'],None,row['created'],1.0,note_tickers,['pinned'],
                                 item_id=row['item_id'],kind='note'))
            if len(out)>=5:break
        return out

    def remember(self,session_id,question,answer,run_id,tickers):
        """Write one accepted research turn to every backend. Runs after the run slot is released.
        The keyword copy is written under the ledger lock (fast). The vector copy is written OUTSIDE it, because the
        first one starts the embedder (seconds), and a note or forget in another conversation must not wait for that.
        Its result is committed under the lock only while the item is still active; if a forget landed meanwhile, the
        fresh copy is deleted at once, so a delayed write can never undo a forget. One vector write per item at a time."""
        keyword,semantic=self.backends();errors=[]
        with self.ledger.lock:
            item=self.ledger.ensure(run_id,session_id,'turn',run_id=run_id,text=question[:160],tickers=tickers)
            if item['state']!=ITEM_ACTIVE:
                return item
            if not item['keyword_id']:
                try:
                    kid=keyword.remember_turn('chat',session_id,question,answer,run_id=run_id,tickers=tickers)
                    self.ledger.update(run_id,keyword_id=kid)
                except (sqlite3.Error,ValueError) as exc:
                    errors.append('keyword: '+type(exc).__name__)
            write=semantic is not None and not item['semantic_id'] and run_id not in self._semantic_inflight
            if write and time.monotonic()<self.semantic_retry_after:
                errors.append('semantic: waiting after an earlier failure');write=False
            if not write:
                self.ledger.update(run_id,last_error='; '.join(errors)[:300])
                return self.ledger.get(run_id)
            self._semantic_inflight.add(run_id)
        sid=None
        try:
            sid=semantic.remember_turn('chat',session_id,question,answer,run_id=run_id,tickers=tickers)
        except Exception as exc:  # semantic memory is an aid; keyword already holds the item
            errors.append('semantic: '+type(exc).__name__);self.semantic_retry_after=time.monotonic()+300
        finally:
            with self.ledger.lock:
                self._semantic_inflight.discard(run_id)
                if sid and hasattr(semantic,'memory_id_for_run'):
                    try:
                        if semantic.memory_id_for_run(run_id,session_id)!=sid:sid=None  # swept meanwhile: never claim it
                    except sqlite3.Error:sid=None
                if sid:self.ledger.update(run_id,semantic_id=str(sid))
                item=self.ledger.get(run_id)
                if sid and item['state']!=ITEM_ACTIVE:  # forgotten while the copy was written: delete the copy now
                    if item['state']==ITEM_FORGOTTEN:self.ledger.update(run_id,state=ITEM_PENDING)
                    self._purge(self.ledger.get(run_id))
                elif item['state']==ITEM_ACTIVE:
                    self.ledger.update(run_id,last_error='; '.join(errors)[:300])
        return self.ledger.get(run_id)

    def sync_session(self,session):
        """Register accepted research turns of this conversation and fill missing backend copies. Replaces the old
        count()==0 backfill: it repairs single missing items and never touches a tombstoned one."""
        self.retry_pending()
        items={i['item_id']:i for i in self.ledger.items(session['id'])}
        keyword,semantic=self.backends();synced=0;question=''
        for message in session.get('messages',[]):
            if message.get('role')=='user':
                question=message.get('content','');continue
            payload=message.get('payload') or {};run_id=message.get('run_id')
            if payload.get('kind')!='research' or not message.get('content') or not run_id:
                continue
            item=items.get(run_id)
            if item and item['state']!=ITEM_ACTIVE:
                continue
            if item is None or not item['keyword_id'] or (semantic is not None and not item['semantic_id'] and time.monotonic()>=self.semantic_retry_after):
                self.remember(session['id'],question,message['content'],run_id,payload.get('tickers',[]));synced+=1
        return synced

    def note_tickers(self,text):
        """Deterministic: explicit tickers plus whole-word registered company names (same backstop as the router)."""
        found=list(dict.fromkeys(extract_tickers(text,UNIVERSE)))
        for alias,mapped in DEFAULT_ALIASES.items():
            if mapped in UNIVERSE and mapped not in found and re.search(r'(?<![A-Za-z0-9])'+re.escape(alias)+r'(?![A-Za-z0-9])',text,re.IGNORECASE):
                found.append(mapped)
        return found

    def prior_op(self,request_id,session_id,op):
        """Stored result for a repeated request id; refuses an id first used for another conversation or action."""
        if not request_id:return None
        valid_id(request_id)
        return self.ledger.op(request_id,session_id,op)

    def register_backend_rows(self,session_id):
        """Forget all must reach copies the ledger never saw (turns older than the visible message window, rows written
        before the ledger existed). Every unknown keyword row and vector link of this conversation becomes a ledger item
        so the normal tombstone, delete and retry path covers it. Caller holds the ledger lock."""
        import hashlib
        keyword,semantic=self.backends()
        def known():
            rows=self.ledger.items(session_id)
            return rows,{r['run_id']:r for r in rows if r['run_id']},{r['item_id']:r for r in rows}
        def adopt(backend_id,run_id,field):
            rows,by_run,by_id=known()
            by_copy={r[field]:r for r in rows if r[field]}
            item=by_run.get(run_id) if run_id else None
            item=item or by_copy.get(backend_id) or by_id.get(backend_id)
            if item is None:
                item_id=run_id or hashlib.md5(f'{session_id}:{backend_id}'.encode()).hexdigest()
                item=self.ledger.ensure(item_id,session_id,'turn',run_id=run_id or None,text='',tickers=[])
            if not item[field]:self.ledger.update(item['item_id'],**{field:backend_id})
            if item['state']==ITEM_FORGOTTEN:self.ledger.update(item['item_id'],state=ITEM_PENDING)  # a surviving copy means the delete is not finished
        if hasattr(keyword,'session_rows'):
            for backend_id,run_id in keyword.session_rows('chat',session_id):adopt(backend_id,run_id,'keyword_id')
        if semantic is not None and hasattr(semantic,'session_links'):
            for backend_id,run_id in semantic.session_links('chat',session_id):adopt(backend_id,run_id,'semantic_id')

    def _refuse_during_run(self,session_id):
        with self.lock:
            if self.closed:raise ModelError('Server is stopping.')
            if self._clearing or session_id in self.deleting:raise ModelError('This conversation is being deleted.')
            if self.active and self.store.job(self.active).get('session_id')==session_id:
                raise ModelError('Wait for the current research run in this conversation to finish.')

    def add_note(self,session_id,text,request_id):
        """Pin a user note to one conversation. Context only: never evidence, never an instruction, never allowlisted.
        A note lives in the ledger only (no search copy, so saving one never starts the embedder). It names its assets
        (deterministic scan) or none, which makes it conversation-wide; pinned_notes() adds it to every matching question."""
        valid_id(session_id);valid_id(request_id)
        if not isinstance(text,str):raise ValueError('A note must be text.')
        if any(ord(c)<32 and c not in '\n\t' for c in text):raise ValueError('Control characters are not allowed.')
        text=' '.join(text.split())
        if not 1<=len(text)<=500:raise ValueError('A note is 1 to 500 characters.')
        def replay():
            existing=self.ledger.by_request(request_id)
            if existing and (existing['session_id']!=session_id or existing['kind']!='note'):
                raise ValueError('That request id was already used for something else.')
            return existing
        existing=replay()
        if existing:return existing  # a repeated request returns what it created, even if a run started since
        self._refuse_during_run(session_id)
        self.store.session(session_id)  # the conversation must exist
        with self.ledger.lock:
            existing=replay()
            if existing:return existing
            same=next((i for i in self.ledger.items(session_id) if i['kind']=='note' and i['state']==ITEM_ACTIVE and i['text']==text),None)
            if same:return same  # the same note twice is one note
            if self.ledger.active_notes(session_id)>=NOTE_LIMIT:raise ValueError(f'This conversation already has {NOTE_LIMIT} notes. Forget one first.')
            created=self.ledger.ensure(uuid.uuid4().hex,session_id,'note',text=text,tickers=self.note_tickers(text),request_id=request_id)
        self.notify_change()
        return created

    # -- attached PDFs (asked by Baris 2026-11-19). Parsed in a child process with a time limit; text only, no OCR. ----
    def add_document(self,session_id,name,data):
        """Parse one PDF and attach its page texts to the conversation. Refused while a run in it is active."""
        import hashlib
        from .documents import clean_name,extract_pages
        valid_id(session_id)
        name=clean_name(name)
        self._refuse_during_run(session_id)
        self.store.session(session_id)  # the conversation must exist before the parse cost is paid
        pages=extract_pages(data)
        self._refuse_during_run(session_id)  # checked again: parsing can take seconds
        return self.store.add_document(session_id,name,hashlib.sha256(bytes(data)).hexdigest(),len(data),pages)

    def remove_document(self,session_id,document_id):
        valid_id(session_id);valid_id(document_id)
        self._refuse_during_run(session_id)
        return self.store.remove_document(session_id,document_id)

    def forget(self,session_id,item_id,request_id=None):
        """Never use one turn or note as memory again. Tombstone first, then delete every backend copy; a failed vector
        delete stays delete_pending and is retried, and the tombstone already blocks every recall path.
        The answer stays in the conversation history and its trace stays as the audit record."""
        valid_id(session_id)
        if item_id=='*':return self.forget_all(session_id,request_id)
        valid_id(item_id)
        prior=self.prior_op(request_id,session_id,'forget')
        if prior is not None:return prior
        self._refuse_during_run(session_id)
        with self.ledger.lock:
            prior=self.prior_op(request_id,session_id,'forget')  # a concurrent twin may have finished while we waited for the lock
            if prior is not None:return prior
            item=self.ledger.get(item_id)
            if item is None:
                session=self.store.session(session_id)
                turn=next((m for m in session.get('messages',[]) if m.get('run_id')==item_id and (m.get('payload') or {}).get('kind')=='research'),None)
                if turn is None:raise ValueError('No such memory item in this conversation.')
                item=self.ledger.ensure(item_id,session_id,'turn',run_id=item_id,tickers=(turn.get('payload') or {}).get('tickers',[]))
            if item['session_id']!=session_id:raise ValueError('No such memory item in this conversation.')
            if item['state']==ITEM_ACTIVE:self.ledger.update(item_id,state=ITEM_PENDING)  # tombstone before any delete
            result=self._purge(self.ledger.get(item_id))
            if request_id:self.ledger.record_op(request_id,'forget',session_id,result)
        self.notify_change()
        return result

    def forget_all(self,session_id,request_id=None):
        """Forget every turn and note of ONE conversation (others are never touched). Same order as forget(): every item is
        tombstoned first, then copies are deleted; failures stay delete_pending and retry later. History stays visible."""
        valid_id(session_id)
        prior=self.prior_op(request_id,session_id,'forget_all')
        if prior is not None:return prior
        self._refuse_during_run(session_id)
        return self._forget_all(session_id,request_id)

    def _forget_all(self,session_id,request_id=None):
        """forget_all() without the run check, for delete_conversation(), which makes a stricter check itself."""
        with self.ledger.lock:
            prior=self.prior_op(request_id,session_id,'forget_all')
            if prior is not None:return prior
            session=self.store.session(session_id)
            for message in session.get('messages',[]):
                payload=message.get('payload') or {};run_id=message.get('run_id')
                if message.get('role')=='assistant' and payload.get('kind')=='research' and run_id:
                    self.ledger.ensure(run_id,session_id,'turn',run_id=run_id,text='',tickers=payload.get('tickers',[]))
            self.register_backend_rows(session_id)  # copies older than the visible message window or never registered
            items=self.ledger.items(session_id)
            for item in items:  # tombstone everything before any delete starts
                if item['state']==ITEM_ACTIVE:self.ledger.update(item['item_id'],state=ITEM_PENDING)
            results=[self._purge(self.ledger.get(item['item_id'])) for item in items if item['state']!=ITEM_FORGOTTEN]
            sweep_error=self._sweep(session_id)
            pending=sum(1 for r in results if r['state']==ITEM_PENDING)
            result={'item_id':'*','forgotten':len(results)-pending,'pending':pending,'already_forgotten':len(items)-len(results),
                    'vector_copy_pending':pending>0 or bool(sweep_error),'sweep_pending':bool(sweep_error),
                    'error':next((r['error'] for r in results if r['error']),sweep_error)}
            if request_id:self.ledger.record_op(request_id,'forget_all',session_id,result)
        self.notify_change()
        return result

    # -- delete conversations (asked by Baris 2026-11-08: delete and clear buttons on the Conversations panel) -----------
    def _refuse_busy(self,session_id):
        """Stricter than _refuse_during_run: also refuses while a finished run of this conversation is still writing its
        memory (self.threads tracks a run until remember() is done), so no late write can follow the delete."""
        with self.lock:
            if self.closed:raise ModelError('Server is stopping.')
            for run_id in [r for r in [self.active,*self.threads] if r]:
                try:owner=self.store.job(run_id).get('session_id')
                except KeyError:continue
                if owner==session_id:
                    raise ModelError('Wait for the research run in this conversation to finish, including its memory write.')

    def _delete_one(self,session_id):
        """Memory first, then history. 1) forget all: every item tombstoned, every copy deleted, the vector store swept
        for this conversation (failures stay pending and retry; the tombstones already block recall). 2) tombstones lose
        their text. 3) messages, runs and the conversation row go in one transaction. 4) the runs' trace files go, since
        they hold the question and the answer. Caller has put session_id in self.deleting, so no run can start here."""
        memory=self._forget_all(session_id)
        with self.ledger.lock:self.ledger.scrub(session_id)
        with self.lock:
            self._refuse_busy(session_id)
            removed=self.store.delete_session(session_id)
        traces,trace_errors=0,0
        trace_dir=self.root/'traces'
        seen_runs=set()
        for run_id in removed['runs']:
            seen_runs.add(run_id)
            try:
                (trace_dir/(valid_id(run_id)+'.json')).unlink();traces+=1
            except FileNotFoundError:pass
            except (OSError,ValueError):trace_errors+=1
        if trace_dir.is_dir():
            for path in trace_dir.glob('*.json'):
                if path.stem in seen_runs:continue
                try:
                    data=json.loads(path.read_text(encoding='utf-8'))
                    summary=data.get('summary') if isinstance(data,dict) else None
                    if isinstance(summary,dict) and summary.get('conversation_id')==session_id:
                        path.unlink();traces+=1
                except FileNotFoundError:pass
                except (OSError,ValueError,TypeError):continue
        self.notify_change()
        return {'session_id':session_id,'deleted':True,'messages':removed['messages'],'runs':len(removed['runs']),
                'traces_deleted':traces,'trace_errors':trace_errors,'memory_forgotten':memory.get('forgotten',0),
                'memory_pending':memory.get('pending',0),'sweep_pending':bool(memory.get('sweep_pending')),'error':memory.get('error') or ''}

    def delete_conversation(self,session_id,request_id):
        """Delete ONE conversation: its memory, messages, runs and run traces. Idempotent per request id."""
        valid_id(session_id);valid_id(request_id)
        prior=self.prior_op(request_id,session_id,'delete_conversation')
        if prior is not None:return prior
        with self.lock:
            if session_id in self.deleting:raise ModelError('This conversation is already being deleted.')
            self._refuse_busy(session_id)
            self.store.session(session_id)  # KeyError when it does not exist
            self.deleting.add(session_id)  # submit() and memory writes refuse it from here on
        try:
            result=self._delete_one(session_id)
        finally:
            with self.lock:self.deleting.discard(session_id)
        self.ledger.record_op(request_id,'delete_conversation',session_id,result)
        return result

    def clear_conversations(self,request_id):
        """Delete every conversation, one at a time with the same steps. Refused while any research run or memory write is
        in flight. Watchlist, collected news, labels and models are not touched. Idempotent per request id."""
        valid_id(request_id)
        prior=self.prior_op(request_id,'*','clear_conversations')
        if prior is not None:return prior
        with self.lock:
            if self.closed:raise ModelError('Server is stopping.')
            if self._clearing:raise ModelError('Conversations are already being cleared.')
            if self.active or self.threads or self._semantic_inflight:raise ModelError('Wait for the current research run, including its memory write, to finish.')
            if self.news['phase']=='running':raise ModelError('Wait for news collection to finish before clearing conversations.')
            ids=[s for s in self.store.session_ids() if s not in self.deleting]
            self.deleting.update(ids)
            self._clearing=True
        results=[]
        orphan_traces=0
        cleanup_retry=False
        try:
            for sid in ids:
                try:results.append(self._delete_one(sid))
                except KeyError:continue
            trace_dir=self.root/'traces'
            if trace_dir.is_dir():
                for path in trace_dir.glob('*.json'):
                    try:
                        data=json.loads(path.read_text(encoding='utf-8'))
                        if _is_live_chat_trace(data):
                            path.unlink();orphan_traces+=1
                    except FileNotFoundError:pass
                    except (OSError,ValueError,TypeError):continue
            _,semantic=self.backends()
            if semantic is not None and hasattr(semantic,'scrub_all') and not self.store.session_ids():
                try:semantic.scrub_all()
                except Exception:cleanup_retry=True
        finally:
            with self.lock:
                self.deleting.difference_update(ids)
                self._clearing=False
        if orphan_traces or results:
            self.notify_change()
        result={'deleted':len(results),'messages':sum(r['messages'] for r in results),
                'traces_deleted':sum(r['traces_deleted'] for r in results)+orphan_traces,
                'memory_pending':sum(r['memory_pending'] for r in results),
                'sweep_pending':sum(1 for r in results if r['sweep_pending'])+int(cleanup_retry),
                'error':'Some local memory copies could not be cleared. Retry Clear All.' if cleanup_retry else
                        next((r['error'] for r in results if r['error']), '')}
        if cleanup_retry:result['cleanup_retry']=True
        self.ledger.record_op(request_id,'clear_conversations','*',result)
        return result

    def memory_view(self,session_id):
        """What this conversation remembers, for the Memory list: research turns in the visible window (registered or not
        yet) plus notes and any other registered item, each with its state and which backends hold a copy."""
        valid_id(session_id)
        session=self.store.session(session_id)
        rows={i['item_id']:i for i in self.ledger.items(session_id)}
        items=[];question=''
        def view(row,kind,item_id,label,tickers,created):
            r=row or {}
            return {'item_id':item_id,'kind':kind,'label':label,'tickers':tickers,'state':r.get('state','active'),'registered':bool(row),
                    'in_keyword':bool(r.get('keyword_id')),'in_semantic':bool(r.get('semantic_id')),'last_error':r.get('last_error',''),'created':created}
        for message in session.get('messages',[]):
            if message.get('role')=='user':
                question=message.get('content','');continue
            payload=message.get('payload') or {};run_id=message.get('run_id')
            if payload.get('kind')!='research' or not run_id or not message.get('content'):continue
            items.append(view(rows.pop(run_id,None),'turn',run_id,' '.join(question.split())[:160],payload.get('tickers',[]),message.get('created',0)))
        for row in rows.values():  # notes, and turns outside the visible message window
            label=' '.join((row['text'] or ('Earlier turn' if row['kind']=='turn' else '')).split())[:500]
            items.append(view(row,row['kind'],row['item_id'],label,json.loads(row['tickers'] or '[]'),row['created']))
        items.sort(key=lambda i:i['created'])
        _,semantic=self.backends()
        with self.lock:active=bool(self.active and self.store.job(self.active).get('session_id')==session_id)
        return {'session_id':session_id,'backend':self.memory.backend,'semantic':semantic is not None,'active_run':active,
                'note_limit':NOTE_LIMIT,'notes':self.ledger.active_notes(session_id),'items':items,
                'sweep_pending':session_id in self.ledger.pending_sweeps()}

    def _purge(self,item):
        """Delete backend copies of a tombstoned item; FORGOTTEN only when every copy is gone. Caller holds the lock."""
        keyword,semantic=self.backends();errors=[];cleared={}  # ids of copies that are really gone, so the list never claims a deleted copy
        try:
            keyword.delete('chat',item['session_id'],keyword_id=item['keyword_id'],run_id=item['run_id'])
            cleared['keyword_id']=None
        except sqlite3.Error as exc:
            errors.append('keyword: '+type(exc).__name__)
        sid=item['semantic_id']
        if not sid and item['run_id'] and semantic is not None and hasattr(semantic,'memory_id_for_run'):
            try:sid=semantic.memory_id_for_run(item['run_id'],item['session_id'])
            except sqlite3.Error:errors.append('semantic: link lookup failed')
        if sid:
            if semantic is None or not hasattr(semantic,'delete'):
                errors.append('semantic: backend not available, vector copy kept until it is')
            elif time.monotonic()<self.semantic_retry_after:
                errors.append('semantic: waiting after an earlier failure')
            else:
                try:
                    semantic.delete('chat',item['session_id'],sid)
                    cleared['semantic_id']=None
                except Exception as exc:
                    errors.append('semantic: '+type(exc).__name__);self.semantic_retry_after=time.monotonic()+300
        else:cleared['semantic_id']=None  # no vector copy ever existed
        state=ITEM_PENDING if errors else ITEM_FORGOTTEN
        self.ledger.update(item['item_id'],state=state,last_error='; '.join(errors)[:300],**cleared)
        return {'item_id':item['item_id'],'kind':item['kind'],'state':state,'vector_copy_pending':bool(errors),'error':'; '.join(errors)[:300]}

    def _sweep(self,session_id):
        """Forget-all backstop: delete every vector copy of this ONE conversation, including copies no ledger item or link
        points to (a crash between a vector add and its link write). The worker scopes it by conversation id (verified
        live: tests/test_memory_items.py TestLiveHyMemoryDelete). After a sweep no vector copy of the conversation exists,
        so active items stop claiming one and are re-written by the normal repair. Returns '' when done; a failure is
        kept in the ledger and retried, so forget-all is never silently partial. Caller holds the ledger lock."""
        _,semantic=self.backends()
        if semantic is None or not hasattr(semantic,'delete_session'):return ''
        if time.monotonic()<self.semantic_retry_after:
            error='semantic sweep: waiting after an earlier failure'
        else:
            try:
                semantic.delete_session('chat',session_id)
                for item in self.ledger.items(session_id):
                    if item['semantic_id']:self.ledger.update(item['item_id'],semantic_id=None)
                self.ledger.sweep_done(session_id)
                return ''
            except Exception as exc:
                error='semantic sweep: '+type(exc).__name__;self.semantic_retry_after=time.monotonic()+300
        self.ledger.sweep_pending(session_id,error)
        return error

    def retry_pending(self):
        """Retry deletes left delete_pending and forget-all sweeps that could not run. Bounded by the backoff."""
        pending,sweeps=self.ledger.pending(),self.ledger.pending_sweeps()
        if not (pending or sweeps) or time.monotonic()<self.semantic_retry_after:return 0
        done=0
        with self.ledger.lock:
            for item in pending:
                current=self.ledger.get(item['item_id'])
                if current and current['state']==ITEM_PENDING:done+=self._purge(current)['state']==ITEM_FORGOTTEN
            for session_id in sweeps:
                done+=not self._sweep(session_id)
        return done

    def stop(self,run_id):
        valid_id(run_id)
        with self.lock:
            job=self.store.job(run_id)
            if job['status'] in ACTIVE and run_id in self.events:
                self.events[run_id].set()
                self.store.progress(run_id,'Stopping after the current bounded operation','cancelling')
            return self.store.job(run_id)

    def snapshot(self):
        with self.lock:
            watchlist=self.store.watchlist()
            return {'sessions':self.store.sessions(),'models':self.models.snapshot(),'active_job':self.store.job(self.active) if self.active else None,'watchlist':watchlist,'news':self.news_snapshot(watchlist)}

    def news_snapshot(self,watchlist=None):
        watchlist=self.store.watchlist() if watchlist is None else watchlist
        with self.lock:state={k:v for k,v in self.news.items() if k!='request_id'}
        return {**state,'health':news_health(self.root/'news.db',[w['ticker'] for w in watchlist])}

    def collect_news(self,request_id):
        """Start one explicit collection over the registered official feeds. Idempotent per request id."""
        valid_id(request_id)
        with self.lock,self.models.lock:
            if self.news['request_id']==request_id:return self.news_snapshot()
            if self.closed:raise ModelError('Server is stopping.')
            if self._clearing or self.deleting:raise ModelError('Wait for conversation deletion to finish before collecting news.')
            if self.news['phase']=='running':raise ModelError('News collection is already running.')
            if self.active or self.threads:raise ModelError('Wait for the active research run to finish before collecting news.')
            if self.models.thread and self.models.thread.is_alive():raise ModelError('Wait for the model operation to finish before collecting news.')
            if not self.news_backend_factory and not self.gliner_path().exists():
                raise ModelError('The pinned GLiNER cache is missing. No unapproved download was attempted.')
            trace=LiveTrace('News collection: official company feeds',directory=self.root/'traces',progress=self.news_progress)
            cancel=threading.Event();self.news_cancel=cancel
            self.news.update(phase='running',stage='Starting collection',run_id=trace.run_id,error='',started=time.time(),finished=None,request_id=request_id)
            self.news_thread=threading.Thread(target=self.collect,args=(trace,cancel),daemon=True)
            self.news_thread.start()
        self.notify_change()
        return self.news_snapshot()

    def news_progress(self,stage):
        changed=False
        with self.lock:
            if self.news['phase']=='running':
                self.news['stage']=stage
                changed=True
        if changed:
            self.notify_change()

    def collect(self,trace,cancel):
        from .gliner_decide import GlinerDecideClassifier
        from .news_service import FeedFetcher
        from .news_store import NewsStore
        from .run_trace import run_traced_clock1
        def check():
            if cancel.is_set():raise Cancelled('News collection stopped.')
        store=None
        try:
            backend=self.news_backend_factory() if self.news_backend_factory else self.local_extractor(trace,cancel)
            fetch=FeedFetcher(self.feed_fetch,check=check) if self.feed_fetch else FeedFetcher(check=check)
            store=NewsStore(str(self.root/'news.db'))
            summary=run_traced_clock1(feed_tickers(),fetch,GlinerDecideClassifier(backend),trace,store=store)
            stage='Collected '+str(summary.fetched)+' items'+(' with source errors' if summary.errors or any(str(v).startswith('error') for s in summary.source_status.values() for v in s.values()) else '')
            headlines=self.judge_headlines(store,check)
            if headlines.get('judged'):stage+='; judged '+str(headlines['judged'])+' headlines'
            with self.lock:self.news.update(phase='idle',stage=stage,finished=time.time(),headlines=headlines)
            self.notify_change()
        except Exception as exc:
            if trace.data.get('status')=='running':trace.finish(error=True)
            with self.lock:self.news.update(phase='error',stage='Collection failed',error=f'{type(exc).__name__}: {str(exc)[:200]}',finished=time.time())
            self.notify_change()
        finally:
            if store:store.close()

    def judge_headlines(self,store,check):
        """headline-v1 at ingestion, after the Clock 1 transaction committed (never inside it). Experimental: runs only
        with a qualified narrator, and a failure never fails the collection. COFINANCE_HEADLINE_SIGNAL=off disables it."""
        from .headline_signal import VERSION,judge_pending,version_for
        if os.environ.get('COFINANCE_HEADLINE_SIGNAL','on').strip().lower()=='off':return {'version':VERSION,'status':'off'}
        if not self.models.qualified():return {'version':VERSION,'status':'narrator_not_qualified'}
        version=version_for(self.models.narrator_id)
        proc=getattr(self.models,'process',None)
        was_loaded=bool(getattr(self.models,'ready',False) and proc and proc.poll() is None)
        import contextlib
        trace=LiveTrace('Headline judgments: '+VERSION+' (experimental)',directory=self.root/'traces',progress=self.news_progress)
        n_lock=getattr(self,'narrator_lock',None)
        if not hasattr(n_lock,'__enter__'):n_lock=contextlib.nullcontext()
        m_lock=getattr(self.models,'lock',None)
        if not hasattr(m_lock,'__enter__'):m_lock=contextlib.nullcontext()
        with n_lock:
            with m_lock:
                self.models.busy=True
            try:
                self.news_progress('Judging served headlines')
                self.models.load()
                result=trace.call('Judge served headlines','model',lambda:judge_pending(store.conn,self.models.choice,model=self.models.model_id+' '+self.models.active_backend,check=check,version=version),
                                  actor='Headline Judge',model=self.models.model_id,inputs={'version':version,'inputs':'asset and title only','gate_label_hidden':True})
                trace.finish(summary=result)
                return {**result,'status':'complete' if not result['failed'] else 'partial','backend':self.models.active_backend}
            except Cancelled:
                trace.finish(error=True);raise
            except Exception as exc:
                trace.finish(error=True)
                return {'version':VERSION,'status':'error','error':f'{type(exc).__name__}: {str(exc)[:160]}'}
            finally:
                with m_lock:
                    self.models.busy=False
                if not was_loaded and not getattr(self.models,'busy',False):
                    try:self.models.unload(keep_choice=True)
                    except Exception:pass

    def close(self):
        self.closed=True;self.warm_cancel.set()
        self.notify_change()
        if self.warm_thread:self.warm_thread.join(timeout=2)
        if self.narrator_warm_thread:self.narrator_warm_thread.join(timeout=2)
        if self.news_cancel:self.news_cancel.set()
        if self.news_thread:self.news_thread.join(timeout=2)
        with self.lock:
            for event in self.events.values():event.set()
        for thread in list(self.threads.values()):thread.join(timeout=2)
        if self.extractor and hasattr(self.extractor,"close"):self.extractor.close()
        if hasattr(self.memory,'close'):self.memory.close()  # stops the Hy-Memory worker and embedder when selected
        with self.narrator_lock:
            self.models.close()
