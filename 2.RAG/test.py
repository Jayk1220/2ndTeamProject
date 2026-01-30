import os
import joblib
import pandas as pd
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

# 한국 시간
KST = timezone(timedelta(hours=9))

# 모델 경로
CLASSIFIER_PATH = "모델_이진분류.joblib"
REGRESSOR_PATH  = "모델_회기분석.joblib"   # ✅ 추가

clf = joblib.load(CLASSIFIER_PATH)
print("✅ 이진분류 모델 로드 완료")

reg = joblib.load(REGRESSOR_PATH)          # ✅ 이 줄이 빠졌던 것
print("✅ 회귀 모델 로드 완료")

# 기상청 API
BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
VILAGE_BASE_TIMES = ["0200","0500","0800","1100","1400","1700","2000","2300"]

# 공항 격자 CSV (공항, nx, ny)
AIRPORT_MAP_CSV = "airport_nxny_map.csv"

def load_service_key():
    load_dotenv()
    key = os.getenv("KMA_SERVICE_KEY")
    if not key:
        raise ValueError("KMA_SERVICE_KEY 없음 (.env 확인)")
    return key

def pick_latest_vilage_base(now_kst=None):
    if now_kst is None:
        now_kst = datetime.now(KST)

    ymd = now_kst.strftime("%Y%m%d")
    hm  = now_kst.strftime("%H%M")

    candidates = [t for t in VILAGE_BASE_TIMES if t <= hm]
    if candidates:
        return ymd, candidates[-1]

    # 새벽이면 전날 23시
    ymd_yesterday = (now_kst - timedelta(days=1)).strftime("%Y%m%d")
    return ymd_yesterday, "2300"

def get_nxny(airport_name):
    df = pd.read_csv(AIRPORT_MAP_CSV)
    row = df[df["공항"] == airport_name]
    if row.empty:
        raise ValueError(f"공항 '{airport_name}' 없음")
    return int(row.iloc[0]["nx"]), int(row.iloc[0]["ny"])

def get_weather(departure_airport, dep_dt):
    service_key = load_service_key()
    nx, ny = get_nxny(departure_airport)

    now_kst = datetime.now(KST)
    base_date, base_time = pick_latest_vilage_base(now_kst)

    params = {
        "serviceKey": service_key,
        "numOfRows": 500,
        "pageNo": 1,
        "dataType": "JSON",
        "base_date": base_date,
        "base_time": base_time,
        "nx": nx,
        "ny": ny,
    }

    r = requests.get(f"{BASE_URL}/getVilageFcst", params=params, timeout=20)
    r.raise_for_status()

    items = r.json()["response"]["body"]["items"]["item"]
    df = pd.DataFrame(items)

    df = df[df["category"].isin(["TMP", "WSD"])].copy()
    df["fcst_datetime"] = pd.to_datetime(
        df["fcstDate"] + df["fcstTime"],
        format="%Y%m%d%H%M"
    )

    before = df[df["fcst_datetime"] <= dep_dt]
    if before.empty:
        return None

    latest = before.sort_values("fcst_datetime").iloc[-1]

    temp = df[(df["category"]=="TMP") & (df["fcst_datetime"]==latest["fcst_datetime"])]["fcstValue"].values
    wind = df[(df["category"]=="WSD") & (df["fcst_datetime"]==latest["fcst_datetime"])]["fcstValue"].values

    return {
        "기온(°C)": float(temp[0]) if len(temp)>0 else None,
        "풍속_ms": float(wind[0]) if len(wind)>0 else None,
    }

def predict_delay_binary(
    airline: str,
    departure_airport: str,
    flight_type: str,
    departure_datetime: str,
    arrival_code: str,
    threshold: float = 0.4,
):
    dep_dt = pd.to_datetime(departure_datetime)

    # 1️⃣ 날씨
    weather = get_weather(departure_airport, dep_dt)
    if weather is None:
        return {"ok": False, "reason": "날씨 데이터 없음"}

    # 2️⃣ 시간 파생
    dep_hour = dep_dt.hour
    dep_minute = dep_dt.minute
    dep_weekday = dep_dt.weekday()
    is_weekend = int(dep_weekday in [5,6])

    # 3️⃣ 모델 입력 (분류/회귀 공통)
    X = pd.DataFrame([{
        "기온(°C)": weather["기온(°C)"],
        "풍속_ms": weather["풍속_ms"],
        "dep_hour": dep_hour,
        "dep_minute": dep_minute,
        "dep_weekday": dep_weekday,
        "is_weekend": is_weekend,
        "항공사": airline,
        "출발지": departure_airport,
        "arrival_code": arrival_code,
        "flight_type": flight_type,
    }])

    for c in ["항공사","출발지","arrival_code","flight_type"]:
        X[c] = X[c].astype("category")

    # 4️⃣ 이진분류 (지연 여부)
    prob = float(clf.predict_proba(X)[:,1][0])
    is_delay = int(prob >= threshold)

    result = {
        "ok": True,
        "delay_prob": prob,
        "is_delay": is_delay,
        "weather": weather,
    }

    # 5️⃣ ✅ 지연일 때만 회귀 실행
    if is_delay == 1:
        delay_minutes = float(reg.predict(X)[0])

        # 음수 방지 (회귀 모델에서 가끔 발생)
        delay_minutes = max(0.0, delay_minutes)

        result["predicted_delay_minutes"] = delay_minutes

    return result

result = predict_delay_binary(
    airline="가루다인도네시아",
    departure_airport="인천",
    flight_type="국내",
    departure_datetime="2026-02-01 20:30",
    arrival_code="CJU",
    threshold=0.75
)

print(result)
