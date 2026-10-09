import hashlib

import pytest

from app.agents.risk_knowledge import agent as agent_module
from app.agents.risk_knowledge.agent import RiskKnowledgeAgent


PROJECT_ID = "TEST_PROJECT"
SOURCE_NAME = "project-report.pdf"


class FakeRetrievalService:
    """In-memory substitute for ChromaDB used only by these tests."""

    def __init__(self):
        self.documents = {}
        self.events = []
        self.fail_after_store = False

    def seed_document(
        self,
        document_id,
        source,
        document_hash,
        chunks=2,
    ):
        self.documents[document_id] = {
            "document_id": document_id,
            "source": source,
            "document_hash": document_hash,
            "chunks": chunks,
        }

    def find_document_by_hash(self, project_id, document_hash):
        return [
            {
                "document_id": document_id,
                "source": document["source"],
                "document_hash": document["document_hash"],
            }
            for document_id, document in self.documents.items()
            if document["document_hash"] == document_hash
        ]

    def find_documents_by_source(self, project_id, source):
        return [
            {
                "document_id": document_id,
                "source": document["source"],
                "document_hash": document["document_hash"],
            }
            for document_id, document in self.documents.items()
            if document["source"] == source
        ]

    def count_document(self, project_id, document_id):
        document = self.documents.get(document_id)
        return document["chunks"] if document else 0

    def add_documents(
        self,
        embeddings,
        chunks,
        source,
        project_id,
        document_id,
        metadata_list=None,
    ):
        self.events.append(("add", document_id))

        document_hash = (
            metadata_list[0]["document_hash"]
            if metadata_list
            else ""
        )

        # Simulate a partial write followed by a failure.
        self.documents[document_id] = {
            "document_id": document_id,
            "source": source,
            "document_hash": document_hash,
            "chunks": len(chunks),
        }

        if self.fail_after_store:
            raise RuntimeError("Simulated indexing failure")

    def delete_document(self, project_id, document_id):
        self.events.append(("delete", document_id))
        document = self.documents.pop(document_id, None)
        return document["chunks"] if document else 0


class FakeNLPService:
    model_name = "fake-test-model"

    def extract_entities(self, text):
        return []


class FakeEmbeddingService:
    def embed_texts(self, chunks):
        return [[0.1, 0.2, 0.3] for _ in chunks]


@pytest.fixture
def setup_agent(monkeypatch, tmp_path):
    """Create an agent without initializing real models or databases."""

    retrieval = FakeRetrievalService()

    agent = RiskKnowledgeAgent.__new__(RiskKnowledgeAgent)
    agent.retrieval_service = retrieval
    agent.nlp_service = FakeNLPService()
    agent.embedding_service = FakeEmbeddingService()

    uploaded_file = tmp_path / "uploaded.pdf"
    uploaded_file.write_bytes(b"new PDF content")

    monkeypatch.setattr(
        agent_module,
        "extract_text_from_pdf",
        lambda path: "Extracted project report text.",
    )
    monkeypatch.setattr(
        agent_module,
        "clean_text",
        lambda text: text.strip(),
    )
    monkeypatch.setattr(
        agent_module,
        "split_text_into_chunks",
        lambda text, chunk_size, chunk_overlap: [text],
    )

    return agent, retrieval, uploaded_file


def test_identical_pdf_is_not_indexed_twice(setup_agent, tmp_path):
    agent, retrieval, uploaded_file = setup_agent

    uploaded_file.write_bytes(b"identical PDF content")
    document_hash = hashlib.sha256(
        uploaded_file.read_bytes()
    ).hexdigest()

    retrieval.seed_document(
        document_id="existing-document",
        source=SOURCE_NAME,
        document_hash=document_hash,
        chunks=3,
    )

    result = agent.ingest_pdf(
        file_path=str(uploaded_file),
        source_name=SOURCE_NAME,
        project_id=PROJECT_ID,
    )

    assert result["already_indexed"] is True
    assert result["duplicate_detected"] is True
    assert result["document_id"] == "existing-document"
    assert result["chunks_created"] == 3
    assert retrieval.events == []
    assert list(retrieval.documents) == ["existing-document"]


