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
            "airline": "N/A"
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
        1. flight_no: 편명(예: KE77). 없으면 "N/A".
        2. departure: 출발 공항 리스트. 언급 없으면 [].
        3. destination: 도착지 공항 리스트. 도시 이름이 나오면 해당 도시의 모든 주요 IATA 코드를 포함하세요. 
            (예: 북경 -> ["PEK", "PKX"], 토론토 -> ["YYZ", "YTZ", "YTO"], 서울 -> ["ICN", "GMP"])
        4. date: YYYYMMDD 형식. '내일'은 {tomorrow}입니다.

        입력: {user_text} | 이전 데이터: {current_info}
        JSON: {{ "flight_no": "N/A", "departure": [], "destination": [], "date": "YYYYMMDD" }}
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
        except Exception as e:
            print(f"⚠️ 분석 오류: {e}")

    # ==========================================================
    # [구간 4] 노선 기반 항공편 검색 (Scraping)
    # 특정 구간(출발-도착)의 모든 운항 정보를 조회하여 선택 리스트 생성
    # ==========================================================
    async def search_by_route(self):
        info = self.current_info
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
                    route = f"{dep}/{arr}"
                    url = f"https://www.flightstats.com/v2/flight-tracker/route/{route}?year={y}&month={m}&date={d}"
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        soup = BeautifulSoup(await page.content(), 'html.parser')
                        links = soup.select('a[href*="/v2/flight-tracker/"]')
                        for link in links:
                            h2s = [h.get_text(strip=True) for h in link.find_all('h2')]
                            if len(h2s) >= 3:
                                f_no = h2s[0].replace(" ", "")
                                match = re.match(r'([A-Z]+)(\d+)', f_no)
                                air, num = match.groups() if match else ("N/A", f_no)
                                all_flights[f_no] = {
                                    "no": f_no, "dep": dep, "arr": arr,
                                    "url": f"https://www.flightstats.com/v2/flight-details/{air}/{num}?year={y}&month={m}&date={d}"
                                }
                    except: continue
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

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            # 불필요한 리소스 차단으로 로딩 속도 최적화
            await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2}", lambda route: route.abort())
            
            try:
                # 페이지 구조가 로드될 때까지만 대기 (타임아웃 방지)
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_selector('div.flight-ticket', timeout=15000)
                
                soup = BeautifulSoup(await page.content(), 'html.parser')
                res = {"status": "N/A", "dep": {"t": "-", "g": "-", "time": []}, "arr": {"t": "-", "g": "-", "time": []}}
                
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
    print("🤖 항공 비서 가동 중... ('나 내일 북경가' 또는 'KE77' 입력)")

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
            if d: print_result(f_no, d)
            continue

        if not agent.current_info["destination"]:
            print("🤖 목적지를 알 수 없습니다.")
            continue

        # 편명을 모를 경우 노선 검색 후 목록 출력
        print(f"📡 노선 검색 중: {agent.current_info['departure']} -> {agent.current_info['destination']}")
        flights = await agent.search_by_route()
        
        if not flights:
            print("❌ 검색 결과가 없습니다.")
        elif len(flights) == 1:
            f = flights[0]
            print(f"✅ 1개의 항공편 [{f['no']} | {f['dep']} -> {f['arr']}] 발견. 상세 조회 시작...")
            d = await agent.get_details(f['no'])
            if d: print_result(f['no'], d)
        else:
            print(f"\n✅ {len(flights)}개의 항공편을 찾았습니다.")
            for i, f in enumerate(flights):
                print(f"[{i+1}] {f['no'].ljust(8)} | {f['dep']} -> {f['arr']}")
            
            sel = input("\n💡 번호 입력 (n: 취소): ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(flights):
                target = flights[int(sel)-1]
                d = await agent.get_details(target['no'])
                if d: print_result(target['no'], d)

# ==========================================================
# [구간 7] 결과 출력 포맷팅
# 수집된 상세 정보를 깔끔한 표 형태로 출력
# ==========================================================
def print_result(no, d):
    print("\n" + "="*50)
    print(f"✈️  {no} 상세 정보 ({d['status']})")
    print("="*50)
    for k in ["dep", "arr"]:
        label = "🛫 출발" if k == "dep" else "🛬 도착"
        info = d[k]
        print(f"{label}: (Terminal: {info['t']} / Gate: {info['g']})")
        for t in info['time']: print(f"  - {t}")
        print("-" * 50)
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())