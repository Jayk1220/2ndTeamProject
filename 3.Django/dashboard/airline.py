# dashboard/airline.py
import os
import math
import requests
from datetime import datetime, date, timedelta  
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from django.utils import timezone
from django.core.cache import cache

load_dotenv()
KST = ZoneInfo("Asia/Seoul")

AIRLINE_URL = "https://api.odcloud.kr/api/FlightStatusListDTL/v1/getFlightStatusListDetail"

# 공항코드 -> 공공데이터의 한글 공항명(매칭용)
AIRPORT_KOR = {
    "ICN": "인천",
    "GMP": "김포",
    "CJU": "제주",
    "PUS": "김해",  # 부산/김해
    "MWX": "무안",
    "USN": "울산",
    "RSU": "여수",
    "YNY": "양양",
}

def _fetch(page: int, per_page: int) -> dict:
    key = os.getenv("airline_key")
    if not key:
        raise RuntimeError("Missing env airline_key")

    params = {"page": page, "perPage": per_page, "serviceKey": key}
    r = requests.get(AIRLINE_URL, params=params, timeout=(3, 20))
    r.raise_for_status()
    return r.json()

def _total_pages(per_page: int) -> int:
    payload = _fetch(page=1, per_page=per_page)
    total_count = payload.get("totalCount")
    if total_count is None:
        # 혹시 키 이름이 다르면 여기서 확인
        raise RuntimeError(f"totalCount not found in response keys={list(payload.keys())}")
    return max(1, math.ceil(int(total_count) / per_page))

def _first_last_date(items: list[dict]) -> tuple[str | None, str | None]:
    if not items:
        return None, None
    return items[0].get("FLIGHT_DATE"), items[-1].get("FLIGHT_DATE")

def _hhmm(s: str | None) -> str:
    if not s or len(s) != 4:
        return "-"
    return f"{s[:2]}:{s[2:]}"

def _is_future(f: dict, now: datetime) -> bool:
    d = f.get("FLIGHT_DATE")
    t = f.get("STD")   # 'hhmm' 문자열
    if not d or not t:
        return False

    t = str(t).strip()
    if len(t) != 4 or not t.isdigit():
        return False

    try:
        dt = datetime.strptime(d + t, "%Y%m%d%H%M").replace(tzinfo=now.tzinfo)
    except ValueError:
        return False

    return dt >= now


def find_page_for_date(target: date, per_page: int = 10000) -> int:
    """
    데이터가 FLIGHT_DATE 기준 오름차순 정렬이라는 전제에서,
    target 날짜가 포함된 page를 이진탐색으로 찾음.
    """
    cache_key = f"air:page_for:{target.strftime('%Y%m%d')}:{per_page}"
    cached = cache.get(cache_key)
    
    if cached is not None:
        return cached
    
    target_str = target.strftime("%Y%m%d")
    lo, hi = 1, _total_pages(per_page)

    while lo <= hi:
        mid = (lo + hi) // 2
        payload = _fetch(page=mid, per_page=per_page)
        items = payload.get("data", [])
        if not items:
            hi = mid - 1
            continue

        first, last = _first_last_date(items)
        if not first or not last:
            hi = mid - 1
            continue

        if target_str < first:
            hi = mid - 1
        elif target_str > last:
            lo = mid + 1
        else:
            cache.set(cache_key, mid, timeout=600)
            return mid

    # 못 찾으면 가장 가까운 값으로 fallback
    fallback = min(max(1, lo), _total_pages(per_page))
    cache.set(cache_key, fallback, timeout=600)
    return fallback

def _today_kst() -> date:
    # settings.TIME_ZONE=Asia/Seoul, USE_TZ=True 기준: localdate가 KST 날짜를 줌
    return timezone.localdate()

def _parse_hhmm(s: str | None) -> str | None:
    s = (s or "").strip()
    if len(s) == 4 and s.isdigit():
        return s
    return None

def iter_flights_for_date(target: date, per_page: int = 10000):
    """
    target 날짜의 raw flight dict들을 yield.
    find_page_for_date로 찾은 페이지 주변을 훑고, FLIGHT_DATE == target만 골라냄.
    """
    target_str = target.strftime("%Y%m%d")
    p = find_page_for_date(target, per_page=per_page)

    # 주변 몇 페이지를 보면 충분 (데이터 분포에 따라 조정 가능)
    pages = [x for x in [p - 2, p - 1, p, p + 1, p + 2] if x >= 1]

    for page in pages:
        payload = _fetch(page=page, per_page=per_page)
        for f in payload.get("data", []):
            if (f.get("FLIGHT_DATE") or "").strip() == target_str:
                yield f

