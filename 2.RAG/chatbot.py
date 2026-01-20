import os
import re
import datetime
import pandas as pd
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

# 1. 모델 설정
def get_llm():
    return ChatOllama(model='exaone3.5:7.8b', format="json", temperature=0)

class FlightAgent:
    def __init__(self, llm, api_keys_str):
        self.llm = llm
        self.api_keys = [k.strip() for k in api_keys_str.split(',')]
        self.current_key_index = 0
        self.parser = JsonOutputParser()
        self.db = pd.read_csv(r".\0.Data\flight_data_use.csv",low_memory=False)

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
    def extract_potential_flight_number(self, user_text):
        """사용자 문장에서 항공 정보(편명, 날짜, 장소 등) 추출"""
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        prompt = ChatPromptTemplate.from_template("""
        오늘 날짜는 {today_str}입니다. 사용자의 입력 문장에서 항공 정보를 추출하세요.
        
        [필수 변환 규칙]
        1. 사용자의 최신 입력 문장에서 언급된 정보만 추출하세요.
        2. **지명 정규화**: 한자음 도시명은 반드시 표준 외래어 표기로 바꿉니다.
           - 북경 -> 베이징 / 상해 -> 상하이 / 동경 -> 도쿄 / 대판 -> 오사카
           - '공항', '도' 등 불필요한 접미사 제거 (인천공항 -> 인천, 제주도 -> 제주)
        3. **시간**: 24시간제 4자리 숫자로 변환 (오전 9시 -> 0900, 오후 4시 -> 1600)
        4. **날짜**: 명시적 언급 없으면 반드시 'N/A'.
        5. **편명 정규화**: 항공사 코드 뒤의 숫자가 "3자리 미만"인 경우 앞의 0을 붙여서 통일하세요. 숫자가 3자리 이상인 경우 수정하지 마세요
            (예: AC62 -> AC062, KE1 -> KE001, KE9907 -> KE9907) 가능하면 숫자를 추출하여 표준화된 형식을 만드세요.
        6. **편명(flight_no)**: 사용자가 "KE9077"이라고 말하면 반드시 "KE9077"을 추출하세요. 임의로 다른 편명(예: KE062)으로 바꾸거나 추측하지 마세요
        7. **항공사명 통일**: 반드시 **한국어 풀네임**으로 변환하세요. (Korean Air -> 대한항공, Air Canada -> 에어캐나다)
        [추가 지침]
        8. **편명 조합**: 항공사가 이미 파악되었고(current_data 참고) 사용자가 숫자만 입력한 경우, 해당 항공사의 코드와 숫자를 결합하여 'flight_no'를 생성하세요.
            단 숫자가 주어지지 않은 경우 숫자 정보가 추가 입력될 때 까지 flight_no에 입력하지 마세요
        - 예: 항공사가 '아시아나항공'인 상태에서 사용자가 "501" 입력 -> "OZ501" 추출
        - 예: 항공사가 '대한항공'인 상태에서 사용자가 "73" 입력 -> "KE073" (3자리 정규화 적용)

        반드시 다음 JSON 형식을 지키세요:
        {{
            "flight_no": "항공기 편명 또는 N/A",
            "airlines": "항공사 또는 N/A",
            "destination": "도시 이름 (표준 외래어 표기)",
            "departure": "출발 도시",
            "date": "YYYYMMDD 또는 N/A",
            "time": "HHMM 또는 N/A"
        }}

        입력 문장: {user_text}
        """)
        chain = prompt | self.llm | self.parser
        return chain.invoke({"user_text": user_text, "today_str": today_str})

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
                print(f"\n✨ 항공편을 찾았습니다! [{row['편명']}]")
                print(f"상세정보: {row['항공사']} | {row['계획시간']} 출발 | {row['도착지']} 도착")
                print("-" * 50)
               
                final_check = input("다른 항공편을 추가로 확인하시겠습니까? (네/아니오): ").strip()
                if final_check in ['네', '예', 'y', 'Y']:
                    break # 내부 루프 탈출 -> 처음 질문(바깥 while문)으로 이동
                else:
                    print("이용해 주셔서 감사합니다. 프로그램을 종료합니다.")
                    exit() # 프로그램 전체 종료

            # CASE 2: 결과가 없는 경우
            elif unique_count == 0:
                print("\n" + "!"*30)
                print("❌ 일치하는 항공편을 찾을 수 없습니다.")
                print("현재 파악된 정보:", {k: v for k, v in current_info.items() if v != 'N/A'})
                print("!"*30)
               
                retry_answer = input("\n💡 수정할 정보를 말씀해 주세요 (다시입력/그만): ").strip()
                if retry_answer == '그만': break
               
                new_correction = agent.extract_potential_flight_number(retry_answer)
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

                new_data = agent.extract_potential_flight_number(answer)
                for k, v in new_data.items():
                    if v != 'N/A': current_info[k] = v