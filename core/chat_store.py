"""New v2 user namespace. Does not open, alter, or migrate legacy databases."""
from __future__ import annotations
import contextlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

ID=re.compile(r"[0-9a-f]{32}")
FINISHED=frozenset({"complete","degraded"})
STOPPED=frozenset({"cancelled","error","interrupted"})
NEXT={
    "queued":frozenset({"running","cancelling"})|STOPPED,
    "running":frozenset({"cancelling"})|FINISHED|STOPPED,
    "cancelling":STOPPED,
}
ACTIVE=frozenset(NEXT)
STATUSES=ACTIVE|FINISHED|STOPPED
WHY={"running":"worker starts","cancelling":"user asks to stop","complete":"answer saved",
     "degraded":"answer saved, a step fell back","cancelled":"stop honoured","error":"run failed",
     "interrupted":"server restarted"}


def statechart():
    """The chat job lifecycle as a Mermaid state diagram, built from NEXT so the picture cannot drift from the code.
    A run saves an answer only on the way to complete or degraded; every other end has none; no end ever moves."""
    lines=["stateDiagram-v2","    [*] --> queued"]
    lines+=[f"    {state} --> {to}: {WHY[to]}" for state,moves in NEXT.items() for to in sorted(moves)]
    lines+=[f"    {end} --> [*]" for end in sorted(FINISHED|STOPPED)]
    return "\n".join(lines)+"\n"


def valid_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError("Invalid local record identifier.")
    return value


