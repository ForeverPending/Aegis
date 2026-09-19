#storage for my chat bot
import sqlite3

DB_PATH = 'chat.db'

def init_db():
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS polling_state(
                conversation_id TEXT PRIMARY KEY NOT NULL,
                last_seen_id INTEGER NOT NULL
            )
        """)
    finally:
        connection.close()

#regarding messages
def save_message(conversation_id, role, content):
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""INSERT INTO messages (conversation_id, role, content) VALUES(?,?,?)""",(conversation_id, role, content))
        connection.commit()
    finally:
        connection.close()


def load_recent_messages(conversation_id, limit=6):
    messages = []
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""SELECT role, content
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id DESC
        LIMIT ? 
        """, 
        (conversation_id, limit))
        
        rows = cursor.fetchall()
        rows.reverse()

        for role, content in rows:
            messages.append({'role':role, 'content':content})
    finally:
        connection.close()
    return messages

def clear_conversation(conversation_id):
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""DELETE FROM messages WHERE conversation_id = ?""",(conversation_id,))
        connection.commit()
    finally:
        connection.close()

#regarding notes
def save_note(conversation_id, note):
    note = note.strip()
    if note == '':
        raise ValueError('Note is empty.')
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""INSERT INTO notes (conversation_id,note) VALUES(?,?) """,
                           (conversation_id,note))
        connection.commit()
    finally:
        connection.close()

def load_notes(conversation_id):
    notes = []
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""SELECT id, note FROM notes
        WHERE conversation_id = ?
        ORDER BY id ASC
        """, (conversation_id,))
        rows = cursor.fetchall()

        for id, note in rows:
            notes.append((id, note))
    finally:
        connection.close()
    return notes

def delete_note(conversation_id, note_id):
    connection = sqlite3.connect(DB_PATH)

    
    try:
        connection.execute("""
            DELETE FROM notes
            WHERE id = ?
            AND conversation_id = ?
            """, (note_id, conversation_id))
        connection.commit()
    finally:
        connection.close()

def load_last_seen_id(conversation_id):
    connection = sqlite3.connect(DB_PATH)

    try:
        cursor = connection.execute("""
            SELECT last_seen_id FROM polling_state
            WHERE conversation_id = ?
        """, (conversation_id,))

        rows = cursor.fetchone()
        if not rows:
            return None
        else:
            return rows[0]
    finally:
        connection.close()

def save_last_seen_id(conversation_id, last_seen_id):
    connection = sqlite3.connect(DB_PATH)

    try:
        connection.execute("""
            INSERT INTO polling_state (conversation_id, last_seen_id) 
            VALUES (?,?)
            ON CONFLICT(conversation_id)
            DO UPDATE SET last_seen_id = excluded.last_seen_id
        """, (conversation_id, last_seen_id))
        connection.commit()
    finally:
        connection.close()