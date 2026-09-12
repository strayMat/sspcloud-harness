"""Minimal end-to-end WS client for local testing (not part of the app)."""
import asyncio
import json
import sys
import time

import websockets

URL = "ws://127.0.0.1:8099/ws"


async def chat(prompt: str, timeout: float = 90) -> None:
    async with websockets.connect(URL) as ws:
        await ws.send(json.dumps({"type": "prompt", "message": prompt}))
        text: list[str] = []
        tools: list[str] = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            rec = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
            t = rec.get("type")
            ev = rec.get("assistantMessageEvent")
            if t == "tool_execution_start":
                tools.append(rec.get("toolName"))
            elif t == "message_update" and ev and ev.get("type") == "text_delta":
                text.append(ev["delta"])
            elif t == "agent_settled":
                break
        if tools:
            print("TOOLS:", tools)
        print("REPLY:", "".join(text).strip())


if __name__ == "__main__":
    asyncio.run(chat(sys.argv[1]))
