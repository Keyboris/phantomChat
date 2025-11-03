import websockets
import asyncio
import string
import random
import json

async def main():
    id = ''.join(random.SystemRandom().choice(string.ascii_uppercase + string.digits) for _ in range(10))

    message_payload = {
        "type": "registration",
        "id": id,
        "isActive": True
    }
    
    uri = "___"
    print(f"Connecting to {uri}")

    try:
        async with websockets.connect(uri) as websocket:
            print("Сonnected")
            
            message_to_send = json.dumps(message_payload)
            
            print(f"Sending message: {message_to_send}")
            await websocket.send(message_to_send)
            
            response = await websocket.recv()
            print(f"Received from server: {response}")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())