#file that deals with the tools and stuff of the assistant
import storage
import logging
import ollama_client
import requests


#system prompt
SYSTEM_PROMPT = ( "You are a concise personal assistant. Answer briefly. "
"Only call save_note when the user explicitly asks to save or create a note. "
"For ordinary questions and conversation, answer directly without saving a note. "
    "Do not include analysis or <response> tags in your final reply.")


# tools
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
]

#tools function
def execute_tool(conversation_id,name,argument):
    if name != 'save_note':
        raise ValueError(f'Unknown tool {name}')

    if not isinstance(argument,dict):
        raise ValueError(f'Argument must be a dictionary')

    note = argument.get('note')

    if not isinstance(note,str):
        raise ValueError('note is not a string')
    storage.save_note(conversation_id,note)
    logging.info("Note saved for conversation %s", conversation_id)
    return 'Note saved successfully'

def handle_message(conversation_id, inp):

    parts = inp.split(maxsplit = 1)

    if not parts:
        return 'Please type a message.'
    
    command = parts[0].lower()
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
            return 'Usage: /note {your note here}'


    if command == '/notes':
        notes = storage.load_notes(conversation_id)
        if notes == []:
            return 'No notes saved yet'
        else:
            lines = []
            for number,(note_id,note) in enumerate(notes,start=1):
                 lines.append(f'{number}: {note}')
            return '\n'.join(lines)


    if command == '/delete_note':
        if not argument.isnumeric():
            return 'Usage: /delete_note note_id'
        
        else:
            number = int(argument)
            notes = storage.load_notes(conversation_id)
            if not 1<= number<= len(notes):
                return 'No note with that number'
 
            else:
                note_id, note = notes[number-1]

        storage.delete_note(conversation_id, note_id)
        logging.info(
        "Deleted note %s from conversation %s",
        note_id,
        conversation_id,
        )           
        return f'Successfully deleted note: {note}'

    messages = storage.load_recent_messages(conversation_id)
    messages.append({'role':'user', 'content':inp})
    request_messages = [{'role': 'system', 'content': SYSTEM_PROMPT}] + messages

    message = ollama_client.ask_ollama(request_messages, TOOLS)
    request_messages.append(message)

    tool_calls = message.get('tool_calls', [])
    response = message['content']


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
            tool_result['tool_call_id'] = id
            
    if tool_calls:
        final_message = ollama_client.ask_ollama(request_messages, [])
        response = final_message['content']
    storage.save_message(conversation_id, 'user', inp)
    storage.save_message(conversation_id, 'assistant', response)

    return response
