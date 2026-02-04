# 표준 라이브러리
import os
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from functools import lru_cache
from typing import Optional
import logging

# 데이터 처리 및 로드
import pandas as pd
import joblib
import requests
from dotenv import load_dotenv

# 웹 스크래핑
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# AI/LLM 프레임워크 (LangChain)
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 벡터 DB 및 임베딩
from langchain_chroma import Chroma
from sentence_transformers import SentenceTransformer

import warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", message=".*X does not have valid feature names.*")
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
import transformers
transformers.utils.logging.set_verbosity_error()
load_dotenv()




# ==========================================================
# [구간 1] 환경 최적화 및 시스템 설정
# ==========================================================
try:
    import torch
    if os.name == 'nt':
        torch_lib_path = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.exists(torch_lib_path): os.add_dll_directory(torch_lib_path)
except: pass

BASE_DIR = Path(__file__).resolve().parent.parent      # chatbot/
CHROMA_DIR = BASE_DIR / "chroma_db"                   # chatbot/chroma_db

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

# [수정] IATA 코드와 기상청 격자 데이터용 한글 공항명 매핑
IATA_TO_KOR = {
    "ICN": "인천", "GMP": "김포", "CJU": "제주", "PUS": "김해",
    "CJJ": "청주", "TAE": "대구", "KWJ": "광주", "RSU": "여수",
    "USN": "울산", "KPO": "포항경주", "HIN": "사천", "KUV": "군산",
    "WJU": "원주", "MWX": "무안", "YNY": "양양"
}

def get_nxny(airport_name):
    
    kor_name = IATA_TO_KOR.get(airport_name.upper(), airport_name)

    df = pd.read_csv(AIRPORT_MAP_CSV)
    row = df[df["공항"] == kor_name]
    if row.empty:
        raise ValueError(f"공항 '{kor_name}' 없음")
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
    X = X[clf.feature_name_] # clf와 reg의 feature_name_은 같아야 함

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

