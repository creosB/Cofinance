import sqlite3
import os

DB_FILE = "watchlist.db"
AGENT_DB = "agent_storage.db"

def init_db():
    """Initialize the SQLite database for the watchlist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT UNIQUE,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def add_to_watchlist(ticker: str) -> str:
    """
    Add a ticker symbol to the watchlist.
    
    Args:
        ticker (str): The stock or crypto ticker symbol (e.g., 'AAPL', 'BTC-USD')
    
    Returns:
        str: Success or error message
    
    Example:
        add_to_watchlist(ticker='AAPL')
        add_to_watchlist(ticker='BTC-USD')
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Check if already exists
        c.execute('SELECT ticker FROM watchlist WHERE ticker = ?', (ticker.upper(),))
        if c.fetchone():
            conn.close()
            return f"ℹ️ {ticker.upper()} is already in your watchlist."
        c.execute('INSERT INTO watchlist (ticker) VALUES (?)', (ticker.upper(),))
        conn.commit()
        conn.close()
        return f"✅ Added {ticker.upper()} to watchlist."
    except Exception as e:
        return f"❌ Error adding to watchlist: {e}"

def get_watchlist():
    """Retrieve the current watchlist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT ticker, added_at FROM watchlist ORDER BY added_at DESC')
    data = c.fetchall()
    conn.close()
    return data

def remove_from_watchlist(ticker: str) -> str:
    """
    Remove a specific ticker from the watchlist.
    
    Args:
        ticker (str): The stock or crypto ticker symbol to remove
    
    Returns:
        str: Success or error message
    """
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('DELETE FROM watchlist WHERE ticker = ?', (ticker.upper(),))
        rows_affected = c.rowcount
        conn.commit()
        conn.close()
        
        if rows_affected > 0:
            return f"✅ Removed {ticker.upper()} from watchlist."
        else:
            return f"ℹ️ {ticker.upper()} was not in your watchlist."
    except Exception as e:
        return f"❌ Error removing from watchlist: {e}"

def clear_watchlist():
    """Clear the entire watchlist."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('DELETE FROM watchlist')
    conn.commit()
    conn.close()

def get_all_sessions():
    """Retrieve all agent sessions from storage."""
    try:
        conn = sqlite3.connect(AGENT_DB)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_sessions'")
        if not c.fetchone():
            conn.close()
            return []
        c.execute("SELECT session_id, created_at FROM agent_sessions ORDER BY created_at DESC LIMIT 10")
        data = c.fetchall()
        conn.close()
        return data
    except Exception:
        return []

def delete_session(session_id: str):
    """Delete a specific session from agent_sessions and memory tables."""
    try:
        # Delete from agent_sessions
        conn = sqlite3.connect(AGENT_DB)
        c = conn.cursor()
        c.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        conn.close()
        
        # Delete from memory database if exists
        from utils.memory import MemoryStore
        mem = MemoryStore()
        # Delete messages, entities, facts, events, artifacts for this session
        mem_conn = sqlite3.connect(mem.db_path)
        mem_c = mem_conn.cursor()
        for table in ['messages', 'entities', 'facts', 'events', 'artifacts']:
            try:
                mem_c.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
            except Exception:
                pass
        mem_conn.commit()
        mem_conn.close()
    except Exception:
        pass

def delete_all_sessions():
    """Delete all sessions from agent_sessions and memory tables."""
    try:
        # Delete all from agent_sessions
        conn = sqlite3.connect(AGENT_DB)
        c = conn.cursor()
        c.execute("DELETE FROM agent_sessions")
        conn.commit()
        conn.close()
        
        # Delete all from memory database if exists
        from utils.memory import MemoryStore
        mem = MemoryStore()
        # Delete all messages, entities, facts, events, artifacts
        mem_conn = sqlite3.connect(mem.db_path)
        mem_c = mem_conn.cursor()
        for table in ['messages', 'entities', 'facts', 'events', 'artifacts']:
            try:
                mem_c.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        mem_conn.commit()
        mem_conn.close()
    except Exception:
        pass
