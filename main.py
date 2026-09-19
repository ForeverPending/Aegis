#this is a practice for http and ollama
import requests
import storage
import logging
import assistant
import ollama_client as o
import imessage_client

#initialize variables
CONVERSATION_ID = 'test_1'

def main():
    logging.basicConfig(
    filename='assistant.log',
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
    )

    storage.init_db()
    MESSAGES = storage.load_recent_messages(CONVERSATION_ID)
    new_messages = imessage_client.load_conversation(CONVERSATION_ID)
    print('Assitant: How can I help you?')
    while True:
        print('user: ',end='')
        inp = input("").strip()

        if inp.strip() == '/quit':
                break

        try:
            response = assistant.handle_message(CONVERSATION_ID,inp)
            print(f'Assistant: {response}')
        except requests.exceptions.Timeout:
            logging.exception("Ollama took too long to resopnd")
            print("Ollama took too long to respond.")
            
        except requests.exceptions.ConnectionError:
            logging.exception("Could not connect to Ollama")
            print("Could not connect to Ollama")

        except requests.exceptions.HTTPError:
            logging.exception("Ollama returned an HTTP error")
            print('Ollama returned an HTTP error')


if __name__ == '__main__':
    main()


