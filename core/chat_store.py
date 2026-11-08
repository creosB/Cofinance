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
ACTIVE={"queued","running","cancelling"}


def valid_id(value):
    if not isinstance(value,str) or not ID.fullmatch(value):raise ValueError("Invalid local record identifier.")
    return value


class ChatStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY,title TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,tickers TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS chat_messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created REAL NOT NULL,run_id TEXT,payload TEXT NOT NULL DEFAULT '{}');
            CREATE INDEX IF NOT EXISTS chat_messages_session ON chat_messages(session_id,created);
            CREATE TABLE IF NOT EXISTS chat_jobs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,session_id TEXT NOT NULL,query TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS user_watchlist(ticker TEXT PRIMARY KEY,added REAL NOT NULL);
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

    def recover(self):
        with self.db() as db:
            db.execute("UPDATE chat_jobs SET status='interrupted',stage='Server restarted',error='Run interrupted by server restart. Not automatically retried.',updated=? WHERE status IN ('queued','running','cancelling')",(time.time(),))

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
            db.execute("DELETE FROM chat_sessions WHERE id=?",(session_id,))
        with self.db() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return {'messages':messages,'runs':runs}

    def create_session(self,request_id):
        valid_id(request_id);now=time.time()
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO chat_sessions(id,title,created,updated) VALUES (?,'New research',?,?)",(request_id,now,now))
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
            return result

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
        return self.job(run_id)

    def progress(self,run_id,stage,status=None,error=""):
        with self.db() as db:
            if status == 'running':
                db.execute("UPDATE chat_jobs SET stage=?,status='running',updated=?,error=? WHERE id=? AND status='queued'",(stage[:160],time.time(),error[:300],run_id))
            elif status == 'cancelling':
                db.execute("UPDATE chat_jobs SET stage=?,status='cancelling',updated=?,error=? WHERE id=? AND status IN ('queued','running')",(stage[:160],time.time(),error[:300],run_id))
            elif status:
                db.execute("UPDATE chat_jobs SET stage=?,status=?,updated=?,error=? WHERE id=? AND status IN ('queued','running','cancelling')",(stage[:160],status,time.time(),error[:300],run_id))
            else:db.execute("UPDATE chat_jobs SET stage=?,updated=? WHERE id=? AND status='running'",(stage[:160],time.time(),run_id))

    def finish(self,run_id,answer,payload,tickers,status):
        encoded=json.dumps(payload,ensure_ascii=True,allow_nan=False)
        if len(encoded)>2*1024*1024:raise ValueError("Research result exceeded the storage limit.")
        with self.db() as db:
            job=db.execute("SELECT * FROM chat_jobs WHERE id=?",(run_id,)).fetchone()
            if not job:raise KeyError("Run not found.")
            now=time.time()
            db.execute("INSERT INTO chat_messages(id,session_id,role,content,created,run_id,payload) VALUES (?,?,'assistant',?,?,?,?)",(uuid.uuid4().hex,job['session_id'],answer[:16000],now,run_id,encoded))
            db.execute("UPDATE chat_jobs SET status=?,stage='Finished',updated=? WHERE id=?",(status,now,run_id))
            db.execute("UPDATE chat_sessions SET tickers=?,updated=? WHERE id=?",(json.dumps(tickers),now,job['session_id']))

    def watchlist(self):
        with self.db() as db:return [dict(r) for r in db.execute("SELECT * FROM user_watchlist ORDER BY added DESC")]

    def add_watch(self,ticker):
        from .market import valid_symbol
        valid_symbol(ticker)
        with self.db() as db:db.execute("INSERT OR IGNORE INTO user_watchlist VALUES (?,?)",(ticker,time.time()))
        return self.watchlist()
