# 1. 모델 설정
import os
import re
import datetime
from langchain_ollama import ChatOllama
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

def get_llm():
    return ChatOllama(model='exaone3.5:7.8b', format="json", temperature=0)
llm = get_llm()

class flightAgent:
    def __init__(self, llm, api_keys_str):
        self.llm = llm
        self.api_keys = [k.strip() for k in api_keys_str.split(',')]
        self.current_key_index = 0
        self.parser =JsonOutputParser()

    def get_api_key(self): #현재 순서 API 키
        return self.api_keys[self.current_key_index]
    
    def other_api_key(self): #사용 중이던 API 막혔을 때 다른 API 키
        if self.current_key_index < len(self.api_keys) - 1:
            self.current_key_index += 1
            return True
        else:
            print("내일 다시 시도하십시오")
            return False
    
#  ------ [편명 유추] ------
    def extract_potential_flight_number(self, user_text):
        """문장 속에서 항공기편명/날짜 추출"""
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        prompt = ChatPromptTemplate.from_template("""
        오늘 날짜는 {today_str}입니다.
        사용자의 입력 문장에서 '항공기 편명'과 '날짜'만 찾아내세요
        
        항공기 편명 규칙: [IATA 지정 2자리 영문/숫자] + 3-4자리 숫자. 예시: KE703, OZ102 , 7C2103
        
        날짜 추출 규칙 : 
        - 연도가 언급되지 않았다면, 오늘 날짜({today_str})를 기준으로 현재 연도를, 현재 연도의 일자가 과거일 경우 그 다음해의 연도를 입력하세요
        - 월 이름이 명시된 경우 반드시 해당 월로 변환하세요. 예시: december => 12월
        - 정보가 없다면 해당하는 항목에 'N/A'라고 답하세요 .
        -YYYY-MM-DD 형식으로 변환 (예시: 오늘 -> 2026-02-01, 내일 -> 2026-02-02)
        
        입력 문장: {user_text}
        다음 JSON 형식을 지키시오: {{'flight_no':'항공기 편명 또는 N/A',
                                    'date': 'YYYY-MM-DD 또는 N/A'}}
        """)
        
        chain = prompt | self.llm | self.parser
        response = chain.invoke({"user_text": user_text, "today_str": today_str})
        return response


#  ------ [항공편 정보 가져오기] ------


#----Test

# 실험을 위한 객체 생성 (실제 API 키가 없어도 추출 실험은 가능합니다)
agent = flightAgent(llm, "DUMMY_KEY_1, DUMMY_KEY_2")

test_inputs = [
    "I'll takeke073 tomorrow",
    '나 대한항공 타고 토론토가',
    '나 내일 7C 2103타',
    "I'm gonna take oz102 on december 2"
]

print("===  편명/날짜 추출 실험 결과 ===")
for text in test_inputs:
    result = agent.extract_potential_flight_number(text)
    print(f"입력: {text}")
    print(f"추출: {result}") # 결과: {'flight_no': '...', 'date': '...'}
    print("-" * 30)