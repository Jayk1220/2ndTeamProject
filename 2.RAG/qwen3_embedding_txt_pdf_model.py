import os
import argparse
from pathlib import Path

from langchain_text_splitters import TokenTextSplitter
from langchain_community.document_loaders import TextLoader, PyPDFLoader

try:
    from langchain_chroma import Chroma
except Exception:
    from langchain_community.vectorstores import Chroma

from langchain_community.embeddings import HuggingFaceEmbeddings


def iter_files(input_dir: str):
    base = Path(input_dir)
    if not base.exists():
        raise FileNotFoundError(f"Input dir not found: {base}")
    files = sorted(list(base.rglob("*.txt")) + list(base.rglob("*.pdf")))
    if not files:
        raise FileNotFoundError(f"No .txt/.pdf found under: {base}")
    return files


def load_docs(file_path: Path):
    ext = file_path.suffix.lower()
    if ext == ".txt":
        loader = TextLoader(str(file_path), encoding="utf-8")
    elif ext == ".pdf":
        loader = PyPDFLoader(str(file_path))
    else:
        raise ValueError(f"Unsupported file type: {ext}")
    return loader.load(), ext


def ingest(
    input_dir: str,
    persist_dir: str,
    collection_name: str = "airline_terms",
    chunk_size_tokens: int = 1500,
    chunk_overlap_tokens: int = 200,
    model_name: str = "Qwen/Qwen3-Embedding-0.6B",
    device: str | None = None,
):
    persist_dir = str(Path(persist_dir))
    os.makedirs(persist_dir, exist_ok=True)

    splitter = TokenTextSplitter(
        encoding_name="cl100k_base",
        chunk_size=chunk_size_tokens,
        chunk_overlap=chunk_overlap_tokens,
    )

    embed_kwargs = {"encode_kwargs": {"normalize_embeddings": True}}
    if device:
        embed_kwargs["model_kwargs"] = {"device": device}

    embeddings = HuggingFaceEmbeddings(model_name=model_name, **embed_kwargs)

    # ✅ 누적 저장을 위해 from_documents()가 아니라 add_documents()
    db = Chroma(
        persist_directory=persist_dir,
        collection_name=collection_name,
        embedding_function=embeddings,
    )

    total_chunks = 0
    files = iter_files(input_dir)

    for fp in files:
        docs, ext = load_docs(fp)

        source_name = fp.stem.replace("여객운송약관", "")
        file_type = ext.lstrip(".")

        # doc-level metadata (pdf page는 보통 loader가 이미 넣어줌)
        for d in docs:
            d.metadata["source"] = source_name
            d.metadata["file_type"] = file_type
            d.metadata["file_name"] = fp.name

        chunks = splitter.split_documents(docs)

        for i, c in enumerate(chunks):
            c.metadata["source"] = source_name
            c.metadata["file_type"] = file_type
            c.metadata["file_name"] = fp.name
            c.metadata["chunk_id"] = f"{fp.stem}:{i}"

        db.add_documents(chunks)
        total_chunks += len(chunks)

        print(f"✅ added: {fp}  chunks={len(chunks)}")

    try:
        db.persist()
    except Exception:
        pass

    print("\n✅ Done")
    print(f"- Input dir: {input_dir}")
    print(f"- Files: {len(files)}")
    print(f"- Total chunks: {total_chunks}")
    print(f"- Collection: {collection_name}")
    print(f"- Persist dir: {persist_dir}")

    return db


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", type=str, required=True, help="Folder containing .txt/.pdf (recursive)")
    parser.add_argument("--persist_dir", type=str, default="./chroma_db")
    parser.add_argument("--collection", type=str, default="airline_terms")
    parser.add_argument("--chunk_size", type=int, default=1500)
    parser.add_argument("--chunk_overlap", type=int, default=200)
    parser.add_argument("--model", type=str, default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--device", type=str, default="cuda") #gpu 활용 가능하도록 수정
    args = parser.parse_args()

    ingest(
        input_dir=args.input_dir,
        persist_dir=args.persist_dir,
        collection_name=args.collection,
        chunk_size_tokens=args.chunk_size,
        chunk_overlap_tokens=args.chunk_overlap,
        model_name=args.model,
        device=args.device,
    )
