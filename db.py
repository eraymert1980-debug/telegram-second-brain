import os
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "brain.db")

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

if TURSO_DATABASE_URL.startswith("libsql://"):
    TURSO_DATABASE_URL = "https://" + TURSO_DATABASE_URL[9:]

_turso_client = None

def is_turso() -> bool:
    return bool(TURSO_DATABASE_URL and TURSO_AUTH_TOKEN)

def get_turso_client():
    global _turso_client
    if _turso_client is None:
        import libsql_client
        _turso_client = libsql_client.create_client_sync(
            url=TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN
        )
    return _turso_client

def get_sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def execute_query(sql: str, params=None) -> list:
    """SELECT sorguları çalıştırır ve dict listesi döner."""
    params = params or []
    if is_turso():
        client = get_turso_client()
        res = client.execute(sql, params)
        return [row.asdict() for row in res.rows]
    else:
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return rows

def execute_modify(sql: str, params=None):
    """INSERT, UPDATE, DELETE sorguları çalıştırır. (last_insert_rowid, rows_affected) döner."""
    params = params or []
    if is_turso():
        client = get_turso_client()
        res = client.execute(sql, params)
        return res.last_insert_rowid, res.rows_affected
    else:
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        cursor.execute(sql, params)
        last_id = cursor.lastrowid
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return last_id, affected

