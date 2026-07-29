import os
import uuid
from typing import List, Optional

import ollama
from qdrant_client import QdrantClient, models

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

MEMORY_COLLECTION = "agent_memories"

_client: Optional[QdrantClient] = None
_collection_ready = False


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(
            host=QDRANT_HOST, port=QDRANT_PORT, check_compatibility=False
        )
    return _client


def embed_text(text: str) -> List[float]:
    ollama_client = ollama.Client(host=OLLAMA_HOST)
    response = ollama_client.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


def _ensure_collection() -> None:
    global _collection_ready
    if _collection_ready:
        return
    client = get_qdrant_client()
    if not client.collection_exists(MEMORY_COLLECTION):
        vector_size = len(embed_text("dimension probe"))
        client.create_collection(
            collection_name=MEMORY_COLLECTION,
            vectors_config=models.VectorParams(
                size=vector_size, distance=models.Distance.COSINE
            ),
        )
    _collection_ready = True


def upsert_memory(agent_id: str, tick: int, text: str) -> None:
    """Embed `text` and store it as a memory point owned by `agent_id`."""
    _ensure_collection()
    client = get_qdrant_client()
    vector = embed_text(text)
    client.upsert(
        collection_name=MEMORY_COLLECTION,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={"agent_id": agent_id, "tick": tick, "text": text},
            )
        ],
    )


def query_memories(agent_id: str, query_text: str, top_k: int = 5) -> List[str]:
    """Return up to `top_k` memory texts owned by `agent_id`, ranked by relevance to `query_text`."""
    _ensure_collection()
    client = get_qdrant_client()
    vector = embed_text(query_text)
    results = client.query_points(
        collection_name=MEMORY_COLLECTION,
        query=vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="agent_id", match=models.MatchValue(value=agent_id)
                )
            ]
        ),
        limit=top_k,
    )
    return [point.payload["text"] for point in results.points]
