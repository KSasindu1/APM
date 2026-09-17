# ============================================================
# APM - Agentic Project Management System
# Initial Project Structure Setup
#
# This script creates ONLY the custom project structure.
# React/Vite frontend will be created separately using Vite.
#
# Stack:
#   Backend  : Python + FastAPI
#   Frontend : React + Vite
#   Database : SQLite initially
#   AI       : LLM + RAG
#   Agents   : 4
#
# Docker is NOT included.
# Environment files are NOT created.
# Dependencies are NOT installed.
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " APM - Agentic Project Management System" -ForegroundColor Cyan
Write-Host " Initial Project Structure Setup" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# ============================================================
# BACKEND DIRECTORIES
# ============================================================

Write-Host "Creating backend structure..." -ForegroundColor Yellow

$backendFolders = @(

    "backend",
    "backend/app",

    # Agents
    "backend/app/agents",
    "backend/app/agents/coordinator",
    "backend/app/agents/planning",
    "backend/app/agents/risk_knowledge",
    "backend/app/agents/monitoring",

    # API
    "backend/app/api",

    # Agent communication
    "backend/app/communication",

    # Shared services
    "backend/app/services",

    # Database
    "backend/app/database",

    # Core configuration and security
    "backend/app/core",

    # Utilities
    "backend/app/utils",

    # Agent orchestration
    "backend/app/orchestration",

    # Tests
    "backend/tests"
)

foreach ($folder in $backendFolders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

Write-Host "[OK] Backend directories created" -ForegroundColor Green


# ============================================================
# PYTHON PACKAGE FILES
# ============================================================

$pythonInitFiles = @(

    "backend/app/__init__.py",

    "backend/app/agents/__init__.py",

    "backend/app/agents/coordinator/__init__.py",
    "backend/app/agents/planning/__init__.py",
    "backend/app/agents/risk_knowledge/__init__.py",
    "backend/app/agents/monitoring/__init__.py",

    "backend/app/api/__init__.py",

    "backend/app/communication/__init__.py",

    "backend/app/services/__init__.py",

    "backend/app/database/__init__.py",

    "backend/app/core/__init__.py",

    "backend/app/utils/__init__.py",

    "backend/app/orchestration/__init__.py"
)

foreach ($file in $pythonInitFiles) {
    New-Item -ItemType File -Force -Path $file | Out-Null
}

Write-Host "[OK] Python package files created" -ForegroundColor Green


# ============================================================
# BACKEND SOURCE FILES
# ============================================================

$backendFiles = @(

    # Main application
    "backend/app/main.py",

    # --------------------------------------------------------
    # Coordinator / Decision Agent
    # --------------------------------------------------------

    "backend/app/agents/coordinator/agent.py",
    "backend/app/agents/coordinator/prompts.py",
    "backend/app/agents/coordinator/schemas.py",

    # --------------------------------------------------------
    # Planning & Scheduling Agent
    # --------------------------------------------------------

    "backend/app/agents/planning/agent.py",
    "backend/app/agents/planning/prompts.py",
    "backend/app/agents/planning/schemas.py",

    # --------------------------------------------------------
    # Risk & Knowledge Retrieval Agent
    # --------------------------------------------------------

    "backend/app/agents/risk_knowledge/agent.py",
    "backend/app/agents/risk_knowledge/prompts.py",
    "backend/app/agents/risk_knowledge/schemas.py",

    # --------------------------------------------------------
    # Monitoring & Reporting Agent
    # --------------------------------------------------------

    "backend/app/agents/monitoring/agent.py",
    "backend/app/agents/monitoring/prompts.py",
    "backend/app/agents/monitoring/schemas.py",

    # --------------------------------------------------------
    # API
    # --------------------------------------------------------

    "backend/app/api/chat.py",
    "backend/app/api/projects.py",
    "backend/app/api/tasks.py",
    "backend/app/api/risks.py",
    "backend/app/api/reports.py",

    # --------------------------------------------------------
    # Agent Communication
    # --------------------------------------------------------

    "backend/app/communication/messages.py",
    "backend/app/communication/protocol.py",

    # --------------------------------------------------------
    # Services
    # --------------------------------------------------------

    "backend/app/services/llm_service.py",
    "backend/app/services/retrieval_service.py",
    "backend/app/services/embedding_service.py",
    "backend/app/services/security_service.py",

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    "backend/app/database/database.py",
    "backend/app/database/models.py",
    "backend/app/database/schemas.py",

    # --------------------------------------------------------
    # Core
    # --------------------------------------------------------

    "backend/app/core/config.py",
    "backend/app/core/security.py",

    # --------------------------------------------------------
    # Utilities
    # --------------------------------------------------------

    "backend/app/utils/helpers.py",

    # --------------------------------------------------------
    # Orchestration
    # --------------------------------------------------------

    "backend/app/orchestration/workflow.py",

    # --------------------------------------------------------
    # Tests
    # --------------------------------------------------------

    "backend/tests/test_coordinator.py",
    "backend/tests/test_planning.py",
    "backend/tests/test_risk_knowledge.py",
    "backend/tests/test_monitoring.py",
    "backend/tests/test_api.py"
)

foreach ($file in $backendFiles) {
    New-Item -ItemType File -Force -Path $file | Out-Null
}

Write-Host "[OK] Backend files created" -ForegroundColor Green


# ============================================================
# DATA DIRECTORIES
# ============================================================

Write-Host "Creating data structure..." -ForegroundColor Yellow

$dataFolders = @(
    "data",
    "data/documents",
    "data/knowledge_base",
    "data/sample_projects"
)

foreach ($folder in $dataFolders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

New-Item -ItemType File -Force -Path "data/documents/.gitkeep" | Out-Null
New-Item -ItemType File -Force -Path "data/knowledge_base/.gitkeep" | Out-Null
New-Item -ItemType File -Force -Path "data/sample_projects/.gitkeep" | Out-Null

Write-Host "[OK] Data structure created" -ForegroundColor Green


# ============================================================
# DOCUMENTATION DIRECTORIES
# ============================================================

Write-Host "Creating documentation structure..." -ForegroundColor Yellow

$docFolders = @(
    "docs",
    "docs/architecture",
    "docs/agents",
    "docs/api",
    "docs/responsible_ai"
)

foreach ($folder in $docFolders) {
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}

$documentationFiles = @(

    "docs/architecture/system_architecture.md",

    "docs/agents/coordinator.md",
    "docs/agents/planning.md",
    "docs/agents/risk_knowledge.md",
    "docs/agents/monitoring.md",

    "docs/api/api_specification.md",

    "docs/responsible_ai/responsible_ai.md"
)

foreach ($file in $documentationFiles) {
    New-Item -ItemType File -Force -Path $file | Out-Null
}

Write-Host "[OK] Documentation structure created" -ForegroundColor Green


# ============================================================
# FINISHED
# ============================================================

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host " APM INITIAL STRUCTURE CREATED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""

Write-Host "IMPORTANT:" -ForegroundColor Yellow
Write-Host "Frontend was intentionally NOT created by this script."
Write-Host "Vite will create the React frontend files automatically."
Write-Host ""

Write-Host "Next step for frontend:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  cd frontend"
Write-Host "  npm create vite@latest . -- --template react"
Write-Host ""

Write-Host "After Vite creates the frontend:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  npm install"
Write-Host "  npm install axios react-router-dom recharts"
Write-Host ""

Write-Host "Check project structure with:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  tree /F"
Write-Host ""

Write-Host "No Docker, dependencies, .env files, or frontend files were created."
Write-Host ""