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

def list_tables():
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)
    try:
        cursor = connection.execute('''
            SELECT name FROM sqlite_master 
            WHERE type='table'
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name   
        ''')

        rows = cursor.fetchall()
        result = []
        for name in rows:
            result.append(name)
        return result
    finally:
        connection.close()

def list_chats():
    db_path = Path.home() / 'Library' / 'Messages' / 'chat.db'
    connection = sqlite3.connect(db_path.as_uri() + "?mode=ro",uri=True)
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
    chat_id = 1
    storage.init_db()
    conversation_id = f'imessage:{chat_id}'

    load_dotenv()
    recipient = os.getenv("IMESSAGE_RECIPIENT")

    if not recipient:
        raise ValueError("Set IMESSAGE_RECIPIENT in your .env file")

    last_seen_id = storage.load_last_seen_id(conversation_id)

    if last_seen_id is None:
        recent = load_conversation(chat_id,limit=1)
        if recent:
            last_seen_id = recent[0]['row_id']
        else:
            last_seen_id = 0
        storage.save_last_seen_id(conversation_id, last_seen_id)

    while True:
        new_messages = load_new_messages(chat_id,last_seen_id)
        for message in new_messages:
            message_text = message['text']
            if isinstance(message_text, str) and message_text.strip():
                print(f'Recieved message: {message_text}')
                try:
                    reply = assistant.handle_message(conversation_id, message_text)
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
            storage.save_last_seen_id(conversation_id, message['row_id'])
            last_seen_id = message['row_id']


        time.sleep(2)



