
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

    Handles:

    1. User/project document ingestion.
    2. Text extraction and cleaning.
    3. Named Entity Recognition (NLP).
    4. Chunking.
    5. Embedding generation.
    6. ChromaDB knowledge storage.
    7. Semantic document retrieval.
    8. PostgreSQL planning-data retrieval.
    9. Retrieval-Augmented Generation (RAG).
    10. Groq-based risk analysis.
    11. Evidence-backed structured risk output.
    12. Duplicate-document detection using SHA-256.
    """

    def __init__(self):

        # -----------------------------------------------------
        # 1. Embedding service
        # -----------------------------------------------------

        self.embedding_service = EmbeddingService()

        # -----------------------------------------------------
        # 2. NLP service - Named Entity Recognition
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
        # 4. PostgreSQL planning-data service
        # -----------------------------------------------------
        #
        # PostgreSQL is used for Planning Agent information.
        #
        # If PostgreSQL cannot currently be initialized, the
        # Risk Agent can continue operating with document
        # knowledge, ChromaDB, NLP, RAG and the LLM.
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

            print(
                f"PostgreSQL error: {error}"
            )

            self.postgres_service = None

        # -----------------------------------------------------
        # 5. LLM service
        # -----------------------------------------------------

        self.llm_service = LLMService()

    def ingest_pdf(
        self,
        file_path: str,
        source_name: str,
        project_id: str,
    ) -> dict:

        if not project_id:
            raise ValueError(
                "project_id is required."
            )

        if not source_name:
            raise ValueError(
                "source_name is required."
            )

        # -----------------------------------------------------
        # 1. Calculate SHA-256 hash of the uploaded document
        # -----------------------------------------------------
        #
        # The hash represents the actual file content.
        #
        # This is more reliable than using the filename because
        # two files can have different names but identical
        # contents.
        # -----------------------------------------------------

        with open(
            file_path,
            "rb",
        ) as input_file:

            document_hash = hashlib.sha256(
                input_file.read()
            ).hexdigest()

        # -----------------------------------------------------
        # 2. Check whether the same document already exists
        #    for this project.
        # -----------------------------------------------------

        existing_documents = (
            self.retrieval_service.find_document_by_hash(
                project_id=project_id,
                document_hash=document_hash,
            )
        )

        # -----------------------------------------------------
        # 3. Remove existing chunks for the same document.
        # -----------------------------------------------------
        #
        # This prevents repeated uploads of the same document
        # from creating duplicate knowledge chunks.
        # -----------------------------------------------------

        replaced_documents = []

        for existing_document in existing_documents:

            deleted_chunks = (
                self.retrieval_service.delete_document(
                    project_id=project_id,
                    document_id=(
                        existing_document["document_id"]
                    ),
                )
            )

            replaced_documents.append(
                {
                    "document_id": (
                        existing_document["document_id"]
                    ),
                    "source": (
                        existing_document.get(
                            "source",
                            source_name,
                        )
                    ),
                    "chunks_removed": deleted_chunks,
                }
            )

        # -----------------------------------------------------
        # 4. Create a new document ID
        # -----------------------------------------------------

        document_id = str(
            uuid.uuid4()
        )

        # -----------------------------------------------------
        # 5. Extract PDF text
        # -----------------------------------------------------

        raw_text = extract_text_from_pdf(
            file_path
        )

        # -----------------------------------------------------
        # 6. Clean extracted text
        # -----------------------------------------------------

        cleaned_text = clean_text(
            raw_text
        )

        if not cleaned_text:

            raise ValueError(
                "No extractable text was found in the PDF."
            )

        # -----------------------------------------------------
        # 7. Split the document into chunks
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
        # 8. NLP - Named Entity Recognition
        # -----------------------------------------------------
        #
        # NER is performed separately for every chunk so that
        # extracted entities can be associated with the exact
        # project knowledge chunk where they occur.
        # -----------------------------------------------------

        metadata_list = []

        total_entities = 0

        for index, chunk in enumerate(
            chunks
        ):

            entities = (
                self.nlp_service.extract_entities(
                    chunk
                )
            )

            total_entities += len(
                entities
            )

            entity_strings = []

            for entity in entities:

                entity_strings.append(
                    (
                        f"{entity['text']} "
                        f"({entity['label']}, "
                        f"{entity['score']:.4f})"
                    )
                )

            metadata_list.append(
                {
                    "document_name": source_name,
                    "chunk_index": index,
                    "document_hash": document_hash,
                    "entities": (
                        " | ".join(
                            entity_strings
                        )
                        if entity_strings
                        else ""
                    ),
                }
            )

        # -----------------------------------------------------
        # 9. Generate embeddings
        # -----------------------------------------------------

        embeddings = (
            self.embedding_service.embed_texts(
                chunks
            )
        )

        # -----------------------------------------------------
        # 10. Store chunks, embeddings and NLP metadata
        #     in ChromaDB
        # -----------------------------------------------------

        self.retrieval_service.add_documents(
            embeddings=embeddings,
            chunks=chunks,
            source=source_name,
            project_id=project_id,
            document_id=document_id,
            metadata_list=metadata_list,
        )

        # -----------------------------------------------------
        # 11. Return ingestion information
        # -----------------------------------------------------

        return {
            "project_id": project_id,
            "document_id": document_id,
            "source": source_name,
            "characters_extracted": len(
                cleaned_text
            ),
            "chunks_created": len(
                chunks
            ),
            "entities_extracted": total_entities,
            "document_hash": document_hash,
            "duplicate_documents_replaced": len(
                replaced_documents
            ),
            "replaced_documents": replaced_documents,
            "knowledge_store": "ChromaDB",
            "nlp": {
                "technique": (
                    "Named Entity Recognition"
                ),
                "model": (
                    self.nlp_service.model_name
                ),
            },
        }

    def retrieve_document_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> list:

        if not project_id:

            raise ValueError(
                "project_id is required."
            )

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

    def retrieve_planning_knowledge(
        self,
        project_id: str,
        query: str,
    ):

        if self.postgres_service is None:
            return []

        return (
            self.postgres_service.search_project_knowledge(
                project_id=project_id,
                query=query,
            )
        )

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

        if not project_id:

            raise ValueError(
                "project_id is required."
            )

        # -----------------------------------------------------
        # 1. Retrieve relevant document knowledge
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

            entities = item.metadata.get(
                "entities",
                "",
            )

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
            "\n".join(
                document_evidence_parts
            )
            if document_evidence_parts
            else (
                "No relevant project-document "
                "knowledge was found."
            )
        )

        # -----------------------------------------------------
        # 2. Retrieve Planning Agent knowledge
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
        # 3. Handle information supplied by another agent
        # -----------------------------------------------------

        if not project_information.strip():

            project_information = (
                "No additional current project information "
                "was supplied directly by another agent."
            )

        # -----------------------------------------------------
        # 4. Build RAG prompt
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
        # 5. Send retrieved context to Groq LLM
        # -----------------------------------------------------

        result = self.llm_service.analyse_risks(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
        )

        # -----------------------------------------------------
        # 6. Validate structured response
        # -----------------------------------------------------

        validated_result = (
            RiskAnalysisResult.model_validate(
                result
            )
        )

        # -----------------------------------------------------
        # 7. Record knowledge sources
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

    def search_project_knowledge(
        self,
        project_id: str,
        query: str,
        top_k: int = 5,
    ) -> KnowledgeSearchResult:

        # -----------------------------------------------------
        # 1. Search document knowledge
        # -----------------------------------------------------

        document_results = (
            self.retrieve_document_knowledge(
                project_id=project_id,
                query=query,
                top_k=top_k,
            )
        )

        # -----------------------------------------------------
        # 2. Search Planning Agent knowledge
        # -----------------------------------------------------

        postgres_results = (
            self.retrieve_planning_knowledge(
                project_id=project_id,
                query=query,
            )
        )

        results = []

        # -----------------------------------------------------
        # 3. Add document results
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
        # 4. Add Planning Agent results
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # 5. Return combined knowledge
        # -----------------------------------------------------

        return KnowledgeSearchResult(
            project_id=project_id,
            results=results,
        )
