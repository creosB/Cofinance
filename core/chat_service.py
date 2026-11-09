"""Interactive v2 vertical slice. One bounded job at a time, explicit user writes,
real local routing and optional qualified narrator. No legacy Streamlit imports.
"""
from __future__ import annotations
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
from .entities import extract_tickers
from .gliner_decide import DEFAULT_ALIASES, GlinerQueryRouter, GlinerOutputError
from .local_models import LocalModels, MODEL_ID, ModelError, Cancelled
from .market import MarketTools
from .news_service import coverage_note, feed_tickers, news_health
from .memory import HybridMemory, LocalKeywordMemory, MemoryHit, context_from
# Aliased: chat_store.ACTIVE (job statuses) is a different thing; shadowing it broke Stop once (caught by tests).
from .memory_items import ACTIVE as ITEM_ACTIVE, FORGOTTEN as ITEM_FORGOTTEN, NOTE_LIMIT, PENDING as ITEM_PENDING, MemoryLedger
from .orchestrator import DataAnalystAgent, NewsResearcherAgent, TeamLeadOrchestrator
from .signals import Item, Outcome, Judgment, Action
from .trace import TraceRecorder, run_traced_team

GLINER_REVISION="9d1bfb848cd16d93ee56ccbc59cd92bf0f284d90"
UNIVERSE=set(DEFAULT_ALIASES.values())|{"SPY","QQQ","NFLX","INTC","COIN","PLTR","SOFI","F","BABA","RIVN","ADBE","ORCL","AVAX","ADA","DOGE","XRP","BNB","DOT","LINK"}


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
        self.store=ChatStore(self.root/'chat_v2.db');self.store.recover()
        self.models=models or LocalModels(self.root/'local_models',self.repo)
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
        self.deleting=set()  # conversations being deleted: no run, note or forget may start in them
        # One explicit Clock 1 collection at a time. No scheduler: a person starts every run.
        self.news={'phase':'idle','stage':'','run_id':None,'error':'','started':None,'finished':None,'request_id':None}
        self.news_cancel=None;self.news_thread=None
        self.models.state['router']='cached' if self.gliner_path().exists() else 'missing'

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
        if self.extractor is None:
            if not self.gliner_path().exists():raise ModelError('The pinned GLiNER cache is missing. No unapproved download was attempted.')
            self.extractor=RouterProcess(self.repo,self.root/'local_models',cancel)
        self.extractor.cancel=cancel
        if not self.extractor.process or self.extractor.process.poll() is not None:
            self.models.update(router='loading')
            with trace.span('Load cached routing model','tool',actor='Local runtime',inputs={'model':'fastino/GLiNER2.5-Decide','revision':GLINER_REVISION,'network':False}):
                self.extractor.start()
            self.models.update(router='loaded')
        return self.extractor

    def router(self,query,trace,cancel):
        if self.router_factory:return self.router_factory()
        self.local_extractor(trace,cancel)
        # Explicit uppercase ticker tokens can extend the fixed alias universe, without
        # letting arbitrary paths, URLs or long strings reach a tool.
        explicit=set(re.findall(r'(?<![A-Za-z])\$?([A-Z][A-Z0-9.\-]{0,9})(?![A-Za-z])',query))
        explicit-={'I','A','THE','AND','OR','USD','PE','EPS','ATH','CEO','API','AI'}
        return GlinerQueryRouter(self.extractor,UNIVERSE|explicit)

    def submit(self,session_id,query,request_id,model):
        valid_id(session_id);valid_id(request_id)
        if not isinstance(query,str) or not 1<=len(query.strip())<=3000:raise ValueError('Enter a question of up to 3000 characters.')
        query=query.strip()
        if any(ord(c)<32 and c not in '\n\t' for c in query):raise ValueError('Control characters are not allowed.')
        if model not in {'facts',MODEL_ID}:raise ValueError('Select a supported local model.')
        with self.lock,self.models.lock:
            existing=self.store.by_request(request_id,session_id,query,model)
            if existing:return existing
            if self.closed:raise ModelError('Server is stopping.')
            if session_id in self.deleting:raise ModelError('This conversation is being deleted.')
            if self.active:raise ModelError('One research run is already active. Wait or stop it first.')
            if self.news['phase']=='running':raise ModelError('News collection is running. Wait for it to finish, then ask again.')
            if self.models.thread and self.models.thread.is_alive():raise ModelError('Wait for the model operation to finish.')
            if model!=self.models.state['selected']:raise ModelError('The selected model changed. Refresh and choose it again.')
            if model!= 'facts' and not self.models.qualified():raise ModelError('The narrator must pass qualification before use.')
            session=self.store.session(session_id)
            trace=LiveTrace(query[:100],directory=self.root/'traces',progress=lambda stage:self.store.progress(trace.run_id,stage))
            job=self.store.create_job(trace.run_id,request_id,session_id,query,model)
            event=threading.Event();self.events[job['id']]=event;self.active=job['id'];self.models.busy=True
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
            if job['model']!= 'facts':
                self.store.progress(job['id'],'Loading the selected narrator')
                self.models.load()
            router=self.router(job['query'],trace,cancel)
            # Always validate routing output and require explicit context when none exists.
            from .trace import TracedExtractor
            if isinstance(router,GlinerQueryRouter):
                router=GlinerQueryRouter(TracedExtractor(router.backend,trace,'fastino/GLiNER2.5-Decide','Routing executive'),router.universe,router.aliases)
            routed=trace.call('Route conversational request','route',lambda:router.route(job['query']),actor='Routing executive',inputs={'query':job['query']})
            check()
            if routed.intent not in {'price_snapshot','news_catalysts','compare_assets','watchlist_add','thesis_review'}:
                raise GlinerOutputError('Unsupported router intent.')
            tickers=list(dict.fromkeys(routed.tickers or session.get('tickers',[])))
            if not routed.tickers and tickers:
                trace.event('Reuse conversation assets','decision',{'reason':'no_asset_in_query','tickers':tickers,
                            'note':'The question named no asset, so the assets of this conversation are reused.'},actor='Python')
            if routed.intent=='compare_assets' and len(tickers)==1:
                tickers=list(dict.fromkeys(session.get('tickers',[])+tickers))
            if 'watchlist' in job['query'].lower() and not routed.tickers:
                tickers=[r['ticker'] for r in self.store.watchlist()]
            if len(tickers)>6:raise ValueError('Limit a research run to six assets.')
            if not tickers:
                answer='Which stock or crypto asset should I research? Name a ticker or company, such as NVDA or Nvidia.'
                trace.event('Asset clarification required','decision',{'reason':'no_asset_in_query_or_session'},actor='Python')
                trace.finish(summary={'answer':answer,'risk_mode':'not_resolved'})
                with self.lock:
                    check()
                    self.store.finish(job['id'],answer,{'kind':'clarification','charts':[],'evidence':[],'tickers':[],'mode':job['model']},[], 'complete')
                return
            tools=self.market_factory(cancel=cancel)
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
            llm=CheckedNarrator()
            orch=TeamLeadOrchestrator(FixedRouter(),DataAnalystAgent(llm,tools),NewsResearcherAgent(llm,news),llm,tools)
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
            result=run_traced_team(orch,job['query'],trace,session_id=job['session_id'],context=context_from(previous,hits))
            check()
            answer=result.final_synthesis or 'No accepted evidence is available for this request.'
            if routed.intent=='watchlist_add':answer+=' Use the Add to watchlist button to confirm tracking.'
            odds=None
            if self.crowd_odds:
                # Deterministic and outside the narrator: these values are shown as recorded, never narrated or guarded.
                try:odds=trace.call('Look up crowd odds','tool',lambda:self.crowd_odds(tickers),actor='Prediction markets',
                                    inputs={'tickers':tickers,'source':'Polymarket public API, read-only','trading':False})
                except Exception:odds=None  # an aid, never a blocker
                check()
            health=news_health(self.root/'news.db')
            payload={'kind':'research','mode':job['model'],'tickers':tickers,'charts':list(tools.charts.values()),
                     'crowd_odds':odds if odds and (odds.get('markets') or odds.get('errors')) else None,
                     'snapshots':tools.snapshots,'evidence':tools.evidence+news.evidence,
                     'faithful_synthesis':result.faithful_synthesis,'risk_mode':result.risk_mode,
                     'coverage_note':coverage_note(health,tickers),
                     'news_collected':bool(health.get('collected')),
                     # Accepted specialist outputs, so the UI can show structure without re-parsing the answer.
                     'reports':{'data':result.data_report.summary if result.data_report else None,
                                'news':result.news_report.summary if result.news_report else None,
                                'news_items':sum(len(v) for v in result.news_report.raw_facts.values()) if result.news_report else 0},
                     'memory':{'backend':self.memory.backend,'used_previous':bool(previous),
                               'recalled':[{'question':h.question[:160],'run_id':h.run_id,'item_id':h.item_id,'kind':h.kind,'score':h.score,**({'found_by':h.sources} if h.sources else {})} for h in hits]}}
            trace.data['summary'].update({'answer':answer[:2500],'model_choice':job['model']});trace.flush()
            with self.lock:
                check()
                self.store.finish(job['id'],answer,payload,tickers,trace.data['status'])
            remember=(job['session_id'],job['query'],answer,job['id'],tickers)
        except Cancelled:
            trace.event('Run cancelled','decision',{'reason':'user_cancelled'},fallback=True)
            trace.finish(error=True)
            self.store.progress(job['id'],'Stopped','cancelled','Stopped by user. Partial tool observations remain in the trace; no final answer was accepted.')
        except Exception as exc:
            trace.event('Run failed','decision',{'error_type':type(exc).__name__},fallback=True)
            trace.finish(error=True)
            self.store.progress(job['id'],'Could not complete research','error',f'{type(exc).__name__}: {str(exc)[:200]}')
        finally:
            with self.lock,self.models.lock:
                self.models.busy=False
                if self.active==job['id']:self.active=None
                self.events.pop(job['id'],None)
                if remember is None:self.threads.pop(job['id'],None)  # clarification, cancel and error paths
        if remember is None:return
        try:
            if not self.closed:self.remember(*remember)
        finally:
            with self.lock:self.threads.pop(job['id'],None)  # tracked until memory is written, so close() waits for it

    # -- memory items (spec: docs/designs/remember_forget_draft.md, review round 1) ---------------------------------
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
            if session_id in self.deleting:raise ModelError('This conversation is being deleted.')
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
            return self.ledger.ensure(uuid.uuid4().hex,session_id,'note',text=text,tickers=self.note_tickers(text),request_id=request_id)

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
        for run_id in removed['runs']:
            try:
                (self.root/'traces'/(valid_id(run_id)+'.json')).unlink();traces+=1
            except FileNotFoundError:pass
            except (OSError,ValueError):trace_errors+=1
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
            if self.active or self.threads:raise ModelError('Wait for the current research run, including its memory write, to finish.')
            ids=[s for s in self.store.session_ids() if s not in self.deleting]
            self.deleting.update(ids)
        results=[]
        try:
            for sid in ids:
                try:results.append(self._delete_one(sid))
                except KeyError:continue  # deleted by another request meanwhile
        finally:
            with self.lock:self.deleting.difference_update(ids)
        result={'deleted':len(results),'messages':sum(r['messages'] for r in results),
                'traces_deleted':sum(r['traces_deleted'] for r in results),
                'memory_pending':sum(r['memory_pending'] for r in results),
                'sweep_pending':sum(1 for r in results if r['sweep_pending']),
                'error':next((r['error'] for r in results if r['error']),'')}
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
        with self.lock:
            if self.news['request_id']==request_id:return self.news_snapshot()
            if self.closed:raise ModelError('Server is stopping.')
            if self.news['phase']=='running':raise ModelError('News collection is already running.')
            if self.active:raise ModelError('Wait for the active research run to finish before collecting news.')
            if not self.news_backend_factory and not self.gliner_path().exists():
                raise ModelError('The pinned GLiNER cache is missing. No unapproved download was attempted.')
            trace=LiveTrace('News collection: official company feeds',directory=self.root/'traces',progress=self.news_progress)
            cancel=threading.Event();self.news_cancel=cancel
            self.news.update(phase='running',stage='Starting collection',run_id=trace.run_id,error='',started=time.time(),finished=None,request_id=request_id)
            self.news_thread=threading.Thread(target=self.collect,args=(trace,cancel),daemon=True)
            self.news_thread.start()
        return self.news_snapshot()

    def news_progress(self,stage):
        with self.lock:
            if self.news['phase']=='running':self.news['stage']=stage

    def collect(self,trace,cancel):
        from .gliner_decide import GlinerDecideClassifier
        from .news_service import FeedFetcher
        from .news_store import NewsStore
        from .trace import run_traced_clock1
        def check():
            if cancel.is_set():raise Cancelled('News collection stopped.')
        store=None
        try:
            backend=self.news_backend_factory() if self.news_backend_factory else self.local_extractor(trace,cancel)
            fetch=FeedFetcher(self.feed_fetch,check=check) if self.feed_fetch else FeedFetcher(check=check)
            store=NewsStore(str(self.root/'news.db'))
            summary=run_traced_clock1(feed_tickers(),fetch,GlinerDecideClassifier(backend),trace,store=store)
            stage='Collected '+str(summary.fetched)+' items'+(' with source errors' if summary.errors or any(str(v).startswith('error') for s in summary.source_status.values() for v in s.values()) else '')
            with self.lock:self.news.update(phase='idle',stage=stage,finished=time.time())
        except Exception as exc:
            if trace.data.get('status')=='running':trace.finish(error=True)
            with self.lock:self.news.update(phase='error',stage='Collection failed',error=f'{type(exc).__name__}: {str(exc)[:200]}',finished=time.time())
        finally:
            if store:store.close()

    def close(self):
        self.closed=True
        if self.news_cancel:self.news_cancel.set()
        if self.news_thread:self.news_thread.join(timeout=2)
        with self.lock:
            for event in self.events.values():event.set()
        for thread in list(self.threads.values()):thread.join(timeout=2)
        if self.extractor and hasattr(self.extractor,"close"):self.extractor.close()
        if hasattr(self.memory,'close'):self.memory.close()  # stops the Hy-Memory worker and embedder when selected
        self.models.close()
