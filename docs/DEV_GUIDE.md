# Developer Guide: Getting Started 🚀

Welcome to the **Agentic Project Management System (APM)** development team.

This guide covers the initial setup required to run the frontend and backend before starting individual feature development.

---

## 🛠 1. Prerequisites

Before starting development, make sure you have:

- **Python 3.13+**
- **Node.js / npm**
- **Git**
- **VS Code** (recommended)

Verify the installations:

```bash
python --version
node --version
npm --version
git --version
```

---

## 🖥 2. Backend Setup (FastAPI)

The backend is located inside:

```text
backend/
```

### Step 2.1: Navigate to Backend

```powershell
cd backend
```

### Step 2.2: Activate Virtual Environment

If the virtual environment already exists:

```powershell
.\venv\Scripts\Activate.ps1
```

You should see:

```text
(venv)
```

in the terminal.

### Step 2.3: Install Dependencies

For a fresh setup:

```powershell
python -m pip install -r requirements.txt
```

### Step 2.4: Run Backend

```powershell
uvicorn app.main:app --reload
```

The backend will run at:

```text
http://127.0.0.1:8000
```

### Step 2.5: Swagger API Documentation

FastAPI automatically provides Swagger UI at:

```text
http://127.0.0.1:8000/docs
```

ReDoc:

```text
http://127.0.0.1:8000/redoc
```

---

## 🎨 3. Frontend Setup (React + Vite)

The frontend is located inside:

```text
frontend/
```

### Step 3.1: Navigate to Frontend

Open a **new terminal**:

```powershell
cd frontend
```

### Step 3.2: Install Dependencies

For the first setup:

```powershell
npm install
```

### Step 3.3: Run Frontend

```powershell
npm run dev
```

The frontend will normally be available at:

```text
http://localhost:5173
```

---

## 💡 4. Typical Development Workflow

During development, use two terminals.

### Terminal 1 – Backend

```powershell
cd backend
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

### Terminal 2 – Frontend

```powershell
cd frontend
npm run dev
```

The development environment will therefore be:

```text
Frontend
http://localhost:5173

        ↓

FastAPI Backend
http://127.0.0.1:8000

        ↓

AI Agents / Services / Database
```

---

## 📁 5. Important Project Folders

### Backend

```text
backend/app/
├── agents/       # AI agents
├── api/          # API endpoints
├── services/     # Business/AI services
├── models/       # Database models
└── main.py       # FastAPI application
```

### Frontend

```text
frontend/src/
├── components/   # Reusable UI components
├── pages/        # Application pages
├── services/     # API communication
├── App.jsx       # Main application
└── main.jsx      # React entry point
```

---

## 🌿 6. Git Workflow

Do not directly develop features on `main`.

Create a feature branch:

```powershell
git checkout -b feature/your-feature-name
```

Example:

```powershell
git checkout -b feature/planning-agent
```

After completing your work:

```powershell
git add .
git commit -m "Add planning agent"
git push -u origin feature/planning-agent
```

Then create a Pull Request.

---

## 🔐 7. Important Security Rules

Never commit:

```text
.env
backend/venv/
frontend/node_modules/
__pycache__/
.pytest_cache/
```

Never commit API keys, passwords, database credentials, or other secrets.

Use environment variables for sensitive configuration.

---

## ⚠️ 8. Before Starting Your Assigned Part

Before implementing an agent or feature, make sure the team has agreed on:

- What the feature/agent is responsible for
- What input it receives
- What output it produces
- How it communicates with other components
- Which API endpoint is required
- Which files/folders should be modified

Avoid changing the project architecture independently.

---

## 🚧 9. Current Project Status

The project is currently at the **initial setup stage**.

### Already completed

- React + Vite setup
- FastAPI setup
- Python virtual environment
- Uvicorn setup
- Basic frontend
- Basic backend
- Git/GitHub setup

### Not implemented yet

- Agent implementation
- Database
- Authentication
- LLM integration
- NLP
- Information Retrieval
- RAG
- Agent communication
- Project management features

The team should first agree on the architecture and responsibilities before starting these implementations.

---

## 🤝 10. Team Development Principle

> **Agree on the architecture and interfaces first, then implement individual components.**

Every developer should ensure their work can integrate with the rest of the APM system.
