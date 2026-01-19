import os
import re
import statistics
import numpy as np
import chromadb
from sentence_transformers import SentenceTransformer
from collections import defaultdict

def inspect_chroma(persist_dir: str, collection_name: str | None = None, sample_n: int = 30):
    import chromadb

    client = chromadb.PersistentClient(path=persist_dir)

    # 1) 컬렉션 확인
    cols = client.list_collections()
    print("Collections:", [c.name for c in cols])
    if not cols:
        raise RuntimeError("컬렉션이 없습니다. persist_dir 경로가 맞는지 확인하세요.")

    if collection_name is None:
        collection_name = cols[0].name

    col = client.get_collection(collection_name)
    total = col.count()
    print(f"\n[Collection: {collection_name}] count = {total}")

    # 2) 문서(청크) 샘플링: 길이/메타데이터 키 확인
    n = min(sample_n, total)
    got = col.get(limit=n, include=["documents", "metadatas"])
    docs = got.get("documents", []) or []
    metas = got.get("metadatas", []) or []

    lengths = [len(d) for d in docs if d is not None]
    print("\n[Chunk length stats (chars)]")
    if lengths:
        print("  min/median/max =", min(lengths), int(statistics.median(lengths)), max(lengths))
    else:
        print("  documents가 비어있습니다. ingestion 시 documents 저장이 되었는지 확인하세요.")

    # 메타데이터 키 분포
    meta_keys = set()
    for m in metas:
        if isinstance(m, dict):
            meta_keys.update(m.keys())
    print("\n[Metadata keys (union)]", sorted(meta_keys))

    # 3) 임베딩 벡터 점검: 차원/노름/NaN 여부/전부 같은 벡터인지
    got2 = col.get(limit=n, include=["embeddings"])
    embs = got2.get("embeddings", None)
    if embs is None or len(embs) == 0:
        print("\n[Embeddings] 조회 실패 또는 비어있음")
        print(" - HNSW 인덱스 파일이 persist_dir에 같이 있어야 하는데 sqlite3만 있는 경우가 많습니다.")
        print(" - 또는 collection을 만들 때 embeddings를 저장하지 않았을 수도 있습니다.")
        return

    X = np.array(embs, dtype=np.float32)
    print("\n[Embeddings]")
    print("  shape =", X.shape)

    norms = np.linalg.norm(X, axis=1)
    print("  norm: min/mean/max =", float(norms.min()), float(norms.mean()), float(norms.max()))
    print("  any NaN? =", bool(np.isnan(X).any()))

    # 벡터가 모두 똑같은지(임베딩 함수/배치 문제 등) 간단 체크
    # (완전히 같은 경우는 매우 의심)
    uniq_approx = len({tuple(np.round(v, 4)) for v in X[:min(20, len(X))]})
    print("  approx-unique(rounded, first 20) =", uniq_approx)

    # 4) “자기 자신” 임베딩으로 top-k 검색해보기 (의미적으로 비슷한 청크가 나오는지)
    q = X[0].tolist()
    res = col.query(
        query_embeddings=[q],
        n_results=5,
        include=["documents", "metadatas", "distances"],
    )
    print("\n[Query sanity check: using embedding of the 1st chunk]")
    for i in range(len(res["ids"][0])):
        dist = res["distances"][0][i]
        doc = (res["documents"][0][i] or "").replace("\n", " ")
        doc = doc[:160] + ("..." if len(doc) > 160 else "")
        print(f"  #{i+1} dist={dist:.4f}  doc='{doc}'")

def normalize_filename(s: str) -> str:
    s = (s or "").strip()
    return os.path.splitext(s)[0]  # 확장자 제거


def infer_route(text: str) -> str | None:
    t = (text or "").lower()
    # 한국어
    if "국내" in t:
        return "domestic"
    if "국제" in t:
        return "international"
    # 영어
    if re.search(r"\bdomestic\b", t):
        return "domestic"
    if re.search(r"\binternational\b", t):
        return "international"
    return None

# 파일명/타이틀에서 항공사명 + 국내/국제 추출
def infer_airline_from_name(name: str) -> str:
    """
    파일명에서 항공사명만 남기기.
    - 국내/국제는 'route'로 분리해야 하므로 항공사명에서는 제거하는 게 정석.
    """
    base = normalize_filename(name)
    t = base

    junk = [
        # route 단어(항공사명에서 제거)
        "국내", "국제",
        # 약관 꼬리표
        "여객", "운송", "약관", "운송약관", "여객운송약관", "운송조건", "여객운송조건",
        "조건", "탑승", "수하물",
        # 흔한 영문 꼬리표(있을 수 있음)
        "general", "conditions", "of", "carriage", "for", "domestic", "international",
        "passenger", "passengers", "and", "baggage",
    ]
    junk = sorted(set(junk), key=len, reverse=True)
    for j in junk:
        t = t.replace(j, "")

    # 기호/공백 정리
    t = re.sub(r"[_\-\(\)\[\]\s]+", "", t).strip()
    return t or base

