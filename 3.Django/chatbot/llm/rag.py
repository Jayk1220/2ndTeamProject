from pathlib import Path
from functools import lru_cache

from langchain_chroma import Chroma
from sentence_transformers import SentenceTransformer
from typing import Optional
import re

BASE_DIR = Path(__file__).resolve().parent.parent      # chatbot/
CHROMA_DIR = BASE_DIR / "chroma_db"                   # chatbot/chroma_db

AIRLINE_HINTS = {
    "대한항공": ["대한항공", "KAL", "KOREAN AIR", "KE"],
    "아시아나": ["아시아나", "ASIANA", "OZ"],
    "제주항공": ["제주항공", "JEJU AIR", "7C"],
    "진에어": ["진에어", "JINAIR", "LJ"],
    "티웨이": ["티웨이", "T'WAY", "TW"],
    "에어부산": ["에어부산", "AIR BUSAN", "BX"],
}

def _guess_airline(question: str) -> Optional[str]:
    q = (question or "").upper()
    for name, hints in AIRLINE_HINTS.items():
        for h in hints:
            if h.upper() in q:
                return name
    return None

def _guess_dom_intl(question: str) -> Optional[str]:
    q = (question or "")
    # 대충이라도 힌트가 있으면 필터
    if re.search(r"국내|국내선|domestic", q, re.I):
        return "Domestic"
    if re.search(r"국제|국제선|international|해외", q, re.I):
        return "International"
    return None


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


def retrieve_context(query: str, k: int = 3) -> str:
    query = (query or "").strip()
    if not query:
        return ""

    airline = _guess_airline(query)
    dom_intl = _guess_dom_intl(query)

    emb = _embedder().encode(query).tolist()

    # 일단 넉넉히 뽑고
    results = _vectordb()._collection.query(
        query_embeddings=[emb],
        n_results=max(12, k),
        include=["documents", "metadatas"]
    )
    docs = results.get("documents", [[]])[0] or []
    metas = results.get("metadatas", [[]])[0] or []

    picked = []
    for d, m in zip(docs, metas):
        ok = True
        if airline:
            s = (m.get("source") or "") + " " + (m.get("file_name") or "")
            if airline not in s:
                ok = False
        if dom_intl == "Domestic":
            if "Domestic" not in (m.get("title") or ""):
                ok = False
        if dom_intl == "International":
            if "International" not in (m.get("title") or ""):
                ok = False
        if ok:
            picked.append(d)
        if len(picked) >= k:
            break

    if not picked:
        picked = docs[:k]

    return ("\n\n".join(picked))[:6000]