class FlightAgent:
    # ==========================================================
    # [구간 2] 에이전트 초기화
    # LLM 모델 연동 및 대화 맥락 유지를 위한 기본 정보 구조 생성
    # ==========================================================
    def __init__(self, llm):
        self.llm = llm
        self.parser = JsonOutputParser()
        self.current_info = {
            "flight_no": "N/A",
            "departure": [], 
            "destination": [], 
            "date": "N/A", 
            "airline_name": "N/A",
            "airline_code": "N/A" 
        }

    # ==========================================================
    # [구간 3] 사용자 의도 분석 (LLM)
    # 자연어 입력에서 편명, 출발지, 목적지, 날짜 정보를 추출하고 정규화
    # ==========================================================
    def reset_current_info(self):
        self.current_info = {
        "flight_no": "N/A",
        "departure": [], 
        "destination": [], 
        "date": "N/A", 
        "airline_name": "N/A",
        "airline_code": "N/A" 
    }

    def analyze_and_update(self, user_text):
        now_dt = datetime.now()
        today_str = now_dt.strftime("%Y-%m-%d")
        tomorrow_str = (now_dt + timedelta(days=1)).strftime("%Y%m%d")
        self.current_info["flight_no"] = "N/A"

        prompt = ChatPromptTemplate.from_template("""
        당신은 항공 노선 분석 전문가입니다. 오늘 날짜는 {today}입니다.
        사용자의 입력이 **이전 대화와 이어지는 추가 질문**인지, 아니면 **새로운 여정 검색**인지 판단하세요.
        사용자의 입력에서 정보를 추출하여 JSON으로 반환하세요.

        [추출 규칙]
        1. flight_no: 편명(예: KE77). 항공사 이름만 있고 숫자가 없으면 "N/A".
        2. airline_name: 추출된 airline_code를 바탕으로 한글 이름을 반드시 매핑하세요.
            - KE: "대한항공", OZ: "아시아나항공", LJ: "진에어"
            - TW: "티웨이항공", ZE: "이스타항공", 7C: "제주항공"
            - BX: "에어부산", RS: "에어서울"
            - MU: "동방항공", CA: "중국국제항공"
            - AC: "에어캐나다"
        3. airline_code: 항공사 IATA 코드. 언급된 항공사나 편명을 보고 추론하세요.
           (예: "대한항공" -> "KE", "진에어" -> "LJ", "티웨이" -> "TW", "에어캐나다" -> "AC")
        4. departure: 출발지 IATA 코드 리스트. 언급 없으면 ["ICN", "GMP"].
        5. destination: 도착지 IATA 코드 리스트. 공항이 여러 개인 도시는 같이 모든 코드를 포함할 것."
           (예: 오키나와 -> ["OKA"], 북경 -> ["PEK", "PKX"], 토론토 -> ["YYZ", "YTZ"], 서울 ->["ICN",'GMP'], 상해 -> ["SHA","PVG], 도쿄 -> ["NRT","HND"])
            - **중요**: 사용자가 '도시'를 말하면 해당 도시의 모든 공항을 포함하세요. (상해 -> ["SHA", "PVG"]). 다른 지역의 공항은 넣지 마세요
            - 사용자가 '홋카이도', '규슈' 같은 '지역'을 말하면, 해당 지역의 최대 관문 공항(홋카이도 -> ["CTS", "HKD"], 규슈 -> ["FUK", "KOJ"])을 추론하여 포함하세요.
        6. date: YYYYMMDD 형식. '내일'은 {tomorrow}입니다.

        입력: {user_text} | 이전 데이터: {current_info}
        JSON: {{ "flight_no": "N/A", "airline_name": "N/A", "airline_code": "N/A", "departure": [], "destination": [], "date": "YYYYMMDD" }}
        """)
        
        chain = prompt | self.llm | self.parser
        try:
            res = chain.invoke({"user_text": user_text, "today": today_str, "tomorrow": tomorrow_str, "current_info": self.current_info})
            if res.get("is_new_search", True):
                self.reset_current_info()
            # 출발지 미지정 시 국내 주요 공항(ICN, GMP 등)으로 자동 보완
            if not res.get("departure") or len(res["departure"]) == 0:
                self.current_info["departure"] = ["ICN", "GMP", "PUS", "CJU"]
            else:
                self.current_info["departure"] = res["departure"]

            if res.get("flight_no") and res.get("flight_no") != "N/A":
                self.current_info["flight_no"] = str(res["flight_no"]).upper().replace(" ", "")
            if res.get("date") and res.get("date") != "N/A":
                self.current_info["date"] = str(res["date"])
            if res.get("destination"):
                self.current_info["destination"] = res["destination"]
            if res.get("airline_code"): 
                self.current_info["airline_code"] = res["airline_code"].upper()
            if res.get("airline_name"): 
                self.current_info["airline_name"] = res["airline_name"]

        except Exception as e:
            print(f"⚠️ 분석 오류: {e}")

    
    # ==========================================================
    # [구간 4] 노선 기반 항공편 검색 (Scraping)
    # 특정 구간(출발-도착)의 모든 운항 정보를 조회하여 선택 리스트 생성
    # ==========================================================

    async def search_by_route(self):
        info = self.current_info
        air_code = info.get("airline_code", "")
        if air_code == "N/A": air_code = ""
        
        try:
            dt = info['date']
            y, m, d = dt[:4], str(int(dt[4:6])), str(int(dt[6:]))
        except: return []

        all_flights = {}
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            for dep in info['departure']:
                for arr in info['destination']:
                    # URL 경로 생성: ICN/TPE/LJ 형태
                    path_segments = [dep]
                    if arr: path_segments.append(arr)
                    if air_code: path_segments.append(air_code)

                    route_path = "/".join(path_segments)
                    url = f"https://www.flightstats.com/v2/flight-tracker/route/{route_path}?year={y}&month={m}&date={d}"
                    
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        soup = BeautifulSoup(await page.content(), 'html.parser')
                        links = soup.select('a[href*="/v2/flight-tracker/"]')
                        
                        for link in links:
                            h2s = [h.get_text(strip=True) for h in link.find_all('h2')]
                            if len(h2s) >= 3:
                                f_no = h2s[0].replace(" ", "")
                                
                                # [추가 검증] URL 필터링 후에도 혹시 모를 타사 코드 제외
                                if air_code and not f_no.startswith(air_code):
                                    continue

                                match = re.match(r'([A-Z0-9]+)(\d+)', f_no)
                                if match:
                                    air, num = match.groups()
                                    all_flights[f_no] = {
                                        "no": f_no, "dep": dep, "arr": arr,
                                        "url": f"https://www.flightstats.com/v2/flight-details/{air}/{num}?year={y}&month={m}&date={d}"
                                    }
                    except Exception as e:
                        print(f"⚠️ {route_path} 검색 중 오류: {e}")
                        continue
            await browser.close()
        return list(all_flights.values())

    # ==========================================================
    # [구간 5] 항공편 상세 정보 파싱 (Scraping)
    # 특정 편명의 실시간 상태, 게이트, 터미널, 시간 정보를 정밀 추출
    # ==========================================================
    async def get_details(self, flight_no):
        info = self.current_info
        dt = info['date']
        y, m, d = dt[:4], str(int(dt[4:6])), str(int(dt[6:]))
        
        # 특수문자 제거 및 숫자 포함 항공사 코드 대응
        clean_no = re.sub(r'[^a-zA-Z0-9]', '', flight_no).upper()
        match = re.match(r'^([A-Z0-9]{2,3}?)(\d+)$', clean_no)
        
        if not match: return None
        air, num = match.groups()
        url = f"https://www.flightstats.com/v2/flight-details/{air}/{num}?year={y}&month={m}&date={d}"

        print(f"'{flight_no}'로 조회를 시작합니다...") 
        info = self.current_info

        match = re.match(r'^([A-Z0-9]{2,3}?)(\d+)$', clean_no)
        if match:
            air, num = match.groups()
            # print(f"DEBUG: 항공사 코드 -> {air}, 편명 숫자 -> {num}") 
            # url = f"https://www.flightstats.com/v2/flight-details/{air}/{num}?year={y}&month={m}&date={d}"
            # print(f"DEBUG: 최종 생성 URL -> {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # 불필요한 리소스 차단으로 로딩 속도 최적화
            await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2}", lambda route: route.abort())
            
            try:
                # 페이지 구조가 로드될 때까지만 대기 (타임아웃 방지)
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_selector('div.flight-ticket', timeout=15000)
                
                try:
                    await page.wait_for_function(
                        """() => {
                            const gates = document.querySelectorAll('div[class*="gateBlock"] h4');
                            return Array.from(gates).some(g => g.innerText.trim() !== '-' && g.innerText.trim() !== '');
                        }""", timeout=3000
                    )
                except:
                    pass
                soup = BeautifulSoup(await page.content(), 'html.parser')
                codes = [el.get_text(strip=True) for el in soup.select('h2.airportCodeTitle')]
                s_dep = codes[0] if len(codes) >= 1 else None
                s_arr = codes[1] if len(codes) >= 2 else None
                route_type = self.determine_route_type(s_dep, s_arr)

                # 1. 항공사 이름이 N/A인 경우에만 추출 시도
                if self.current_info.get("airline_name") == "N/A":
                    # 캡처 이미지의 h1.carrier-text-style 타겟팅
                    airline_h1 = soup.select_one('h1.carrier-text-style')
                    
                    if airline_h1:
                        full_text = airline_h1.get_text(strip=True)
                        # (코드) 이름 숫자 -> 형태에서 이름만 추출
                        # 예: "(UA) United Airlines 7318..." -> "United Airlines"
                        match = re.search(r'\([A-Z0-9]+\)\s*(.*?)\s*\d+', full_text)
                        
                        if match:
                            self.current_info["airline_name"] = match.group(1).strip()
                        else:
                            # 패턴 매칭 실패 시 " Flight" 이전까지만 가져오기
                            self.current_info["airline_name"] = full_text.split(" Flight")[0].strip()
                            
                    # 2. h1 태그도 없다면 마지막 보루로 항공사 코드(air) 사용
                    if self.current_info["airline_name"] == "N/A":
                        self.current_info["airline_name"] = air
                        
                res = {
                    "status": "N/A", 
                    "route_type": route_type,
                    "s_dep": s_dep,  
                    "s_arr": s_arr, 
                    "dep": {"t": "-", "g": "-", "time": []}, 
                    "arr": {"t": "-", "g": "-", "time": []}
                }

                # 실시간 상태 정보 추출 (최신 레이아웃 및 백업 대응)
                status_el = soup.select_one('p[class*="status-text-style"]')
                if status_el:
                    res["status"] = status_el.get_text(strip=True)
                else:
                    sb = soup.select_one('div[class*="statusBlock"]')
                    if sb: 
                        res["status"] = sb.get_text(" ", strip=True).replace("*", "")

                # 출도착 게이트, 터미널, 예정/실제 시간 파싱
                tickets = soup.select('div.flight-ticket')
                for i, ticket in enumerate(tickets[:2]):
                    key = "dep" if i == 0 else "arr"
                    t_el = ticket.select_one('div[class*="terminalBlock"] h4')
                    if t_el: res[key]["t"] = t_el.get_text(strip=True)
                    g_el = ticket.select_one('div[class*="gateBlock"] h4')
                    if g_el:
                        g_val = g_el.get_text(strip=True)
                        res[key]["g"] = g_val if "TIMES" not in g_val.upper() else "-"

                    blocks = ticket.select('div[class*="timeBlock"]')[:2]
                    for b in blocks:
                        lbl = b.select_one('p[class*="title"]')
                        val = b.select_one('h4')
                        if lbl and val: 
                            res[key]["time"].append(f"{lbl.get_text(strip=True)}: {val.get_text(strip=True)}")
                return res
            except Exception as e: 
                print(f"⚠️ 상세 정보 파싱 실패 ({flight_no}): {e}")
                return None
            finally: await browser.close()

