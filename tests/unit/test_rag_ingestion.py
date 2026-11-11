import importlib.util
from pathlib import Path
import sys
import types

import pytest


MODULE_PATH = Path(__file__).resolve().parents[2] / "rag" / "ingestion" / "ingester.py"


class FakeEmbeddingModel:
    instances = []

    def __init__(self, _model_name):
        self.calls = []
        self.instances.append(self)

    def encode(self, texts):
        self.calls.append(texts)
        if isinstance(texts, str):
            return FakeEmbedding([0.1, 0.2, 0.3])
        return FakeEmbedding([[float(index), 0.2, 0.3] for index, _ in enumerate(texts)])

    def get_sentence_embedding_dimension(self):
        return 3


class FakeEmbedding(list):
    def tolist(self):
        return list(self)


class FakePoint:
    def __init__(self, id, vector, payload):
        self.id = id
        self.vector = vector
        self.payload = payload


class FakeVectorParams:
    def __init__(self, size, distance):
        self.size = size
        self.distance = distance


class FakeQdrantClient:
    def __init__(self, path):
        self.path = path
        self.collection_exists_result = False
        self.create_collection_args = None
        self.upsert_args = None
        self.query_args = None
        self.query_response = types.SimpleNamespace(points=[
            types.SimpleNamespace(
                payload={"content": "pump procedure", "metadata": {"source": "pump.txt"}},
                score=0.9,
            )
        ])

    def collection_exists(self, _collection_name):
        return self.collection_exists_result

    def create_collection(self, **kwargs):
        self.create_collection_args = kwargs
        self.collection_exists_result = True

    def upsert(self, **kwargs):
        self.upsert_args = kwargs

    def query_points(self, **kwargs):
        self.query_args = kwargs
        return self.query_response


class FakeQdrantModels:
    PointStruct = FakePoint
    VectorParams = FakeVectorParams
    Distance = types.SimpleNamespace(COSINE="cosine")


@pytest.fixture
def rag_module(monkeypatch):
    FakeEmbeddingModel.instances.clear()
    clients = []

    def create_client(path):
        client = FakeQdrantClient(path)
        clients.append(client)
        return client

    qdrant_stub = types.ModuleType("qdrant_client")
    qdrant_stub.QdrantClient = create_client
    qdrant_stub.models = FakeQdrantModels
    sentence_transformers_stub = types.ModuleType("sentence_transformers")
    sentence_transformers_stub.SentenceTransformer = FakeEmbeddingModel
    monkeypatch.setitem(sys.modules, "qdrant_client", qdrant_stub)
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers_stub)

    spec = importlib.util.spec_from_file_location("test_rag_ingester", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, clients


def test_chunk_text_uses_exact_size_and_overlap(rag_module):
    module, _ = rag_module
    ingester = module.DocumentIngester.__new__(module.DocumentIngester)

    chunks = ingester.chunk_text("one two three four five six seven", chunk_size=4, overlap=2)

    assert [chunk["text"] for chunk in chunks] == [
        "one two three four",
        "three four five six",
        "five six seven",
    ]
    assert [(chunk["start_word"], chunk["end_word"]) for chunk in chunks] == [
        (0, 4), (2, 6), (4, 7)
    ]


@pytest.mark.parametrize(
    ("chunk_size", "overlap"),
    [(0, 0), (4, -1), (4, 4)],
)
def test_chunk_text_rejects_invalid_sizes(rag_module, chunk_size, overlap):
    module, _ = rag_module
    ingester = module.DocumentIngester.__new__(module.DocumentIngester)

    with pytest.raises(ValueError):
        ingester.chunk_text("some words", chunk_size=chunk_size, overlap=overlap)


def test_ingestion_encodes_each_chunk_once_and_reuses_embeddings(rag_module, tmp_path):
    module, clients = rag_module
    document = tmp_path / "procedure.txt"
    document.write_text("one two three four five", encoding="utf-8")
    ingester = module.DocumentIngester(str(tmp_path))

    result = ingester.ingest_document(str(document))

    assert result["chunks"] == 1
    assert len(FakeEmbeddingModel.instances[-1].calls) == 1
    assert FakeEmbeddingModel.instances[-1].calls[0] == ["one two three four five"]
    point = clients[0].upsert_args["points"][0]
    assert point.vector == [0.0, 0.2, 0.3]
    assert point.payload["content"] == "one two three four five"
    assert point.payload["metadata"]["source"] == "procedure.txt"
    assert clients[0].create_collection_args["vectors_config"].size == 3


def test_retriever_creates_missing_collection_and_encodes_query(rag_module):
    module, clients = rag_module
    retriever = module.RAGRetriever("vector-store")

    results = retriever.retrieve("pump procedure")

    assert clients[0].create_collection_args["vectors_config"].size == 3
    assert clients[0].query_args["query"] == [0.0, 0.2, 0.3]
    assert clients[0].query_args["limit"] == 5
    assert results == [{
        "content": "pump procedure",
        "score": pytest.approx(0.9),
        "metadata": {"source": "pump.txt"},
    }]
    assert len(FakeEmbeddingModel.instances[-1].calls) == 1


def test_retriever_filters_low_confidence_points(rag_module):
    module, clients = rag_module
    retriever = module.RAGRetriever("vector-store")
    clients[0].query_response.points[0].score = 0.2

    assert retriever.retrieve("pump procedure", min_score=0.3) == []


def test_retriever_does_not_hide_vector_store_errors(rag_module):
    module, clients = rag_module
    retriever = module.RAGRetriever("vector-store")

    def fail_query_points(**_kwargs):
        raise RuntimeError("vector store unavailable")

    clients[0].query_points = fail_query_points
    with pytest.raises(RuntimeError, match="vector store unavailable"):
        retriever.retrieve("pump procedure")
