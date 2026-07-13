"""可切換檢索層（ADR-0002）。

- LocalTfidfRetriever：對 kb/*.md 做字元 n-gram TF-IDF（中英混合皆可），
  賽前開發與比賽當天的 fallback。
- BedrockKBRetriever：Bedrock Knowledge Bases 託管檢索，Learner Lab 測不了，
  比賽正式環境設定 HW_RETRIEVER=bedrock_kb + HW_BEDROCK_KB_ID 即切換。

兩者同介面，rag_evaluation 管線可同時評估。
"""

from __future__ import annotations

import re
from pathlib import Path

from app import config


class LocalTfidfRetriever:
    """kb 目錄下 markdown 的輕量向量檢索。"""

    def __init__(self, kb_dir: Path | None = None):
        from sklearn.feature_extraction.text import TfidfVectorizer

        self.chunks: list[dict] = []
        kb = Path(kb_dir or config.KB_DIR)
        for f in sorted(kb.glob("*.md")):
            for section in self._split(f.read_text(encoding="utf-8")):
                self.chunks.append({"source": f.name, "text": section})
        texts = [c["text"] for c in self.chunks] or ["(empty)"]
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 3), max_features=50000)
        self.matrix = self.vec.fit_transform(texts)

    @staticmethod
    def _split(text: str, max_len: int = 700) -> list[str]:
        parts = re.split(r"\n(?=#{1,3} )", text)
        out = []
        for p in parts:
            p = p.strip()
            while len(p) > max_len:
                cut = p.rfind("\n", 0, max_len)
                cut = cut if cut > 100 else max_len
                out.append(p[:cut]); p = p[cut:].strip()
            if p:
                out.append(p)
        return out

    def search(self, query: str, k: int = 3) -> list[dict]:
        if not self.chunks:
            return []
        import numpy as np

        q = self.vec.transform([query])
        scores = (self.matrix @ q.T).toarray().ravel()
        idx = np.argsort(scores)[::-1][:k]
        return [{"source": self.chunks[i]["source"], "text": self.chunks[i]["text"],
                 "score": round(float(scores[i]), 4)} for i in idx if scores[i] > 0]


class BedrockKBRetriever:
    """Bedrock Knowledge Bases 檢索（比賽正式環境用；賽前無法測試）。"""

    def __init__(self, kb_id: str | None = None, region: str | None = None):
        import boto3

        self.kb_id = kb_id or config.BEDROCK_KB_ID
        self.client = boto3.client("bedrock-agent-runtime",
                                   region_name=region or config.BEDROCK_REGION)

    def search(self, query: str, k: int = 3) -> list[dict]:
        resp = self.client.retrieve(
            knowledgeBaseId=self.kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": k}},
        )
        return [{
            "source": r.get("location", {}).get("s3Location", {}).get("uri", "kb"),
            "text": r.get("content", {}).get("text", ""),
            "score": round(float(r.get("score", 0.0)), 4),
        } for r in resp.get("retrievalResults", [])]


def get_retriever():
    if config.RETRIEVER == "bedrock_kb" and config.BEDROCK_KB_ID:
        return BedrockKBRetriever()
    return LocalTfidfRetriever()