# ==========================================================
# [구간 6] 노선 타입 판별 (국내/국제)
# 내부 데이터 또는 스크랩된 데이터를 기반으로 판별
# ==========================================================
    def determine_route_type(self, scraped_dep=None, scraped_arr=None):
        # [수정] 리스트가 아닌 '단일 코드'가 들어가도록 우선순위 조정
        dep = scraped_dep if scraped_dep else (self.current_info.get("departure")[0] if self.current_info.get("departure") else "N/A")
        dest = scraped_arr if scraped_arr else (self.current_info.get("destination")[0] if self.current_info.get("destination") else "N/A")
        f_no = self.current_info.get("flight_no", "N/A")
        
        prompt = ChatPromptTemplate.from_template("""
        System: 당신은 항공 노선 판별기입니다. 입력된 공항 코드를 보고 '국내' 혹은 '국제' 노선인지 판별하세요.
        
        [데이터 정보]
        - 출발 공항: {dep}
        - 도착 공항: {dest}
        - 편명: {f_no}

        [판단 규칙]
        1. 출발지와 도착지의 국가가 다르면 무조건 '국제'입니다. (예: ICN-CTS, ICN-NRT는 국제선)
        2. 같은 국가 내 공항 이동(예: GMP-CJU)인 경우만 '국내'입니다.
        
        [출력 규칙]
        1. 다른 설명 없이 오직 {{"type": "국내"}} 또는 {{"type": "국제"}} 형식의 JSON만 출력하세요.
        2. 한글(Korean)만 사용하며, 한자나 영어는 절대 섞지 마세요.
        """)
        
        chain = prompt | self.llm | self.parser
        try:
            res = chain.invoke({"dep": dep, "dest": dest, "f_no": f_no})
            return res.get("type", "정보 없음")
        except:
            return "정보 없음"

