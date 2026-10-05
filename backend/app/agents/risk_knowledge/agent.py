from pathlib import Path

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
from app.services.postgres_service import PostgreSQLService
from app.services.retrieval_service import RetrievalService
from app.services.text_service import clean_text


class RiskKnowledgeAgent:
    """
    Risk & Knowledge Retrieval Agent.

    Handles:

    1. User/project document ingestion.
    2. Text extraction and cleaning.
    3. Chunking.
    4. Embedding generation.
    5. ChromaDB knowledge storage.
    6. Semantic document retrieval.
    7. PostgreSQL planning-data retrieval.
    8. RAG.
    9. Groq-based risk analysis.
    10. Evidence-backed structured risk output.
    """

    def __init__(self):

        self.embedding_service = EmbeddingService()

        self.retrieval_service = RetrievalService(
            persist_directory=settings.chroma_persist_directory,
            collection_name=settings.chroma_collection_name,
        )

        self.postgres_service = PostgreSQLService()

        self.llm_service = LLMService()

    # ---------------------------------------------------------
    # DOCUMENT INGESTION
    # ---------------------------------------------------------

    def ingest_pdf(
        self,
        file_path: str,
        source_name: str,
        project_id: str,
    ) -> dict:
        """
        Process an uploaded project PDF and store its knowledge
        in ChromaDB.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        raw_text = extract_text_from_pdf(
            file_path
        )

        cleaned_text = clean_text(
            raw_text
        )

        if not cleaned_text:
            raise ValueError(
                "No extractable text was found in the PDF."
            )

        chunks = split_text_into_chunks(
            cleaned_text,
            chunk_size=1000,
            chunk_overlap=200,
        )

        embeddings = (
            self.embedding_service.embed_texts(
                chunks
            )
        )

        metadata_list = [
            {
                "document_name": source_name,
                "chunk_index": index,
            }
            for index in range(len(chunks))
        ]

        self.retrieval_service.add_documents(
            embeddings=embeddings,
            chunks=chunks,
            source=source_name,
            project_id=project_id,
            metadata_list=metadata_list,
        )

        return {
            "project_id": project_id,
            "source": source_name,
            "characters_extracted": len(
                cleaned_text
            ),
            "chunks_created": len(chunks),
            "knowledge_store": "ChromaDB",
        }

    # ---------------------------------------------------------
    # DOCUMENT KNOWLEDGE RETRIEVAL
    # ---------------------------------------------------------

    def retrieve_document_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> list:

        query_embedding = (
            self.embedding_service.embed_query(
                query
            )
        )

        return self.retrieval_service.search(
            query_embedding=query_embedding,
            project_id=project_id,
            top_k=top_k,
        )

    # ---------------------------------------------------------
    # POSTGRESQL PLANNING KNOWLEDGE
    # ---------------------------------------------------------

    def retrieve_planning_knowledge(
        self,
        project_id: str,
        query: str,
    ):

        return self.postgres_service.search_project_knowledge(
            project_id=project_id,
            query=query,
        )

    # ---------------------------------------------------------
    # COMPLETE RISK ANALYSIS
    # ---------------------------------------------------------

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
        """
        Complete Risk & Knowledge workflow.

        Information sources:

        ChromaDB:
            Uploaded project documents.

        PostgreSQL:
            Project plans created by the Planning Agent.

        Current project information:
            Information supplied directly by another agent.
        """

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        # -----------------------------------------------------
        # 1. Retrieve document knowledge from ChromaDB
        # -----------------------------------------------------

        document_results = (
            self.retrieve_document_knowledge(
                project_id=project_id,
                query=query,
                top_k=top_k,
            )
        )

        document_evidence_parts = []

        for item in document_results:

            document_evidence_parts.append(
                f"""
SOURCE: {item.source}
KNOWLEDGE TYPE: Project Document
RELEVANCE SCORE: {item.score:.4f}
CHUNK ID: {item.chunk_id}

CONTENT:
{item.text}
"""
            )

        retrieved_evidence = (
            "\n".join(
                document_evidence_parts
            )
            if document_evidence_parts
            else "No relevant project-document knowledge was found."
        )

        # -----------------------------------------------------
        # 2. Retrieve Planning Agent information from PostgreSQL
        # -----------------------------------------------------

        planning_results = (
            self.retrieve_planning_knowledge(
                project_id=project_id,
                query=query,
            )
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
            "\n".join(
                planning_information_parts
            )
            if planning_information_parts
            else (
                "No matching Planning Agent information "
                "was found in PostgreSQL."
            )
        )

        # -----------------------------------------------------
        # 3. Combine current plan information
        # -----------------------------------------------------

        if not project_information.strip():
            project_information = (
                "No additional current project information "
                "was supplied directly by another agent."
            )

        # -----------------------------------------------------
        # 4. Build grounded RAG prompt
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
        # 5. Send evidence + plan information to Groq
        # -----------------------------------------------------

        result = self.llm_service.analyse_risks(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        validated_result = (
            RiskAnalysisResult.model_validate(
                result
            )
        )

        # -----------------------------------------------------
        # 6. Add knowledge-source information
        # -----------------------------------------------------

        knowledge_sources = []

        for item in document_results:
            source_name = (
                f"Document: {item.source}"
            )

            if source_name not in knowledge_sources:
                knowledge_sources.append(
                    source_name
                )

        for item in planning_results:
            source_name = (
                f"PostgreSQL table: "
                f"{item.table_name}"
            )

            if source_name not in knowledge_sources:
                knowledge_sources.append(
                    source_name
                )

        validated_result.knowledge_sources = (
            knowledge_sources
        )

        return validated_result

    # ---------------------------------------------------------
    # KNOWLEDGE SEARCH WITHOUT LLM
    # ---------------------------------------------------------

    def search_project_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> KnowledgeSearchResult:
        """
        Search both:

        - ChromaDB project documents
        - PostgreSQL Planning Agent data

        without asking the LLM to analyse the result.

        This is useful for knowledge retrieval itself.
        """

        document_results = (
            self.retrieve_document_knowledge(
                project_id=project_id,
                query=query,
                top_k=top_k,
            )
        )

        postgres_results = (
            self.retrieve_planning_knowledge(
                project_id=project_id,
                query=query,
            )
        )

        results = []

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

        for item in postgres_results:

            results.append(
                {
                    "knowledge_type": "planning_agent",
                    "source": (
                        f"PostgreSQL:"
                        f"{item.table_name}"
                    ),
                    "record_id": item.record_id,
                    "content": item.content,
                    "metadata": item.metadata,
                }
            )

        return KnowledgeSearchResult(
            project_id=project_id,
            results=results,
        )