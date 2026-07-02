# Adaptive Tutor Project

This repository contains a local-first adaptive learning platform that combines an onboarding experience, diagnostic assessment, path generation, adaptive quizzes, mastery tracking, and quick-study support in a single prototype. Refer to the docs/"Capstone Group Pre" for more information about implementation and results.

## Overview

The project is designed to help a learner:

- create or update a learner profile
- choose a subject and topic of interest
- receive a diagnostic assessment preview
- follow a personalized learning path
- answer adaptive quiz questions and track mastery
- continue studying with quick-study sessions backed by uploaded content

## Current capabilities

- Learner onboarding and profile capture
- Subject/topic entry and dashboard flow
- Knowledge assessment and context retrieval
- Learning path generation with step previews
- Adaptive quiz start/submit workflow
- Mastery tracking updates after quiz responses
- Quick-study chat sessions for study support
- SQLite-backed persistence with a local content store

## Repository structure

```text
.
├── agents/
│   ├── adaptive_quiz.py
│   ├── content_service.py
│   ├── knowledge_assessment.py
│   ├── learning_path.py
│   ├── mastery_tracking.py
│   └── quick_study_chat.py
├── backend/
│   ├── app.py
│   └── adaptive_tutor_v2.db
├── data/
│   ├── chroma_db/
│   ├── math_pdfs/
│   ├── sciq/
│   └── sql/
├── docs/
│   ├── README.md
│   └── related architecture and design notes
├── frontend/
│   ├── app/
│   ├── adaptive_quiz.html
│   ├── dashboard.html
│   ├── diagnostic_quiz.html
│   ├── index.html
│   ├── onboarding_profile_form.html
│   ├── quick_study.html
│   ├── quiz_summary.html
│   ├── roadmap_graph.html
│   └── subject_topic_entry.html
├── notebooks/
├── scripts/
└── requirements.txt (if added later)
```

## Prerequisites

- Python 3.10 or newer
- A virtual environment is recommended
- A local browser for the frontend pages

## Setup

On Windows PowerShell:

```powershell
cd C:\Users\YourName\Downloads\arch-agents-clena
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Install any Python packages required by the backend and agents in your active environment. If dependencies are missing, install them with `pip` as errors appear.

## Run the backend

From the project root:

```powershell
python backend\app.py
```

The backend API will start on:

```text
http://localhost:8001
```

The first run initializes the SQLite database automatically.

## Run the frontend

In a second terminal, serve the frontend files:

```powershell
python -m http.server 8000
```

Then open one of the available entry pages in your browser:

```text
http://localhost:8000/frontend/index.html
```

Useful entry points include:

- `frontend/index.html` for the entry flow
- `frontend/onboarding_profile_form.html` for onboarding
- `frontend/dashboard.html` for the main learner dashboard
- `frontend/quick_study.html` for quick-study sessions

## Documentation

The docs folder contains architecture and design notes for the project, including:

- agent flow
- content storage contract
- user profile design
- data architecture
- ER diagram and query flow
- BKT mastery design

## Development notes

- The project is currently a local prototype and uses SQLite for persistence.
- The frontend is lightweight and is meant to be exercised through a local static server.
- The backend wires together the assessment, path generation, quiz, and mastery-tracking agents.

## Next steps

- add stronger validation and error handling
- connect more of the frontend flows to the backend endpoints
- expand the quick-study experience with richer document support
- add automated tests and a dependency manifest
