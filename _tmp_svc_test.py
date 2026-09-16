import asyncio, sys, os
sys.path.insert(0, r"D:\Documents\FewType\tauri\backend\api")
import tts_service

async def main():
    try:
        b = await tts_service.synthesize("测试音量提升", "zh-CN-XiaoxiaoNeural", "+0%")
        print("synthesize OK", len(b), "bytes")
    except Exception as e:
        print("synthesize FAIL", type(e).__name__, e)
    try:
        b = await tts_service.tts_speak("测试音量提升", "zh-CN-XiaoxiaoNeural", "+0%")
        print("tts_speak OK", len(b), "bytes")
    except Exception as e:
        print("tts_speak FAIL", type(e).__name__, e)

asyncio.run(main())
