from pathlib import Path
import uuid

from fastapi import (
    APIRouter,
    File,
    HTTPException,
    UploadFile,
)

from app.agents.risk_knowledge.agent import (
    RiskKnowledgeAgent,
)
from app.agents.risk_knowledge.schemas import (
    ProjectKnowledgeSearchRequest,
    ProjectPlanAnalysisRequest,
)
from app.communication.messages import (
    KnowledgeSearchMessage,
    ProjectPlanMessage,
    RiskRequestMessage,
)
from app.communication.protocol import (
    RiskAgentProtocol,
)
from app.core.config import settings


router = APIRouter(
    prefix="/api/risk",
    tags=["Risk & Knowledge Agent"],
)


# One agent instance is shared by this API router.
# ChromaDB itself is persistent, so knowledge survives
# application restarts.
agent = RiskKnowledgeAgent()

protocol = RiskAgentProtocol(
    agent=agent
)


# ---------------------------------------------------------
# DOCUMENT UPLOAD
# ---------------------------------------------------------

@router.post("/documents")
async def upload_document(
    project_id: str,
    file: UploadFile = File(...),
):
    """
    Upload a project document for knowledge ingestion.

    Current implementation supports PDF documents.
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

    original_filename = Path(
        file.filename
    ).name

    if not original_filename.lower().endswith(
        ".pdf"
    ):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are supported.",
        )

    content = await file.read()

    max_size = (
        settings.max_upload_size_mb
        * 1024
        * 1024
    )

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

    documents_dir = Path(
        settings.upload_directory
    )

    documents_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    unique_name = (
        f"{uuid.uuid4()}_"
        f"{original_filename}"
    )

    file_path = (
        documents_dir
        / unique_name
    )

    try:

        with open(
            file_path,
            "wb",
        ) as output_file:
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
        )

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "An error occurred while "
                "processing the project document."
            ),
        )

    finally:

        if file_path.exists():
            file_path.unlink()


# ---------------------------------------------------------
# KNOWLEDGE SEARCH
# ---------------------------------------------------------

@router.post("/knowledge/search")
async def search_knowledge(
    request: ProjectKnowledgeSearchRequest,
):
    """
    Search project knowledge from:

    - ChromaDB project documents
    - PostgreSQL Planning Agent information

    No LLM analysis is performed here.
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
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to search project knowledge."
            ),
        )


# ---------------------------------------------------------
# FULL RISK ANALYSIS
# ---------------------------------------------------------

@router.post("/analyse")
async def analyse_risks(
    request: RiskRequestMessage,
):
    """
    Perform evidence-backed risk analysis using:

    - project documents from ChromaDB
    - Planning Agent information from PostgreSQL
    - current project information supplied by another agent
    - Groq LLM
    """

    try:

        result = protocol.process_request(
            request
        )

        return result.model_dump()

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to analyse project risks."
            ),
        )


# ---------------------------------------------------------
# PLANNING AGENT PLAN ANALYSIS
# ---------------------------------------------------------

@router.post("/analyse-plan")
async def analyse_planning_agent_plan(
    request: ProjectPlanAnalysisRequest,
):
    """
    Analyse project-plan information created by the
    Planning & Scheduling Agent.

    The Risk Agent retrieves the corresponding planning
    information from PostgreSQL and combines it with
    ChromaDB project knowledge.
    """

    try:

        result = agent.analyse_project(
            project_id=request.project_id,
            project_information=(
                request.project_information
            ),
            query=request.query,
            top_k=request.top_k,
        )

        return result.model_dump()

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to analyse the project plan."
            ),
        )


# ---------------------------------------------------------
# DIRECT PLAN MESSAGE FROM PLANNING AGENT
# ---------------------------------------------------------

@router.post("/agent/plan")
async def receive_plan_from_planning_agent(
    message: ProjectPlanMessage,
):
    """
    Communication endpoint for the Planning Agent.

    The Planning Agent can send a newly created plan directly
    to the Risk & Knowledge Agent.

    The Risk Agent will:
    1. receive the plan,
    2. combine it with existing project knowledge,
    3. retrieve relevant PostgreSQL information,
    4. retrieve relevant ChromaDB information,
    5. perform RAG,
    6. analyse risks.
    """

    try:

        result = protocol.process_plan(
            message
        )

        return result.model_dump()

    except ValueError as error:

        raise HTTPException(
            status_code=400,
            detail=str(error),
        )

    except Exception:

        raise HTTPException(
            status_code=500,
            detail=(
                "Unable to process the "
                "Planning Agent message."
            ),
        )