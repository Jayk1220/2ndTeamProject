import os
import json
import requests
from datetime import timedelta
from django.utils import timezone
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

# 프로젝트 모델 및 봇 인스턴스 임포트
from dashboard.models import WeatherCurrent
from .llm.bot_instance import GlobalFlightBot

# --- 1. 날씨 정보 API (기상청 API 허브용) ---
def get_weather(request):
    airport_code = request.GET.get('airport', 'ICN')
    weather_obj = WeatherCurrent.objects.filter(airport_code=airport_code).order_by('-updated_at').first()
    
    now = timezone.now()
    should_update = not weather_obj or (now - weather_obj.updated_at > timedelta(minutes=30))

    if should_update:
        try:
            auth_key = os.getenv("weather_key", "b37-aw-FQMm-_msPhfDJ6Q")
            stn_map = {
                'ICN': '110', 'GMP': '110', 'CJU': '184', 
                'PUS': '159', 'RSU': '168', 'USN': '152', 'YNY': '105'
            }
            stn_id = stn_map.get(airport_code, '108')

            url = "https://apihub.kma.go.kr/api/typ01/url/kma_sfctm2.php"
            params = {
                'tm': now.strftime('%Y%m%d%H%M'),
                'stn': stn_id,
                'help': '0',
                'authKey': auth_key
            }
            headers = {'User-Agent': 'Mozilla/5.0'}

            response = requests.get(url, params=params, headers=headers, timeout=7)
            
            if response.status_code == 200 and "#START7777" in response.text:
                lines = response.text.strip().split('\n')
                data_line = next((l for l in lines if not l.startswith('#') and len(l.split()) > 30), None)
                
                if data_line:
                    parts = data_line.split()
                    api_ta = float(parts[11])
                    api_ws = float(parts[3])
                    api_vs = int(parts[32])

                    if api_ta <= -50: api_ta = 0.0
                    if api_ws <= -9: api_ws = 0.0
                    if api_vs <= -9: api_vs = 1000

                    weather_obj, _ = WeatherCurrent.objects.update_or_create(
                        airport_code=airport_code,
                        defaults={
                            'stn': stn_id, 'ta': api_ta, 'ws02': api_ws,
                            'l_vis': api_vs * 10, 'observed_at': now, 'updated_at': now,
                        }
                    )
        except Exception as e:
            print(f"❌ 날씨 업데이트 실패: {e}")

    if weather_obj:
        return JsonResponse({
            "status": "success",
            "TA": weather_obj.ta,
            "WS02": weather_obj.ws02,
            "L_VIS": weather_obj.l_vis,
            "R_VIS": weather_obj.l_vis,
            "last_updated": weather_obj.updated_at.strftime('%Y-%m-%d %H:%M:%S')
        })
    return JsonResponse({"status": "error", "message": "데이터 없음"}, status=404)

# --- 2. 출발 현황 API (HTML 보드용) ---
def get_departures(request):
    """
    항공정보포털(ODCloud) API를 호출하여 실시간 출발 현황을 가져옵니다.
    """
    airport_code = request.GET.get('airport', 'ICN')
    
    # 1. API 설정
    # 공공데이터포털에서 발급받은 '항공정보포털' 전용 서비스키를 .env에 등록해서 사용하세요.
    service_key = os.getenv("KMA_SERVICE_KEY", "여기에_발급받은_인코딩키_또는_디코딩키_입력")
    
    # API URL (실시간 출발 현황 상세 조회)
    url = "https://api.odcloud.kr/api/GetFlightScheduleService/v1/getDepartureFlightSchedule"
    
    # 파라미터 설정 (명세서 기준)
    params = {
        'serviceKey': service_key,
        'page': '1',
        'perPage': '10',          # 화면에 8~10개 정도 표시하므로 10개 요청
        'schAirCode': airport_code, # 조회할 기준 공항 (ICN, GMP 등)
        'returnType': 'JSON'
    }

    try:
        response = requests.get(url, params=params, timeout=7)
        
        if response.status_code == 200:
            api_data = response.json()
            
            # API 응답 구조에 맞춰 데이터 추출 (명세서의 data 필드 참조)
            raw_departures = api_data.get('data', [])
            
            processed_departures = []
            for item in raw_departures:
                # HTML 렌더링에 필요한 키값으로 매핑
                processed_departures.append({
                    "airline": item.get("airlineKorean", "알 수 없음"),
                    "destination": item.get("arrivalCity", "정보 없음"),
                    "flight_no": item.get("flightId", "-"),
                    "std": item.get("std", "0000"), # 출발예정시간
                    "status": item.get("remark", "정상") # 지연, 결항 등 상태
                })
            
            return JsonResponse({
                "status": "success",
                "airport": airport_code,
                "departures": processed_departures,
                "last_updated": timezone.now().strftime('%Y-%m-%d %H:%M:%S')
            })
        else:
            print(f"⚠️ 항공 API 호출 실패: {response.status_code}")
            return JsonResponse({"status": "error", "message": "API 호출 실패"}, status=response.status_code)

    except Exception as e:
        print(f"❌ 항공 API 연동 에러: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

# --- 3. 챗봇 스트리밍 뷰 ---
def chat_stream_view(request):
    user_input = request.GET.get('msg', '').strip()
    if not user_input:
        return JsonResponse({"error": "no_message"}, status=400)

    bot = GlobalFlightBot.get_instance()
    def stream_generator():
        try:
            for chunk in bot.send_message_stream(user_input):
                yield chunk
        except Exception as e:
            yield f"\n[Stream Error]: {str(e)}"

    response = StreamingHttpResponse(stream_generator(), content_type='text/plain; charset=utf-8')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response

# --- 4. 일반 채팅 API (CSRF 면제) ---
@csrf_exempt
@require_POST
def api_chat(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
        message = (payload.get("message") or "").strip()
        bot = GlobalFlightBot.get_instance()
        reply = bot.send_message(message)
        return JsonResponse({"reply": reply})
    except Exception as e:
        return JsonResponse({"error": "chat_failed", "detail": str(e)}, status=500)