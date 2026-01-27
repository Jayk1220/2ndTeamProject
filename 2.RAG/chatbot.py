import os
import sys
import ctypes
import requests
# [중요] WinError 1114 해결을 위한 DLL 로드 선점 패치
try:
    import torch
    # RTX 5080 등 최신 GPU 환경에서 DLL 충돌 방지
    if os.name == 'nt':
        torch_lib_path = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.exists(torch_lib_path):
            os.add_dll_directory(torch_lib_path)
except Exception:
    pass
import re
import datetime
import pandas as pd
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 1. 모델 설정
def get_llm():
    return ChatOllama(model='qwen2.5:14b', format="json", temperature=0)

class FlightAgent:
    def __init__(self, llm, api_keys_str):
        self.llm = llm
        self.api_keys = [k.strip() for k in api_keys_str.split(',')]
        self.current_key_index = 0
        self.parser = JsonOutputParser()
        self.db = pd.read_csv(r".\0.Data\flight_data.csv",low_memory=False)

    # ------ [API 키 관리] ------
    def get_api_key(self):
        return self.api_keys[self.current_key_index]

    def other_api_key(self):
        if self.current_key_index < len(self.api_keys) - 1:
            self.current_key_index += 1
            return True

        else:
            print("내일 다시 시도하십시오")
            return False

    # ------ [정보 추출 및 분석] ------
    def extract_potential_flight_number(self, user_text, current_data=None):
        """사용자 문장에서 항공 정보(편명, 날짜, 장소 등) 추출"""
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            
        prompt = ChatPromptTemplate.from_template("""
        오늘 날짜는 {today_str}입니다. 사용자의 최신 입력 문장에서 항공 정보를 추출하여 JSON으로 변환하세요.

        ### [데이터 추출 및 변환 규칙]

        1. **최신 정보 우선**: 사용자의 마지막 입력 문장에서 명시된 정보만 추출하되, 'N/A'로 반환될 항목은 이전 문맥(current_data)을 참고하여 보완할 수 있습니다.
        2. **정보 덮어쓰기**: 새로운 입력에 포함된 정보는 이전 문맥(current_data)보다 무조건 우선합니다.
        2. **지명 정규화 (필수)**: 
        - 한자음 도시명은 표준 외래어로 변환 (북경->베이징, 상해->상하이, 동경->도쿄, 대판->오사카).
        - 불필요한 접미사 제거 (인천공항->인천, 제주도->제주).
        3. **시간 형식**: 24시간제 4자리 숫자로 통일 (오전 9시->0900, 오후 4시->1600, 11시쯤->1100).
        4. **날짜 형식**: 'YYYYMMDD' 형식으로 변환. 명시적 언급이 없으면 'N/A'.
        5. **항공사명**: 반드시 한국어 풀네임으로 통일 (Korean Air->대한항공, Air Canada->에어캐나다).

        ### [편명(flight_no) 생성 특별 지침]

            1. **숫자 그대로 사용 (우선순위 1)**:
            - 사용자가 입력한 숫자가 **3자리 이상**인 경우(예: 901, 8901), 앞에 '0'을 절대 붙이지 말고 그대로 사용하세요.
            - 예: 901 -> 901 / 8901 -> 8901

            2. **항공사 코드 결합 필수**:
            - `current_data`에 항공사가 있다면 해당 코드를 반드시 숫자 앞에 붙이세요.
            - 대한항공(Korean Air) -> **KE** / 아시아나항공(Asiana Airlines) -> **OZ**
            - 예: 대한항공 상태에서 "901" 입력 -> **KE901** (반드시 이 형식이어야 함)

            3. **부족한 자릿수 채우기 (1~2자리일 때만)**:
            - 오직 숫자가 **1자리 혹은 2자리**일 때만 3자리를 맞추기 위해 0을 붙입니다.
            - 예: KE + "7" -> KE007 / KE + "73" -> KE073

            4. **추측 금지**:
            - 사용자가 숫자를 말하지 않았다면 `flight_no`는 반드시 "N/A"여야 합니다. (KE001, KE009 등 임의 생성 금지)
                                                  

        ### [출력 형식]
        반드시 아래 JSON 구조를 지키고, 정보가 없으면 "N/A"를 입력하세요.
        {{
            "flight_no": "항공편명 (예: KE001)",
            "airlines": "항공사 풀네임",
            "destination": "도착 도시명",
            "departure": "출발 도시명",
            "date": "YYYYMMDD",
            "time": "HHMM",
            "type": "International" 또는 "Domestic" (한국 내 노선은 Domestic, 그 외 International)
        }}
        이전 파악 정보: {current_data}
        입력 문장: {user_text}
        """)
        
        chain = prompt | self.llm | self.parser
        return chain.invoke({"user_text": user_text, "today_str": today_str,"current_data": current_data})

    def to_minutes(self, hhmm):
        """HHMM 형식을 분 단위 숫자로 변환"""
        if pd.isna(hhmm) or hhmm == 'N/A' or str(hhmm).strip() == '':
            return None
        try:
            raw_val = re.sub(r'[^0-9]', '', str(hhmm))
            if not raw_val: return None
            s_hhmm = str(int(float(hhmm))).zfill(4)
            hh, mm = int(s_hhmm[:2]), int(s_hhmm[2:])
            if hh >= 24: hh %= 24
            return hh * 60 + mm

        except:
            return None

    # ------ [데이터 필터링] ------
    def csv_filter(self, data):
        """추출된 정보를 바탕으로 CSV 데이터 필터링"""
        if self.db.empty: return self.db
        today_val = int(datetime.datetime.now().strftime("%Y%m%d"))

        # 컬럼명 정의
        COL = {
            'FLIGHT_NO': '편명',
            'AIRLINE_K': '항공사',
            'DEST_K': '도착지',
            'DEPA_K': '출발지',
            'DATE': '일자',
            'TIME': '계획시간'
        }
        result = self.db.copy()

        # 1. 편명 필터링
        f_no = data.get('flight_no')
        if f_no and f_no != 'N/A':
            result = result[result[COL['FLIGHT_NO']] == f_no]

        # 2. 날짜 필터링 (과거 날짜인 경우만 검색 제한)
        f_date = data.get('date')
        if f_date and f_date != 'N/A':
            if int(f_date) < today_val:
                result = result[result[COL['DATE']].astype(str) == str(f_date)]

        # 3. 출발지 필터링
        f_depa = data.get('departure')
        if f_depa and f_depa != 'N/A':
            result = result[
                (result[COL['DEPA_K']].str.contains(f_depa, na=False, case=False))
            ]

        # 4. 도착지 필터링
        f_dest = data.get('destination')
        if f_dest and f_dest != 'N/A':
            result = result[
                (result[COL['DEST_K']].str.contains(f_dest, na=False, case=False))
            ]

        # 5. 항공사 필터링
        f_airline = data.get('airlines')
        if f_airline and f_airline != 'N/A':
            result = result[
                (result[COL['AIRLINE_K']].str.contains(f_airline, na=False, case=False))
            ]

        # 6. 시간 필터링 (사용자 시간 기준 ±60분)
        f_time = data.get('time')
        if f_time and f_time != 'N/A' and not result.empty:
            user_min = self.to_minutes(f_time)
            if user_min is not None:
                db_min = result[COL['TIME']].apply(self.to_minutes)
                low, high = max(0, user_min - 60), min(1439, user_min + 60)
                result = result[(db_min >= low) & (db_min <= high)]
       
        return result

    # ------ [LLM 질문 생성] ------
    def generate_llm_question(self, final_df, current_data):
        """후보군이 많을 때 사용자에게 던질 추가 질문 생성"""
        sample_list = final_df[['항공사', '편명', '계획시간', '도착지']].head(10).to_dict(orient='records')
       
        prompt = ChatPromptTemplate.from_template("""
        당신은 사용자가 예매한 항공편을 찾아주는 도우미입니다. 후보가 여러 개이므로, 데이터를 좁힐 수 있는 질문을 하세요.

        [후보 리스트]: {sample_list}
        [현재 파악 정보]: {current_data}

        지침:
        1. **데이터 기반**: 시간대 차이나 항공사 차이를 언급하며 질문하세요.
        2. **효율성**: 범위를 가장 빨리 좁힐 수 있는 요소를 먼저 물어보세요.
        3. **형식**: 반드시 JSON {{"question": "질문 내용"}} 형식으로 답변하세요.
        4. **간소화**: 동일 편명 동일 항공사, 동일 목적지의 정보는 중복 없이 하나만 출력
        """)
       
       
        chain = prompt | self.llm | self.parser
        response = chain.invoke({"sample_list": sample_list, "current_data": current_data})
        return response.get('question', "더 자세한 정보를 말씀해 주시겠어요?")
    
    # ==========================================
    # 확정된 편명 조회
    # ==========================================
    def fetch_realtime_status(self, flight_no):
            """확정된 편명을 사용하여 외부 API에서 실시간 정보를 가져옴"""
            url = "http://api.aviationstack.com/v1/flights"
            params = {
                'access_key': self.get_api_key(),
                'flight_iata': flight_no
            }
            
            try:
                response = requests.get(url, params=params)
                res_data = response.json()
                
                if 'data' in res_data and len(res_data['data']) > 0:
                    # 가장 최신 운항 정보 추출
                    flight_info = res_data['data'][0]
                    status = flight_info.get('flight_status', 'N/A')
                    dep_gate = flight_info.get('departure', {}).get('gate', '미정')
                    arr_time = flight_info.get('arrival', {}).get('estimated', '정보없음')
                    
                    return {
                        "status": status,
                        "gate": dep_gate,
                        "estimated_arrival": arr_time
                    }
                return None
            except Exception as e:
                print(f"API 호출 중 오류 발생: {e}")
                return None

