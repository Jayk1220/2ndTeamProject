import re
from django.utils import timezone
from dashboard.models import FlightSnapshot

FLIGHT_RE = re.compile(r"\b([A-Z]{2}\d{2,4})\b", re.I)  # KE1401, OZ8234 등

def find_flight_context(message: str, airport_code: str | None = None) -> str:
    """
    메시지에서 편명을 찾고, 오늘/현재 이후 기준으로 FlightSnapshot에서 가장 가까운 항공편 1개를 찾아 요약 문자열로 반환
    """
    m = (message or "").upper()
    m2 = FLIGHT_RE.search(m)
    if not m2:
        return ""

    flight_no = m2.group(1)
    today = timezone.localdate().strftime("%Y%m%d")
    now_hhmm = timezone.localtime().strftime("%H%M")

    qs = FlightSnapshot.objects.filter(
        flight_no=flight_no,
        flight_date=today,
    )

    if airport_code:
        qs = qs.filter(airport_code=airport_code)

    # 지금 이후 가까운 편 우선
    obj = qs.filter(std__gte=now_hhmm).order_by("std").first()
    if not obj:
        # 그래도 없으면 오늘 중 아무거나
        obj = qs.order_by("-updated_at").first()
    if not obj:
        return ""

    when = f"{obj.flight_date} {obj.std}"
    status = obj.status or "정상"
    place = obj.destination if obj.kind == "dep" else obj.origin

    return (
        f"[실시간 항공편 상태]\n"
        f"- 공항: {obj.airport_code}\n"
        f"- 구분: {'출발' if obj.kind=='dep' else '도착'}\n"
        f"- 편명: {obj.flight_no}\n"
        f"- 항공사: {obj.airline}\n"
        f"- {'목적지' if obj.kind=='dep' else '출발지'}: {place}\n"
        f"- 예정시간: {when}\n"
        f"- 상태: {status}\n"
    )
