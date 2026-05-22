"""BoilerMind — persistent ChromaDB book RAG (FastEmbed BGE-small-en)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from chromadb import PersistentClient
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings, Space
from chromadb.utils.embedding_functions import register_embedding_function

from paths import chroma_db_path_str

logger = logging.getLogger("boilermind-rag")

EMBED_MODEL_ID = "BAAI/bge-small-en-v1.5"
COLLECTION_NAME = "engineering_books"


@register_embedding_function
class _BgeFastEmbed(EmbeddingFunction[Documents]):
    """EmbeddingFunction wrapping fastembed TextEmbedding (BGE-small-en-v1.5)."""

    _singleton: "_BgeFastEmbed | None" = None

    def __init__(self) -> None:
        from fastembed import TextEmbedding  # pylint: disable=import-outside-toplevel

        super().__init__()
        self.model_name = EMBED_MODEL_ID
        self._model = TextEmbedding(model_name=self.model_name)

    def __call__(self, input: Documents) -> Embeddings:
        if not input:
            return []
        out: Embeddings = []
        for vec in self._model.embed(list(input)):
            out.append(np.array(vec, dtype=np.float32))
        return out

    @staticmethod
    def name() -> str:
        return "boilermind_fastembed_bge_small_en_v15"

    def default_space(self) -> Space:
        return "cosine"

    def supported_spaces(self) -> List[Space]:
        return ["cosine", "l2", "ip"]

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "EmbeddingFunction[Documents]":
        return _BgeFastEmbed.instance()

    def get_config(self) -> Dict[str, Any]:
        return {"model_name": self.model_name}

    def validate_config_update(
        self, old_config: Dict[str, Any], new_config: Dict[str, Any]
    ) -> None:
        return

    @staticmethod
    def validate_config(config: Dict[str, Any]) -> None:
        pass

    @classmethod
    def instance(cls) -> "_BgeFastEmbed":
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton


class BookRAG:
    _singleton: Optional["BookRAG"] = None

    _client = None
    _collection = None

    def __init__(self) -> None:
        self._connected = False

    @classmethod
    def instance(cls) -> "BookRAG":
        if cls._singleton is None:
            cls._singleton = BookRAG()
        return cls._singleton

    def connect(self) -> bool:
        """Connect to persistent ChromaDB and ensure collection exists."""
        try:
            ef = _BgeFastEmbed.instance()
            chroma_path = chroma_db_path_str()
            self._client = PersistentClient(path=chroma_path)
            try:
                self._collection = self._client.get_collection(
                    name=COLLECTION_NAME,
                    embedding_function=ef,
                )
            except Exception:
                self._collection = self._client.create_collection(
                    name=COLLECTION_NAME,
                    embedding_function=ef,
                    metadata={"description": "Engineering PDF chunks"},
                )
            self._connected = True
            logger.info(
                "ChromaDB connected at %s, collection '%s'",
                chroma_path,
                COLLECTION_NAME,
            )
            return True
        except Exception as e:
            logger.exception("BookRAG connect failed: %s", e)
            self._connected = False
            self._client = None
            self._collection = None
            return False

    def query(
        self,
        user_question: str,
        n_results: int = 5,
        book_filter: Optional[str] = None,
    ) -> str:
        """Vector search + formatted citations."""
        try:
            if not self.is_ready():
                logger.warning("query called but RAG not ready")
                return "Loaded books mein is topic ki information nahi mili."
            kw: Dict = {}
            if book_filter and str(book_filter).strip():
                bf = str(book_filter).strip()
                kw["where"] = {"book_id": bf}
            res = self._collection.query(
                query_texts=[user_question.strip()],
                n_results=max(1, min(n_results, 50)),
                **kw,
            )
            texts = res.get("documents") or [[]]
            metas = res.get("metadatas") or [[]]
            if not texts or not texts[0]:
                return "Loaded books mein is topic ki information nahi mili."
            parts: List[str] = []
            for doc, meta in zip(texts[0], metas[0] or []):
                md = meta or {}
                book_name = md.get("book_name", "unknown")
                page = md.get("page", "?")
                parts.append(f"[Source: {book_name}, Page {page}]\n{doc}")
            return "\n\n".join(parts)
        except Exception as e:
            logger.exception("BookRAG query error: %s", e)
            return "Loaded books mein is topic ki information nahi mili."

    def get_loaded_books(self) -> List[dict]:
        """Distinct book_ids with chunk counts."""
        if self._collection is None:
            return []
        aggregated: Dict[str, dict] = {}
        batch_size = 2000
        offset = 0
        try:
            while True:
                out = self._collection.get(
                    include=["metadatas"],
                    limit=batch_size,
                    offset=offset,
                )
                ids = out.get("ids") or []
                metas = out.get("metadatas") or []
                if not ids:
                    break
                for md in metas:
                    if not md:
                        continue
                    bid = str(md.get("book_id", "") or "").strip()
                    if not bid:
                        continue
                    if bid not in aggregated:
                        aggregated[bid] = {
                            "book_id": bid,
                            "book_name": md.get("book_name", bid),
                            "total_chunks": 0,
                        }
                    aggregated[bid]["total_chunks"] += 1
                offset += len(ids)
        except Exception as e:
            logger.exception("get_loaded_books error: %s", e)

        return sorted(aggregated.values(), key=lambda x: x["book_name"])

    def is_ready(self) -> bool:
        if not self._connected or self._collection is None:
            return False
        try:
            return self._collection.count() > 0
        except Exception:
            return False

    def get_books_summary(self) -> str:
        books = self.get_loaded_books()
        if not books:
            return "No books currently loaded."

        fragments = [
            f"{b['book_name']} ({int(b['total_chunks'])} chunks)"
            for b in books
        ]
        n = len(books)
        return f"{n} book(s) loaded: " + ", ".join(fragments)

    def remove_book(self, book_id: str) -> int:
        """Delete all vectors for ``book_id``. Returns number of rows removed (estimate)."""
        bid = str(book_id or "").strip()
        if not bid or self._collection is None:
            return 0
        try:
            before = None
            try:
                peek = self._collection.get(where={"book_id": bid}, limit=10000)
                before = len(peek.get("ids") or [])
            except Exception:
                before = None
            self._collection.delete(where={"book_id": bid})
            logger.info("Removed book_id=%s (approx_chunks=%s)", bid, before)
            return before or 1
        except Exception as e:
            logger.exception("remove_book failed: %s", e)
            return 0


book_rag = BookRAG.instance()
