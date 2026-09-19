#ollama blyat
#manages http and stuff lol
import requests
import storage

#initialize stuff
MODEL = 'qwen3:4b'
STREAM = False


def ask_ollama(messages, tools):
    payload = {'model':MODEL, 'messages': messages, 'stream':STREAM, 'tools': tools}

    url = "http://localhost:11434/api/chat"
    response = requests.post(url, json=payload, timeout=120)
    response.raise_for_status()

    data = response.json()
    message = data['message']
    return data['message']