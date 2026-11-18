import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "brain.db")

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # Notlar tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        msg_type TEXT NOT NULL,
        content TEXT NOT NULL,
        summary TEXT,
        tags TEXT
    )
    """)
    
    # Tam metin arama (FTS5)
    cursor.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
        content,
        summary,
        tags,
        content='notes',
        content_rowid='id'
    )
    """)
    
    # Trigger: Not eklendiğinde FTS'e de ekle
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
        INSERT INTO notes_fts(rowid, content, summary, tags) 
        VALUES (new.id, new.content, new.summary, new.tags);
    END;
    """)

    # Trigger: Not silindiğinde FTS'ten sil
    cursor.execute("""
    CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
        INSERT INTO notes_fts(notes_fts, rowid, content, summary, tags) 
        VALUES('delete', old.id, old.content, old.summary, old.tags);
    END;
    """)

    # Hatırlatıcılar tablosu
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        remind_at TEXT NOT NULL,
        content TEXT NOT NULL,
        is_completed INTEGER DEFAULT 0
    )
    """)
    
    conn.commit()
    conn.close()

def add_note(user_id: int, msg_type: str, content: str, summary: str = "", tags: str = "") -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO notes (user_id, created_at, msg_type, content, summary, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, now_str, msg_type, content, summary, tags)
    )
    note_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return note_id

def search_notes(user_id: int, query: str, limit: int = 5):
    conn = get_connection()
    cursor = conn.cursor()
    
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
            cursor.execute("""
                SELECT n.id, n.created_at, n.msg_type, n.content, n.summary
                FROM notes n
                JOIN notes_fts f ON n.id = f.rowid
                WHERE n.user_id = ? AND notes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
            """, (user_id, fts_match, limit))
            results = [dict(row) for row in cursor.fetchall()]
        except Exception:
            results = []
            
    # Eğer FTS boş döndüyse anahtar kelimelerle LIKE ile arayalım
    if not results:
        for t in search_terms:
            if len(t) >= 3:
                like_term = f"%{t}%"
                cursor.execute("""
                    SELECT id, created_at, msg_type, content, summary
                    FROM notes
                    WHERE user_id = ? AND (content LIKE ? OR summary LIKE ? OR tags LIKE ?)
                    ORDER BY id DESC
                    LIMIT ?
                """, (user_id, like_term, like_term, like_term, limit))
                for row in cursor.fetchall():
                    if row["id"] not in [r["id"] for r in results]:
                        results.append(dict(row))
                if len(results) >= limit:
                    break
        
    # Eğer hala çok az sonuç varsa son eklenen notları getir
    if len(results) < 2:
        cursor.execute("""
            SELECT id, created_at, msg_type, content, summary
            FROM notes
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
        """, (user_id, limit))
        recent = [dict(row) for row in cursor.fetchall()]
        # Birleştir ve tekilleştir
        existing_ids = {r["id"] for r in results}
        for r in recent:
            if r["id"] not in existing_ids:
                results.append(r)
                
    conn.close()
    return results[:limit]

def add_reminder(user_id: int, remind_at: str, content: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO reminders (user_id, created_at, remind_at, content, is_completed) VALUES (?, ?, ?, ?, 0)",
        (user_id, now_str, remind_at, content)
    )
    reminder_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return reminder_id

def get_due_reminders():
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "SELECT id, user_id, content, remind_at FROM reminders WHERE is_completed = 0 AND remind_at <= ?",
        (now_str,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def mark_reminder_completed(reminder_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()

def cancel_reminder(user_id: int, reminder_id: int) -> bool:
    """Hatırlatıcıyı kullanıcı isteğiyle iptal eder (is_completed = 2)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE reminders SET is_completed = 2 WHERE id = ? AND user_id = ?",
        (reminder_id, user_id)
    )
    affected = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return affected

def get_last_active_reminder(user_id: int):
    """En son eklenen bekleyen aktif hatırlatıcıyı getirir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, remind_at, content FROM reminders WHERE user_id = ? AND is_completed = 0 ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_active_reminders(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, remind_at, content FROM reminders WHERE user_id = ? AND is_completed = 0 ORDER BY remind_at ASC",
        (user_id,)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_recent_notes(user_id: int, limit: int = 10):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, created_at, msg_type, content, summary FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_last_note(user_id: int):
    """En son kaydedilen notu getirir."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, content, summary, msg_type, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_note(user_id: int, note_id: int, new_content: str, new_summary: str = "") -> bool:
    """Mevcut bir notu günceller ve arama indeksini yeniler."""
    conn = get_connection()
    cursor = conn.cursor()
    if not new_summary:
        new_summary = new_content[:120]
    cursor.execute(
        "UPDATE notes SET content = ?, summary = ? WHERE id = ? AND user_id = ?",
        (new_content, new_summary, note_id, user_id)
    )
    affected = cursor.rowcount > 0
    if affected:
        try:
            cursor.execute("INSERT INTO notes_fts(notes_fts, rowid, content, summary, tags) VALUES('delete', ?, ?, ?, ?)", (note_id, "", "", ""))
        except Exception:
            pass
        try:
            cursor.execute("INSERT INTO notes_fts(rowid, content, summary, tags) VALUES(?, ?, ?, ?)", (note_id, new_content, new_summary, ""))
        except Exception:
            pass
    conn.commit()
    conn.close()
    return affected

