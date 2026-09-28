# SQLite persistence for Aegis: conversation history, notes, and iMessage polling state.
import sqlite3

DB_PATH = 'chat.db'

# Create the tables if they don't already exist. Safe to call on every startup.
def init_db():
    connection = sqlite3.connect(DB_PATH)
    try:
        # Conversation history: user messages and Aegis's replies.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # User notes, scoped per conversation.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS notes(
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Last processed Messages ROWID per conversation, so polling resumes where it left off.
        connection.execute("""
            CREATE TABLE IF NOT EXISTS polling_state(
                conversation_id TEXT PRIMARY KEY NOT NULL,
                last_seen_id INTEGER NOT NULL
            )
        """)
    finally:
        connection.close()

# Append one message to a conversation's history.
def save_message(conversation_id, role, content):
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""INSERT INTO messages (conversation_id, role, content) VALUES(?,?,?)""",(conversation_id, role, content))
        connection.commit()
    finally:
        connection.close()

# Return the last `limit` messages as [{'role', 'content'}], oldest first.
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

# Delete all message history for a conversation. Notes are kept.
def clear_conversation(conversation_id):
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute("""DELETE FROM messages WHERE conversation_id = ?""",(conversation_id,))
        connection.commit()
    finally:
        connection.close()

# Save a note (whitespace-stripped). Raises ValueError if the note is empty.
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


# Return a conversation's notes as [{'id', 'content'}], ordered by id.
def list_notes(conversation_id):
    notes = []
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""SELECT id, note FROM notes
        WHERE conversation_id = ?
        ORDER BY id ASC
        """, (conversation_id,))
        rows = cursor.fetchall()

        for id, content in rows:
            notes.append({'id': id, 'content': content})
    finally:
        connection.close()
    return notes

# Delete a note by ID. Returns True if a note was deleted, False if none matched.
def delete_note(conversation_id, note_id):
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""
            DELETE FROM notes
            WHERE id = ?
            AND conversation_id = ?
            """, (note_id, conversation_id))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()

# Replace a note's text. Returns True if a note was updated, False if none matched.
def update_note(conversation_id, note_id, content):
    content = content.strip()
    if content == '':
        raise ValueError('Note is empty.')

    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""
            UPDATE notes
            SET note = ?
            WHERE id = ?
            AND conversation_id = ?
        """, (content, note_id, conversation_id))
        connection.commit()
        return cursor.rowcount > 0 
    finally:
        connection.close()

# Return the stored last_seen_id for a conversation, or None if there is none yet.
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

# Insert or update the last_seen_id for a conversation.
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