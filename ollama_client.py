# Thin wrapper around the local Ollama /api/chat endpoint.
import requests
import storage
import os
from dotenv import load_dotenv

# Model name comes from MODEL_NAME in .env; fail fast at import if it's missing.
STREAM = False
load_dotenv()
MODEL = os.getenv('MODEL_NAME')
if not MODEL:
    raise ValueError('Set a MODEL_NAME in your .env')


# Send the conversation (and tool schemas) to Ollama and return the assistant message dict.
# Raises requests exceptions on timeout, connection failure, or HTTP error.
def ask_ollama(messages, tools):
    payload = {'model':MODEL, 'messages': messages, 'stream':STREAM, 'tools': tools, 'think': True}  # Workaround: with think=False the model's reasoning leaked into 'content'; think=True keeps it in the separate 'thinking' field.

    url = "http://localhost:11434/api/chat"
    response = requests.post(url, json=payload, timeout=120)  # Seconds to wait before raising Timeout.
    response.raise_for_status()

    data = response.json()
    message = data['message']
    return data['message']  # Dict with role, content, and optional thinking/tool_calls.