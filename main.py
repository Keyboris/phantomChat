import websockets
import asyncio
import json
import sys

URI = "wss://extendo.uk" 

async def listen_for_messages(websocket):
    """Continuously listens for messages from the server."""
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if isinstance(data, dict) and "status" in data:
                    if data["status"] == 429:
                        print(f"\n[SYSTEM]: {data['message']}")
                    elif data["status"] == 200:
                        pass
                    else:
                        print(f"\n[SYSTEM]: {data.get('message', message)}")
                else:
                    print(f"\n[RECEIVED]: {message}")
            except json.JSONDecodeError:
                print(f"\n[RECEIVED]: {message}")
            
            sys.stdout.write("You: ")
            sys.stdout.flush()
            
    except websockets.exceptions.ConnectionClosed:
        print("\nConnection closed by server.")

async def send_message(websocket):
    """Reads input in a separate thread."""
    loop = asyncio.get_running_loop()
    
    while True:
        sys.stdout.write("You: ")
        sys.stdout.flush()
        
        message = await loop.run_in_executor(None, input)
        
        if message.strip(): 
            await websocket.send(message)

async def main():
    room_id = input("Enter room id to join: ")
    password = input("Enter the password: ")

    print(f"Connecting to {URI}...")
    
    try:
        async with websockets.connect(URI) as websocket:
            
            connect_message = {"type": "join", "room_id": room_id, "password": password}
            await websocket.send(json.dumps(connect_message))
            
            response = await websocket.recv()
            print(f"Server Response: {response}")
            
            if "200" not in response:
                print("Failed to join room.")
                return

            print("Successfully joined! You can chat now.")
            print("-" * 30)

            await asyncio.gather(
                listen_for_messages(websocket),
                send_message(websocket)
            )
            
    except websockets.exceptions.ConnectionClosed:
        print("Disconnected.")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting...")