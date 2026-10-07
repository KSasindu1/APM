
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb


@dataclass
class RetrievedChunk:
    text: str
    score: float
    source: str
    chunk_id: str
    metadata: dict[str, Any]


class RetrievalService:
    def __init__(
        self,
        persist_directory: str,
        collection_name: str,
    ):
        Path(persist_directory).mkdir(
            parents=True,
            exist_ok=True,
        )

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
        document_id: str,
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> None:

        if len(embeddings) != len(chunks):
            raise ValueError(
                "The number of embeddings must match "
                "the number of chunks."
            )

        if not chunks:
            return

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not document_id:
            raise ValueError(
                "document_id is required."
            )

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
                f"{document_id}:"
                f"{index}"
            )

            ids.append(chunk_id)

            metadata = {
                "project_id": project_id,
                "document_id": document_id,
                "source": source,
                "chunk_index": index,
                "knowledge_type": "document",
            }

            if metadata_list:
                metadata.update(
                    metadata_list[index]
                )

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

    def delete_document(
        self,
        project_id: str,
        document_id: str,
    ) -> int:
        """
        Delete all ChromaDB chunks belonging to
        one specific project document.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not document_id:
            raise ValueError(
                "document_id is required."
            )

        result = self.collection.get(
            where={
                "$and": [
                    {
                        "project_id": project_id,
                    },
                    {
                        "document_id": document_id,
                    },
                ],
            },
        )

        ids = result.get(
            "ids",
            [],
        )

        if ids:
            self.collection.delete(
                ids=ids
            )

        return len(ids)

    def find_document_by_hash(
        self,
        project_id: str,
        document_hash: str,
    ) -> list[dict[str, Any]]:
        """
        Find documents in a project that have the
        specified content hash.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not document_hash:
            raise ValueError(
                "document_hash is required."
            )

        result = self.collection.get(
            where={
                "$and": [
                    {
                        "project_id": project_id,
                    },
                    {
                        "document_hash": document_hash,
                    },
                ],
            },
        )

        documents = {}

        ids = result.get(
            "ids",
            [],
        )

        metadatas = result.get(
            "metadatas",
            [],
        )

        for index, chunk_id in enumerate(ids):

            metadata = {}

            if (
                metadatas
                and index < len(metadatas)
            ):
                metadata = (
                    metadatas[index]
                    or {}
                )

            document_id = metadata.get(
                "document_id"
            )

            if not document_id:
                continue

            if document_id not in documents:

                documents[document_id] = {
                    "document_id": document_id,
                    "source": metadata.get(
                        "source",
                        "unknown",
                    ),
                    "document_hash": document_hash,
                }

        return list(
            documents.values()
        )

    def search(
        self,
        query_embedding: list[float],
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievedChunk]:

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

        documents = result.get(
            "documents",
            [[]],
        )[0]

        distances = result.get(
            "distances",
            [[]],
        )[0]

        ids = result.get(
            "ids",
            [[]],
        )[0]

        metadatas = result.get(
            "metadatas",
            [[]],
        )[0]

        retrieved = []

        for index, document in enumerate(
            documents
        ):

            metadata = {}

            if (
                metadatas
                and index < len(metadatas)
            ):
                metadata = (
                    metadatas[index]
                    or {}
                )

            source = str(
                metadata.get(
                    "source",
                    "unknown",
                )
            )

            chunk_id = (
                str(ids[index])
                if ids
                and index < len(ids)
                else str(index)
            )

            distance = (
                float(distances[index])
                if distances
                and index < len(distances)
                else 0.0
            )

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

        if project_id:

            result = self.collection.get(
                where={
                    "project_id": project_id,
                },
            )

            return len(
                result.get(
                    "ids",
                    [],
                )
            )

        return self.collection.count()

    def count_document(
        self,
        project_id: str,
        document_id: str,
    ) -> int:

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not document_id:
            raise ValueError(
                "document_id is required."
            )

        result = self.collection.get(
            where={
                "$and": [
                    {
                        "project_id": project_id,
                    },
                    {
                        "document_id": document_id,
                    },
                ],
            },
        )

        return len(
            result.get(
                "ids",
                [],
            )
        )
