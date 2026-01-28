from functools import lru_cache
from langchain_ollama import ChatOllama
from .flight_ctx import find_flight_context
from .rag import retrieve_context

@lru_cache(maxsize=1)
def _llm():
    # 너의 올라마 모델명에 맞춰 변경
    return ChatOllama(
        model="qwen2.5:14b",
        temperature=0,
    )

def answer_question(question: str, airport: str | None = None) -> str:
    question = (question or "").strip()
    if not question:
        return ""
    
    flight_ctx = find_flight_context(question, airport_code=airport)
    context = retrieve_context(question, k=3)

    prompt = f"""
당신은 항공편 지연/결항 보상 안내 챗봇입니다.
아래 [관련 문서] 내용 안에서 근거를 찾아, 한국어로 간단명료하게 답하세요.
불확실하면 "확인이 필요합니다"라고 말하세요.
사용자 항공사가 명확하지 않으면, 검색된 문서 중 대한항공 국내 약관을 우선으로 답하세요.

[실시간 항공편 정보]
{flight_ctx if flight_ctx else ""}

[관련 문서]
{context if context else "(관련 문서 없음)"}

[질문]
{question}

[답변]
""".strip()

    resp = _llm().invoke(prompt)
    # resp는 AIMessage일 수 있으니 content로 안전하게 뽑기
    return getattr(resp, "content", str(resp)).strip()
