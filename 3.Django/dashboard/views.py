from django.shortcuts import render

# Create your views here.
import os
import requests
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from django.db.models import Max
from dashboard.models import FlightSnapshot
from .models import WeatherSnapshot
from dashboard.models import WeatherCurrent


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

def _last_updated_kst():
    dt = FlightSnapshot.objects.aggregate(Max("updated_at"))["updated_at__max"]
    if not dt:
        return None
    return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M:%S")

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

def dashboard_view(request):
    return render(request,"dashboard.html")


from django.http import JsonResponse
from django.views.decorators.http import require_GET
from .airline import get_board

@require_GET
def api_departures(request):
    airport = request.GET.get("airport", "ICN")
    limit = int(request.GET.get("limit", "5"))
    today = timezone.localdate().strftime("%Y%m%d")
    now_hhmm = timezone.localtime().strftime("%H%M")

    qs = (
        FlightSnapshot.objects
        .filter(
            airport_code=airport,
            kind="dep",
            flight_date=today,
            std__gt=now_hhmm,
        )
        .order_by("std")[:limit]
    )

    return JsonResponse({
        "airport": airport,
        "last_updated": _last_updated_kst(),
        "departures": list(qs.values(
            "airline", "destination", "flight_no", "std", "status"
        )),
    })

@require_GET
def api_arrivals(request):
    airport = request.GET.get("airport", "ICN")
    limit = int(request.GET.get("limit", "5"))
    today = timezone.localdate().strftime("%Y%m%d")
    now_hhmm = timezone.localtime().strftime("%H%M")

    qs = (
        FlightSnapshot.objects
        .filter(
            airport_code=airport,
            kind="arr",
            flight_date=today,
            std__gt=now_hhmm,
        )
        .order_by("std")[:limit]
    )

    return JsonResponse({
        "airport": airport,
        "last_updated": _last_updated_kst(),
        "arrivals": list(qs.values(
            "airline", "origin", "flight_no", "std", "status"
        )),
    })

import time
from django.core.cache import cache

@require_GET
def api_airport_weather_simple(request):
    airport = request.GET.get("airport", "ICN")

    obj = WeatherCurrent.objects.filter(airport_code=airport).first()
    if not obj:
        return JsonResponse({"error": "no_weather_data"}, status=404)

    return JsonResponse({
        "airport": airport,
        "observed_at": obj.observed_at.isoformat(),
        "ta": obj.ta,
        "ws02": obj.ws02,
        "ws02_max": obj.ws02_max,
        "l_vis": obj.l_vis,
        "r_vis": obj.r_vis,
        "updated_at": obj.updated_at.isoformat(),
    })

@require_GET
def api_weather(request):
    airport = request.GET.get("airport", "ICN").upper()

    obj = (
        WeatherCurrent.objects
        .filter(airport_code=airport)
        .order_by("-observed_at")
        .first()
    )
    if not obj:
        return JsonResponse({"error": "no_weather", "airport": airport}, status=404)

    return JsonResponse({
        "airport": airport,
        "observed_at": obj.observed_at.isoformat(),
        "TA": obj.ta,
        "WS02": obj.ws02,
        "WS02_MAX": obj.ws02_max,
        "L_VIS": obj.l_vis,
        "R_VIS": obj.r_vis,
        "last_updated": obj.updated_at.isoformat(),
    })
