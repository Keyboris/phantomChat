#!/usr/bin/python3

import websockets
import json
import asyncio
from websockets.asyncio.server import serve
import uuid
from pydantic import BaseModel, ConfigDict, ValidationError
import redis.asyncio as redis

# decode_responses=True ensures we get strings, not bytes
redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

active_rooms = {}


RATE_LIMIT = 5
RATE_WINDOW = 10

async def is_rate_limited(user_ip):
    """Checks if the user IP has exceeded the message limit."""
    key = f"rate_limit:{user_ip}"
    
    #increment counter for this ip
    current_count = await redis_client.incr(key)
    
    #if this is the first message in the window, set expiration
    if current_count == 1:
        await redis_client.expire(key, RATE_WINDOW)
    
    if current_count > RATE_LIMIT:
        return True
    return False

class JoinRequest(BaseModel):
    model_config = ConfigDict(strict=True)
    type: str
    room_id: str
    password: str

class Room:
    def __init__(self, room_id, password):
        self.room_id = room_id
        self.connections = set()
        self.password = password

    async def add_user(self, websocket):
        self.connections.add(websocket)
        print(f"User joined {self.room_id}. Total: {len(self.connections)}")

    async def remove_user(self, websocket):
        if websocket in self.connections:
            self.connections.remove(websocket)
        print(f"User left {self.room_id}. Total: {len(self.connections)}")

    async def broadcast(self, message, sender_socket):
        send_tasks = []
        for connection in self.connections:
            if connection != sender_socket:
                send_tasks.append(connection.send(message))
        
        if send_tasks:
            await asyncio.gather(*send_tasks)

async def handler(websocket):
    current_room = None
    user_ip = websocket.remote_address[0] #get ip for rate limiting
    
    try:
        init_data = await websocket.recv()
        data = json.loads(init_data)

        try:
            JoinRequest.model_validate(data)
        except ValidationError as e:
            print(f"Validation Error: {e}")
            await websocket.close()
            return

        if data.get("type") == "join":
            print(f"Received join request from {user_ip}")
            room_id = data["room_id"]
            password = data["password"]
            
            if room_id not in active_rooms:
                active_rooms[room_id] = Room(room_id, password)
            else:
                if active_rooms[room_id].password != password:
                    await websocket.send(json.dumps({"status": 401, "message": "Wrong password"}))
                    return
            
            current_room = active_rooms[room_id]
            await current_room.add_user(websocket)

            await websocket.send(json.dumps({"status": 200}))
        else:
            await websocket.close()
            return

        async for message in websocket:
            if await is_rate_limited(user_ip):
                warning = {
                    "status": 429, 
                    "message": f"You are chatting too fast! Wait {RATE_WINDOW}s."
                }
                await websocket.send(json.dumps(warning))
                continue #skip broadcast

            await current_room.broadcast(message, websocket)

    except websockets.exceptions.ConnectionClosed:
        pass 
    finally:
        if current_room:
            await current_room.remove_user(websocket)
            if len(current_room.connections) == 0 and current_room.room_id in active_rooms:
                del active_rooms[current_room.room_id]

async def main():
    async with websockets.serve(handler, "localhost", 8080):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())