def board_for_date(
    airport_code: str,
    kind: str,
    target: date,
    limit: int = 500,
    per_page: int = 10000,
) -> list[dict]:
    """
    특정 날짜(target) 기준으로 공항별 출/도착 raw 데이터를 정리해서 반환.
    sync_flights_week에서 DB upsert에 쓰기 좋은 키로 반환함.

    kind: "dep" | "arr"
    """
    kor = AIRPORT_KOR.get(airport_code)
    if not kor:
        return []

    out: list[dict] = []
    target_str = target.strftime("%Y%m%d")

    for f in iter_flights_for_date(target, per_page=per_page):
        std = _parse_hhmm(f.get("STD"))
        if not std:
            continue

        origin = f.get("BOARDING_KOR") or "-"
        dest = f.get("ARRIVED_KOR") or "-"

        # dep: 선택 공항에서 출발
        if kind == "dep":
            if kor not in origin:
                continue
            out.append({
                "airport_code": airport_code,
                "kind": "dep",
                "flight_date": target_str,                # ✅ target 날짜로 저장
                "std": std,                               # "HHMM"
                "airline": f.get("AIRLINE_KOREAN") or "-",
                "flight_no": f.get("AIR_FLN") or "-",
                "origin": origin or "-",
                "destination": dest or "-",
                "status": f.get("RMK_KOR") or "정상",
            })

        # arr: 선택 공항으로 도착
        elif kind == "arr":
            if kor not in dest:
                continue
            out.append({
                "airport_code": airport_code,
                "kind": "arr",
                "flight_date": target_str,                # ✅ target 날짜로 저장
                "std": std,                               # "HHMM"
                "airline": f.get("AIRLINE_KOREAN") or "-",
                "flight_no": f.get("AIR_FLN") or "-",
                "origin": origin or "-",
                "destination": dest or "-",
                "status": f.get("RMK_KOR") or "정상",
            })

        if len(out) >= limit:
            break

    out.sort(key=lambda x: (x["flight_date"], x["std"], x["flight_no"]))
    return out

def get_board(airport_code: str, kind: str, limit: int = 30, per_page: int = 10000) -> list[dict]:
    """
    kind:
        - "dep": 선택 공항에서 출발(출발지에 공항명 포함)
        - "arr": 선택 공항으로 도착(도착지에 공항명 포함)

    반환 키는 dashboard.html이 바로 쓰게:
        airline, dest, flight, time, status
    """
    cache_key = f"air:board:{airport_code}:{kind}:{limit}:{per_page}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    kor = AIRPORT_KOR.get(airport_code)
    if not kor:
        return []

    now = timezone.localtime(timezone.now(), KST)
    today_page = find_page_for_date(now.date(), per_page=per_page)

    pages = [p for p in [today_page - 3, today_page - 2, today_page - 1, today_page, today_page + 1] if p >= 1]

    out: list[dict] = []

    for p in pages:
        payload = _fetch(page=p, per_page=per_page)
        items = payload.get("data", [])

        for f in items:
            if not _is_future(f, now):
                continue

            today_str = now.strftime("%Y%m%d")
            if f.get("FLIGHT_DATE") != today_str:
                continue

            origin = f.get("BOARDING_KOR") or "-"
            destination = f.get("ARRIVED_KOR") or "-"

            # 출발현황: 출발지가 선택 공항
            if kind == "dep":
                if kor not in origin:
                    continue
                out.append({
                    # 확인용
                    #"date": f.get("FLIGHT_DATE"),

                    "airline": f.get("AIRLINE_KOREAN") or "-",
                    "destination": destination or "-",   # 목적지
                    "flight": f.get("AIR_FLN") or "-",
                    "time": _hhmm(f.get("STD")),         # 출발 예정(네 데이터 기준)
                    "status": f.get("RMK_KOR") or "정상",
                })

            # 도착현황: 도착지가 선택 공항
            elif kind == "arr":
                if kor not in destination:
                    continue
                out.append({
                    # 확인용
                    #"date": f.get("FLIGHT_DATE"),

                    "airline": f.get("AIRLINE_KOREAN") or "-",
                    "origin": origin or "-",             # 도착현황에서는 '출발지'를 보여주는 게 보통이라 origin을 넣음
                    "flight": f.get("AIR_FLN") or "-",
                    "time": _hhmm(f.get("STD")),         # 이 API에 도착예정이 없으면 일단 STD 사용
                    "status": f.get("RMK_KOR") or "정상",
                })

        if len(out) >= limit:
            break

    seen = set()
    unique = []
    for x in out:
        if kind == "dep":
            key = (x.get("flight"), x.get("time"), x.get("destination"))
        else:
            key = (x.get("flight"), x.get("time"), x.get("origin"))

        if key in seen:
            continue
        seen.add(key)
        unique.append(x)
    out = unique

    out.sort(key=lambda x: ((x.get("date") or "99999999"), (x.get("time") or "99:99")))

    result = out[:limit]
    cache.set(cache_key, result, timeout=30)
    return result