# 항공사 → (국내/국제/전체) 파일 목록 인덱스 생성
def build_airline_file_index(col):
    """
    return:
    {
        "제주항공": {"all":[...], "domestic":[...], "international":[...], "unknown":[...]},
        "일본항공": {...},
        ...
    }
    """
    got = col.get(include=["metadatas"])
    idx = defaultdict(lambda: {"all": set(), "domestic": set(), "international": set(), "unknown": set()})

    for m in got["metadatas"]:
        fn = m.get("file_name") or m.get("source") or ""
        if not fn:
            continue

        airline = infer_airline_from_name(fn)

        # route는 file_name / title 둘 다 참고 (있으면 더 잘 잡힘)
        route = infer_route(fn) or infer_route(m.get("title", "")) or "unknown"

        idx[airline]["all"].add(fn)
        idx[airline][route].add(fn)

    out = {}
    for airline, buckets in idx.items():
        out[airline] = {k: sorted(v) for k, v in buckets.items()}
    return out

def normalize_route_preference(x: str | None) -> str | None:
    if x is None:
        return None
    x = x.strip().lower()

    if x in ("국내", "국내용", "domestic"):
        return "domestic"
    if x in ("국제", "국제선", "international"):
        return "international"
    return None

# 항공사(필수) + 국내/국제(옵션, 부족하면 폴백) 검색
def query_airline_with_optional_route(
    col,
    q_emb,                     # (1024,) 1D
    airline_index,
    airline_name: str,         # 예: "제주항공"
    route_preference=None,     # "domestic" | "international" | None
    k=5,
    min_hits=3
):
    def as_list(x):
        return x.tolist() if hasattr(x, "tolist") else list(x)

    def do_query(files):
        return col.query(
            query_embeddings=[as_list(q_emb)],
            n_results=k,
            where={"file_name": {"$in": files}},
            include=["documents", "metadatas", "distances"],
        )

    if airline_name not in airline_index:
        # 디버깅 편하게 후보 항공사 몇 개 보여주기
        candidates = list(airline_index.keys())
        raise ValueError(f"'{airline_name}'를 못 찾았어요. 항공사 후보 예: {candidates[:30]}")

    files_all = airline_index[airline_name]["all"]
    if not files_all:
        raise ValueError(f"'{airline_name}'의 파일이 비어 있어요.")

    # 1) 국내/국제 우선 (있고, 결과가 충분하면 그대로 사용)
    if route_preference in ("domestic", "international"):
        preferred_files = airline_index[airline_name].get(route_preference, [])
        if preferred_files:
            res1 = do_query(preferred_files)
            hits1 = len(res1.get("ids", [[]])[0])
            if hits1 >= min_hits:
                return res1, f"{airline_name} + {route_preference} (preferred)"
        # 부족하면 폴백

    # 2) 항공사 전체로 폴백
    res2 = do_query(files_all)
    return res2, f"{airline_name} + ALL (fallback)"


def pretty_print(res, max_chars=320):
    for i, (doc, meta, dist) in enumerate(
        zip(res["documents"][0], res["metadatas"][0], res["distances"][0]), 1
    ):
        fn = meta.get("file_name") or meta.get("source")
        rt = infer_route(fn or "") or infer_route(meta.get("title", "")) or "unknown"

        snippet = (doc or "").replace("\n", " ")
        snippet = snippet[:max_chars] + ("..." if len(snippet) > max_chars else "")

        print(f"\n#{i} dist={float(dist):.4f} route={rt} file={fn} page={meta.get('page')}")
        print(snippet)


model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B")

def query_by_text(col, text, k=5):
    q_emb = model.encode([text], normalize_embeddings=True)  # (1,1024)
    res = col.query(
        query_embeddings=q_emb.tolist(),
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )
    return res

if __name__ == "__main__":
    # 예) persist 디렉토리가 ./persist 라면
    persist_dir="./chroma_db"
    
    ## 청킹/임베딩 상태 확인이 필요할 경우
    #inspect_chroma(persist_dir=persist_dir, collection_name=None, sample_n=50) # 데이터 청킹, 임베딩 확인

    client = chromadb.PersistentClient(path="./chroma_db")
    col = client.get_collection("airline_terms")

    airline_index = build_airline_file_index(col)
    
    airline_name = "제주항공" 
    route_preference = normalize_route_preference("국제")  # 국내, 국제, domestic, international, None중 택 1
    query = "refund fee for unused ticket" # query = "항공권 환불 수수료"

    q_emb = model.encode([query], normalize_embeddings=True)[0]

    # 4) 항공사 우선 + 국내/국제 차순위(옵션) + 폴백 검색
    res, mode = query_airline_with_optional_route(
        col,
        q_emb=q_emb,
        airline_index=airline_index,
        airline_name=airline_name,
        route_preference=route_preference,
        k=5,
        min_hits=3
    )
    print(f"\n[Query] {query}")
    for i, (doc, meta, dist) in enumerate(
        zip(res["documents"][0], res["metadatas"][0], res["distances"][0]), 1
    ):
        print(f"\n#{i} dist={dist:.4f}")
        print("file:", meta.get("file_name"), "page:", meta.get("page"))
        print((doc or "")[:300].replace("\n", " "))
