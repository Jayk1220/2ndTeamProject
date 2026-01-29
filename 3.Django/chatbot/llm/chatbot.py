import os
import asyncio
import datetime
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

# ==========================================================
# [구간 1] 환경 최적화 및 시스템 설정
# RTX 5080 등 고성능 GPU 환경에서 DLL 충돌 방지 및 최적화 경로 설정
# ==========================================================
try:
    import torch
    if os.name == 'nt':
        torch_lib_path = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.exists(torch_lib_path): os.add_dll_directory(torch_lib_path)
except: pass

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
    def analyze_and_update(self, user_text):
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        tomorrow_str = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y%m%d")
        self.current_info["flight_no"] = "N/A"

        prompt = ChatPromptTemplate.from_template("""
        당신은 항공 노선 분석 전문가입니다. 오늘 날짜는 {today}입니다.
        사용자의 입력에서 정보를 추출하여 JSON으로 반환하세요.

        [추출 규칙]
        1. flight_no: 편명(예: KE77). 항공사 이름만 있고 숫자가 없으면 "N/A".
        2. airline_name: 언급된 항공사의 한글 이름 (예: "진에어").
        3. airline_code: 항공사 IATA 코드. 언급된 항공사나 편명을 보고 추론하세요.
            (예: "대한항공" -> "KE", "진에어" -> "LJ", "티웨이" -> "TW", "에어캐나다" -> "AC")
        4. departure: 출발지 IATA 코드 리스트. 언급 없으면 ["ICN", "GMP"].
        5. destination: 도착지 IATA 코드 리스트. 
            (예: 오키나와 -> ["OKA"], 북경 -> ["PEK", "PKX"], 토론토 -> ["YYZ", "YTZ"])
        6. date: YYYYMMDD 형식. '내일'은 {tomorrow}입니다.

        입력: {user_text} | 이전 데이터: {current_info}
        JSON: {{ "flight_no": "N/A", "airline_name": "N/A", "airline_code": "N/A", "departure": [], "destination": [], "date": "YYYYMMDD" }}
        """)
        
        chain = prompt | self.llm | self.parser
        try:
            res = chain.invoke({"user_text": user_text, "today": today_str, "tomorrow": tomorrow_str, "current_info": self.current_info})
            
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
# [구간 3-1] 노선 타입 판별 (국내/국제)
# 내부 데이터 또는 스크랩된 데이터를 기반으로 판별
# ==========================================================
    def determine_route_type(self, scraped_dep=None, scraped_arr=None):
        dep = scraped_dep if scraped_dep else self.current_info.get("departure", [])
        dest = scraped_arr if scraped_arr else self.current_info.get("destination", [])
        f_no = self.current_info.get("flight_no", "N/A")
        
        prompt = ChatPromptTemplate.from_template("""
        System: 당신은 '국내' 혹은 '국제' 단 두 단어만 사용할 수 있는 항공 노선 판별기입니다.
        
        [출력 규칙 - 절대 준수]
        1. 반드시 한글로만 답변하세요. (No English, No Chinese characters like 国际)
        2. 다른 설명이나 수식어 없이 오직 {{"type": "국내"}} 또는 {{"type": "국제"}} 형식의 JSON만 출력하세요.
        3. '국제'를 '국외'나 'International'로 바꿔 부르지 마세요.

        [데이터 정보]
        - 출발지: {dep}
        - 목적지: {dest}
        - 편명: {f_no}
        
        [판단 가이드]
        - 한 국가 내 공항 간 이동(예: GMP-CJU)인 경우만 '국내'입니다.
        - 그 이외에는 '국제'입니다
        """)
        
        chain = prompt | self.llm | self.parser
        try:
            res = chain.invoke({"dep": dep, "dest": dest, "f_no": f_no})
            return res.get("type", "정보 없음")
        except:
            return "정보 없음"
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

                res = {"status": "N/A", 
                       "route_type": route_type,
                       "dep": {"t": "-", "g": "-", "time": []}, 
                       "arr": {"t": "-", "g": "-", "time": []}}
                
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
# [구간 6] 메인 루프 및 인터페이스
# 사용자 입력을 루프하며 텍스트 분석 -> 검색 -> 상세 조회 프로세스 실행
# ==========================================================
async def main():
    llm = ChatOllama(model='qwen2.5:14b', format="json", temperature=0)
    agent = FlightAgent(llm)
    print("🤖 항공 비서 가동 중...")

    while True:
        u_in = input("\n👤 사용자: ").strip()
        if u_in.lower() in ['exit', '종료']: break
        
        agent.analyze_and_update(u_in)
        if agent.current_info["date"] == "N/A":
            agent.current_info["date"] = datetime.datetime.now().strftime("%Y%m%d")

        # 편명이 즉시 추출된 경우 바로 상세 정보 조회
        if agent.current_info["flight_no"] != "N/A":
            f_no = agent.current_info["flight_no"]
            d = await agent.get_details(f_no)
            if d:
                # 1. 시각적 출력
                print_result(f_no, d, agent.current_info['date'])

                # 2. 데이터 요약 생성 (agent와 d가 모두 존재하는 시점)
                s_time = "N/A"
                if d['dep']['time']:
                    # "Scheduled: 13:10"에서 "13:10"만 추출
                    s_time = d['dep']['time'][0].split(": ")[-1]

                flight_summary = {
                    "is_international": d.get('route_type', '정보 없음'),
                    "airline": agent.current_info.get("airline_name", "N/A"),
                    "dep_airport": agent.current_info.get("departure")[0] if agent.current_info.get("departure") else "N/A",
                    "arr_airport": agent.current_info.get("destination")[0] if agent.current_info.get("destination") else "N/A",
                    "dep_time": s_time,
                    "date": agent.current_info['date']
                }

                # 확인용 출력
                print(f"💡 요약 결과: {flight_summary['airline']} | {flight_summary['is_international']} | {flight_summary['dep_airport']} 출발 | {flight_summary['arr_airport']} 도착 | {flight_summary['date']}  | {flight_summary['dep_time']}시 예정 | ")
            continue

        if not agent.current_info["destination"]:
            print("🤖 목적지를 알 수 없습니다.")
            continue

        # 편명을 모를 경우 노선 검색 후 목록 출력
        print(f"📡 노선 검색 중: {agent.current_info['departure']} -> {agent.current_info['destination']}")
        flights = await agent.search_by_route()
        
        target_code = agent.current_info.get("airline_code", "N/A")
        if target_code != "N/A":
            # 편명(no)이 해당 항공사 코드(예: LJ)로 시작하는 것만 남김
            filtered_flights = [f for f in flights if f['no'].startswith(target_code)]
            
            # 만약 진에어(LJ)를 검색했는데 결과가 있다면 필터링 적용
            if filtered_flights:
                flights = filtered_flights
                print(f"✨ 요청하신 '{agent.current_info.get('airline_name', target_code)}' 항공편만 모아봤습니다.")
        
        if not flights:
            print("❌ 검색 결과가 없습니다.")
        elif len(flights) == 1:
            # 진에어 등으로 필터링되어 1개만 남으면 바로 상세 정보 출력
            f = flights[0]
            print(f"✅ [{f['no']}] 항공편 발견. 상세 조회 시작...")
            d = await agent.get_details(f['no'])
            if d: print_result(f['no'], d,agent.current_info['date'])
        else:
            # 결과가 여러 개일 때: 항공사 이름을 포함하여 출력
            print(f"\n✅ {len(flights)}개의 항공편을 찾았습니다.")
            llm_airlines = agent.current_info.get("airline_info", {})

            for i, f in enumerate(flights):
                # 항공편 번호에서 코드 추출 (예: LJ341 -> LJ)
                f_code_match = re.match(r'^([A-Z0-9]{2,3})', f['no'])
                f_code = f_code_match.group(1) if f_code_match else ""
                
                # LLM 분석 데이터에서 항공사 이름 매칭
                air_name = llm_airlines.get(f_code, llm_airlines.get(f_code[:2], ""))
                display_name = f" | {air_name}" if air_name else ""
                
                print(f"[{i+1}] {f['no'].ljust(8)} | {f['dep']} -> {f['arr']}{display_name}")
            
            # 사용자의 선택 받기
            sel = input("\n💡 상세 정보를 확인할 번호를 입력하세요 (n: 취소): ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(flights):
                target = flights[int(sel)-1]
                d = await agent.get_details(target['no'])
                if d: 
                    print_result(target['no'], d,agent.current_info['date'])


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

if __name__ == "__main__":
    asyncio.run(main())

# print(f"💡 요약 결과: {flight_summary['airline']} 이용, {flight_summary['dep_time']} 출발")