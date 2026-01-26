import asyncio
import re
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

async def get_flight_details(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            # 대기 시간을 늘리고 페이지 로드 완료를 확실히 보장
            await page.goto(url, wait_until="networkidle", timeout=60000)
            await page.wait_for_selector('div.flight-ticket', timeout=20000)
            
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')

            details = {
                "status": "N/A",
                "departure": {"airport": "-", "terminal": "-", "gate": "-", "times": []},
                "arrival": {"airport": "-", "terminal": "-", "gate": "-", "times": []}
            }

            # 1. 상태 정보
            status_div = soup.select_one('div[class*="statusBlock"]')
            if status_div:
                details["status"] = status_div.get_text(" ", strip=True)

            # 2. 출도착 정보 파싱 (인덴트 및 선택자 수정)
            tickets = soup.select('div.flight-ticket')
            
            # 검색된 티켓이 없으면 여기서 에러를 내지 않고 종료되므로 체크 필요
            if not tickets:
                print("⚠️ 티켓 요소를 찾지 못했습니다.")
                return None

            for i, ticket in enumerate(tickets[:2]):
                key = "departure" if i == 0 else "arrival"
                
                # 공항 코드 (정확한 클래스 조준)
                airport_code = ticket.select_one('h2[class*="airportCodeTitle"]')
                if airport_code:
                    details[key]["airport"] = airport_code.get_text(strip=True)

                # 터미널 & 게이트 (이미지 기반 h4.detail 직접 추출)
                # 클래스 선택 시 공백 포함 가능성을 고려해 [class*="..."] 사용
                t_block = ticket.select_one('div[class*="terminalBlock"] h4')
                g_block = ticket.select_one('div[class*="gateBlock"] h4')
                
                if t_block: details[key]["terminal"] = t_block.get_text(strip=True)
                if g_block: 
                    g_text = g_block.get_text(strip=True)
                    # "TIMES" 글자가 들어오는 것을 코드 수준에서 방어
                    details[key]["gate"] = g_text if "TIMES" not in g_text.upper() else "-"

                # 3. 시간 정보 (상위 2개)
                time_blocks = ticket.select('div[class*="timeBlock"]')
                for block in time_blocks[:2]:
                    lbl = block.select_one('p[class*="title"]')
                    val = block.select_one('h4')
                    if lbl and val:
                        details[key]["times"].append(f"{lbl.get_text(strip=True)}: {val.get_text(strip=True)}")

            return details

        except Exception as e:
            print(f"❌ 파싱 중 오류 발생: {e}")
            return None
        finally:
            await browser.close()

if __name__ == "__main__":
    target_url = "https://www.flightstats.com/v2/flight-details/KE/77?year=2026&month=1&date=26"
    result = asyncio.run(get_flight_details(target_url))
    
    if result:
        print("\n" + "="*50)
        print(f"✈️  운항 상세 정보")
        print("="*50)
        print(f"상태: {result['status']}") # Delayed by 16m | Departed 출력 유도
        print("-" * 50)
        for section in ["departure", "arrival"]:
            title = "[출발]" if section == "departure" else "[도착]"
            data = result[section]
            print(f"{title} {data['airport']}")
            print(f"터미널 / 게이트: {data['terminal']} / {data['gate']}")
            print("시간 정보:")
            for t in data['times']:
                print(f"  - {t}") # Scheduled: 10:05 KST 등 리스트 출력
            print("-" * 50)
        print("="*50)