"""RAG service backend (pattern 2): retrieval over the real 5gprod corpus.

This is the skill backend the RCA agent calls over HTTP; the index never
lives inside the agent pod. The corpus is built exactly the way the source
llm-rca experiment builds its documents: one record per telemetry row
("timestamp - kpi: value | ...") plus one record per alert.

Retriever: TF-IDF + cosine (scikit-learn), dependency-light and fully local.
The original experiment used OpenAI embeddings + FAISS; swap `Retriever`
for that without touching the API surface, which is the point of pattern 2.

Run:  uvicorn backend.rag_service:app --port 8201
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Query
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

DATA_DIR = Path(os.environ.get(
    "DATA_DIR",
    Path(__file__).resolve().parent.parent.parent / "101-noc-assistant" / "data",
))

NFS = ("amf", "smf", "upf")


def build_corpus() -> tuple[list[dict], list[str]]:
    docs: list[dict] = []
    for nf in NFS:
        df = pd.read_csv(DATA_DIR / f"{nf}_metrics.csv")
        cols = [c for c in df.columns if c != "timestamp"]
        for i, row in df.iterrows():
            text = f"{row['timestamp']} - " + " | ".join(
                f"{c}: {row[c]}" for c in cols
            )
            docs.append({"id": f"{nf}-{i}", "nf": nf, "text": text})
    alerts = json.loads((DATA_DIR / "alerts.json").read_text())
    for i, a in enumerate(alerts.get("alerts", [])):
        text = (
            f"ALERT {a.get('type')} severity={a.get('severity')} "
            f"component={a.get('component')} window={a.get('start_time')}"
            f"..{a.get('end_time')} :: {a.get('description')} :: "
            f"delta_percent={json.dumps(a.get('metrics_snapshot', {}).get('delta_percent', {}))}"
        )
        docs.append({"id": f"alert-{i}", "nf": a.get("component", "").lower(), "text": text})
    return docs, [d["text"] for d in docs]


def _normalize(text: str) -> str:
    """Split snake_case KPI and alert tokens so plain-word queries match."""
    return text.replace("_", " ").replace("-", " ").lower()


class Retriever:
    def __init__(self) -> None:
        self.docs, texts = build_corpus()
        self.vectorizer = TfidfVectorizer(max_features=50000, preprocessor=_normalize)
        self.matrix = self.vectorizer.fit_transform(texts)

    def search(self, query: str, nf: str = "all", k: int = 5) -> list[dict]:
        scores = linear_kernel(self.vectorizer.transform([query]), self.matrix)[0]
        order = scores.argsort()[::-1]
        out = []
        for idx in order:
            doc = self.docs[int(idx)]
            if nf != "all" and doc["nf"] != nf:
                continue
            out.append({"id": doc["id"], "nf": doc["nf"],
                        "score": round(float(scores[int(idx)]), 4),
                        "text": doc["text"][:600]})
            if len(out) >= k:
                break
        return out


app = FastAPI(title="rca-rag-service")
retriever = Retriever()


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "documents": len(retriever.docs)}


@app.get("/search")
def search(q: str = Query(...), nf: str = "all", k: int = 5) -> dict:
    return {"query": q, "nf": nf, "results": retriever.search(q, nf=nf, k=k)}