# ==========================================================
# [구간 7] 결과 출력 포맷팅
# 수집된 상세 정보를 깔끔한 표 형태로 출력
# ==========================================================
def print_result(no, d, date_str):
    # 날짜 포맷팅
    formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    
    print("\n" + "="*50)
    print(f"✈️  {no} 상세 정보 ({d['status']}) -- {d.get('route_type', '정보 없음')} --")
    print("="*50)
    
    for k in ["dep", "arr"]:
        label = "🛫 출발" if k == "dep" else "🛬 도착"
        info = d[k]
        
        # k가 "dep"(출발)일 때만 뒤에 날짜를 붙임
        if k == "dep":
            print(f"{label}: (Terminal: {info['t']} / Gate: {info['g']}) {formatted_date}")
        else:
            print(f"{label}: (Terminal: {info['t']} / Gate: {info['g']})")
            
        for t in info['time']: 
            print(f"  - {t}")
        print("-" * 50)
    print("="*50)

# RAG, model 연동
def display_summary(agent, details, flight_no):
    s_time = "N/A"
    if details.get('dep') and details['dep'].get('time'):
        s_time = details['dep']['time'][0].split(": ")[-1]

    # [수정] 크롤링된 실제 코드(s_dep, s_arr)가 있으면 그것을 사용, 없으면 입력값 사용
    dep_airport = details.get('s_dep') or (agent.current_info.get("departure")[0] if agent.current_info.get("departure") else "N/A")
    arr_airport = details.get('s_arr') or (agent.current_info.get("destination")[0] if agent.current_info.get("destination") else "N/A")

    summary = {
        "is_international": details.get('route_type', '정보 없음'),
        "airline": agent.current_info.get("airline_name", "N/A"),
        "dep_airport": dep_airport,
        "arr_airport": arr_airport,
        "dep_time": s_time,
        "date": agent.current_info['date']
    }

    print(f"💡 요약 결과: {summary['airline']} | {summary['is_international']} | "
          f"{summary['dep_airport']} 출발 | {summary['arr_airport']} 도착 | "
          f"{summary['date']} | {summary['dep_time']}시 예정")
    return summary 

