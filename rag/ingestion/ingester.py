"""Document Ingestion and RAG Pipeline"""
import os
import logging
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timezone
import hashlib
import uuid

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

COLLECTION_NAME = "alarm_procedures"
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class DocumentIngester:
    def __init__(
        self,
        vector_store_path: str = "./rag/qdrant_data",
        model_name: str = EMBEDDING_MODEL_NAME,
    ):
        self.vector_store_path = vector_store_path
        self.model_name = model_name
        self.embedding_model = SentenceTransformer(model_name)

        os.makedirs(vector_store_path, exist_ok=True)
        self.client = QdrantClient(path=vector_store_path)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self.client.collection_exists(COLLECTION_NAME):
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=self.embedding_model.get_sentence_embedding_dimension(),
                    distance=models.Distance.COSINE,
                ),
            )

    def chunk_text(
        self,
        text: str,
        chunk_size: int = 500,
        overlap: int = 100,
    ) -> List[Dict[str, Any]]:
        """Split text into fixed-size word chunks with a fixed overlap."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than zero")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be between zero and chunk_size - 1")

        chunks = []
        words = text.split()
        start = 0
        while start < len(words):
            end = min(start + chunk_size, len(words))
            chunks.append({
                "text": " ".join(words[start:end]),
                "start_word": start,
                "end_word": end,
            })
            if end == len(words):
                break
            start = end - overlap

        return chunks

    def ingest_document(self, file_path: str, doc_type: str = "procedure") -> Dict[str, Any]:
        """Ingest a document and store chunks in vector DB"""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Document not found: {file_path}")

        # Read document
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        filename = os.path.basename(file_path)
        doc_id = hashlib.sha256(f"{filename}-{content}".encode("utf-8")).hexdigest()[:16]

        # Chunk text
        chunks = self.chunk_text(content)
        if not chunks:
            raise ValueError(f"Document is empty: {file_path}")

        # Generate embeddings and store
        documents = [chunk["text"] for chunk in chunks]
        embeddings = self.embedding_model.encode(documents).tolist()
        ingested_at = datetime.now(timezone.utc).isoformat()
        metadatas = [
            {
                "source": filename,
                "doc_type": doc_type,
                "chunk_index": i,
                "total_chunks": len(chunks),
                "start_word": chunk["start_word"],
                "end_word": chunk["end_word"],
                "ingestion_date": ingested_at,
            }
            for i, chunk in enumerate(chunks)
        ]
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}_chunk_{i}")),
                vector=embedding,
                payload={"content": document, "metadata": metadata},
            )
            for i, (document, embedding, metadata) in enumerate(
                zip(documents, embeddings, metadatas)
            )
        ]
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
        )

        logger.info(f"Ingested {filename}: {len(chunks)} chunks")
        return {
            "doc_id": doc_id,
            "filename": filename,
            "chunks": len(chunks),
            "status": "success"
        }

    def ingest_directory(self, directory: str, doc_type: str = "procedure") -> List[Dict[str, Any]]:
        """Ingest all documents in a directory"""
        results = []
        doc_path = Path(directory)

        for file_path in doc_path.glob("*.txt"):
            try:
                result = self.ingest_document(str(file_path), doc_type)
                results.append(result)
            except Exception as e:
                logger.error(f"Error ingesting {file_path}: {e}")
                results.append({"filename": str(file_path), "status": "failed", "error": str(e)})

        return results

class RAGRetriever:
    def __init__(self, vector_store_path: str = "./rag/qdrant_data"):
        self.vector_store_path = vector_store_path
        os.makedirs(vector_store_path, exist_ok=True)
        self.client = QdrantClient(path=vector_store_path)
        self.embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        if not self.client.collection_exists(COLLECTION_NAME):
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=self.embedding_model.get_sentence_embedding_dimension(),
                    distance=models.Distance.COSINE,
                ),
            )

    def retrieve(self, query: str, top_k: int = 5, min_score: float = 0.3) -> List[Dict[str, Any]]:
        """Retrieve relevant documents for a query"""
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not 0 <= min_score <= 1:
            raise ValueError("min_score must be between zero and one")

        query_embedding = self.embedding_model.encode([query]).tolist()
        results = self.client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_embedding[0],
            limit=top_k,
        )

        retrieved = []
        for point in results.points:
            payload = point.payload or {}
            score = max(0.0, min(1.0, float(point.score)))
            if score >= min_score:
                retrieved.append({
                    "content": payload.get("content", ""),
                    "score": score,
                    "metadata": payload.get("metadata", {}),
                })

        return retrieved

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ingester = DocumentIngester()
    results = ingester.ingest_directory(
        os.getenv("DOCUMENT_PATH", "./rag/documents")
    )
    logger.info("Document ingestion results: %s", results)
