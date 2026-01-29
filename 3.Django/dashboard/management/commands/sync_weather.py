import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from dashboard.models import WeatherCurrent

AMOS_URL = "https://apihub.kma.go.kr/api/typ01/url/amos.php"
KST = ZoneInfo("Asia/Seoul")

# 공항코드 -> AMOS stn (김해/부산은 AMOS 지점이 없어서 None)
AIRPORT_TO_STN = {
    "ICN": "113",
    "GMP": "110",
    "CJU": "182",
    "MWX": "163",
    "USN": "151",
    "RSU": "167",
    "YNY": "92",
    "PUS": None,  # ❗ AMOS에 없으면 스킵
}

def _to_int(x: str):
    try:
        return int(x)
    except:
        return None

def _clean_missing(v: int | None):
    if v is None:
        return None
    if v in (-99999, -9999, 99999):
        return None
    return v

def _parse_latest_amos_row(text: str) -> dict | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if not ln.startswith("#")]
    if not data_lines:
        return None

    cols = data_lines[-1].split()
    # 0:STN 1:TM 2:L_VIS 3:R_VIS ... 7:TA ... 18:WS02 19:WS02_MAX
    stn = cols[0]
    tm = cols[1]

    l_vis = _clean_missing(_to_int(cols[2]))
    r_vis = _clean_missing(_to_int(cols[3]))

    ta_raw = _clean_missing(_to_int(cols[7]))
    ta = None if ta_raw is None else ta_raw / 10.0

    ws02_raw = _clean_missing(_to_int(cols[18]))
    ws02 = None if ws02_raw is None else ws02_raw / 10.0

    ws02_max_raw = _clean_missing(_to_int(cols[19]))
    ws02_max = None if ws02_max_raw is None else ws02_max_raw / 10.0

    # TM: YYYYMMDDHHMI
    observed = datetime.strptime(tm, "%Y%m%d%H%M").replace(tzinfo=KST)

    return {
        "stn": stn,
        "observed_at": observed,
        "ta": ta,
        "ws02": ws02,
        "ws02_max": ws02_max,
        "l_vis": l_vis,
        "r_vis": r_vis,
    }

class Command(BaseCommand):
    help = "Fetch AMOS weather for airports and upsert WeatherSnapshot"

    def add_arguments(self, parser):
        parser.add_argument("--dtm", type=int, default=10, help="minutes window for AMOS (default 10)")
        parser.add_argument("--only", type=str, default="", help="comma-separated airport codes e.g. ICN,GMP")

    def handle(self, *args, **opts):
        key = (os.getenv("KEY") or "").strip()
        if not key:
            raise SystemExit("missing KEY in env")

        dtm = opts["dtm"]
        only = [x.strip().upper() for x in opts["only"].split(",") if x.strip()]
        airports = only or list(AIRPORT_TO_STN.keys())

        upserts = 0
        skipped = 0

        for airport in airports:
            stn = AIRPORT_TO_STN.get(airport)
            if not stn:
                skipped += 1
                continue

            params = {"stn": stn, "dtm": dtm, "authKey": key}
            try:
                r = requests.get(AMOS_URL, params=params, timeout=(3, 7))
                r.raise_for_status()
                parsed = _parse_latest_amos_row(r.text)
                if not parsed:
                    continue

                WeatherCurrent.objects.update_or_create(
                    airport_code=airport,
                    defaults={
                        "stn": parsed["stn"],
                        "observed_at": parsed["observed_at"],
                        "ta": parsed["ta"],
                        "ws02": parsed["ws02"],
                        "ws02_max": parsed["ws02_max"],
                        "l_vis": parsed["l_vis"],
                        "r_vis": parsed["r_vis"],
                    }
                )

                upserts += 1
            except Exception as e:
                self.stderr.write(f"[{airport}] failed: {type(e).__name__}")

        self.stdout.write(self.style.SUCCESS(
            f"Weather sync done: {upserts} upserts, {skipped} skipped(PUS etc)"
        ))