# ==========================================================
# [구간 8] RAG 연동
# ==========================================================
@lru_cache(maxsize=1)
def _embedder():
    # chroma DB 만들 때 쓴 임베딩 모델과 동일해야 정확도가 가장 좋음
    return SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")

COLLECTION_NAME = "airline_terms"

@lru_cache(maxsize=1)
def _vectordb():
    # persist_directory만 있으면 기존 DB 로드됨
    return Chroma(
        persist_directory=str(CHROMA_DIR),
        collection_name=COLLECTION_NAME,
        embedding_function=None,  # 문서 임베딩은 이미 DB에 저장되어 있음
    )


def retrieve_context(query: str, summary: dict, k: int = 3) -> str:
    if not query: return ""
    
    # [추가] 항공사 이름이 정확히 전달되는지 확인 (디버깅용)
    target_airline = summary.get('airline', '알 수 없는 항공사')
    target_route = summary.get('is_international', '정보 없음')
    print(f"🔍 RAG 검색 시작 - 대상 항공사: {target_airline}") # 이 로그가 N/A면 안 됩니다.

    # 1. DB에서 후보군 추출
    emb = _embedder().encode(query).tolist()
    results = _vectordb()._collection.query(
        query_embeddings=[emb], 
        n_results=10, 
        include=["documents", "metadatas"]
    )
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]

    # 2. LLM 필터링 프롬프트 강화
    filter_prompt = ChatPromptTemplate.from_template("""
    당신은 항공 규정 매칭 전문가입니다. 
    사용자가 현재 이용 중인 항공사는 **'{airline}'**입니다.

    [검색된 문서 리스트]
    {doc_list}

    [필터링 규칙 - 유연한 매칭]
    1. **언어 중립 매칭**: '{airline}'이 영어(예: Air Busan)든 한글(예: 에어부산)이든 혹은 코드로 되어있더라도 동일한 항공사로 간주하여 선택하세요.
    2. **별칭 허용**: 항공사 코드(예: BX, KE, OZ)나 약칭이 문서에 포함되어 있어도 해당 항공사의 규정으로 판단하세요.
    3. **포함 우선**: 문서 내용 중에 '{airline}'에 대한 언급이 단 한 줄이라도 있다면, 사용자를 위해 해당 문서를 반드시 포함(indices에 추가)하세요.
    4. **공통 규정 활용**: 만약 '{airline}' 전용 규정이 없더라도, 모든 항공사에 공통으로 적용되는 '일반 항공 규정'이나 '공통 수하물 안내' 문구가 있다면 포함하세요.
    5. **결과 형식**: 선택한 문서의 인덱스 번호를 JSON 형식의 {{"indices": [번호]}}로 반환하세요. 관련 내용이 전혀 없다면 {{"indices": []}}를 반환하세요.
    """)
    
    doc_list_str = "\n".join([f"[{i}] 메타데이터: {m} | 내용 요약: {d[:150]}..." for i, (d, m) in enumerate(zip(docs, metas))])
    
    filter_chain = filter_prompt | ChatOllama(model='qwen2.5:14b', format="json", temperature=0) | JsonOutputParser()
    
    try:
        res = filter_chain.invoke({
            "airline": target_airline,  # <--- 여기서 정확히 전달됨
            "is_international": target_route,
            "doc_list": doc_list_str
        })
        
        valid_indices = res.get("indices", [])
        picked = [docs[i] for i in valid_indices if i < len(docs)]
        
        if not picked:
            return f"현재 {target_airline}의 해당 규정 데이터가 부족하여 일반적인 항공법 기준으로 답변해 드립니다."
            
        return "\n\n".join(picked[:k])
        
    except Exception as e:
        print(f"⚠️ 필터링 로직 실행 중 오류: {e}")
        return ""
