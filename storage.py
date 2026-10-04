# SQLite persistence for Aegis: conversation history, notes, and iMessage polling state.
import sqlite3
from datetime import datetime, timedelta

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
        connection.execute("""
            CREATE TABLE IF NOT EXISTS reminders(
            id INTEGER PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            active BOOL NOT NULL,
            recurrence TEXT,
            content TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

#--------------------------reminders------------------------#

DATETIME_FORMAT = '%Y-%m-%d %H:%M:%S'  # Stored remind_at format; fixed width, so string order matches time order.
ALLOWED_RECURRENCE = {None, 'daily', 'weekly'}
RECURRENCE_STEPS = {'daily': timedelta(days=1), 'weekly': timedelta(weeks=1)}
PAST_GRACE = timedelta(minutes=1)  # the model only sees the time to the minute, so allow "now" to be slightly behind
_UNSET = object()  # sentinel: caller didn't pass this field

# Step a recurring reminder forward until it's after now, skipping occurrences that have already passed.
def next_occurrence(remind_at, recurrence, now):
    step = RECURRENCE_STEPS[recurrence]
    while remind_at <= now:
        remind_at += step
    return remind_at

# Make sure a new remind_at isn't already in the past, so it isn't sent straight away as a missed reminder.
# Recurring reminders move to their next occurrence; one-time reminders in the past are rejected.
def _future_remind_at(remind_at, recurrence):
    dt = datetime.strptime(remind_at, DATETIME_FORMAT)
    now = datetime.now()
    if dt >= now - PAST_GRACE:
        return remind_at
    if not recurrence:
        raise ValueError(f'remind_at {remind_at} is in the past (current time is {now:%Y-%m-%d %H:%M}). '
                         'Use a future time, e.g. the same time tomorrow if that is what the user meant.')
    return next_occurrence(dt, recurrence, now).strftime(DATETIME_FORMAT)

# Strip whitespace and NUL bytes (which can't be passed to osascript) from reminder text.
# Raises ValueError if nothing is left.
def _clean_content(content):
    if not isinstance(content, str):
        raise ValueError('Reminder is empty.')
    content = content.replace('\x00', '').strip()
    if content == '':
        raise ValueError('Reminder is empty.')
    return content

# Parse remind_at and return it in one fixed format so ORDER BY remind_at sorts correctly.
def _clean_remind_at(remind_at):
    try:
        dt = datetime.fromisoformat(remind_at)
    except (TypeError, ValueError):
        raise ValueError(f'Invalid remind_at: {remind_at!r}. Use ISO format, e.g. 2026-10-02 09:00.')
    if len(remind_at) <= 10:  # Date only (e.g. 2026-10-02), which fromisoformat would turn into midnight.
        raise ValueError(f'remind_at {remind_at!r} has no time. Ask the user what time to remind them.')
    if dt.tzinfo:
        dt = dt.astimezone().replace(tzinfo=None)  # convert to this machine's local time, then drop the offset
    return dt.strftime(DATETIME_FORMAT)

# Normalize recurrence and make sure it's one we support. Empty string or 'none' means no recurrence.
def _clean_recurrence(recurrence):
    if isinstance(recurrence, str):
        recurrence = recurrence.strip().lower()
        if recurrence in ('', 'none'):
            recurrence = None
    # Type check first: a set lookup on an unhashable value (list, dict) would raise TypeError instead.
    if not isinstance(recurrence, (str, type(None))) or recurrence not in ALLOWED_RECURRENCE:
        raise ValueError(f'Invalid recurrence: {recurrence!r}.')
    return recurrence

# Save a reminder (whitespace-stripped) and return (new ID, scheduled remind_at).
# Raises ValueError if there's no content or a one-time reminder is in the past.
def save_reminder(conversation_id, content, remind_at, recurrence=None):
    content = _clean_content(content)
    recurrence = _clean_recurrence(recurrence)
    remind_at = _future_remind_at(_clean_remind_at(remind_at), recurrence)

    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""
        INSERT INTO reminders (conversation_id, content, recurrence, remind_at,active) VALUES(?,?,?,?,?)
        """,
        (conversation_id,content,recurrence,remind_at,True)) #set active to true automatically
        connection.commit()
        return cursor.lastrowid, remind_at
    finally:
        connection.close()


