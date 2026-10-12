import asyncio, sys
import edge_tts

async def test():
    try:
        com = edge_tts.Communicate("你好，测试朗读。", "zh-CN-XiaoxiaoNeural")
        n = 0
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                n += len(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                pass
        print("SUCCESS audio_bytes=", n)
    except Exception as e:
        print("FAILED:", type(e).__name__, str(e)[:400])

asyncio.run(test())