# ==========================================================
# [구간 9] 최종 답변 생성 (추가)
# ==========================================================
def get_rag_answer(llm, query, context, flight_info, prediction_result=None):
    pred_text = "N/A"
    if prediction_result and prediction_result.get('ok'):
        status = "지연 예상" if prediction_result.get('is_delay') == 1 else "정시 운항 예상"
        delay_min = prediction_result.get('predicted_delay_minutes', 0)
        pred_text = f"{status} (예상 지연 시간: {delay_min:.1f}분)"

    prompt = ChatPromptTemplate.from_template("""
    당신은 항공 규정 및 실시간 운항 정보 전문가입니다. 
    사용자가 규정집을 뒤지는 수고를 덜어주는 '해결사' 역할을 수행해야 합니다.

    [핵심 답변 원칙]
    1. **회피 금지**: "고객센터에 문의하세요", "직접 확인하세요"라는 답변은 시스템의 실패입니다. 절대 금지합니다.
    2. **적극적 가이드**: {airline}의 특정 문구가 {context}에 없더라도, 문서 내의 '일반 운송 약관'이나 '보상 지침'을 활용하여 현재 상황(예: {query})에 대한 최선의 행동 지침을 제공하세요.
    3. **출처 명시**: 답변 서두에 반드시 "현재 확보된 {airline} 규정(또는 일반 항공 규정)에 근거하여 안내해 드립니다."라고 명기하세요.
    4. **언어 정제**: 한국어로만 작성하며, 불필요한 한자(国际, 际 등)나 기계적인 번역투를 지양하세요.

    [상황별 답변 로직]
    1. **지연/결항 상황**:
       - {context}에서 해당 시간(예: 4시간)에 따른 서비스(식사권, 숙박, 통신 등)를 즉시 나열하세요.
       - 만약 기상 악화(천재지변)라면, '항공사 귀책 없음'을 설명하되 그럼에도 불구하고 제공받을 수 있는 '대기 서비스'가 있는지 {context}에서 찾아 안내하세요.
    2. **행동 지침 (Action Plan)**:
       - 승객이 지금 당장 해야 할 일(예: "게이트 카운터 방문", "지연 증명서 발급 요청", "바우처 수령")을 번호 순서대로 명확히 제시하세요.

    [참고 데이터]
    - 대상 항공사: {airline}
    - 실시간 항공 정보: {flight_info}
    - 항공사 규정 문서(Context): {context}
    - 사용자 질문: {query}

    [최종 미션]
    사용자가 이 답변을 듣고 "아, 이제 어떻게 해야 할지 알겠다"라고 확신하게 만드세요.
    """)
    
    target_airline = flight_info.get('airline_name', '해당 항공사')
    
    # Context가 비어있을 경우를 대비한 최소한의 가이드 문구 삽입
    if not context or context.strip() == "":
        context = f"현재 {target_airline}의 개별 규정 데이터가 부족합니다. 항공교통이용자 보호기준 등 보편적인 항공법 권고 사항을 바탕으로 답변하세요."


    chain = prompt | llm
    return chain.invoke({"query": query, "context": context, "flight_info": str(flight_info),"airline": target_airline, "prediction_text": pred_text})
