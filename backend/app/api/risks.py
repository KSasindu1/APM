
import logging
import uuid
from pathlib import Path

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
)

from app.agents.risk_knowledge.agent import RiskKnowledgeAgent
from app.agents.risk_knowledge.schemas import (
    ProjectKnowledgeSearchRequest,
    ProjectPlanAnalysisRequest,
    RiskAnalysisResult,
)
from app.communication.messages import (
    ProjectPlanMessage,
    RiskRequestMessage,
)
from app.communication.protocol import RiskAgentProtocol
from app.core.config import settings
from app.core.security import require_admin


router = APIRouter(
    prefix="/api/risk",
    tags=["Risk & Knowledge Agent"],
)

logger = logging.getLogger(__name__)

# One shared agent instance for this API router.
# Existing ChromaDB data remains persistent.
agent = RiskKnowledgeAgent()

protocol = RiskAgentProtocol(
    agent=agent,
)


# ---------------------------------------------------------
# PLANNING AGENT CALLBACK
# ---------------------------------------------------------

async def send_risk_results_to_planning(
    result: RiskAnalysisResult,
) -> None:
    """
    Send completed risk analysis to the Planning Agent.

    Callback failures are logged without discarding the
    completed analysis or changing the existing API response.
    """

    headers = {}

    # Include the configured API key if the receiving endpoint
    # uses the same key for authentication.
    if settings.planning_api_key:
        headers["X-API-Key"] = settings.planning_api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                settings.planning_risk_results_url,
                json=result.model_dump(mode="json"),
                headers=headers,
            )

            response.raise_for_status()

        logger.info(
            "Risk results delivered to Planning Agent for project %s.",
            result.project_id,
        )

    except httpx.HTTPError:
        logger.exception(
            "Risk analysis completed for project %s, "
            "but delivery to the Planning Agent failed.",
            result.project_id,
        )


# ---------------------------------------------------------
# DOCUMENT UPLOAD
# ---------------------------------------------------------

@router.post("/documents")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(require_admin),
):
    """
    Upload a project PDF for knowledge ingestion.

    Only authenticated administrators can upload documents.
    """

    if not project_id.strip():
        raise HTTPException(
            status_code=400,
            detail="project_id is required.",
        )

    if not file.filename:
        raise HTTPException(
            status_code=400,
            detail="A file is required.",
        )

    original_filename = Path(file.filename).name

    if not original_filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported.",
        )

    content = await file.read()

    max_size = settings.max_upload_size_mb * 1024 * 1024

    if not content:
        raise HTTPException(
            status_code=400,
            detail="The uploaded file is empty.",
        )

    if len(content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=(
                "File exceeds the "
                f"{settings.max_upload_size_mb} MB limit."
            ),
        )

    documents_dir = Path(settings.upload_directory)
    documents_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_name = f"{uuid.uuid4()}_{original_filename}"
    file_path = documents_dir / unique_name

    try:
        with open(file_path, "wb") as output_file:
            output_file.write(content)

        result = agent.ingest_pdf(
            file_path=str(file_path),
            source_name=original_filename,
            project_id=project_id,
        )

        return {
            "message": (
                "Project document processed "
                "and added to ChromaDB."
            ),
            "result": result,
        }

    except FileNotFoundError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception("Unable to process uploaded project document.")

        raise HTTPException(
            status_code=500,
            detail=(
                "An error occurred while "
                "processing the project document."
            ),
        ) from error

    finally:
        if file_path.exists():
            file_path.unlink()


# ---------------------------------------------------------
# KNOWLEDGE SEARCH
# ---------------------------------------------------------

@router.post("/knowledge/search")
async def search_knowledge(
    request: ProjectKnowledgeSearchRequest,
    admin: dict = Depends(require_admin),
):
    """
    Search project knowledge using the existing Risk Agent.
    No LLM analysis is performed by this endpoint.
    """

    try:
        result = agent.search_project_knowledge(
            project_id=request.project_id,
            query=request.query,
            top_k=request.top_k,
        )

        return result.model_dump()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception("Unable to search project knowledge.")

        raise HTTPException(
            status_code=500,
            detail="Unable to search project knowledge.",
        ) from error


# ---------------------------------------------------------
# FULL RISK ANALYSIS
# ---------------------------------------------------------

@router.post("/analyse")
async def analyse_risks(
    request: RiskRequestMessage,
):
    """
    Perform risk analysis using the existing Risk Agent workflow.

    The response format is unchanged for existing agent callers.
    """

    try:
        result = protocol.process_request(request)
        return result.model_dump()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception("Unable to analyse project risks.")

        raise HTTPException(
            status_code=500,
            detail="Unable to analyse project risks.",
        ) from error


# ---------------------------------------------------------
# DIRECT PLAN ANALYSIS
# ---------------------------------------------------------

@router.post("/analyse-plan")
async def analyse_planning_agent_plan(
    request: ProjectPlanAnalysisRequest,
):
    """
    Analyse project-plan information provided in the request.

    Uses the existing Risk Agent analysis workflow.
    """

    try:
        result = agent.analyse_project(
            project_id=request.project_id,
            project_information=request.project_information,
            query=request.query,
            top_k=request.top_k,
        )

        return result.model_dump()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception("Unable to analyse the project plan.")

        raise HTTPException(
            status_code=500,
            detail="Unable to analyse the project plan.",
        ) from error


# ---------------------------------------------------------
# PLANNING AGENT -> RISK AGENT -> PLANNING AGENT
# ---------------------------------------------------------

