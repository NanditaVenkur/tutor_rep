# Adaptive Tutor

This repository currently contains the first frontend onboarding flow for the adaptive learning system.

## Current Structure

```text
.
├── frontend/
│   ├── index.html
│   ├── onboarding_profile_form.html
│   ├── dashboard.html
│   ├── subject_topic_entry.html
│   └── app/
│   ├── styles.css
│   ├── entry.js
│   ├── dashboard.js
│   ├── onboarding.js
│   └── subject.js
├── backend/
│   └── app.py
├── agents/
│   └── knowledge_assessment.py
├── data/
│   ├── sql/
│   │   ├── user_profile_schema.sql
│   │   ├── seed_demo_data.sql
│   │   └── dashboard_queries.sql
│   ├── sciq/
│   └── chroma_db/
├── notebooks/
│   ├── knowledge_assessment_agent.ipynb
│   └── adaptive_tutor_langchain (1).ipynb
├── scripts/
│   └── ingest_sciq.py
├── user_profile_design.md
```

## What the app does right now

- Collects first-time learner details
- Captures learning goals and preferences
- Uses a small step-based onboarding flow
- Redirects to a separate subject/topic input screen
- Saves draft data in browser local storage
- Writes onboarding and topic data into SQLite through a small Python API
- Uses email as the unique learner identity so repeat onboarding updates the same person

## Flow

1. User enters email on `frontend/index.html`.
2. If the email exists, the app goes to `frontend/dashboard.html`.
3. If the email does not exist, the app goes to `frontend/onboarding_profile_form.html`.
4. After onboarding, the app goes to `frontend/dashboard.html`.
5. From the dashboard, the user starts a new subject/topic request.

## Run the backend

```bash
python3 backend/app.py
```

The API runs on:

```text
http://localhost:8001
```

It creates `backend/adaptive_tutor_v2.db` automatically.

## Run the frontend

In a second terminal, from the project root:

```bash
python3 -m http.server 8000
```

Then open:

```text
http://localhost:8000/frontend/onboarding_profile_form.html
```

For the full flow, open:

```text
http://localhost:8000/frontend/index.html
```

## How to run

Open `frontend/index.html` in a browser, or use a local server:

```bash
python3 -m http.server 8000
```

Then visit:

```text
http://localhost:8000/frontend/index.html
```

## Next steps

- add validation and error states
- generate diagnostic quizzes from the selected subject
- build the learning path flow
- review [`data_architecture.md`](/Users/akankshacheeti/Capstone%20Project%20/data_architecture.md) for the content vs SQLite split
- review [`er_diagram_and_query_flow.md`](/Users/akankshacheeti/Capstone%20Project%20/er_diagram_and_query_flow.md) for table relationships and dashboard retrieval
- review [`agent_architecture.md`](/Users/akankshacheeti/Capstone%20Project%20/agent_architecture.md) for the prescribed LangGraph agent flow
- review [`dashboard_queries.sql`](/Users/akankshacheeti/Capstone%20Project%20/data/sql/dashboard_queries.sql) for the dashboard read layer
- review [`seed_demo_data.sql`](/Users/akankshacheeti/Capstone%20Project%20/data/sql/seed_demo_data.sql) for sample data that exercises the schema
