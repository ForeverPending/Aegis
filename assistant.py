# Core assistant logic: builds the prompt sent to the model, handles slash commands,
# and runs the tool-call loop. Contains the system prompt, tool schemas, and tool dispatch.
# Also sends due reminders, shared by the iMessage and CLI front ends.
import storage
import logging
import ollama_client
import requests
import json
import datetime
import threading
import time



# Base system prompt. The save_note rule stops the model from calling the tool on every message.
# handle_message() appends the current notes as JSON so the model can reference note IDs directly.
SYSTEM_PROMPT = ("""You are a concise personal assistant. Answer briefly.
Only call save_note when the user explicitly asks to save or create a note.
Only call save_reminder when the user explicitly asks to be reminded of something.
For ordinary questions and conversation, answer directly without saving a note or reminder.
The user's notes and active reminders as of this message are listed below as JSON.
To update or delete a note, use its ID from this list or from list_notes. IDs mentioned in earlier messages may be outdated; ignore them.
Notes you save while answering will not appear in this list. Call list_notes if you need their IDs.
To update or delete a reminder, use its ID from the reminders list. Reminders you save while answering will not appear in it; use the ID returned by save_reminder.
Never invent an ID.
If multiple notes or reminders could match the user's request, ask which one they mean.
Current date and time are below in ISO format.
Use the current date and time to resolve relative times such as 'tomorrow' into ISO format""")

MAX_TOOL_ROUNDS = 5  # Caps model/tool round trips per message to prevent infinite tool loops.

