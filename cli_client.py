# Terminal front end for Aegis. Run this file and type messages to chat; /quit exits.
# Uses CLI_CONVERSATION_ID from .env as the conversation key.

import requests
import storage
import logging
import assistant
import ollama_client as o
import os
from dotenv import load_dotenv

load_dotenv()
CONVERSATION_ID = os.getenv('CLI_CONVERSATION_ID')
if not CONVERSATION_ID:
     raise ValueError('Set a CLI_CONVERSATION_ID in your .env')

# Print a due reminder while input() is waiting, then redraw the prompt.
def show_reminder(text):
    print(f'\nAegis: {text}\nuser: ', end='', flush=True)

# Read-eval-print loop: pass each input line to the assistant and print the reply.
def main():
    logging.basicConfig(
    filename='assistant.log',
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
    )

    # Create tables if needed.
    storage.init_db()

    # Note: unused; handle_message() loads history itself.
    MESSAGES = storage.load_recent_messages(CONVERSATION_ID)

    # Send due reminders from a background thread while waiting for input.
    assistant.start_reminder_thread(CONVERSATION_ID, show_reminder)

    print('Aegis: How can I help you?')
    while True:
        print('user: ',end='')
        inp = input("").strip()

        if inp.strip() == '/quit':
                break

        try:
            response = assistant.handle_message(CONVERSATION_ID,inp)
            if not response or not response.strip():
                 response = 'The model returned no text content.'
            print(f'Aegis: {response}')
        except requests.exceptions.Timeout:
            logging.exception("Ollama took too long to respond")
            print("Ollama took too long to respond.")
            
        except requests.exceptions.ConnectionError:
            logging.exception("Could not connect to Ollama")
            print("Could not connect to Ollama")

        except requests.exceptions.HTTPError:
            logging.exception("Ollama returned an HTTP error")
            print('Ollama returned an HTTP error')
        except Exception:
            logging.exception("Unexpected error handling message")
            print('Something went wrong, check assistant.log')

if __name__ == '__main__':
    main()


