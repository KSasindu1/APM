from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importlib import import_module


def _load_chromadb() -> Any:
    """Load ChromaDB without requiring static import resolution."""

    try:
        return import_module("chromadb")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "ChromaDB is required. Install it with: pip install chromadb"
        ) from error


@dataclass
class RetrievedChunk:
    """
    Represents knowledge retrieved from ChromaDB.
    """

    text: str
    score: float
    source: str
    chunk_id: str
    metadata: dict[str, Any]


class RetrievalService:
    """
    ChromaDB-based semantic retrieval service.

    ChromaDB is persisted to disk so that the knowledge does not
    disappear when the FastAPI server is restarted.
    """

    def __init__(
        self,
        persist_directory: str,
        collection_name: str,
    ):
        Path(persist_directory).mkdir(
            parents=True,
            exist_ok=True,
        )

        chromadb = _load_chromadb()
        self.client = chromadb.PersistentClient(
            path=persist_directory
        )

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "hnsw:space": "cosine",
            },
        )

    def add_documents(
        self,
        embeddings: list[list[float]],
        chunks: list[str],
        source: str,
        project_id: str,
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Add document chunks to ChromaDB.

        Each chunk is associated with a project_id so knowledge
        belonging to different projects does not get mixed.
        """

        if len(embeddings) != len(chunks):
            raise ValueError(
                "The number of embeddings must match "
                "the number of chunks."
            )

        if not chunks:
            return

        if metadata_list is not None:
            if len(metadata_list) != len(chunks):
                raise ValueError(
                    "metadata_list must contain one item per chunk."
                )

        ids = []

        metadatas = []

        for index, chunk in enumerate(chunks):
            chunk_id = (
                f"{project_id}:"
                f"{source}:"
                f"{index}"
            )

            ids.append(chunk_id)

            metadata = {
                "project_id": project_id,
                "source": source,
                "chunk_index": index,
                "knowledge_type": "document",
            }

            if metadata_list:
                metadata.update(metadata_list[index])

            # Chroma metadata values must be primitive values.
            metadata = {
                str(key): value
                for key, value in metadata.items()
                if isinstance(
                    value,
                    (str, int, float, bool),
                )
            }

            metadatas.append(metadata)

        self.collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(
        self,
        query_embedding: list[float],
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:
        """
        Search ChromaDB for semantically relevant knowledge
        belonging to one project.
        """

        if not project_id:
            raise ValueError(
                "project_id is required for knowledge retrieval."
            )

        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than zero."
            )

        result = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where={
                "project_id": project_id,
            },
        )

        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        ids = result.get("ids", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        retrieved = []

        for index, document in enumerate(documents):
            metadata = {}

            if metadatas and index < len(metadatas):
                metadata = metadatas[index] or {}

            source = str(
                metadata.get(
                    "source",
                    "unknown",
                )
            )

            chunk_id = (
                str(ids[index])
                if ids and index < len(ids)
                else str(index)
            )

            distance = (
                float(distances[index])
                if distances and index < len(distances)
                else 0.0
            )

            # With cosine distance:
            # smaller distance = greater similarity.
            similarity = 1.0 - distance

            retrieved.append(
                RetrievedChunk(
                    text=document,
                    score=similarity,
                    source=source,
                    chunk_id=chunk_id,
                    metadata=metadata,
                )
            )

        return retrieved

    def count(
        self,
        project_id: str | None = None,
    ) -> int:
        """
        Return the number of stored knowledge chunks.

        When project_id is provided, count only that project's
        knowledge.
        """

        if project_id:
            result = self.collection.get(
                where={
                    "project_id": project_id,
                },
            )

            return len(
                result.get("ids", [])
            )

        return self.collection.count()