def test_updated_pdf_replaces_old_version_after_indexing(setup_agent):
    agent, retrieval, uploaded_file = setup_agent

    old_hash = hashlib.sha256(b"old PDF content").hexdigest()
    retrieval.seed_document(
        document_id="old-document",
        source=SOURCE_NAME,
        document_hash=old_hash,
        chunks=2,
    )

    result = agent.ingest_pdf(
        file_path=str(uploaded_file),
        source_name=SOURCE_NAME,
        project_id=PROJECT_ID,
    )

    new_document_id = result["document_id"]

    assert new_document_id != "old-document"
    assert result["duplicate_documents_replaced"] == 1
    assert "old-document" not in retrieval.documents
    assert new_document_id in retrieval.documents

    # The replacement must be indexed before the old version is deleted.
    add_position = retrieval.events.index(("add", new_document_id))
    delete_position = retrieval.events.index(("delete", "old-document"))
    assert add_position < delete_position


def test_extraction_failure_preserves_existing_document(
    setup_agent,
    monkeypatch,
):
    agent, retrieval, uploaded_file = setup_agent

    retrieval.seed_document(
        document_id="old-document",
        source=SOURCE_NAME,
        document_hash=hashlib.sha256(b"old content").hexdigest(),
        chunks=2,
    )

    def fail_extraction(path):
        raise ValueError("PDF extraction failed")

    monkeypatch.setattr(
        agent_module,
        "extract_text_from_pdf",
        fail_extraction,
    )

    with pytest.raises(ValueError, match="PDF extraction failed"):
        agent.ingest_pdf(
            file_path=str(uploaded_file),
            source_name=SOURCE_NAME,
            project_id=PROJECT_ID,
        )

    assert "old-document" in retrieval.documents
    assert retrieval.documents["old-document"]["chunks"] == 2
    assert not any(event[0] == "delete" for event in retrieval.events)
    assert not any(event[0] == "add" for event in retrieval.events)


def test_indexing_failure_preserves_old_document_and_cleans_partial_write(
    setup_agent,
):
    agent, retrieval, uploaded_file = setup_agent

    retrieval.seed_document(
        document_id="old-document",
        source=SOURCE_NAME,
        document_hash=hashlib.sha256(b"old content").hexdigest(),
        chunks=2,
    )
    retrieval.fail_after_store = True

    with pytest.raises(RuntimeError, match="Simulated indexing failure"):
        agent.ingest_pdf(
            file_path=str(uploaded_file),
            source_name=SOURCE_NAME,
            project_id=PROJECT_ID,
        )

    assert "old-document" in retrieval.documents
    assert retrieval.documents["old-document"]["chunks"] == 2

    # The partial replacement is removed during best-effort cleanup.
    assert list(retrieval.documents) == ["old-document"]

    added_ids = [
        document_id
        for action, document_id in retrieval.events
        if action == "add"
    ]
    deleted_ids = [
        document_id
        for action, document_id in retrieval.events
        if action == "delete"
    ]

    assert len(added_ids) == 1
    assert added_ids[0] in deleted_ids
    assert "old-document" not in deleted_ids


def test_blank_extracted_text_preserves_existing_document(
    setup_agent,
    monkeypatch,
):
    agent, retrieval, uploaded_file = setup_agent

    retrieval.seed_document(
        document_id="old-document",
        source=SOURCE_NAME,
        document_hash=hashlib.sha256(b"old content").hexdigest(),
        chunks=2,
    )

    monkeypatch.setattr(
        agent_module,
        "extract_text_from_pdf",
        lambda path: "   ",
    )
    monkeypatch.setattr(
        agent_module,
        "clean_text",
        lambda text: text,
    )

    with pytest.raises(ValueError, match="No extractable text"):
        agent.ingest_pdf(
            file_path=str(uploaded_file),
            source_name=SOURCE_NAME,
            project_id=PROJECT_ID,
        )

    assert list(retrieval.documents) == ["old-document"]
    assert retrieval.events == []