# Return a conversation's active reminders as [{'id','content','recurrence','remind_at'}], ordered by remind_at.
# If now is given (in DATETIME_FORMAT), only reminders due at or before it are returned.
def list_reminders(conversation_id,now = None):
    due_filter = 'AND remind_at <= ?' if now else ''
    params = (conversation_id, now) if now else (conversation_id,)

    reminders = []
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute(f"""SELECT id, content, recurrence, remind_at FROM reminders
        WHERE conversation_id = ?
        AND active = 1
        {due_filter}
        ORDER BY remind_at ASC
        """, params)
        rows = cursor.fetchall()

        for id, content, recurrence, remind_at in rows:
            reminders.append({'id': id, 'content': content, 'recurrence': recurrence, 'remind_at': remind_at})
    finally:
        connection.close()
    return reminders

# Delete a reminder by ID. Returns True if a reminder was deleted, False if none matched.
def delete_reminder(conversation_id, reminder_id):
    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute("""
            DELETE FROM reminders
            WHERE id = ?
            AND conversation_id = ?
            """, (reminder_id, conversation_id))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()

# Return a reminder's current recurrence, or None if it has none or doesn't exist.
# Takes the caller's connection so the read happens in the same transaction as the caller's write.
def _get_recurrence(connection, conversation_id, reminder_id):
    row = connection.execute("""
        SELECT recurrence FROM reminders
        WHERE id = ?
        AND conversation_id = ?
        """, (reminder_id, conversation_id)).fetchone()
    return row[0] if row else None

# Update any subset of a reminder's fields. Returns True if a reminder was updated, False if none matched.
def update_reminder(conversation_id, reminder_id, *,
                    content=_UNSET, remind_at=_UNSET,
                    recurrence=_UNSET, active=_UNSET):
    updates = {}

    if content is not _UNSET:
        updates['content'] = _clean_content(content)
    if remind_at is not _UNSET:
        updates['remind_at'] = _clean_remind_at(remind_at)
    if recurrence is not _UNSET:
        updates['recurrence'] = _clean_recurrence(recurrence)
    if active is not _UNSET:
        if not isinstance(active, bool):
            raise ValueError('active must be true or false.')
        updates['active'] = active

    if not updates:
        raise ValueError('Nothing to update.')

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.execute('BEGIN IMMEDIATE')  # Read the current recurrence and write in one transaction.

        # A new time is checked against the recurrence it will have after this update.
        if 'remind_at' in updates:
            recurrence = updates['recurrence'] if 'recurrence' in updates else _get_recurrence(connection, conversation_id, reminder_id)
            updates['remind_at'] = _future_remind_at(updates['remind_at'], recurrence)

        # Column names come from the fixed keys above, never from input, so the f-string is safe.
        set_clause = ', '.join(f'{column} = ?' for column in updates)
        params = (*updates.values(), reminder_id, conversation_id)

        cursor = connection.execute(f"""
            UPDATE reminders
            SET {set_clause}
            WHERE id = ?
            AND conversation_id = ?
        """, params)
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()

#Marks reminder sent. Checks for updates between when this call was made and when the update is occuring to avoid errors.
def mark_reminder_sent(conversation_id, reminder_id, sent_remind_at, next_remind_at=None):
    if next_remind_at:
        set_clause, params = 'remind_at = ?', (next_remind_at,)
    else:
        set_clause, params = 'active = 0', ()

    connection = sqlite3.connect(DB_PATH)
    try:
        cursor = connection.execute(f"""
            UPDATE reminders
            SET {set_clause}
            WHERE id = ?
            AND conversation_id = ?
            AND remind_at = ?
            AND active = 1
            """, (*params, reminder_id, conversation_id, sent_remind_at))
        connection.commit()
        return cursor.rowcount > 0
    finally:
        connection.close()