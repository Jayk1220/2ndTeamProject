import asyncio
from asgiref.sync import sync_to_async

try:
    from .bot_instance import GlobalFlightBot
except ImportError:
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from bot_instance import GlobalFlightBot

async def get_flight_response_stream(user_input):
    """
    제너레이터를 반환하여 StreamingHttpResponse가 한 줄씩 읽을 수 있게 합니다.
    """
    # 봇 인스턴스를 가져옵니다. (없으면 여기서 생성 및 로딩 대기까지 수행함)
    bot = await sync_to_async(GlobalFlightBot.get_instance, thread_sensitive=False)()
    
    # 만약 프로세스가 아직 시동 중이라면 잠시 대기
    retry_count = 0
    while bot.process is None and retry_count < 10:
        await asyncio.sleep(1)
        retry_count += 1

    return bot.send_message_stream(user_input)

# views.py에서 ImportError가 나지 않도록 일반 응답용도 남겨둡니다.
async def get_flight_response(user_input, request=None):
    bot = await sync_to_async(GlobalFlightBot.get_instance, thread_sensitive=False)()
    response = await sync_to_async(bot.send_message, thread_sensitive=False)(user_input)
    return response