# ==========================================================
# [구간 10] 메인 루프 통합 (수정)
# ==========================================================
    async def main():
        llm = ChatOllama(model='qwen2.5:14b', format="json", temperature=0)
        # RAG용 LLM은 JSON 형식이 아닐 수 있으므로 별도 생성하거나 설정을 유연하게 가져갑니다.
        rag_llm = ChatOllama(model='qwen2.5:14b', temperature=0) 
        agent = FlightAgent(llm)
        print("🤖 항공 비서 가동 중...")
    
        while True:
            u_in = input("\n👤 사용자 (항공편명 혹은 '출발-도착' 입력): ").strip()
            if u_in.lower() in ['exit', '종료']: break
            
            agent.analyze_and_update(u_in)
            
            flight_details = None
            # ... (중략: 편명 추출 및 search_by_route 로직은 기존과 동일) ...
            
            # 상세 정보(d)가 획득된 시점
            if d:
                print_result(target_no, d, agent.current_info['date'])
                summary_data = display_summary(agent, d, target_no)
                
                # [RAG 프로세스 추가]
                follow_up = input("\n💡 해당 항공편 규정(수하물 등)에 대해 궁금한 점이 있나요? (n: 건너뛰기): ").strip()
                if follow_up.lower() != 'n':
                    print("🔍 규정 확인 중...")
                    context = retrieve_context(follow_up, summary_data)
                    answer = get_rag_answer(rag_llm, follow_up, context, d)
                    print(f"\n🤖 답변: {answer.content}")

