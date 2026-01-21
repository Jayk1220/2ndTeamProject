import json
from serpapi.google_search import GoogleSearch
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

# 1. 모델 설정 (사용자님의 RTX 5080 환경 최적화)
def get_llm():
    return ChatOllama(model='qwen2.5:14b', temperature=0)

# 2. 구글 검색 함수
def get_google_flight_details(flight_no, api_key):
    params = {
        "engine": "google",
        "q": f"{flight_no} flight status terminal gate",
        "hl": "ko", 
        "gl": "kr",
        "api_key": api_key
    }
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
        
        if "answer_box" in results:
            print("✅ 구글 항공 정보 카드를 찾았습니다!")
            return results["answer_box"]
        elif "knowledge_graph" in results:
            print("✅ 지식 그래프 정보를 찾았습니다!")
            return results["knowledge_graph"]
        else:
            print("❌ 상세 카드가 없어 일반 검색 결과(Snippet)를 사용합니다.")
            return results.get("organic_results", [{}])[0]
    except Exception as e:
        return {"error": str(e)}

# 3. LLM 요약 함수
def parse_flight_details_with_llm(llm, search_result):
    # 1. SerpApi 결과에서 중요할 수 있는 모든 필드를 JSON 텍스트로 변환
    # (결과가 너무 길면 LLM이 힘들어하므로 answer_box나 knowledge_graph 위주로 추출)
    raw_json_text = json.dumps(search_result, indent=2, ensure_ascii=False)
    
    if not search_result or search_result == {}:
        return "현재 검색 결과에서 실시간 운항 정보를 찾을 수 없습니다."

    # 2. 프롬프트 강화: LLM에게 '데이터 분석가' 역할을 부여
    prompt = ChatPromptTemplate.from_template("""
    당신은 전 세계 항공 운항 데이터를 분석하는 전문가입니다. 
    아래 제공된 [검색 결과 데이터]는 구글 검색 API(SerpApi)로부터 가져온 로우 데이터(Raw Data)입니다.
    
    데이터 구조가 복잡하더라도 당신의 지능을 활용해 다음 정보를 찾아내어 사용자에게 브리핑하세요.
    
    [검색 결과 데이터]
    {json_data}

    [미션]
    1. 데이터 내에서 항공편 상태(On Time, Delayed, Arrived 등)를 찾으세요.
    2. 출발/도착 공항의 터미널(Terminal)과 게이트(Gate) 번호를 찾으세요.
    3. 출발/도착 예정 시간과 실제 시간을 찾으세요.
    4. 위 정보를 종합하여 "현재 항공편은 ~상태이며, ~터미널 ~게이트에서 ~시에 출발(또는 도착) 예정입니다"라고 친절하게 답변하세요.
    
    [주의사항]
    - 데이터가 영어로 되어 있어도 반드시 한국어로 번역해서 답변하세요.
    - 만약 데이터에 게이트 번호가 없다면 "게이트 정보는 아직 업데이트되지 않았습니다"라고 하세요.
    - 절대로 "데이터가 부족하다"거나 "JSON을 달라"는 말을 하지 마세요. 어떻게든 데이터 안의 텍스트를 읽고 답변하세요.
    """)

    # 3. 실행
    chain = prompt | llm
    response = chain.invoke({"json_data": raw_json_text})
    return response.content
def get_flight_status_briefing(llm, search_result, flight_no):
    """
    운항 전 항공편의 출발 예정 시간 및 지연 여부를 집중 분석합니다.
    """
    # 데이터 전체를 텍스트화 (Qwen 14b는 이 정도는 우습게 처리합니다)
    raw_data = json.dumps(search_result, indent=2, ensure_ascii=False)

    prompt = ChatPromptTemplate.from_template("""
    당신은 항공 운항 통제 센터의 브리핑 요원입니다. 
    아래 [운항 데이터]를 분석하여 {flight_no} 항공편에 대해 답변하세요.

    [분석 목표]
    1. 이 비행기가 이미 출발했는가, 아니면 대기 중인가?
    2. 출발 전이라면, '계획된 시간'은 언제이고 '실제 출발 예정 시간'은 언제인가?
    3. 원래 시간보다 지연(Delay)되었는가? 그렇다면 얼마나 지연되었는가?

    [운항 데이터]
    {json_data}

    [답변 양식]
    - 현재 상태: (예: 출발 대기 중 / 지연 중 / 정시 운항 예정)
    - 계획 시간: (예: 10:20 AM)
    - 예상 출발: (예: 11:00 AM - 약 40분 지연)
    - 브리핑: (상황을 종합하여 한 문장으로 친절하게 설명)

    [주의]
    - 터미널, 게이트 정보는 생략하세요.
    - 시간 정보가 명확하지 않다면 "현재 실시간 스케줄 확인 중입니다"라고 답하세요.
    """)

    chain = prompt | llm
    response = chain.invoke({
        "flight_no": flight_no,
        "json_data": raw_data
    })
    return response.content
# --- 실제 실행부 ---
if __name__ == "__main__":
    MY_SERPAPI_KEY = ""
    test_flight = "KE023"
    
    llm = get_llm() # LLM 로드
    
    # 1단계: 검색 (q에서 terminal gate를 빼면 status 카드가 더 잘 뜹니다)
    search_res = get_google_flight_details(test_flight, MY_SERPAPI_KEY)
    
    # 2단계: 결과 요약 (지연/스케줄 특화 함수 호출)
    if "error" not in search_res:
        # 사용자님이 원하시는 '지연 여부/예정 시간' 중심 브리핑 호출
        final_answer = get_flight_status_briefing(llm, search_res, test_flight)
        
        print("\n" + "="*50)
        print(f"📡 {test_flight} 실시간 운항 스케줄 브리핑")
        print("="*50)
        print(final_answer)
        print("="*50)
    else:
        print(f"오류 발생: {search_res['error']}")