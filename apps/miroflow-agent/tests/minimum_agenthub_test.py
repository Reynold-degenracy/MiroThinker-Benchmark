import asyncio
import os
from agenthub import AutoLLMClient

os.environ["QWEN3_API_KEY"] = os.environ["SILICONFLOW_API_KEY"]
os.environ["QWEN3_BASE_URL"] = os.environ["SILICONFLOW_BASE_URL"]

async def main():  
    client = AutoLLMClient(model="Qwen/Qwen3-8B")
    async for event in client.streaming_response_stateful(
        message={
            "role": "user",
            "content_items": [{"type": "text", "text": "What is 2 + 2?"}]
        },
        config={}
    ):
        print(event)

asyncio.run(main())