class ChatStore:
    def __init__(self,path,on_change=None):
        self.path=Path(path)
        self._on_change=on_change
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,tickers TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS chat_messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created REAL NOT NULL,run_id TEXT,payload TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(session_id,created);
            CREATE TABLE IF NOT EXISTS chat_jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,query TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS user_watchlist(ticker TEXT PRIMARY KEY,added REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_documents(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,name TEXT NOT NULL,sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,page_count INTEGER NOT NULL,pages TEXT NOT NULL,created REAL NOT NULL,UNIQUE(session_id,sha256));
            CREATE TABLE IF NOT EXISTS pdf_source_v1(document_id TEXT PRIMARY KEY,pages TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS pdf_source_on_remove AFTER DELETE ON chat_documents BEGIN
                DELETE FROM pdf_source_v1 WHERE document_id=OLD.id;
            END;
            """)

    @contextlib.contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA secure_delete=ON")  # updates and deletes zero the freed space, so deleted chats are not left readable
        try:
            with db:yield db
        finally:db.close()

    def _changed(self):
        if self._on_change:
            self._on_change()

    def recover(self):
        with self.db() as db:
            changed=db.execute("UPDATE chat_jobs SET status='interrupted',stage='Server restarted',error='Run interrupted by server restart. Not automatically retried.',updated=? WHERE status IN (?,?,?)",(time.time(),*sorted(ACTIVE))).rowcount
        if changed:
            self._changed()

    def sessions(self):
        with self.db() as db:
            return [dict(r) for r in db.execute("SELECT id,title,created,updated FROM chat_sessions ORDER BY updated DESC LIMIT 100")]

    def session_ids(self):
        """Every conversation id, not just the 100 the sidebar lists (Clear all must reach all of them)."""
        with self.db() as db:
            return [r[0] for r in db.execute("SELECT id FROM chat_sessions ORDER BY updated DESC")]

    def delete_session(self,session_id):
        """Remove one conversation for good: its messages, runs and the conversation row, in one transaction.
        secure_delete zeroes the freed pages and the checkpoint moves that into the main file, so the text does not stay
        readable on disk. Refused while one of its runs is queued, running or cancelling. Returns the run ids, so the
        caller can delete their trace files."""
        valid_id(session_id)
        with self.db() as db:
            db.execute("PRAGMA secure_delete=ON")
            if not db.execute("SELECT 1 FROM chat_sessions WHERE id=?",(session_id,)).fetchone():raise KeyError("Conversation not found.")
            if db.execute("SELECT 1 FROM chat_jobs WHERE session_id=? AND status IN ('queued','running','cancelling')",(session_id,)).fetchone():
                raise ValueError("A run in this conversation is still active.")
            runs=[r[0] for r in db.execute("SELECT id FROM chat_jobs WHERE session_id=?",(session_id,))]
            runs+=[r[0] for r in db.execute("SELECT DISTINCT run_id FROM chat_messages WHERE session_id=? AND run_id IS NOT NULL",(session_id,)) if r[0] not in runs]
            messages=db.execute("DELETE FROM chat_messages WHERE session_id=?",(session_id,)).rowcount
            db.execute("DELETE FROM chat_jobs WHERE session_id=?",(session_id,))
            db.execute("DELETE FROM chat_documents WHERE session_id=?",(session_id,))  # attached PDFs' text goes with them
            db.execute("DELETE FROM chat_sessions WHERE id=?",(session_id,))
        with self.db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._changed()
        return {'messages':messages,'runs':runs}

    def create_session(self,request_id):
        valid_id(request_id);now=time.time()
        with self.db() as db:
            changed=db.execute("INSERT OR IGNORE INTO chat_sessions(id,title,created,updated) VALUES (?,'New research',?,?)",(request_id,now,now)).rowcount
        if changed:
            self._changed()
        return self.session(request_id)

    def session(self,session_id):
        valid_id(session_id)
        with self.db() as db:
            row=db.execute("SELECT * FROM chat_sessions WHERE id=?",(session_id,)).fetchone()
            if row is None:raise KeyError("Conversation not found.")
            result=dict(row);result['tickers']=json.loads(result['tickers'])
            messages=[]
            for row in db.execute("SELECT * FROM (SELECT * FROM chat_messages WHERE session_id=? ORDER BY created DESC LIMIT 100) ORDER BY created",(session_id,)):
                message=dict(row);message['payload']=json.loads(message['payload']);messages.append(message)
            result['messages']=messages
            result['jobs']=[dict(r) for r in db.execute("SELECT * FROM chat_jobs WHERE session_id=? ORDER BY created DESC LIMIT 20",(session_id,))]
            result['message_count']=db.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?",(session_id,)).fetchone()[0]
            result['documents']=[dict(r) for r in db.execute("SELECT id,name,bytes,page_count,created FROM chat_documents WHERE session_id=? ORDER BY created",(session_id,))]
            return result

    # -- attached PDFs (2026-11-19). Text only: the file itself is never stored, just its page texts and sha256. ------
    DOCUMENT_LIMIT=5

    def add_document(self,session_id,name,sha256,size,pages):
        """Attach one parsed PDF. The same file twice in one conversation is one document (returned as it is)."""
        valid_id(session_id)
        if not re.fullmatch(r"[0-9a-f]{64}",sha256 or ""):raise ValueError("Invalid document digest.")
        with self.db() as db:
            if not db.execute("SELECT 1 FROM chat_sessions WHERE id=?",(session_id,)).fetchone():raise KeyError("Conversation not found.")
            row=db.execute("SELECT id,name,bytes,page_count,created FROM chat_documents WHERE session_id=? AND sha256=?",(session_id,sha256)).fetchone()
            if row:return dict(row,duplicate=True)
            if db.execute("SELECT COUNT(*) FROM chat_documents WHERE session_id=?",(session_id,)).fetchone()[0]>=self.DOCUMENT_LIMIT:
                raise ValueError(f"A conversation can hold {self.DOCUMENT_LIMIT} PDFs. Remove one first.")
            doc={'id':uuid.uuid4().hex,'name':name,'bytes':int(size),'page_count':len(pages),'created':time.time()}
            db.execute("INSERT INTO chat_documents VALUES (?,?,?,?,?,?,?,?)",(doc['id'],session_id,name,sha256,doc['bytes'],doc['page_count'],json.dumps(list(pages)),doc['created']))
            from .documents import ExtractedPages
            if isinstance(pages, ExtractedPages):
                db.execute('INSERT INTO pdf_source_v1(document_id,pages) VALUES (?,?)',(doc['id'],json.dumps(pages.source_pages)))
            db.execute("UPDATE chat_sessions SET updated=? WHERE id=?",(doc['created'],session_id))
        self._changed()
        return dict(doc,duplicate=False)

    def documents(self,session_id):
        """Every attached PDF of one conversation with its page texts, read when a run starts."""
        valid_id(session_id)
        with self.db() as db:
            rows=db.execute('SELECT d.id,d.name,d.page_count,d.pages,s.pages AS source FROM chat_documents d LEFT JOIN pdf_source_v1 s ON s.document_id=d.id WHERE d.session_id=? ORDER BY d.created',(session_id,))
            return [dict(id=r['id'],name=r['name'],page_count=r['page_count'],pages=json.loads(r['pages']),
                         **({'source_pages':json.loads(r['source'])} if r['source'] is not None else {})) for r in rows]

    def remove_document(self,session_id,document_id):
        valid_id(session_id);valid_id(document_id)
        with self.db() as db:
            db.execute("PRAGMA secure_delete=ON")
            if db.execute("DELETE FROM chat_documents WHERE id=? AND session_id=?",(document_id,session_id)).rowcount!=1:
                raise KeyError("Document not found in this conversation.")
        with self.db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._changed()
        return {'removed':document_id}

    def job(self,run_id):
        valid_id(run_id)
        with self.db() as db:
            row=db.execute("SELECT * FROM chat_jobs WHERE id=?",(run_id,)).fetchone()
            if row is None:raise KeyError("Run not found.")
            return dict(row)

    def by_request(self,request_id,session_id,query,model):
        valid_id(request_id)
        with self.db() as db:
            row=db.execute("SELECT * FROM chat_jobs WHERE request_id=?",(request_id,)).fetchone()
            if row is not None:
                result=dict(row)
                if (result['session_id'],result['query'],result['model'])!=(session_id,query,model):raise ValueError("Request identifier was reused for different content.")
                return result

    def create_job(self,run_id,request_id,session_id,query,model):
        for item in (run_id,request_id,session_id):valid_id(item)
        now=time.time()
        with self.db() as db:
            if not db.execute("SELECT 1 FROM chat_sessions WHERE id=?",(session_id,)).fetchone():raise KeyError("Conversation not found.")
            if db.execute("SELECT COUNT(*) FROM chat_messages WHERE session_id=?",(session_id,)).fetchone()[0]>=500:raise ValueError("Start a new conversation before adding more messages.")
            db.execute("INSERT INTO chat_jobs VALUES (?,?,?,?,?,'queued','Queued',?,?,'')",(run_id,request_id,session_id,query,model,now,now))
            db.execute("INSERT INTO chat_messages(id,session_id,role,content,created,run_id) VALUES (?,?,'user',?,?,?)",(uuid.uuid4().hex,session_id,query,now,run_id))
            db.execute("UPDATE chat_sessions SET title=CASE WHEN title='New research' THEN ? ELSE title END,updated=? WHERE id=?",(query[:80],now,session_id))
        self._changed()
        return self.job(run_id)

    def progress(self,run_id,stage,status=None,error=""):
        """Move a run along NEXT, or with no status update the stage text of a running run. A move NEXT does not allow
        from the run's current status changes nothing, so a late or repeated update is harmless."""
        if status in FINISHED:raise ValueError("A finished status needs its answer: use finish().")
        sources=sorted(s for s,moves in NEXT.items() if status in moves)
        if status is not None and not sources:raise ValueError("Unknown run status.")
        with self.db() as db:
            if status is None:
                changed=db.execute("UPDATE chat_jobs SET stage=?,updated=? WHERE id=? AND status='running'",(stage[:160],time.time(),run_id)).rowcount
            else:
                changed=db.execute(f"UPDATE chat_jobs SET stage=?,status=?,updated=?,error=? WHERE id=? AND status IN ({','.join('?'*len(sources))})",
                           (stage[:160],status,time.time(),error[:300],run_id,*sources)).rowcount
        if changed:
            self._changed()

    def finish(self,run_id,answer,payload,tickers,status):
        """Save the answer and end the run in one transaction. Only a running run can finish, so a stopped, failed or
        interrupted run never gains an answer and no run is finished twice."""
        if status not in FINISHED:raise ValueError("A run with an answer ends as complete or degraded.")
        encoded=json.dumps(payload,ensure_ascii=True,allow_nan=False)
        if len(encoded)>2*1024*1024:raise ValueError("Research result exceeded the storage limit.")
        with self.db() as db:
            job=db.execute("SELECT * FROM chat_jobs WHERE id=?",(run_id,)).fetchone()
            if not job:raise KeyError("Run not found.")
            now=time.time()
            if db.execute("UPDATE chat_jobs SET status=?,stage='Finished',updated=? WHERE id=? AND status='running'",(status,now,run_id)).rowcount!=1:
                raise ValueError("The run is no longer running, so its answer was not saved.")
            db.execute("INSERT INTO chat_messages(id,session_id,role,content,created,run_id,payload) VALUES (?,?,'assistant',?,?,?,?)",(uuid.uuid4().hex,job['session_id'],answer[:16000],now,run_id,encoded))
            db.execute("UPDATE chat_sessions SET tickers=?,updated=? WHERE id=?",(json.dumps(tickers),now,job['session_id']))
        self._changed()

    def watchlist(self):
        with self.db() as db:return [dict(r) for r in db.execute("SELECT * FROM user_watchlist ORDER BY added DESC")]

    def add_watch(self,ticker):
        from .market import valid_symbol
        valid_symbol(ticker)
        with self.db() as db:changed=db.execute("INSERT OR IGNORE INTO user_watchlist VALUES (?,?)",(ticker,time.time())).rowcount
        if changed:
            self._changed()
        return self.watchlist()

    def remove_watch(self,ticker):
        from .market import valid_symbol
        valid_symbol(ticker)
        with self.db() as db:changed=db.execute("DELETE FROM user_watchlist WHERE ticker=?",(ticker,)).rowcount
        if changed:
            self._changed()
        return self.watchlist()
