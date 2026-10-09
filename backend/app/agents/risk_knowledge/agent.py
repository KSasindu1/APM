
import hashlib
import uuid

from app.agents.risk_knowledge.prompts import (
    SYSTEM_PROMPT,
    build_risk_prompt,
)
from app.agents.risk_knowledge.schemas import (
    KnowledgeSearchResult,
    RiskAnalysisResult,
)
from app.core.config import settings
from app.services.chunking_service import split_text_into_chunks
from app.services.document_service import extract_text_from_pdf
from app.services.embedding_service import EmbeddingService
from app.services.llm_service import LLMService
from app.services.nlp_service import NLPService
from app.services.postgres_service import PostgreSQLService
from app.services.retrieval_service import RetrievalService
from app.services.text_service import clean_text


class RiskKnowledgeAgent:
    """
    Risk & Knowledge Retrieval Agent.

    Responsibilities:
    1. PDF document ingestion.
    2. Text extraction and cleaning.
    3. Named Entity Recognition (NER).
    4. Text chunking and embedding generation.
    5. ChromaDB knowledge storage and retrieval.
    6. PostgreSQL Planning Agent knowledge retrieval.
    7. Retrieval-Augmented Generation (RAG).
    8. Groq-based risk analysis.
    9. Evidence-backed structured risk output.
    10. SHA-256 duplicate detection.
    11. Safe replacement of older document versions.
    """

    def __init__(self):
        # -----------------------------------------------------
        # 1. Embedding service
        # -----------------------------------------------------
        self.embedding_service = EmbeddingService()

        # -----------------------------------------------------
        # 2. NLP service
        # -----------------------------------------------------
        self.nlp_service = NLPService()

        # -----------------------------------------------------
        # 3. ChromaDB retrieval service
        # -----------------------------------------------------
        self.retrieval_service = RetrievalService(
            persist_directory=settings.chroma_persist_directory,
            collection_name=settings.chroma_collection_name,
        )

        # -----------------------------------------------------
        # 4. PostgreSQL Planning Agent service
        # -----------------------------------------------------
        # PostgreSQL is optional so document-based knowledge
        # retrieval can continue if database setup is unavailable.
        # -----------------------------------------------------
        try:
            if settings.postgres_database_url:
                self.postgres_service = PostgreSQLService()
            else:
                self.postgres_service = None

        except Exception as error:
            print(
                "Warning: PostgreSQL planning-data service "
                "could not be initialized."
            )
            print(f"PostgreSQL error: {error}")
            self.postgres_service = None

        # -----------------------------------------------------
        # 5. LLM service
        # -----------------------------------------------------
        self.llm_service = LLMService()

    # =========================================================
    # DOCUMENT INGESTION
    # =========================================================

    def ingest_pdf(
        self,
        file_path: str,
        source_name: str,
        project_id: str,
    ) -> dict:
        """
        Extract, process and index a PDF in ChromaDB.

        Replacement behaviour:
        - Identical content with the same filename is not indexed
          a second time.
        - A changed PDF with the same filename replaces the old
          indexed version after the new version is indexed.
        - Documents are replaced only within the specified project.
        - Existing content is not deleted before processing the
          replacement successfully.
        - If adding the new document fails, partial new chunks are
          cleaned up on a best-effort basis.
        """

        # -----------------------------------------------------
        # 1. Validate inputs
        # -----------------------------------------------------
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")

        if not source_name or not source_name.strip():
            raise ValueError("source_name is required.")

        if not file_path or not file_path.strip():
            raise ValueError("file_path is required.")

        project_id = project_id.strip()
        source_name = source_name.strip()

        # -----------------------------------------------------
        # 2. Calculate the uploaded PDF's SHA-256 hash
        # -----------------------------------------------------
        # The hash identifies file content, rather than relying
        # exclusively on the filename.
        # -----------------------------------------------------
        with open(file_path, "rb") as input_file:
            document_hash = hashlib.sha256(
                input_file.read()
            ).hexdigest()

        # -----------------------------------------------------
        # 3. Find existing documents with identical content
        # -----------------------------------------------------
        existing_hash_matches = (
            self.retrieval_service.find_document_by_hash(
                project_id=project_id,
                document_hash=document_hash,
            )
        )

        # -----------------------------------------------------
        # 4. Avoid re-indexing the same content under the same
        #    filename and project
        # -----------------------------------------------------
        # Returning early avoids creating duplicate chunks when
        # the same PDF is uploaded repeatedly.
        # -----------------------------------------------------
        for existing_document in existing_hash_matches:
            if existing_document.get("source") == source_name:
                existing_document_id = existing_document.get(
                    "document_id"
                )

                existing_chunk_count = (
                    self.retrieval_service.count_document(
                        project_id=project_id,
                        document_id=existing_document_id,
                    )
                )

                return {
                    "project_id": project_id,
                    "document_id": existing_document_id,
                    "source": source_name,
                    "characters_extracted": 0,
                    "chunks_created": existing_chunk_count,
                    "entities_extracted": 0,
                    "document_hash": document_hash,
                    "duplicate_documents_replaced": 0,
                    "replaced_documents": [],
                    "duplicate_detected": True,
                    "already_indexed": True,
                    "knowledge_store": "ChromaDB",
                    "nlp": {
                        "technique": "Named Entity Recognition",
                        "model": self.nlp_service.model_name,
                    },
                }

        # -----------------------------------------------------
        # 5. Extract PDF text
        # -----------------------------------------------------
        # No existing ChromaDB documents are deleted here.
        # -----------------------------------------------------
        raw_text = extract_text_from_pdf(file_path)

        # -----------------------------------------------------
        # 6. Clean extracted text
        # -----------------------------------------------------
        cleaned_text = clean_text(raw_text)

        if not cleaned_text or not cleaned_text.strip():
            raise ValueError(
                "No extractable text was found in the PDF."
            )

        # -----------------------------------------------------
        # 7. Split text into overlapping chunks
        # -----------------------------------------------------
        chunks = split_text_into_chunks(
            cleaned_text,
            chunk_size=1000,
            chunk_overlap=200,
        )

        if not chunks:
            raise ValueError(
                "No text chunks were created from the PDF."
            )

        # -----------------------------------------------------
        # 8. Perform Named Entity Recognition on each chunk
        # -----------------------------------------------------
        metadata_list = []
        total_entities = 0

        for index, chunk in enumerate(chunks):
            entities = self.nlp_service.extract_entities(chunk)
            total_entities += len(entities)

            entity_strings = []

            for entity in entities:
                entity_strings.append(
                    f"{entity['text']} "
                    f"({entity['label']}, "
                    f"{entity['score']:.4f})"
                )

            metadata_list.append(
                {
                    "document_name": source_name,
                    "chunk_index": index,
                    "document_hash": document_hash,
                    "entities": (
                        " | ".join(entity_strings)
                        if entity_strings
                        else ""
                    ),
                }
            )

        # -----------------------------------------------------
        # 9. Generate embeddings
        # -----------------------------------------------------
        embeddings = self.embedding_service.embed_texts(chunks)

        # -----------------------------------------------------
        # 10. Create an ID for the new document
        # -----------------------------------------------------
        document_id = str(uuid.uuid4())

        # -----------------------------------------------------
        # 11. Index the new document BEFORE deleting old versions
        # -----------------------------------------------------
        # If indexing raises an exception, attempt to remove any
        # partially indexed chunks belonging to this new ID.
        # Existing documents are left untouched in that case.
        # -----------------------------------------------------
        try:
            self.retrieval_service.add_documents(
                embeddings=embeddings,
                chunks=chunks,
                source=source_name,
                project_id=project_id,
                document_id=document_id,
                metadata_list=metadata_list,
            )

        except Exception:
            try:
                self.retrieval_service.delete_document(
                    project_id=project_id,
                    document_id=document_id,
                )
            except Exception as cleanup_error:
                print(
                    "Warning: Could not completely clean up "
                    "partially indexed document "
                    f"{document_id}: {cleanup_error}"
                )

            raise

        # -----------------------------------------------------
        # 12. Find older versions with the same filename
        # -----------------------------------------------------
        # This method groups indexed chunks into documents.
        # It is project-scoped and matches the source filename.
        # -----------------------------------------------------
        old_source_documents = (
            self.retrieval_service.find_documents_by_source(
                project_id=project_id,
                source=source_name,
            )
        )

        # -----------------------------------------------------
        # 13. Build the list of old versions to remove
        # -----------------------------------------------------
        # Also include identical-content matches under a different
        # filename to retain the original content-deduplication
        # behaviour. Never include the newly indexed document.
        # -----------------------------------------------------
        documents_to_replace = {}

        for old_document in old_source_documents:
            old_id = old_document.get("document_id")

            if old_id and old_id != document_id:
                documents_to_replace[old_id] = old_document

        for old_document in existing_hash_matches:
            old_id = old_document.get("document_id")

            if old_id and old_id != document_id:
                documents_to_replace[old_id] = old_document

        # -----------------------------------------------------
        # 14. Remove old versions only after successful indexing
        # -----------------------------------------------------
        replaced_documents = []

        for old_id, old_document in documents_to_replace.items():
            removed_chunks = self.retrieval_service.delete_document(
                project_id=project_id,
                document_id=old_id,
            )

            replaced_documents.append(
                {
                    "document_id": old_id,
                    "source": old_document.get(
                        "source",
                        source_name,
                    ),
                    "chunks_removed": removed_chunks,
                }
            )

        # -----------------------------------------------------
        # 15. Return ingestion information
        # -----------------------------------------------------
        return {
            "project_id": project_id,
            "document_id": document_id,
            "source": source_name,
            "characters_extracted": len(cleaned_text),
            "chunks_created": len(chunks),
            "entities_extracted": total_entities,
            "document_hash": document_hash,
            "duplicate_documents_replaced": len(
                replaced_documents
            ),
            "replaced_documents": replaced_documents,
            "duplicate_detected": bool(existing_hash_matches),
            "already_indexed": False,
            "knowledge_store": "ChromaDB",
            "nlp": {
                "technique": "Named Entity Recognition",
                "model": self.nlp_service.model_name,
            },
        }

    # =========================================================
    # DOCUMENT KNOWLEDGE RETRIEVAL
    # =========================================================

    def retrieve_document_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> list:
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")

        if not query or not query.strip():
            raise ValueError("query is required.")

        if top_k < 1:
            raise ValueError("top_k must be at least 1.")

        query_embedding = self.embedding_service.embed_query(query)

        return self.retrieval_service.search(
            query_embedding=query_embedding,
            project_id=project_id,
            top_k=top_k,
        )

    # =========================================================
    # PLANNING AGENT KNOWLEDGE RETRIEVAL
    # =========================================================

    def retrieve_planning_knowledge(
        self,
        project_id: str,
        query: str,
    ):
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")

        if not query or not query.strip():
            raise ValueError("query is required.")

        if self.postgres_service is None:
            return []

        return self.postgres_service.search_project_knowledge(
            project_id=project_id,
            query=query,
        )

    # =========================================================
    # PROJECT RISK ANALYSIS
    # =========================================================

    def analyse_project(
        self,
        project_id: str,
        project_information: str = "",
        query: str = (
            "Identify the current project risks, "
            "planning issues, dependencies, "
            "resource constraints, and missing information."
        ),
        top_k: int = 5,
    ) -> RiskAnalysisResult:
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")

        # -----------------------------------------------------
        # 1. Retrieve relevant document knowledge
        # -----------------------------------------------------
        document_results = self.retrieve_document_knowledge(
            project_id=project_id,
            query=query,
            top_k=top_k,
        )

        document_evidence_parts = []

        for item in document_results:
            entities = item.metadata.get("entities", "")

            document_evidence_parts.append(
                f"""
SOURCE: {item.source}
KNOWLEDGE TYPE: Project Document
RELEVANCE SCORE: {item.score:.4f}
CHUNK ID: {item.chunk_id}
DOCUMENT ID: {item.metadata.get("document_id", "unknown")}
DOCUMENT HASH: {item.metadata.get("document_hash", "unknown")}
NAMED ENTITIES: {entities or "None identified"}

CONTENT:
{item.text}
"""
            )

        retrieved_evidence = (
            "\n".join(document_evidence_parts)
            if document_evidence_parts
            else "No relevant project-document knowledge was found."
        )

        # -----------------------------------------------------
        # 2. Retrieve Planning Agent knowledge from PostgreSQL
        # -----------------------------------------------------
        planning_results = self.retrieve_planning_knowledge(
            project_id=project_id,
            query=query,
        )

        planning_information_parts = []

        for item in planning_results:
            planning_information_parts.append(
                f"""
SOURCE TABLE: {item.table_name}
RECORD ID: {item.record_id}
KNOWLEDGE TYPE: Planning Agent / PostgreSQL

CONTENT:
{item.content}
"""
            )

        planning_database_information = (
            "\n".join(planning_information_parts)
            if planning_information_parts
            else (
                "No matching Planning Agent information "
                "was found in PostgreSQL."
            )
        )

        # -----------------------------------------------------
        # 3. Handle information supplied by another agent
        # -----------------------------------------------------
        if not project_information.strip():
            project_information = (
                "No additional current project information "
                "was supplied directly by another agent."
            )

        # -----------------------------------------------------
        # 4. Build the RAG prompt
        # -----------------------------------------------------
        prompt = build_risk_prompt(
            project_id=project_id,
            project_information=project_information,
            retrieved_evidence=retrieved_evidence,
            planning_database_information=(
                planning_database_information
            ),
        )

        # -----------------------------------------------------
        # 5. Ask the Groq LLM to analyse project risks
        # -----------------------------------------------------
        result = self.llm_service.analyse_risks(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        # -----------------------------------------------------
        # 6. Validate the structured response
        # -----------------------------------------------------
        validated_result = RiskAnalysisResult.model_validate(result)

        # -----------------------------------------------------
        # 7. Record the knowledge sources used
        # -----------------------------------------------------
        knowledge_sources = []

        for item in document_results:
            source_name = f"Document: {item.source}"

            if source_name not in knowledge_sources:
                knowledge_sources.append(source_name)

        for item in planning_results:
            source_name = f"PostgreSQL table: {item.table_name}"

            if source_name not in knowledge_sources:
                knowledge_sources.append(source_name)

        validated_result.knowledge_sources = knowledge_sources

        return validated_result

    # =========================================================
    # COMBINED DOCUMENT AND PLANNING KNOWLEDGE SEARCH
    # =========================================================

    def search_project_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> KnowledgeSearchResult:
        if not project_id or not project_id.strip():
            raise ValueError("project_id is required.")

        # -----------------------------------------------------
        # 1. Search document knowledge in ChromaDB
        # -----------------------------------------------------
        document_results = self.retrieve_document_knowledge(
            project_id=project_id,
            query=query,
            top_k=top_k,
        )

        # -----------------------------------------------------
        # 2. Search Planning Agent knowledge in PostgreSQL
        # -----------------------------------------------------
        postgres_results = self.retrieve_planning_knowledge(
            project_id=project_id,
            query=query,
        )

        results = []

        # -----------------------------------------------------
        # 3. Add document search results
        # -----------------------------------------------------
        for item in document_results:
            results.append(
                {
                    "knowledge_type": "document",
                    "source": item.source,
                    "chunk_id": item.chunk_id,
                    "score": item.score,
                    "content": item.text,
                    "metadata": item.metadata,
                }
            )

        # -----------------------------------------------------
        # 4. Add Planning Agent search results
        # -----------------------------------------------------
        for item in postgres_results:
            results.append(
                {
                    "knowledge_type": "planning_agent",
                    "source": f"PostgreSQL:{item.table_name}",
                    "record_id": item.record_id,
                    "content": item.content,
                    "metadata": item.metadata,
                }
            )

        # -----------------------------------------------------
        # 5. Return combined results
        # -----------------------------------------------------
        return KnowledgeSearchResult(
            project_id=project_id,
            results=results,
        )
