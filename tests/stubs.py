"""Minimal-Stubs für langchain/FAISS, damit die Logik ohne Netz testbar ist."""
from __future__ import annotations

import hashlib
import math
import pickle
import sys
import types
from pathlib import Path
from typing import List

import numpy as np


# --------------------------------------------------- langchain_text_splitters
class RecursiveCharacterTextSplitter:
    def __init__(self, chunk_size=500, chunk_overlap=50, separators=None):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", " ", ""]

    def split_text(self, text: str) -> List[str]:
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []
        out, start = [], 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            piece = text[start:end]
            if end < len(text):
                cut = max(piece.rfind("\n\n"), piece.rfind("\n"), piece.rfind(" "))
                if cut > self.chunk_size * 0.4:
                    piece = piece[:cut]
                    end = start + cut
            if piece.strip():
                out.append(piece)
            if end >= len(text):   # Ende erreicht - sonst Endlosschleife durch Overlap
                break
            start = max(end - self.chunk_overlap, start + 1)
        return out


# ------------------------------------------------------------- langchain_core
class Embeddings:
    pass


class Document:
    def __init__(self, page_content: str, metadata: dict):
        self.page_content = page_content
        self.metadata = metadata


# ---------------------------------------------------------- FAISS-Ersatz
class FakeDocstore:
    def __init__(self):
        self._dict = {}


class FakeFAISS:
    def __init__(self, embedding):
        self.embedding = embedding
        self.docstore = FakeDocstore()
        self.vectors = {}

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, ids=None):
        store = cls(embedding)
        store.add_texts(texts, metadatas, ids)
        return store

    def add_texts(self, texts, metadatas=None, ids=None):
        metadatas = metadatas or [{} for _ in texts]
        ids = list(ids or [f"auto-{i}" for i in range(len(texts))])
        vectors = self.embedding.embed_documents(list(texts))
        for i, text in enumerate(texts):
            self.docstore._dict[ids[i]] = Document(text, metadatas[i])
            self.vectors[ids[i]] = np.array(vectors[i], dtype="float32")
        return ids

    def delete(self, ids):
        for i in ids:
            self.docstore._dict.pop(i, None)
            self.vectors.pop(i, None)
        return True

    def similarity_search_with_score_by_vector(self, vector, k=4):
        q = np.array(vector, dtype="float32")
        scored = []
        for key, vec in self.vectors.items():
            distance = float(np.linalg.norm(q - vec))
            scored.append((self.docstore._dict[key], distance))
        scored.sort(key=lambda kv: kv[1])
        return scored[:k]

    def save_local(self, folder_path):
        p = Path(folder_path)
        p.mkdir(parents=True, exist_ok=True)
        with open(p / "index.faiss", "wb") as fh:
            pickle.dump(
                {
                    "docs": {k: (d.page_content, d.metadata) for k, d in self.docstore._dict.items()},
                    "vectors": {k: v.tolist() for k, v in self.vectors.items()},
                },
                fh,
            )

    @classmethod
    def load_local(cls, folder_path, embeddings, allow_dangerous_deserialization=False):
        with open(Path(folder_path) / "index.faiss", "rb") as fh:
            data = pickle.load(fh)
        store = cls(embeddings)
        for key, (text, meta) in data["docs"].items():
            store.docstore._dict[key] = Document(text, meta)
        for key, vec in data["vectors"].items():
            store.vectors[key] = np.array(vec, dtype="float32")
        return store


def deterministic_embedding(text: str, dim: int = 64) -> List[float]:
    """Bag-of-words-Hash-Embedding - reicht, um Retrieval-Verhalten zu prüfen."""
    vec = np.zeros(dim, dtype="float32")
    for token in text.lower().split():
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
    norm = math.sqrt(float((vec ** 2).sum())) or 1.0
    return (vec / norm).tolist()


class StubEmbedding(Embeddings):
    def embed_documents(self, texts):
        return [deterministic_embedding(t) for t in texts]

    def embed_query(self, text):
        return deterministic_embedding(text)


def install() -> None:
    """Fake-Module registrieren, bevor jarvis importiert wird."""
    splitters = types.ModuleType("langchain_text_splitters")
    splitters.RecursiveCharacterTextSplitter = RecursiveCharacterTextSplitter
    sys.modules["langchain_text_splitters"] = splitters

    core = types.ModuleType("langchain_core")
    core_emb = types.ModuleType("langchain_core.embeddings")
    core_emb.Embeddings = Embeddings
    core_docs = types.ModuleType("langchain_core.documents")
    core_docs.Document = Document
    core.embeddings = core_emb
    core.documents = core_docs
    sys.modules["langchain_core"] = core
    sys.modules["langchain_core.embeddings"] = core_emb
    sys.modules["langchain_core.documents"] = core_docs

    community = types.ModuleType("langchain_community")
    vs = types.ModuleType("langchain_community.vectorstores")
    vs.FAISS = FakeFAISS
    community.vectorstores = vs
    sys.modules["langchain_community"] = community
    sys.modules["langchain_community.vectorstores"] = vs
