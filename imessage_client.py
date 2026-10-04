# iMessage front end for Aegis. Polls the local Messages database (~/Library/Messages/chat.db)
# for new incoming messages, passes them to the assistant, and sends replies via AppleScript.
# Run this file directly to start the polling loop.
import sqlite3
from pathlib import Path
import time
import subprocess
import assistant
import storage
import logging
import requests
import os
from dotenv import load_dotenv

# Debug helper: list the tables in the Messages database.
def list_tables():
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'  # Messages database for the current macOS user.
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)  # Open read-only so we never modify Messages' own database.
    try:
        # Table names, excluding SQLite's internal tables.
        cursor = connection.execute(''' 
            SELECT name FROM sqlite_master 
            WHERE type='table'
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name   
        ''')

        rows = cursor.fetchall()
        result = []
        for name in rows:  # Each row is a 1-tuple: (name,)
            result.append(name)
        return result
    finally:
        connection.close()

# Debug helper: return (ROWID, chat_identifier, display_name) for the 20 most recently created chats.
# Use this to find the ROWID to set as IMESSAGE_CHAT_ID.
def list_chats():
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'

    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)  # Read-only.
    try:
        cursor = connection.execute('''
        SELECT ROWID, chat_identifier, display_name
        FROM chat
        ORDER BY ROWID DESC
        LIMIT 20
        ''')
        rows = cursor.fetchall()
        return rows
    finally:
        connection.close()

# Return up to `limit` of the most recent incoming messages in a chat, newest first.
# Only incoming messages (is_from_me = 0) are returned; Aegis's own replies are excluded.
def load_conversation(chat_id,limit=10):
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)
    try:
        query = '''SELECT m.ROWID, m.text, m.is_from_me
                FROM message AS m
                JOIN chat_message_join AS cmj
                ON m.ROWID = cmj.message_id
                WHERE cmj.chat_id = ? AND m.is_from_me = 0
                ORDER BY m.ROWID DESC
                LIMIT ?'''
        cursor = connection.execute(query, (chat_id,limit))

        conversation = []
        rows = cursor.fetchall()

        for rowid, text, from_me in rows:
            conversation.append({'row_id':rowid, 'text': text, 'from_me':from_me})
        return conversation
    finally:
        connection.close()

# Return up to `limit` incoming messages with ROWID > after_id, oldest first.
# Tracking after_id keeps Aegis from replying to the same message twice.
def load_new_messages(chat_id,after_id,limit=20): 
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)
    try:
        query = '''SELECT m.ROWID, m.text, m.is_from_me
                FROM message AS m
                JOIN chat_message_join AS cmj
                ON m.ROWID = cmj.message_id
                WHERE cmj.chat_id = ? AND m.is_from_me = 0 AND m.ROWID > ?
                ORDER BY m.ROWID ASC
                LIMIT ?'''
        cursor = connection.execute(query, (chat_id,after_id,limit))

        conversation = []
        rows = cursor.fetchall()
        for rowid, text, from_me in rows:
            conversation.append({'row_id':rowid, 'text': text, 'from_me':from_me})
        return conversation
    finally:
        connection.close()

# Send `text` to `recipient` (phone number or Apple ID email) over iMessage via AppleScript.
# Raises CalledProcessError or TimeoutExpired if osascript fails.
def send_message(recipient, text):
    script = '''
    on run argv
        set recipientAddress to item 1 of argv
        set outgoingText to item 2 of argv

        tell application "Messages"
            set messagingService to first service whose service type is iMessage
            set destination to buddy recipientAddress of messagingService
            send outgoingText to destination
        end tell
    end run
    '''
    subprocess.run(
        ["/usr/bin/osascript", "-e", script, recipient, text],
        check = True,
        timeout=30
    )

if __name__ == '__main__':
    #set up logging
    logging.basicConfig(
    filename='assistant.log',
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
    )
        
    # Load the reply recipient and the chat to watch from .env.
    load_dotenv()
    recipient = os.getenv("IMESSAGE_RECIPIENT")
    if not recipient:
        raise ValueError("Set IMESSAGE_RECIPIENT in your .env file")
    
    chat_id = os.getenv('IMESSAGE_CHAT_ID')
    if not chat_id:
        chats = list_chats()
        raise ValueError(f'Set IMESSAGE_CHAT_ID in your .env file, your top 20 chats are: \n {chats}')
    conversation_id = f'imessage:{chat_id}'

    # Create Aegis's own tables if needed.
    storage.init_db()

    # ROWID of the last Messages row already handled.
    last_seen_id = storage.load_last_seen_id(conversation_id)

    if last_seen_id is None:  # First run: start from the newest existing message so old history isn't answered.
        recent = load_conversation(chat_id,limit=1)
        if recent:
            last_seen_id = recent[0]['row_id']
        else:
            last_seen_id = 0
        storage.save_last_seen_id(conversation_id, last_seen_id)

    # Send due reminders from a background thread so they aren't held up by slow model replies.
    def send_reminder(text):
        send_message(recipient, text)

    assistant.start_reminder_thread(conversation_id, send_reminder)

    # Poll for new messages and reply to each in order. last_seen_id advances even if a reply
    # fails, so a failing message is skipped rather than retried forever.
    while True:
        try:
            new_messages = load_new_messages(chat_id,last_seen_id)
        except sqlite3.Error:
            logging.exception("Could not read Messages database")
            time.sleep(2)
            continue
        for message in new_messages:
            message_text = message['text']
            if isinstance(message_text, str) and message_text.strip():
                print(f'Received message: {message_text}')
                try:
                    reply = assistant.handle_message(conversation_id, message_text)
                    if not reply or not reply.strip():
                        logging.warning("Empty reply for message %s", message['row_id'])
                        reply = "Sorry, I couldn't come up with a reply. Try rephrasing?"
                    send_message(recipient, reply)
                    print(f"Sent: {reply}")

                except requests.exceptions.RequestException:
                    logging.exception(
                        "Ollama request failed for message %s",
                        message['row_id']
                    )

                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    logging.exception(
                        "Sending reply failed for message %s",
                        message['row_id']
                    )
                except Exception:
                    logging.exception("Unexpected error handling message %s", message['row_id'])

            storage.save_last_seen_id(conversation_id, message['row_id'])
            last_seen_id = message['row_id']
        time.sleep(2)  # Poll interval in seconds.