def init_db():
    """Tabloları ve FTS5 sanal tablolarını başlatır."""
    if is_turso():
        logger.info("⚡ Turso Kalıcı Bulut Veritabanı başlatılıyor...")
        client = get_turso_client()
        client.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            msg_type TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            tags TEXT
        );
        """)
        client.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            content,
            summary,
            tags,
            content='notes',
            content_rowid='id'
        );
        """)
        client.execute("""
        CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(rowid, content, summary, tags) 
            VALUES (new.id, new.content, new.summary, new.tags);
        END;
        """)
        client.execute("""
        CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, content, summary, tags) 
            VALUES('delete', old.id, old.content, old.summary, old.tags);
        END;
        """)
        client.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            content TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0
        );
        """)
        logger.info("✅ Turso veritabanı tabloları ve FTS5 hazır!")
    else:
        logger.info("📁 Yerel SQLite veritabanı (brain.db) başlatılıyor...")
        conn = get_sqlite_conn()
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            msg_type TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            tags TEXT
        );
        """)
        cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            content,
            summary,
            tags,
            content='notes',
            content_rowid='id'
        );
        """)
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
            INSERT INTO notes_fts(rowid, content, summary, tags) 
            VALUES (new.id, new.content, new.summary, new.tags);
        END;
        """)
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
            INSERT INTO notes_fts(notes_fts, rowid, content, summary, tags) 
            VALUES('delete', old.id, old.content, old.summary, old.tags);
        END;
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            content TEXT NOT NULL,
            is_completed INTEGER DEFAULT 0
        );
        """)
        conn.commit()
        conn.close()
        logger.info("✅ Yerel SQLite hazır!")

def add_note(user_id: int, msg_type: str, content: str, summary: str = "", tags: str = "") -> int:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_id, _ = execute_modify(
        "INSERT INTO notes (user_id, created_at, msg_type, content, summary, tags) VALUES (?, ?, ?, ?, ?, ?)",
        [user_id, now_str, msg_type, content, summary, tags]
    )
    return last_id

def search_notes(user_id: int, query: str, limit: int = 5):
    # Kelimeleri temizle ve ekleri/bağlaçları ayıkla
    cleaned_query = "".join(c for c in query if c.isalnum() or c.isspace()).strip()
    words = cleaned_query.split()
    stopwords = {"ne", "nedir", "neydi", "kim", "kimdir", "kaç", "kaçtı", "bana", "söyle", "ve", "ile", "de", "da", "mı", "mi", "mu", "mü"}
    
    search_terms = []
    for w in words:
        wl = w.lower()
        if wl in stopwords:
            continue
        search_terms.append(w)
        # Türkçe iyelik eki temizleme (Örn: mertin -> mert, zarifenin -> zarife)
        for suffix in ["nin", "nın", "nun", "nün", "in", "ın", "un", "ün"]:
            if len(wl) > len(suffix) + 2 and wl.endswith(suffix):
                base = w[:-len(suffix)]
                if base not in search_terms:
                    search_terms.append(base)
    
    if not search_terms:
        search_terms = words
        
    results = []
    if search_terms:
        fts_match = " OR ".join([f'"{w}"*' for w in search_terms])
        try:
            results = execute_query("""
                SELECT n.id, n.created_at, n.msg_type, n.content, n.summary
                FROM notes n
                JOIN notes_fts f ON n.id = f.rowid
                WHERE n.user_id = ? AND notes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, [user_id, fts_match, limit])
        except Exception as e:
            logger.warning(f"FTS arama hatası (fallback edilecek): {e}")
            results = []
            
    # Eğer FTS boş döndüyse anahtar kelimelerle LIKE ile arayalım
    if not results:
        for t in search_terms:
            if len(t) >= 3:
                like_term = f"%{t}%"
                like_results = execute_query("""
                    SELECT id, created_at, msg_type, content, summary
                    FROM notes
                    WHERE user_id = ? AND (content LIKE ? OR summary LIKE ? OR tags LIKE ?)
                    ORDER BY id DESC
                    LIMIT ?
                """, [user_id, like_term, like_term, like_term, limit])
                for row in like_results:
                    if row["id"] not in [r["id"] for r in results]:
                        results.append(row)
                if len(results) >= limit:
                    break
        
    # Eğer hala çok az sonuç varsa son eklenen notları getir
    if len(results) < 2:
        recent = execute_query("""
            SELECT id, created_at, msg_type, content, summary
            FROM notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, [user_id, limit])
        existing_ids = {r["id"] for r in results}
        for r in recent:
            if r["id"] not in existing_ids:
                results.append(r)
                
    return results[:limit]

def add_reminder(user_id: int, remind_at: str, content: str) -> int:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_id, _ = execute_modify(
        "INSERT INTO reminders (user_id, created_at, remind_at, content, is_completed) VALUES (?, ?, ?, ?, 0)",
        [user_id, now_str, remind_at, content]
    )
    return last_id

def get_due_reminders():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return execute_query(
        "SELECT id, user_id, content, remind_at FROM reminders WHERE is_completed = 0 AND remind_at <= ?",
        [now_str]
    )

def mark_reminder_completed(reminder_id: int):
    execute_modify("UPDATE reminders SET is_completed = 1 WHERE id = ?", [reminder_id])

def cancel_reminder(user_id: int, reminder_id: int) -> bool:
    """Hatırlatıcıyı kullanıcı isteğiyle iptal eder (is_completed = 2)."""
    _, affected = execute_modify(
        "UPDATE reminders SET is_completed = 2 WHERE id = ? AND user_id = ?",
        [reminder_id, user_id]
    )
    return affected > 0

def get_last_active_reminder(user_id: int):
    """En son eklenen bekleyen aktif hatırlatıcıyı getirir."""
    rows = execute_query(
        "SELECT id, remind_at, content FROM reminders WHERE user_id = ? AND is_completed = 0 ORDER BY id DESC LIMIT 1",
        [user_id]
    )
    return rows[0] if rows else None

def get_active_reminders(user_id: int):
    return execute_query(
        "SELECT id, remind_at, content FROM reminders WHERE user_id = ? AND is_completed = 0 ORDER BY remind_at ASC",
        [user_id]
    )

def get_recent_notes(user_id: int, limit: int = 10):
    return execute_query(
        "SELECT id, created_at, msg_type, content, summary FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        [user_id, limit]
    )

def get_last_note(user_id: int):
    """En son kaydedilen notu getirir."""
    rows = execute_query(
        "SELECT id, content, summary, msg_type, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        [user_id]
    )
    return rows[0] if rows else None

def update_note(user_id: int, note_id: int, new_content: str, new_summary: str = "") -> bool:
    """Mevcut bir notu günceller ve arama indeksini yeniler."""
    if not new_summary:
        new_summary = new_content[:120]
    _, affected = execute_modify(
        "UPDATE notes SET content = ?, summary = ? WHERE id = ? AND user_id = ?",
        [new_content, new_summary, note_id, user_id]
    )
    if affected > 0:
        try:
            execute_modify(
                "INSERT INTO notes_fts(notes_fts, rowid, content, summary, tags) VALUES('delete', ?, ?, ?, ?)",
                [note_id, "", "", ""]
            )
        except Exception:
            pass
        try:
            execute_modify(
                "INSERT INTO notes_fts(rowid, content, summary, tags) VALUES(?, ?, ?, ?)",
                [note_id, new_content, new_summary, ""]
            )
        except Exception:
            pass
    return affected > 0
