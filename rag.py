# rag.py
import os
from sentence_transformers import SentenceTransformer
import numpy as np
import faiss
from typing import List, Tuple
from tqdm import tqdm

MODEL_NAME = "all-MiniLM-L6-v2"

class SimpleRAGIndex:
    def __init__(self, model_name=MODEL_NAME, index_path=None):
        self.model = SentenceTransformer(model_name)
        self.index = None
        self.embeddings = None
        self.texts = []
        self.index_path = index_path

    def build_from_folder(self, folder_path: str):
        """
        Reads all .txt files from folder, splits into chunks (here by paragraph lines),
        builds embeddings and a FAISS index.
        """
        texts = []
        for fname in os.listdir(folder_path):
            if not fname.lower().endswith(".txt"):
                continue
            p = os.path.join(folder_path, fname)
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()
            # split by double-newline or single newline
            chunks = [chunk.strip() for chunk in raw.split("\n\n") if chunk.strip()]
            # prefix with filename for traceability
            for c in chunks:
                texts.append(f"[{fname}] {c}")
        if not texts:
            raise ValueError("No reference text files found in folder.")
        self.texts = texts
        embs = self.model.encode(texts, show_progress_bar=True, convert_to_numpy=True)
        self.embeddings = embs.astype("float32")
        dim = self.embeddings.shape[1]
        self.index = faiss.IndexFlatL2(dim)
        self.index.add(self.embeddings)
        if self.index_path:
            faiss.write_index(self.index, self.index_path)

    def retrieve(self, query: str, k=4) -> List[Tuple[str, float]]:
        q_emb = self.model.encode([query], convert_to_numpy=True).astype("float32")
        D, I = self.index.search(q_emb, k)
        out = []
        for i, dist in zip(I[0], D[0]):
            if i < 0 or i >= len(self.texts):
                continue
            out.append((self.texts[i], float(dist)))
        return out