# Tool schemas advertised to the model (Ollama function-calling format).
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "save_note",
            "description": "Save a note when the user asks to save or create a note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "The user's note text, preserving their wording and excluding the request to save it. Do not expand or embellish the note."
                    }
                },
                "required": ["note"]
            }
        }
    }
    ,
    {
        'type': 'function',
        'function':{
            'name': 'update_note',
            'description':(
                'Replace the content of an existing note when a user asks to update a note. '
                'If multiple notes match, ask the user which one.'
            ),
            'parameters': {
                'type': 'object',
                'properties': {
                    'note_id' : {
                        'type' : 'integer',
                        'description': 'The database ID of the note to be changed'
                    },
                    'new_note' : {
                        'type' : 'string',
                        'description': 'The complete replacement text for the note.'
                    }
                },
                'required': ['note_id', 'new_note']
            }
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "list_notes",
            "description": (
                "Get saved notes and their IDs for the current conversation. "
                "Use this to identify a note before updating or deleting it."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
    ,
    {
        "type": "function",
        "function": {
            "name": "delete_note",
            "description": (
                'When the user asks to delete a note, delete the note the user asks to delete. '
                'If multiple notes match, ask the user which one.'
            )
            ,
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {
                        "type":"integer",
                        "description": "The database ID of the note to be deleted"
                    }
                },
                "required": ["note_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_reminder",
            "description": (
                "Save a reminder when the user explicitly asks to be reminded of something. "
                "The user should specify the content, date, and time of the reminder at the minimum. "
                "Only add a recurrence if the user says how often to repeat it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content":{
                        "type" : "string",
                        "description": "What to remind the user about, preserving their wording and excluding the request itself."
                    },
                    "date_time":{
                        "type" : "string",
                        "description" : "When to remind the user, as ISO 8601 local time, e.g. 2026-10-02 09:00"
                    },
                    "recurrence": {
                        "type" : "string",
                        "enum": ["daily", "weekly"],
                        "description": "How often to repeat the reminder. Omit for a one-time reminder."
                    }
                },
                "required" : ["content", "date_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_reminder",
            "description": (
                "Change an existing reminder when the user asks to update it. "
                "Only include the fields the user wants to change. "
                "If multiple reminders match, ask the user which one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "integer",
                        "description": "The database ID of the reminder to be changed"
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete replacement text for the reminder."
                    },
                    "date_time": {
                        "type": "string",
                        "description": "The new time to remind the user, as ISO 8601 local time, e.g. 2026-10-02 09:00"
                    },
                    "recurrence": {
                        "type": "string",
                        "enum": ["daily", "weekly", "none"],
                        "description": "How often to repeat the reminder. Use none to make it a one-time reminder."
                    }
                },
                "required": ["reminder_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": (
                "Delete a reminder when the user asks to delete or cancel it. "
                "If multiple reminders match, ask the user which one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {
                        "type": "integer",
                        "description": "The database ID of the reminder to be deleted"
                    }
                },
                "required": ["reminder_id"]
            }
        }
    }
]

# Run a tool requested by the model and return its result as a string.
# Raises ValueError for unknown tools or invalid arguments; handle_message() reports these back to the model.
def execute_tool(conversation_id, name ,argument):
    known_tools = {tool['function']['name'] for tool in TOOLS}  # Reject tool names the model invented.
    if name not in known_tools:
        raise ValueError(f'Unknown tool {name}')

    if name == 'save_note':
        if not isinstance(argument,dict): 
            raise ValueError(f'Argument must be a dictionary')

        note = argument.get('note')

        if not isinstance(note,str):
            raise ValueError('note is not a string')
        
        storage.save_note(conversation_id,note)
        logging.info("Note saved for conversation %s", conversation_id)
        return 'Note saved successfully'
    
    elif name == 'delete_note':
        if not isinstance(argument, dict):
            raise ValueError(f'Argument must be a dictionary')

        note_id = argument.get('note_id')

        if not isinstance(note_id,int):
            raise ValueError('Note_id is not an integer')
        
        deleted = storage.delete_note(conversation_id, note_id)  # True if a row was deleted.

        if not deleted:
            return f'No note with ID {note_id} exists in this conversation'
        
        logging.info('The following note_id %s was deleted from conversation %s', note_id, conversation_id)

        return 'Note deleted successfully'

    elif name == 'list_notes':
        notes = storage.list_notes(conversation_id)

        if not notes:
            return 'No notes saved yet'
        else:
            return json.dumps(notes)

    elif name == 'update_note':
        if not isinstance(argument, dict):
            raise ValueError(f'Argument must be a dictionary')
        
        note_id = argument.get('note_id')
        content = argument.get('new_note')

        if not isinstance(note_id,int):
            raise ValueError('Note_id is not an integer')
        
        if not isinstance(content,str) or not content.strip():
            raise ValueError('Note content is not a string')

        updated = storage.update_note(conversation_id, note_id, content)  # True if a row was updated.

        if not updated:
            return f'No note exists with this note_id within this conversation'
        logging.info('The following note_id %s was updated in this conversation %s', note_id, conversation_id)
        return 'Note updated successfully'

    elif name == 'save_reminder':
        if not isinstance(argument, dict):
            raise ValueError(f'Argument must be a dictionary')

        content = argument.get('content')
        date_time = argument.get('date_time')
        recurrence = argument.get('recurrence') #Optional

        if not isinstance(content, str):
            raise ValueError('content is not a string')

        if not isinstance(date_time, str):
            raise ValueError('date_time is not a string')

        reminder_id, remind_at = storage.save_reminder(conversation_id, content, date_time, recurrence)
        logging.info('Reminder %s saved for conversation %s', reminder_id, conversation_id)
        return f'Reminder saved successfully with ID {reminder_id}, first due {remind_at}'

    elif name == 'update_reminder':
        if not isinstance(argument, dict):
            raise ValueError(f'Argument must be a dictionary')

        reminder_id = argument.get('reminder_id')

        if not isinstance(reminder_id, int):
            raise ValueError('reminder_id is not an integer')

        # Only pass the fields the model sent, so omitted fields keep their current values.
        # Models often fill unused fields with null or '', so treat those as unchanged too.
        changes = {}
        if argument.get('content') not in (None, ''):
            changes['content'] = argument['content']
        if argument.get('date_time') not in (None, ''):
            changes['remind_at'] = argument['date_time']
            changes['active'] = True  # A new time reactivates a one-time reminder that already fired.
        if argument.get('recurrence') not in (None, ''):  # Only 'none' clears it.
            changes['recurrence'] = argument['recurrence']

        updated = storage.update_reminder(conversation_id, reminder_id, **changes)  # True if a row was updated.

        if not updated:
            return f'No reminder with ID {reminder_id} exists in this conversation'
        logging.info('The following reminder_id %s was updated in conversation %s', reminder_id, conversation_id)
        return 'Reminder updated successfully'

    elif name == 'delete_reminder':
        if not isinstance(argument, dict):
            raise ValueError(f'Argument must be a dictionary')

        reminder_id = argument.get('reminder_id')

        if not isinstance(reminder_id, int):
            raise ValueError('reminder_id is not an integer')

        deleted = storage.delete_reminder(conversation_id, reminder_id)  # True if a row was deleted.

        if not deleted:
            return f'No reminder with ID {reminder_id} exists in this conversation'
        logging.info('The following reminder_id %s was deleted from conversation %s', reminder_id, conversation_id)
        return 'Reminder deleted successfully'


REMINDER_POLL_SECONDS = 2
LATE_AFTER = datetime.timedelta(minutes=10)  # Reminders sent later than this are labelled as missed.
_sent_not_recorded = set()  # (id, remind_at) of reminders already sent whose row update failed.

# Send each due reminder in a conversation with send(text), then deactivate it or move it to its next occurrence.
# Sent reminders are saved to the history so the model can follow up on them (e.g. "snooze that").
# Errors are caught per reminder so one failure doesn't block the rest.
RETRY_BACKOFF_MAX = datetime.timedelta(minutes=10)
GIVE_UP_AFTER = datetime.timedelta(days=1)
_send_failures = {}  # (id, remind_at) -> {'first': datetime, 'next_try': datetime, 'delay': timedelta}
def send_due_reminders(conversation_id, send):
    now = datetime.datetime.now()
    reminders = storage.list_reminders(conversation_id, now=now.strftime(storage.DATETIME_FORMAT))
    for reminder in reminders:
        key = (reminder['id'], reminder['remind_at'])
        failure = _send_failures.get(key)
        if failure and now < failure['next_try']:
            continue  # Backing off after a failed send.

        #if the issue is with corrupted or bad time format, deactive the reminder as it will never send
        try:
            remind_at = datetime.datetime.strptime(reminder['remind_at'], storage.DATETIME_FORMAT)
        except ValueError:
            # Unparseable time: retrying can never work, so deactivate it now.
            logging.error("Reminder %s has invalid remind_at %r; deactivating", reminder['id'], reminder['remind_at'])
            try:
                storage.mark_reminder_sent(conversation_id, reminder['id'], reminder['remind_at'])
            except Exception:
                logging.exception("Could not deactivate reminder %s", reminder['id'])
            continue


        # Send first and only update the row if that worked, so a failed send is retried next poll.
        try:
            if now - remind_at > LATE_AFTER:
                text = f"Missed reminder from {remind_at:%b %d %H:%M}: {reminder['content']}"
            else:
                text = f"Reminder: {reminder['content']}"
            if key not in _sent_not_recorded:  # Already sent; only the update below needs retrying.
                send(text)
                logging.info("Reminder %s sent", reminder['id'])
            _send_failures.pop(key, None)

        except Exception:
            if not failure:
                logging.exception("Sending reminder %s failed; will retry", reminder['id'])
                failure = _send_failures[key] = {'first': now, 'delay': datetime.timedelta(seconds=REMINDER_POLL_SECONDS)}
            else:
                logging.warning("Sending reminder %s failed again", reminder['id'])
                failure['delay'] = min(failure['delay'] * 2, RETRY_BACKOFF_MAX)
            failure['next_try'] = now + failure['delay']

            if now - failure['first'] >= GIVE_UP_AFTER:
                logging.error("Giving up on reminder %s after a day of failed sends", reminder['id'])
                next_at = None
                if reminder['recurrence']:
                    next_at = storage.next_occurrence(remind_at, reminder['recurrence'], now).strftime(storage.DATETIME_FORMAT)
                try:
                    storage.mark_reminder_sent(conversation_id, reminder['id'], reminder['remind_at'], next_remind_at=next_at)
                    _send_failures.pop(key, None)
                except Exception:
                    logging.exception("Could not give up on reminder %s", reminder['id'])
            continue

        try:
            if not reminder['recurrence']:
                storage.mark_reminder_sent(conversation_id, reminder['id'], reminder['remind_at'])
            else:
                # Skip past any occurrences missed while Aegis was off, so they aren't all sent as a backlog.
                next_at = storage.next_occurrence(remind_at, reminder['recurrence'], now)
                storage.mark_reminder_sent(conversation_id, reminder['id'],reminder['remind_at'], next_remind_at=next_at.strftime(storage.DATETIME_FORMAT))
            storage.save_message(conversation_id, 'assistant', text)
            _sent_not_recorded.discard(key)
        except Exception:
            # Remember it was sent, so the next poll retries the update instead of sending it again.
            logging.exception("Reminder %s was sent but could not be recorded", reminder['id'])
            _sent_not_recorded.add(key)

# Check for due reminders every REMINDER_POLL_SECONDS in a background thread,
# so slow model replies on the main thread don't delay them.
def start_reminder_thread(conversation_id, send):
    def poll():
        while True:
            try:
                send_due_reminders(conversation_id, send)
            except Exception:
                logging.exception("Could not check reminders")
            time.sleep(REMINDER_POLL_SECONDS)

    threading.Thread(target=poll, daemon=True).start()


# Handle one user message: run a slash command if there is one, otherwise ask the model.
# Returns the reply text and persists the user message and final reply to the conversation history.
def handle_message(conversation_id, inp): 
    parts = inp.split(maxsplit = 1)  # Separate a possible /command from its argument.

    if not parts:  # Empty or whitespace-only input.
        return 'Please type a message.'
    
    command = parts[0].lower()  # Make commands case-insensitive.

    if len(parts) > 1:
        argument = parts[1]

    else:
        argument = ''

    if command == '/clear':
        storage.clear_conversation(conversation_id)
        return 'Conversation cleared.'

    
    if command == '/note':
        try:
            storage.save_note(conversation_id, argument)
            logging.info("Saved a note for conversation %s", conversation_id)
            return f'Successfully saved note {argument}'
        
        except ValueError as error:
            return 'Usage: /note {your note here}'  # save_note raises ValueError on an empty note.


    if command == '/notes':
        notes = storage.list_notes(conversation_id)
        if notes==[]:
            return 'No notes saved yet'
        else:
            lines = []
            for i in range(len(notes)):  # Show 1-based positions rather than database IDs.
                 lines.append(f'{i+1}: {notes[i]['content']}') 
            return '\n'.join(lines)


    if command == '/delete_note':  # Deletes by 1-based position as shown by /notes, not by database ID.
        if not argument.isnumeric(): 
            return 'Usage: /delete_note note_number'
        
        else:
            number = int(argument)
            notes = storage.list_notes(conversation_id)  # List of {'id', 'content'} dicts ordered by id.
            if not 1 <= number<= len(notes):
                return 'No note with that number'
 
            else:
                note_id, note = notes[number-1]['id'], notes[number-1]['content']

        status = storage.delete_note(conversation_id, note_id)
        if status:
            logging.info(
            "Deleted note %s from conversation %s",
            note_id,
            conversation_id,
            )           
            return f'Successfully deleted note: {note}'
        else:
            logging.info("Note %s not found in conversation %s", note_id, conversation_id)
            return 'Note not found'

    # Not a command: build the model request from recent history, current notes, reminders, and date and time.
    messages = storage.load_recent_messages(conversation_id)
    notes = storage.list_notes(conversation_id)
    reminders = storage.list_reminders(conversation_id)  # Active reminders only.
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M (%A)')
    # Snapshots taken once per message; not refreshed between tool rounds.
    system = (SYSTEM_PROMPT
              + "\nCurrent notes: " + json.dumps(notes)
              + "\nCurrent reminders: " + json.dumps(reminders)
              + "\nCurrent date and time: " + now)

    messages.append({'role':'user', 'content':inp})

    request_messages = [{'role': 'system', 'content': system}] + messages 


    # Each round: ask the model, run any tool calls it made, and feed the results back.
    # Stops as soon as the model replies without requesting tools.
    for i in range(MAX_TOOL_ROUNDS): 
        message = ollama_client.ask_ollama(request_messages, TOOLS)
        request_messages.append(message)  # Keep the assistant turn so tool results follow it.

        tool_calls = message.get('tool_calls', [])
        response = message['content']

        if not tool_calls:
            break

        for tool_call in tool_calls:
            function = tool_call['function']
            try:
                result = execute_tool(conversation_id, function['name'], function['arguments'])

            except ValueError as error:
                logging.exception("Tool request rejected")
                result = f"Tool request rejected: {error}"

            tool_result = {'role':'tool', 'tool_name':function['name'], 'content':result}
            request_messages.append(tool_result)

            if tool_call.get('id'): 
                id = tool_call.get('id')
                tool_result['tool_call_id'] = id  # Some models/API versions use this to match results to calls.

    # for/else: runs only if the loop never hit `break`, i.e. MAX_TOOL_ROUNDS was exhausted.
    # Ask once more with no tools so the model must produce a plain-text reply.
    else:
        final_message = ollama_client.ask_ollama(request_messages, []) 
        response = final_message['content']
        request_messages.append(final_message)
            
    storage.save_message(conversation_id, 'user', inp)

    if not response or not response.strip():
        logging.warning('The model returned no text content.')
    else:
        storage.save_message(conversation_id, 'assistant', response)  # Tool calls/results are not persisted, only the user text and final reply.
    return response
