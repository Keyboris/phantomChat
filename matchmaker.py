#!/usr/bin/python3

import websockets
import json
import asyncio
from websockets.asyncio.server import serve

async def printer(websocket):
    async for message in websocket:
        print(message)


async def main():
    async with serve(printer, "localhost", 8080):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())