"""檢索品質評估 — 學長 rag_evaluation 管線的精簡移植（ADR-0002）。

對任何符合 ``search(query, k)`` 介面的 retriever（本地 TF-IDF 或 Bedrock KB）
跑同一組黃金查詢集，回報 hit@k 與 MRR。比賽當天建好 Bedrock KB 後：
    python -m app.llm.rag_eval            # 評本地
    HW_RETRIEVER=bedrock_kb HW_BEDROCK_KB_ID=... python -m app.llm.rag_eval
"""

from __future__ import annotations

# 黃金查詢集：query → 應命中的來源檔（kb/ 檔名；Bedrock KB 時為 S3 URI 子字串）
GOLDEN_SET: list[tuple[str, str]] = [
    ("speed loss 的定義是什麼", "iso19030.md"),
    ("正午報表屬於哪一層方法", "iso19030.md"),
    ("維護觸發 KPI", "iso19030.md"),
    ("水下清潔一次要多少錢", "cleaning-economics.md"),
    ("清洗多久回本", "cleaning-economics.md"),
    ("立方定律 油耗", "cleaning-economics.md"),
    ("CII 碳排 合規", "cleaning-economics.md"),
    ("DailyFOC 怎麼計算", "competition-brief.md"),
    ("良好天氣的篩選條件", "competition-brief.md"),
    ("陽明海運的營運痛點", "competition-brief.md"),
]


def evaluate(retriever, k: int = 3, golden: list[tuple[str, str]] | None = None) -> dict:
    """回傳 {hit_at_k, mrr, n, misses}。"""
    golden = golden or GOLDEN_SET
    hits, rr, misses = 0, 0.0, []
    for query, expected in golden:
        results = retriever.search(query, k=k)
        rank = next((i + 1 for i, r in enumerate(results) if expected in r["source"]), None)
        if rank is not None:
            hits += 1
            rr += 1.0 / rank
        else:
            misses.append({"query": query, "expected": expected,
                           "got": [r["source"] for r in results]})
    n = len(golden)
    return {"hit_at_k": round(hits / n, 3), "mrr": round(rr / n, 3), "n": n, "misses": misses}


if __name__ == "__main__":
    import json

    from app.llm.retrieval import get_retriever

    print(json.dumps(evaluate(get_retriever()), indent=2, ensure_ascii=False))
