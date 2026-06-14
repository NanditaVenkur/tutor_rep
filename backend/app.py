import json
import os
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "adaptive_tutor_v2.db"
ROOT_SCHEMA_PATH = BASE_DIR.parent / "user_profile_schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    schema = ROOT_SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema)
        conn.commit()


def json_response(handler, status_code, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def fetchone_dict(conn, query, params=()):
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def fetchall_dict(conn, query, params=()):
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_dashboard_summary(conn, email):
    learner = fetchone_dict(
        conn,
        """
        SELECT
            learner_id,
            email,
            full_name,
            age_group,
            role,
            preferred_language,
            created_at,
            updated_at
        FROM learners
        WHERE email = ?
        """,
        (email,),
    )
    if not learner:
        return None

    preferences = fetchone_dict(
        conn,
        """
        SELECT
            learner_id,
            content_format,
            explanation_style,
            quiz_style,
            learning_pace,
            session_length,
            feedback_style,
            accessibility_notes,
            updated_at
        FROM learner_preferences
        WHERE learner_id = ?
        """,
        (learner["learner_id"],),
    )

    subject_profiles = fetchall_dict(
        conn,
        """
        SELECT
            lsp.profile_id,
            lsp.learner_id,
            lsp.subject_id,
            s.subject_name,
            lsp.active_path_id,
            lsp.current_topic_id,
            lsp.goal_type,
            lsp.current_level,
            lsp.target_level,
            lsp.status,
            lsp.last_assessed_score,
            lsp.mastery_score,
            lsp.confidence_score,
            lsp.path_completion_pct,
            lsp.completed_step_count,
            lsp.total_step_count,
            lsp.next_review_at,
            lsp.last_activity_at,
            lsp.updated_at
        FROM learner_subject_profiles lsp
        JOIN subjects s ON s.subject_id = lsp.subject_id
        WHERE lsp.learner_id = ?
        ORDER BY lsp.updated_at DESC
        """,
        (learner["learner_id"],),
    )

    active_subject = None
    if subject_profiles:
        active_subject = subject_profiles[0]

        active_path = fetchone_dict(
            conn,
            """
            SELECT
                path_id,
                learner_id,
                subject_id,
                root_topic_id,
                path_title,
                path_status,
                target_outcome,
                total_steps,
                completed_steps,
                created_at,
                updated_at,
                last_accessed_at
            FROM learning_paths
            WHERE path_id = ?
            """,
            (active_subject["active_path_id"],),
        ) if active_subject.get("active_path_id") else None

        path_steps = fetchall_dict(
            conn,
            """
            SELECT
                step_id,
                path_id,
                topic_id,
                resource_id,
                chunk_id,
                content_version,
                step_order,
                step_title,
                step_description,
                step_status,
                estimated_minutes,
                actual_minutes,
                started_at,
                completed_at,
                created_at,
                updated_at
            FROM learning_path_steps
            WHERE path_id = ?
            ORDER BY step_order ASC
            """,
            (active_subject["active_path_id"],),
        ) if active_subject.get("active_path_id") else []

        current_step = None
        for step in path_steps:
            if step.get("step_status") == "in_progress":
                current_step = step
                break
        if current_step is None and path_steps:
            current_step = path_steps[0]

        latest_quiz = fetchone_dict(
            conn,
            """
            SELECT
                attempt_id,
                learner_id,
                subject_id,
                path_id,
                step_id,
                quiz_type,
                difficulty_level,
                score,
                total_questions,
                correct_answers,
                completion_status,
                mastery_delta,
                started_at,
                completed_at
            FROM quiz_attempts
            WHERE learner_id = ? AND subject_id = ?
            ORDER BY COALESCE(completed_at, started_at) DESC
            LIMIT 1
            """,
            (learner["learner_id"], active_subject["subject_id"]),
        )

        latest_quiz_responses = fetchall_dict(
            conn,
            """
            SELECT
                response_id,
                attempt_id,
                question_id,
                question_text,
                selected_answer,
                correct_answer,
                is_correct,
                time_taken_seconds,
                created_at
            FROM quiz_responses
            WHERE attempt_id = ?
            ORDER BY created_at ASC
            """,
            (latest_quiz["attempt_id"],),
        ) if latest_quiz else []

        mastery_rows = fetchall_dict(
            conn,
            """
            SELECT
                mastery_id,
                learner_id,
                subject_id,
                topic_id,
                mastery_probability,
                last_assessed_score,
                review_due_at,
                last_practiced_at,
                mastery_status,
                created_at,
                updated_at
            FROM topic_mastery
            WHERE learner_id = ? AND subject_id = ?
            ORDER BY mastery_probability ASC, updated_at DESC
            """,
            (learner["learner_id"], active_subject["subject_id"]),
        )

        current_view = fetchone_dict(
            conn,
            """
            SELECT
                view_id,
                learner_id,
                path_id,
                step_id,
                topic_id,
                source_resource_id,
                source_chunk_id,
                source_content_version,
                rendered_title,
                rendered_summary,
                rendered_content,
                rendered_format,
                reading_level,
                content_hash,
                view_status,
                rendered_at,
                updated_at
            FROM learner_content_views
            WHERE learner_id = ? AND step_id = ?
              AND view_status = 'active'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (learner["learner_id"], current_step["step_id"]) if current_step else (learner["learner_id"], None),
        ) if current_step else None

        current_step_content = None
        if current_step and active_subject.get("active_path_id"):
            current_step_content = fetchone_dict(
                conn,
                """
                SELECT
                    lps.step_id,
                    lps.path_id,
                    lps.topic_id,
                    lps.resource_id,
                    lps.chunk_id,
                    lps.content_version,
                    lps.step_order,
                    lps.step_title,
                    lps.step_description,
                    lps.step_status,
                    cr.title AS source_title,
                    cr.vector_doc_id,
                    cr.vector_collection,
                    cc.chunk_text,
                    cc.vector_chunk_id,
                    cc.chunk_version
                FROM learning_path_steps lps
                LEFT JOIN content_resources cr ON cr.resource_id = lps.resource_id
                LEFT JOIN content_chunks cc ON cc.chunk_id = lps.chunk_id
                WHERE lps.step_id = ?
                ORDER BY lps.step_order ASC
                LIMIT 1
                """,
                (current_step["step_id"],),
            )

        active_subject = {
            **active_subject,
            "active_path": active_path,
            "path_steps": path_steps,
            "latest_quiz": latest_quiz,
            "latest_quiz_responses": latest_quiz_responses,
            "topic_mastery": mastery_rows,
            "current_view": current_view,
            "current_step_content": current_step_content,
            "current_step": current_step,
        }

    recent_sessions = fetchall_dict(
        conn,
        """
        SELECT
            session_id,
            learner_id,
            subject_id,
            path_id,
            session_type,
            session_status,
            started_at,
            ended_at,
            session_summary,
            created_at,
            updated_at
        FROM study_sessions
        WHERE learner_id = ?
        ORDER BY started_at DESC
        LIMIT 5
        """,
        (learner["learner_id"],),
    )

    return {
        "learner": learner,
        "preferences": preferences,
        "subject_profiles": subject_profiles,
        "active_subject": active_subject,
        "recent_sessions": recent_sessions,
    }


class RequestHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return json_response(self, 200, {"status": "ok"})
        if path == "/api/learner":
            return self.lookup_learner()
        if path == "/api/dashboard":
            return self.get_dashboard()
        return json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = read_json(self)
        except json.JSONDecodeError:
            return json_response(self, 400, {"error": "Invalid JSON"})

        if path == "/api/onboarding":
            return self.create_learner(payload)
        if path == "/api/topic":
            return self.create_study_request(payload)

        return json_response(self, 404, {"error": "Not found"})

    def create_learner(self, payload):
        email = (payload.get("email") or "").strip().lower()
        full_name = (payload.get("full_name") or "").strip()
        preferred_language = (payload.get("preferred_language") or "English").strip()

        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not full_name:
            return json_response(self, 400, {"error": "full_name is required"})

        with get_connection() as conn:
            existing = conn.execute(
                "SELECT learner_id FROM learners WHERE email = ?",
                (email,),
            ).fetchone()
            created = existing is None

            if existing:
                learner_id = existing["learner_id"]
                conn.execute(
                    """
                    UPDATE learners
                    SET full_name = ?, age_group = ?, role = ?, preferred_language = ?, updated_at = datetime('now')
                    WHERE learner_id = ?
                    """,
                    (
                        full_name,
                        payload.get("age_group"),
                        payload.get("role"),
                        preferred_language,
                        learner_id,
                    ),
                )
            else:
                learner_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO learners (learner_id, email, full_name, age_group, role, preferred_language)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        learner_id,
                        email,
                        full_name,
                        payload.get("age_group"),
                        payload.get("role"),
                        preferred_language,
                    ),
                )

            conn.execute(
                """
                INSERT INTO learner_preferences (
                    preference_id, learner_id, content_format, explanation_style, quiz_style,
                    learning_pace, session_length, feedback_style, accessibility_notes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(learner_id) DO UPDATE SET
                    content_format = excluded.content_format,
                    explanation_style = excluded.explanation_style,
                    quiz_style = excluded.quiz_style,
                    learning_pace = excluded.learning_pace,
                    session_length = excluded.session_length,
                    feedback_style = excluded.feedback_style,
                    accessibility_notes = excluded.accessibility_notes,
                    updated_at = datetime('now')
                """,
                (
                    str(uuid.uuid4()),
                    learner_id,
                    payload.get("content_format") or "mixed",
                    payload.get("explanation_style") or "step_by_step",
                    payload.get("quiz_style") or "mixed",
                    payload.get("learning_pace") or "normal",
                    payload.get("session_length") or "30_min",
                    payload.get("feedback_style") or "immediate",
                    payload.get("accessibility_notes"),
                ),
            )
            conn.commit()

        return json_response(
            self,
            201 if created else 200,
            {
                "message": "Learner profile created" if created else "Learner profile updated",
                "learner_id": learner_id,
                "email": email,
            },
        )

    def create_study_request(self, payload):
        topic = (payload.get("topic") or "").strip()
        if not topic:
            return json_response(self, 400, {"error": "topic is required"})

        request_id = str(uuid.uuid4())
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO study_requests (request_id, learner_id, topic, study_mode, familiarity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    payload.get("learner_id"),
                    topic,
                    payload.get("study_mode") or "roadmap",
                    payload.get("familiarity"),
                ),
            )
            conn.commit()

        return json_response(
            self,
            201,
            {
                "message": "Study request created",
                "request_id": request_id,
            },
        )

    def lookup_learner(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            learner = conn.execute(
                """
                SELECT
                    l.learner_id,
                    l.email,
                    l.full_name,
                    l.age_group,
                    l.role,
                    l.preferred_language,
                    p.content_format,
                    p.explanation_style,
                    p.quiz_style,
                    p.learning_pace,
                    p.session_length,
                    p.feedback_style,
                    p.accessibility_notes
                FROM learners l
                LEFT JOIN learner_preferences p ON p.learner_id = l.learner_id
                WHERE l.email = ?
                """,
                (email,),
            ).fetchone()

        if not learner:
            return json_response(self, 404, {"exists": False})

        return json_response(
            self,
            200,
            {
                "exists": True,
                "learner": dict(learner),
            },
        )

    def get_dashboard(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            summary = get_dashboard_summary(conn, email)

        if not summary:
            return json_response(self, 404, {"error": "learner not found"})

        return json_response(self, 200, summary)


def main():
    init_db()
    port = int(os.environ.get("PORT", "8001"))
    server = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"SQLite API running on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
