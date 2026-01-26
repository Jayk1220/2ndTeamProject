from django.shortcuts import render

# Create your views here.
import os
import requests
from django.http import JsonResponse
from django.views.decorators.http import require_GET


AMOS_URL = "https://apihub.kma.go.kr/api/typ01/url/amos.php"

# 공항 stn -> 이름
AIRPORTS = { # 데이터는 7개소 공항만 존재(김해|부산은 X)
    "113": "인천공항",
    "110": "김포공항",
    "182": "제주공항",
    "163": "무안공항",
    "151": "울산공항",
    "167": "여수공항",
    "92":  "양양공항",
}

def _parse_latest_amos_row(text: str) -> dict:
    """
    AMOS 응답에서 가장 마지막 데이터 1줄을 파싱해서
    TA, WS02, WS02_MAX, L_VIS, R_VIS만 추출.
    결측(-99999 등)은 None 처리.
    단위: TA/WS02/WS02_MAX는 0.1 단위 → /10.0
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if not ln.startswith("#")]
    if not data_lines:
        return {"error": "no data"}

    cols = data_lines[-1].split()

    # 헤더 기준 컬럼 순서:
    # STN TM L_VIS R_VIS L_RVR R_RVR CH_MIN TA TD HM PS PA RN 예비1 예비2 WD02 WD02_MAX WD02_MIN WS02 WS02_MAX WS02_MIN ...
    # 필요한 위치만 뽑기 (인덱스 주의!)
    # 0:STN 1:TM 2:L_VIS 3:R_VIS ... 7:TA ... 18:WS02 19:WS02_MAX
    def to_int(v):
        try:
            return int(v)
        except:
            return None

    def clean_missing(v_int):
        if v_int is None:
            return None
        if v_int in (-99999, -9999, 99999):
            return None
        return v_int

    stn = cols[0]
    tm = cols[1]

    L_VIS = clean_missing(to_int(cols[2]))
    R_VIS = clean_missing(to_int(cols[3]))

    TA_raw = clean_missing(to_int(cols[7]))
    TA = None if TA_raw is None else TA_raw / 10.0

    WS02_raw = clean_missing(to_int(cols[18]))
    WS02 = None if WS02_raw is None else WS02_raw / 10.0

    WS02_MAX_raw = clean_missing(to_int(cols[19]))
    WS02_MAX = None if WS02_MAX_raw is None else WS02_MAX_raw / 10.0

    return {
        "STN": stn,
        "TM": tm,
        "TA": TA,
        "WS02": WS02,
        "WS02_MAX": WS02_MAX,
        "L_VIS": L_VIS,
        "R_VIS": R_VIS,
    }

@require_GET
def api_airport_weather_simple(request):
    stn = request.GET.get("stn", "113")
    key = (os.getenv("KEY") or "").strip()
    if not key:
        return JsonResponse({"error": "missing KEY in env"}, status=500)

    params = {
        "stn": stn,
        "dtm": 10,        # 최근 10분(원하면 3~60 조절)
        "authKey": key,
    }

    r = requests.get(AMOS_URL, params=params, timeout=15)
    # 401/403/429 같은 것도 그대로 알기 쉽게 전달
    if r.status_code != 200:
        return JsonResponse(
            {"error": "upstream_error", "status": r.status_code, "text": r.text[:200]},
            status=502
        )

    parsed = _parse_latest_amos_row(r.text)
    parsed["AIRPORT_NAME"] = AIRPORTS.get(str(stn), f"STN {stn}")
    return JsonResponse(parsed)


def dashboard_view(request):
    return render(request,"dashboard.html")


from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .airline import get_board

@require_GET
def api_departures(request):
    airport = request.GET.get("airport", "ICN")
    limit = int(request.GET.get("limit", "5"))
    try:
        return JsonResponse({"airport": airport, "departures": get_board(airport, "dep", limit=limit)})
    except Exception as e:
        return JsonResponse({"airport": airport, "departures": [], "error": str(e)}, status=502)

@require_GET
def api_arrivals(request):
    airport = request.GET.get("airport", "ICN")
    limit = int(request.GET.get("limit", "5"))
    try:
        return JsonResponse({"airport": airport, "arrivals": get_board(airport, "arr", limit=limit)})
    except Exception as e:
        return JsonResponse({"airport": airport, "arrivals": [], "error": str(e)}, status=502)


import time
from django.core.cache import cache

@require_GET
def api_airport_weather_simple(request):
    stn = request.GET.get("stn", "113")
    key = (os.getenv("KEY") or "").strip()
    if not key:
        return JsonResponse({"error": "missing KEY in env"}, status=500)

    cache_key = f"amos:{stn}"
    cached = cache.get(cache_key)

    url = AMOS_URL
    params = {"stn": stn, "dtm": 10, "authKey": key}

    last_err = None
    for attempt in range(2):  # 재시도 2번
        try:
            # (connect_timeout, read_timeout)
            r = requests.get(url, params=params, timeout=(3, 7))
            r.raise_for_status()

            parsed = _parse_latest_amos_row(r.text)
            parsed["AIRPORT_NAME"] = AIRPORTS.get(str(stn), f"STN {stn}")

            # 성공값 캐시(예: 30초)
            cache.set(cache_key, parsed, timeout=30)
            return JsonResponse(parsed)

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectTimeout) as e:
            last_err = f"timeout:{type(e).__name__}"
            time.sleep(0.2)  # 짧게 쉼 후 재시도

        except Exception as e:
            last_err = f"error:{type(e).__name__}"
            break

    # 여기 도달 = 실패
    if cached:
        cached["stale"] = True  # 마지막 값(오래된 값)이라는 표시
        return JsonResponse(cached)

    return JsonResponse(
        {"error": "weather_unavailable", "detail": last_err},
        status=502
    )