@router.post("/agent/plan")
async def receive_plan_from_planning_agent(
    message: ProjectPlanMessage,
):
    """
    Receive a project plan from the Planning Agent, analyse
    it, send the risk results to the Planning Agent callback,
    and return the analysis to the original caller.

    Existing response format:
        RiskAnalysisResult.model_dump()
    """

    try:
        # 1. Analyse the plan using the existing Risk Agent.
        result = protocol.process_plan(message)

        # 2. Send the analysis to the Planning Agent callback.
        # Delivery errors are logged by the helper.
        await send_risk_results_to_planning(result)

        # 3. Preserve the existing API response format.
        return result.model_dump()

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception(
            "Unable to process the Planning Agent message "
            "for project %s.",
            message.project_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to process the Planning Agent message.",
        ) from error


# ---------------------------------------------------------
# LIST INDEXED PROJECT DOCUMENTS
# ---------------------------------------------------------

@router.get("/knowledge/documents")
def list_indexed_documents(
    project_id: str,
    admin: dict = Depends(require_admin),
):
    """
    List documents indexed in ChromaDB for one project,
    including their stored chunk counts.
    """

    project_id = project_id.strip()

    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id is required.",
        )

    collection = agent.retrieval_service.collection

    stored = collection.get(
        where={"project_id": project_id},
        include=["metadatas"],
    )

    ids = stored.get("ids", [])
    metadatas = stored.get("metadatas") or []

    documents = {}

    for index, chunk_id in enumerate(ids):
        metadata = (
            metadatas[index] or {}
            if index < len(metadatas)
            else {}
        )

        document_id = metadata.get("document_id")

        if not document_id:
            continue

        if document_id not in documents:
            documents[document_id] = {
                "document_id": document_id,
                "project_id": project_id,
                "name": metadata.get(
                    "document_name",
                    metadata.get("source", "Unknown document"),
                ),
                "source": metadata.get(
                    "source",
                    "Unknown document",
                ),
                "document_hash": metadata.get("document_hash"),
                "chunks_count": 0,
            }

        documents[document_id]["chunks_count"] += 1

    return {
        "project_id": project_id,
        "total_documents": len(documents),
        "total_chunks": len(ids),
        "documents": sorted(
            documents.values(),
            key=lambda item: item["name"].lower(),
        ),
    }


# ---------------------------------------------------------
# VIEW CHUNKS FOR ONE INDEXED DOCUMENT
# ---------------------------------------------------------

@router.get("/knowledge/documents/{document_id}/chunks")
def list_document_chunks(
    document_id: str,
    project_id: str,
    admin: dict = Depends(require_admin),
):
    """
    Return stored text and metadata for one document.

    Both project_id and document_id are used to prevent
    cross-project access.
    """

    project_id = project_id.strip()
    document_id = document_id.strip()

    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id is required.",
        )

    if not document_id:
        raise HTTPException(
            status_code=400,
            detail="document_id is required.",
        )

    collection = agent.retrieval_service.collection

    stored = collection.get(
        where={
            "$and": [
                {"project_id": project_id},
                {"document_id": document_id},
            ]
        },
        include=["documents", "metadatas"],
    )

    ids = stored.get("ids", [])
    texts = stored.get("documents") or []
    metadatas = stored.get("metadatas") or []

    if not ids:
        raise HTTPException(
            status_code=404,
            detail=(
                "No indexed chunks were found for "
                "this document and project."
            ),
        )

    chunks = []

    for index, chunk_id in enumerate(ids):
        metadata = (
            metadatas[index] or {}
            if index < len(metadatas)
            else {}
        )

        chunks.append(
            {
                "chunk_id": chunk_id,
                "chunk_index": metadata.get("chunk_index", index),
                "text": texts[index] if index < len(texts) else "",
                "metadata": metadata,
            }
        )

    chunks.sort(key=lambda item: item["chunk_index"])

    return {
        "project_id": project_id,
        "document_id": document_id,
        "total_chunks": len(chunks),
        "chunks": chunks,
    }


# ---------------------------------------------------------
# DELETE AN INDEXED PROJECT DOCUMENT
# ---------------------------------------------------------

@router.delete("/knowledge/documents/{document_id}")
def delete_indexed_document(
    document_id: str,
    project_id: str,
    admin: dict = Depends(require_admin),
):
    """
    Delete one indexed document from one project.

    Only authenticated administrators can delete documents.
    Both identifiers are required to prevent cross-project deletion.
    """

    project_id = project_id.strip()
    document_id = document_id.strip()

    if not project_id:
        raise HTTPException(
            status_code=400,
            detail="project_id is required.",
        )

    if not document_id:
        raise HTTPException(
            status_code=400,
            detail="document_id is required.",
        )

    try:
        retrieval = agent.retrieval_service

        # Confirm the document exists in this project.
        existing_chunks = retrieval.count_document(
            project_id=project_id,
            document_id=document_id,
        )

        if existing_chunks == 0:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No indexed document was found "
                    "for this project."
                ),
            )

        # Delete only chunks matching both identifiers.
        deleted_chunks = retrieval.delete_document(
            project_id=project_id,
            document_id=document_id,
        )

        if deleted_chunks == 0:
            raise HTTPException(
                status_code=404,
                detail=(
                    "The document was not found "
                    "or was already deleted."
                ),
            )

        return {
            "message": "Indexed document deleted successfully.",
            "project_id": project_id,
            "document_id": document_id,
            "deleted_chunks": deleted_chunks,
        }

    except HTTPException:
        raise

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error

    except Exception as error:
        logger.exception(
            "Unable to delete indexed document %s.",
            document_id,
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to delete the indexed document.",
        ) from error