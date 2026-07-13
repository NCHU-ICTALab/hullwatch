"""本地檢索器與 RAG 評估的測試。"""

from app.llm.rag_eval import evaluate
from app.llm.retrieval import LocalTfidfRetriever


def test_local_retriever_finds_relevant_chunks():
    r = LocalTfidfRetriever()
    hits = r.search("水下清潔成本多少錢", k=3)
    assert hits and hits[0]["source"] == "cleaning-economics.md"


def test_rag_eval_on_local_retriever():
    metrics = evaluate(LocalTfidfRetriever(), k=3)
    assert metrics["hit_at_k"] >= 0.8, metrics["misses"]
    assert metrics["mrr"] >= 0.6