# ==========================================
# 메인 실행 루프
# ==========================================
if __name__ == "__main__":
    llm = get_llm()
    agent = FlightAgent(llm, "DUMMY_KEY_1")

    print("안녕하세요! 무엇을 도와드릴까요? (종료하시려면 '그만' 또는 'exit' 입력)")

    while True:
        # 1. 매 검색 시작 시 질문 받기
        initial_text = input("\n질문: ").strip()
       
        if initial_text in ['그만', 'exit', '종료']:
            print("이용해 주셔서 감사합니다. 프로그램을 종료합니다.")
            break

        # 2. 정보 추출
        current_info = agent.extract_potential_flight_number(initial_text)

        # 3. 상세 검색 루프 (정보가 부족할 때 추가 질문용)
        while True:
            filtered_df = agent.csv_filter(current_info)
            count = len(filtered_df)
            display_df = filtered_df.drop_duplicates(subset=['편명']).sort_values(by='계획시간')
            unique_count = len(display_df)

            # CASE 1: 결과가 하나로 확정된 경우
            if unique_count == 1:
                row = filtered_df.iloc[0]
                confirmed_flight = row['편명'] # 확정된 편명 추출
                
                print(f"\n✨ 항공편을 찾았습니다! [{confirmed_flight}]")
                print(f"기본정보: {row['항공사']} | {int(row['계획시간'])} 출발 | {row['도착지']} 도착")
                
                # --- [실시간 정보 조회 추가] ---
                print(f"📡 {confirmed_flight}편의 실시간 상태를 조회 중입니다...")
                realtime = agent.fetch_realtime_status(confirmed_flight)
                
                if realtime:
                    print(f"📍 실시간 상태: {realtime['status']} (게이트: {realtime['gate']})")
                    print(f"⏰ 예상 도착 시간: {realtime['estimated_arrival']}")
                else:
                    print("ℹ️ 실시간 운항 정보가 아직 업데이트되지 않았습니다.")
                break

            # CASE 2: 결과가 없는 경우
            elif unique_count == 0:
                f_no = current_info.get('flight_no')
                
                # [추가] 편명이 있다면 API로 실시간 조회를 먼저 시도
                if f_no and f_no != 'N/A':
                    print(f"\n🔍 DB에는 없지만, 입력하신 편명 {f_no}를 실시간으로 조회해 봅니다...")
                    realtime = agent.fetch_realtime_status(f_no)
                    
                    if realtime:
                        print(f"✨ 실시간 데이터에서 찾았습니다! [{f_no}]")
                        print(f"📍 상태: {realtime['status']} | 게이트: {realtime['gate']}")
                        print(f"⏰ 예상 도착: {realtime['estimated_arrival']}")
                        print("-" * 30)
                        break  # 정보를 찾았으므로 루프 탈출
                
                # API로도 정보를 찾지 못한 경우 기존 '찾을 수 없음' 프로세스 진행
                print("\n" + "!"*30)
                print("❌ 일치하는 항공편을 찾을 수 없습니다.")
                print("현재 파악된 정보:", {k: v for k, v in current_info.items() if v != 'N/A'})
                print("!"*30)
                
                retry_answer = input("\n💡 수정하거나 추가할 정보를 말씀해 주세요 (그만/직접입력): ").strip()
                if retry_answer == '그만': break
                
                new_correction = agent.extract_potential_flight_number(retry_answer, current_info)
                for k, v in new_correction.items():
                    if v != 'N/A': current_info[k] = v
                continue

            # CASE 3: 후보가 여러 개인 경우 (중복 제거 로직 포함)
            else:
                # 여기서 subset=['편명'] 으로 수정하면 시간이 달라도 편명이 같으면 하나만 나옵니다.
                

                print(f"\n🔍 검색 결과, {unique_count}개의 고유 항공편이 확인됩니다.")
                print("-" * 50)
                print(display_df[['편명', '항공사', '계획시간', '도착지']].to_string(index=False))
                print("-" * 50)

                smart_q = agent.generate_llm_question(display_df, current_info)
                print(f"🤖 챗봇: {smart_q}")

                answer = input("답변 (그만): ")
                if answer == '그만': break

                new_data = agent.extract_potential_flight_number(answer, current_info)
                updated = False
                for k, v in new_data.items():
                    if v != 'N/A': current_info[k] = v
                    updated = True
                
                if updated:
                    print(f"💡 정보가 업데이트되었습니다: { {k:v for k,v in current_info.items()} }")
                    
                    continue  # 이 구문이 실행되면 while True의 시작점으로 가서 csv_filter를 다시 태웁니다.
                else:
                    print("🤖 챗봇: 추가적인 정보를 파악하지 못했습니다. 조금 더 구체적으로 말씀해 주시겠어요?")

