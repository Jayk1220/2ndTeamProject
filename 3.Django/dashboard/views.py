from django.shortcuts import render

# Create your views here.
import os
import requests
from django.http import JsonResponse
from django.views.decorators.http import require_GET


AMOS_URL = "https://apihub.kma.go.kr/api/typ01/url/amos.php"

# 공항 stn -> 이름
AIRPORTS = {
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

DUMMY_DEPARTURES = {
    "ICN": [
        {"airline": "대한항공", "dest": "도쿄(NRT)", "time": "15:10", "status": "정상"},
        {"airline": "아시아나", "dest": "오사카(KIX)", "time": "15:25", "status": "지연"},
        {"airline": "제주항공", "dest": "방콕(BKK)", "time": "15:40", "status": "정상"},
    ],
    "GMP": [
        {"airline": "대한항공", "dest": "제주(CJU)", "time": "15:05", "status": "정상"},
        {"airline": "진에어", "dest": "부산(PUS)", "time": "15:30", "status": "결항"},
    ],
    "CJU": [
        {"airline": "티웨이", "dest": "김포(GMP)", "time": "15:20", "status": "정상"},
    ],
    "PUS": [
        {"airline": "에어부산", "dest": "타이베이(TPE)", "time": "16:00", "status": "정상"},
    ],
}

@require_GET
def api_departures(request):
    airport = request.GET.get("airport", "ICN")
    items = DUMMY_DEPARTURES.get(airport, [])
    return JsonResponse({"airport": airport, "departures": items})