# ==========================================================
# [구간 10] 통합 메인 루프 (구간 6과 구간 10의 결합)
# ==========================================================
async def main():
    # 1. 모델 초기화
    llm = ChatOllama(model='qwen2.5:32b', format="json", temperature=0)
    rag_llm = ChatOllama(model='qwen2.5:32b', temperature=0.3) 
    agent = FlightAgent(llm)
    
    print("🤖 항공 비서 가동 중... (종료하려면 'exit' 입력)")

    while True:
        u_in = input("\n👤 사용자: ").strip()
        if u_in.lower() in ['exit', '종료']: break
        
        # 2. 사용자 입력 분석
        agent.analyze_and_update(u_in)
        if agent.current_info["date"] == "N/A":
            agent.current_info["date"] = datetime.datetime.now().strftime("%Y%m%d")

        target_no = None
        d = None

        # [경우 1] 편명이 즉시 추출된 경우
        if agent.current_info["flight_no"] != "N/A":
            target_no = agent.current_info["flight_no"]
            d = await agent.get_details(target_no)
        
        # [경우 2] 편명을 모르고 목적지만 있는 경우 (노선 검색)
        elif agent.current_info["destination"]:
            print(f"📡 노선 검색 중: {agent.current_info['departure']} -> {agent.current_info['destination']}")
            flights = await agent.search_by_route()
            
            # 검색 결과 필터링 (항공사 코드가 있을 경우)
            target_code = agent.current_info.get("airline_code", "N/A")
            if target_code != "N/A":
                flights = [f for f in flights if f['no'].startswith(target_code)]

            if not flights:
                print("❌ 검색 결과가 없습니다.")
                continue
            elif len(flights) == 1:
                target_no = flights[0]['no']
                print(f"✅ [{target_no}] 항공편 발견. 상세 조회 시작...")
                d = await agent.get_details(target_no)
            else:
                print(f"\n✅ {len(flights)}개의 항공편을 찾았습니다.")
                for i, f in enumerate(flights):
                    print(f"[{i+1}] {f['no'].ljust(8)} | {f['dep']} -> {f['arr']}")
                
                sel = input("\n💡 상세 정보를 확인할 번호를 입력하세요 (n: 취소): ").strip()
                if sel.lower() == 'n':
                    agent.reset_current_info()
                    print("🧹 검색이 취소되어 이전 검색 조건(항공사 등)이 초기화되었습니다.")
                    continue

                if sel.isdigit() and 1 <= int(sel) <= len(flights):
                    target_no = flights[int(sel)-1]['no']
                    d = await agent.get_details(target_no)

        # 3. 결과 출력 및 RAG 연동
        if d and target_no:
            # ---------------------------------------------------------
            # 1. 항공 정보 출력 (크롤링 결과)
            # ---------------------------------------------------------
            print_result(target_no, d, agent.current_info['date'])
            summary_data = display_summary(agent, d, target_no)
            
            # ---------------------------------------------------------
            # 2. 지연 예측 및 보상안 RAG 연결
            # ---------------------------------------------------------
            print("\n🔍 운항 상태 분석 중...")
            try:
                # 시간 정보 추출 및 전처리
                scheduled_dep = ""
                actual_dep = ""
                for t_str in d['dep']['time']:
                    if "Scheduled" in t_str: scheduled_dep = t_str.split(": ")[-1]
                    if "Actual" in t_str: actual_dep = t_str.split(": ")[-1]

                # 실제 시간이 '--' 거나 비어있으면 실제 운항 데이터가 없는 것으로 간주
                has_actual_data = actual_dep and actual_dep != "--"

                # --- (경우 1) 이미 출발했거나 실제 기록이 있는 경우 ---
                if ("Departed" in d['status'] or has_actual_data) and has_actual_data:
                    fmt = "%H:%M"
                    s_time = datetime.strptime(scheduled_dep, fmt)
                    a_time = datetime.strptime(actual_dep, fmt)
                    
                    delay_delta = int((a_time - s_time).total_seconds() / 60)
                    
                    if delay_delta > 15:
                        print(f"⚠️ [운항 결과] 해당 항공편은 예정보다 **{delay_delta}분 지연 출발**하였습니다.")
                        # (지연 시 RAG 보상안 로직은 동일)
                    else:
                        print(f"✅ [운항 결과] 해당 항공편은 **정상 출발**하였습니다.")

                # --- (경우 2) 출발 전이거나 데이터가 아직 없는 경우: AI 지연 예측 ---
                else:
                    print("🔮 항공편이 아직 출발 전입니다. AI 지연 예측을 시작합니다...")
                    dep_datetime = f"{summary_data['date'][:4]}-{summary_data['date'][4:6]}-{summary_data['date'][6:]} {scheduled_dep if scheduled_dep else '12:00'}"
                    
                    prediction = predict_delay_binary(
                        airline=summary_data['airline'],
                        departure_airport=summary_data['dep_airport'],
                        flight_type=summary_data['is_international'],
                        departure_datetime=dep_datetime,
                        arrival_code=summary_data['arr_airport'],
                        threshold=0.2
                    )
                    
                    if prediction.get('ok'):
                        if prediction.get('is_delay') == 1:
                            delay_min = prediction.get('predicted_delay_minutes', 0)
                            print(f"⚠️ [지연 예측] 결과 지연될 것으로 예상되며, 약 **{delay_min:.1f}분** 지연될 것으로 보입니다.")
                            print(f"📋 {summary_data['airline']}의 지연 관련 보상 규정을 확인합니다...")
                            
                            # 자동으로 질문 생성 (예: "아시아나항공 4분 지연 시 보상 규정")
                            auto_query = f"{summary_data['airline']} {int(delay_min)}분 지연 시 보상 및 서비스 규정"
                            
                            # RAG 실행
                            context = retrieve_context(auto_query, summary_data)
                            delay_answer = get_rag_answer(rag_llm, auto_query, context, d)
                            
                            print(f"\n🤖 **AI 보상 안내:**\n{delay_answer.content}")
                        else:
                            print(f"✅ [지연 예측] 결과 정상 운항할 것으로 보입니다.")
                    else:
                        print(f"ℹ️ 현재 기상 및 공항 상황 데이터 기반으로 정상 운항이 예상됩니다.")

            except Exception as e:
                print(f"⚠️ 상태 분석 과정에서 알 수 없는 오류가 발생했습니다: {e}")

            # ---------------------------------------------------------
            # 3. 추가 질문 (수하물 등 일반 규정)
            # ---------------------------------------------------------
            follow_up = input("\n💡 해당 항공편 규정(수하물 등)에 대해 더 궁금한 점이 있나요? (n: 건너뛰기): ").strip()
            if follow_up.lower() != 'n':
                print("🔍 해당 규정을 확인 중입니다...")
                context = retrieve_context(follow_up, summary_data) 
                answer = get_rag_answer(rag_llm, follow_up, context, d)
                print(f"\n🤖 답변: {answer.content}")
        else:
            if not agent.current_info["destination"] and agent.current_info["flight_no"] == "N/A":
                print("🤖 목적지나 편명을 말씀해 주세요. (예: '내일 도쿄가는 진에어 알려줘' 또는 'KE77')")

if __name__ == "__main__":
    asyncio.run(main())