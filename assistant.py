# Core assistant logic: builds the prompt sent to the model, handles slash commands,
# and runs the tool-call loop. Contains the system prompt, tool schemas, and tool dispatch.
import storage
import logging
import ollama_client
import requests
import json



# Base system prompt. The save_note rule stops the model from calling the tool on every message.
# handle_message() appends the current notes as JSON so the model can reference note IDs directly.
SYSTEM_PROMPT = ("""You are a concise personal assistant. Answer briefly.
Only call save_note when the user explicitly asks to save or create a note.
For ordinary questions and conversation, answer directly without saving a note.
The user's notes as of this message are listed below as JSON.
To update or delete a note, use its ID from this list or from list_notes. IDs mentioned in earlier messages may be outdated; ignore them.
Notes you save while answering will not appear in this list. Call list_notes if you need their IDs.
Never invent an ID.
If multiple notes could match the user's request, ask which one they mean.""")

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
    }
]

# Run a tool requested by the model and return its result as a string.
# Raises ValueError for unknown tools or invalid arguments; handle_message() reports these back to the model.
def execute_tool(conversation_id, name ,argument):
    known_tools = ['save_note', 'delete_note', 'list_notes', 'update_note']  # Reject tool names the model invented.
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

    # Not a command: build the model request from recent history and current notes.
    messages = storage.load_recent_messages(conversation_id)
    notes = storage.list_notes(conversation_id)
    system = SYSTEM_PROMPT + "\nCurrent notes: " + json.dumps(notes)  # Snapshot taken once per message; not refreshed between tool rounds.

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
