import json
import os
import sqlite3
import uuid
import sys
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agents.knowledge_assessment import build_assessment_preview, retrieve_context
from agents.content_service import ensure_dashboard_step_view
from agents.learning_path import build_learning_path, build_path_preview_terms, build_path_step_titles
from agents.learning_path import build_learning_path
from agents.adaptive_quiz import start_adaptive_quiz, submit_adaptive_answer
from agents.mastery_tracking import update_mastery_after_quiz

DB_PATH = BASE_DIR / "adaptive_tutor_v2.db"
ROOT_SCHEMA_PATH = BASE_DIR.parent / "data" / "sql" / "user_profile_schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    schema = ROOT_SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.executescript(schema)
        ensure_runtime_migrations(conn)
        existing_attempt_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(quiz_attempts)")
        }
        for column_name in ("starting_difficulty", "ending_difficulty"):
            if column_name not in existing_attempt_columns:
                conn.execute(f"ALTER TABLE quiz_attempts ADD COLUMN {column_name} TEXT")

        existing_response_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(quiz_responses)")
        }
        for column_name, column_type in (
            ("question_difficulty", "TEXT"),
            ("difficulty_after", "TEXT"),
            ("explanation", "TEXT"),
        ):
            if column_name not in existing_response_columns:
                conn.execute(
                    f"ALTER TABLE quiz_responses ADD COLUMN {column_name} {column_type}"
                )
        conn.commit()


def ensure_runtime_migrations(conn):
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(learning_path_steps)").fetchall()
    }
    if "preview_terms" not in columns:
        conn.execute("ALTER TABLE learning_path_steps ADD COLUMN preview_terms TEXT")


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


def parse_json_list(value):
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item or "").strip()]


def dump_json_list(values):
    return json.dumps([str(item) for item in values if str(item or "").strip()])


def normalize_answer_value(value):
    return str(value or "").strip().upper()


def canonicalize_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _term_matches_text(term, text):
    term_text = canonicalize_text(term)
    text_value = canonicalize_text(text)
    if not term_text or not text_value:
        return False
    if term_text in text_value or text_value in term_text:
        return True
    tokens = [canonicalize_text(part) for part in re.split(r"\s+", str(term)) if canonicalize_text(part)]
    return bool(tokens) and all(token in text_value for token in tokens[:2])


def _assign_term_to_response(response, preview_terms, response_index):
    response_text = " ".join(
        str(part or "")
        for part in (
            response.get("question_text"),
            response.get("correct_answer"),
            response.get("explanation"),
        )
    )
    for term in preview_terms:
        if _term_matches_text(term, response_text):
            return term
    if preview_terms:
        return preview_terms[response_index % len(preview_terms)]
    return None


def build_mastery_breakdown(path_steps, latest_quiz, latest_quiz_responses):
    breakdown = []
    latest_step_id = (latest_quiz or {}).get("step_id")
    step_map = {}

    for step in path_steps or []:
        preview_terms = parse_json_list(step.get("preview_terms"))
        step_map[step.get("step_id")] = {
            "step_id": step.get("step_id"),
            "step_order": step.get("step_order"),
            "step_title": step.get("step_title"),
            "step_status": step.get("step_status"),
            "preview_terms": preview_terms,
            "subtopics": [],
            "topics_to_review": [],
            "topics_to_practice": [],
            "topics_strong": [],
            "answered_questions": 0,
            "correct_answers": 0,
            "accuracy": None,
            "summary": "Not practiced yet.",
        }

    if latest_step_id and latest_step_id in step_map:
        step_summary = step_map[latest_step_id]
        preview_terms = step_summary["preview_terms"]
        term_stats = {
            term: {"answered": 0, "correct": 0}
            for term in preview_terms
        }

        for index, response in enumerate(latest_quiz_responses or []):
            assigned_term = _assign_term_to_response(response, preview_terms, index)
            if not assigned_term:
                continue
            bucket = term_stats.setdefault(assigned_term, {"answered": 0, "correct": 0})
            bucket["answered"] += 1
            if int(response.get("is_correct") or 0):
                bucket["correct"] += 1

        answer_count = len(latest_quiz_responses or [])
        correct_count = sum(1 for response in latest_quiz_responses or [] if int(response.get("is_correct") or 0))
        accuracy = round((correct_count / answer_count) * 100, 1) if answer_count else None

        subtopics = []
        for term in preview_terms:
            stats = term_stats.get(term, {"answered": 0, "correct": 0})
            answered = int(stats["answered"])
            correct = int(stats["correct"])
            term_accuracy = round((correct / answered) * 100, 1) if answered else None
            if answered and term_accuracy >= 75:
                bucket = "strong"
            elif answered and term_accuracy < 60:
                bucket = "review"
            elif answered:
                bucket = "practice"
            else:
                bucket = "untried"
            subtopics.append({
                "topic": term,
                "answered": answered,
                "correct": correct,
                "accuracy": term_accuracy,
                "bucket": bucket,
            })

        step_summary["subtopics"] = subtopics
        step_summary["answered_questions"] = answer_count
        step_summary["correct_answers"] = correct_count
        step_summary["accuracy"] = accuracy
        step_summary["topics_strong"] = [item["topic"] for item in subtopics if item["bucket"] == "strong"]
        step_summary["topics_to_review"] = [item["topic"] for item in subtopics if item["bucket"] == "review"]
        step_summary["topics_to_practice"] = [item["topic"] for item in subtopics if item["bucket"] == "practice"]

        if accuracy is None:
            step_summary["summary"] = "No answered questions yet."
        elif accuracy >= 75:
            step_summary["summary"] = "Strong overall on this step."
        elif accuracy >= 60:
            step_summary["summary"] = "Mixed performance. Some subtopics need another pass."
        else:
            step_summary["summary"] = "Needs review. Focus on the low-scoring subtopics."

    for step in path_steps or []:
        summary = step_map.get(step.get("step_id"))
        if summary:
            breakdown.append(summary)

    return breakdown


def resolve_selected_answer(selected, options):
    selected_value = normalize_answer_value(selected)
    if selected_value in {"A", "B", "C", "D"}:
        return selected_value

    normalized_options = options if isinstance(options, dict) else {}
    for key, option_text in normalized_options.items():
        if normalize_answer_value(key) == selected_value:
            return normalize_answer_value(key)
        if canonicalize_text(option_text) == canonicalize_text(selected):
            return normalize_answer_value(key)
        if canonicalize_text(option_text) and (
            canonicalize_text(option_text) in canonicalize_text(selected)
            or canonicalize_text(selected) in canonicalize_text(option_text)
        ):
            return normalize_answer_value(key)

    match = re.search(r"\b([A-D])\b", selected_value)
    if match:
        return match.group(1)
    return selected_value


def familiarity_to_level(familiarity):
    if not familiarity:
        return "beginner"

    normalized = str(familiarity).strip().lower()
    if normalized in {"new to this", "beginner", "new", "none"}:
        return "beginner"
    if normalized in {"some familiarity", "comfortable with basics", "intermediate"}:
        return "intermediate"
    if normalized in {"already advanced", "advanced"}:
        return "advanced"
    return "beginner"


def resolve_study_flow(study_mode):
    mode = (study_mode or "roadmap").strip().lower()
    if mode in {"roadmap", "create roadmap"}:
        return {
            "study_mode": "roadmap",
            "route": "diagnostic_quiz",
            "agent": "knowledge_assessment",
            "assessment_required": True,
            "description": "Run diagnostic quiz first, then build roadmap.",
        }
    if mode in {"quiz", "quiz_only", "adaptive_quiz"}:
        return {
            "study_mode": "quiz",
            "route": "adaptive_quiz",
            "agent": "adaptive_quiz",
            "assessment_required": False,
            "description": "Skip diagnostic and go directly to adaptive quiz.",
        }
    if mode in {"quick_study", "quick study"}:
        return {
            "study_mode": "quick_study",
            "route": "quick_study",
            "agent": "quick_study",
            "assessment_required": False,
            "description": "Skip diagnostic and show short content immediately.",
        }
    return {
        "study_mode": "roadmap",
        "route": "diagnostic_quiz",
        "agent": "knowledge_assessment",
        "assessment_required": True,
        "description": "Default to roadmap with diagnostic quiz.",
    }


def upsert_subject_and_topic(conn, subject_name, description=None):
    subject_name = (subject_name or "").strip()
    if not subject_name:
        raise ValueError("subject_name is required")

    subject = conn.execute(
        "SELECT subject_id FROM subjects WHERE subject_name = ?",
        (subject_name,),
    ).fetchone()
    if subject:
        subject_id = subject["subject_id"]
        if description is not None:
            conn.execute(
                "UPDATE subjects SET description = ? WHERE subject_id = ?",
                (description, subject_id),
            )
    else:
        subject_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO subjects (subject_id, subject_name, description) VALUES (?, ?, ?)",
            (subject_id, subject_name, description),
        )

    topic = conn.execute(
        """
        SELECT topic_id, topic_name
        FROM topics
        WHERE subject_id = ? AND topic_name = ?
        """,
        (subject_id, subject_name),
    ).fetchone()
    if topic:
        topic_id = topic["topic_id"]
    else:
        topic_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO topics (topic_id, subject_id, topic_name, topic_description, topic_order, estimated_minutes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (topic_id, subject_id, subject_name, description, 0, 15),
        )

    return subject_id, topic_id


def upsert_learner_subject_profile(conn, learner_id, subject_id, topic_id, level, study_mode, familiarity):
    profile = conn.execute(
        """
        SELECT profile_id
        FROM learner_subject_profiles
        WHERE learner_id = ? AND subject_id = ?
        """,
        (learner_id, subject_id),
    ).fetchone()

    if profile:
        conn.execute(
            """
            UPDATE learner_subject_profiles
            SET current_topic_id = ?,
                goal_type = ?,
                current_level = ?,
                target_level = ?,
                status = 'active',
                last_activity_at = datetime('now'),
                updated_at = datetime('now')
            WHERE learner_id = ? AND subject_id = ?
            """,
            (
                topic_id,
                study_mode,
                level,
                "understanding",
                learner_id,
                subject_id,
            ),
        )
        profile_id = profile["profile_id"]
    else:
        profile_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO learner_subject_profiles (
                profile_id, learner_id, subject_id, current_topic_id, goal_type,
                current_level, target_level, status, last_assessed_score,
                mastery_score, confidence_score, path_completion_pct,
                completed_step_count, total_step_count, last_activity_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0, 0, 0, 0, 0, 0, datetime('now'))
            """,
            (
                profile_id,
                learner_id,
                subject_id,
                topic_id,
                study_mode,
                level,
                "understanding",
            ),
        )

    return profile_id


def save_diagnostic_attempt(conn, learner_id, subject_id, path_id, step_id, level, topic, questions, answers):
    attempt_id = str(uuid.uuid4())
    normalized_questions = questions if isinstance(questions, list) else []
    normalized_answers = answers if isinstance(answers, list) else []
    answer_lookup = {}
    for item in normalized_answers:
        question_id = str(item.get("question_id") or "")
        if question_id:
            answer_lookup[question_id] = item

    total_questions = len(normalized_questions)
    correct_answers = 0
    response_rows = []

    for question in normalized_questions:
        question_id = str(question.get("id") or "")
        selected = answer_lookup.get(question_id, {}).get("answer", "")
        options = question.get("options", {})
        correct = resolve_selected_answer(question.get("correct_answer", ""), options)
        selected_value = resolve_selected_answer(selected, options)
        is_correct = selected_value == correct
        if is_correct:
            correct_answers += 1
        response_rows.append(
            {
                "question_id": question_id,
                "question_text": question.get("question", ""),
                "selected_answer": selected_value,
                "correct_answer": correct,
                "is_correct": int(is_correct),
            }
        )

    score = round((correct_answers / total_questions) if total_questions else 0.0, 3)
    mastery_delta = round(max(score - 0.5, 0.0), 3)
    status = "passed" if score >= 0.8 else "needs_review"

    conn.execute(
        """
        INSERT INTO quiz_attempts (
            attempt_id, learner_id, subject_id, path_id, step_id,
            quiz_type, difficulty_level, score, total_questions,
            correct_answers, completion_status, mastery_delta,
            started_at, completed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            attempt_id,
            learner_id,
            subject_id,
            path_id,
            step_id,
            "diagnostic",
            level,
            score,
            total_questions,
            correct_answers,
            status,
            mastery_delta,
        ),
    )

    for row in response_rows:
        conn.execute(
            """
            INSERT INTO quiz_responses (
                response_id, attempt_id, question_id, question_text,
                selected_answer, correct_answer, is_correct, time_taken_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                attempt_id,
                row["question_id"],
                row["question_text"],
                row["selected_answer"],
                row["correct_answer"],
                row["is_correct"],
                None,
            ),
        )

    profile = conn.execute(
        """
        SELECT profile_id
        FROM learner_subject_profiles
        WHERE learner_id = ? AND subject_id = ?
        """,
        (learner_id, subject_id),
    ).fetchone()

    if profile:
        conn.execute(
            """
            UPDATE learner_subject_profiles
            SET last_assessed_score = ?,
                mastery_score = ?,
                confidence_score = ?,
                current_level = ?,
                status = ?,
                last_activity_at = datetime('now'),
                updated_at = datetime('now')
            WHERE learner_id = ? AND subject_id = ?
            """,
            (
                score,
                score,
                min(score + 0.1, 1.0),
                "intermediate" if score >= 0.8 else level,
                "active" if score >= 0.8 else "needs_review",
                learner_id,
                subject_id,
            ),
        )

    return {
        "attempt_id": attempt_id,
        "score": score,
        "total_questions": total_questions,
        "correct_answers": correct_answers,
        "status": status,
        "mastery_delta": mastery_delta,
        "responses": response_rows,
    }


def get_dashboard_summary(conn, email, selected_subject_id=None):
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
            ct.topic_name AS current_topic_name,
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
        LEFT JOIN topics ct ON ct.topic_id = lsp.current_topic_id
        WHERE lsp.learner_id = ?
        ORDER BY lsp.updated_at DESC
        """,
        (learner["learner_id"],),
    )

    selected_subject = None
    if selected_subject_id:
        selected_subject = next(
            (profile for profile in subject_profiles if profile["subject_id"] == selected_subject_id),
            None,
        )
    active_subject = selected_subject or (subject_profiles[0] if subject_profiles else None)

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
    ) if active_subject and active_subject.get("active_path_id") else None

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
            preview_terms,
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
    ) if active_subject and active_subject.get("active_path_id") else []
    stored_terms_by_step = [parse_json_list(step.get("preview_terms")) for step in path_steps]
    missing_preview_terms = any(not terms for terms in stored_terms_by_step)
    generated_terms_by_step = (
        build_path_preview_terms(active_subject["subject_name"], path_steps)
        if active_subject and path_steps and missing_preview_terms
        else []
    )
    preview_terms_by_step = []
    for index, step in enumerate(path_steps):
        terms = stored_terms_by_step[index] if index < len(stored_terms_by_step) else []
        if not terms and index < len(generated_terms_by_step):
            terms = generated_terms_by_step[index]
            conn.execute(
                """
                UPDATE learning_path_steps
                SET preview_terms = ?,
                    updated_at = datetime('now')
                WHERE step_id = ?
                """,
                (dump_json_list(terms), step["step_id"]),
            )
        step["preview_terms"] = terms
        preview_terms_by_step.append(terms)

    display_titles = build_path_step_titles(active_subject["subject_name"], path_steps, preview_terms_by_step) if active_subject and path_steps else []
    for index, step in enumerate(path_steps):
        refined_title = display_titles[index] if index < len(display_titles) else step["step_title"]
        if refined_title and refined_title != step["step_title"]:
            conn.execute(
                """
                UPDATE learning_path_steps
                SET step_title = ?,
                    updated_at = datetime('now')
                WHERE step_id = ?
                """,
                (refined_title, step["step_id"]),
            )
            step["step_title"] = refined_title

    current_step = None
    for step in path_steps:
        if step.get("step_status") == "in_progress":
            current_step = step
            break
    if current_step is None and path_steps:
        current_step = path_steps[0]

    if current_step and active_subject and not active_subject.get("current_view"):
        try:
            ensure_dashboard_step_view(
                conn,
                learner["learner_id"],
                active_subject["subject_name"],
                active_subject.get("goal_type") or "roadmap",
                preferences or {},
                current_step["step_id"],
            )
        except Exception:
            pass

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
        (learner["learner_id"], active_subject["subject_id"]) if active_subject else (learner["learner_id"], None),
    ) if active_subject else None

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

    step_quiz_attempts = fetchall_dict(
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
        WHERE learner_id = ? AND subject_id = ? AND path_id = ?
        ORDER BY COALESCE(completed_at, started_at) DESC
        """,
        (
            learner["learner_id"],
            active_subject["subject_id"],
            active_subject.get("active_path_id"),
        ),
    ) if active_subject and active_subject.get("active_path_id") else []

    step_quiz_map = {}
    for attempt in step_quiz_attempts:
        step_id = attempt.get("step_id")
        if not step_id or step_id in step_quiz_map:
            continue
        attempt_responses = fetchall_dict(
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
            (attempt["attempt_id"],),
        )
        step_quiz_map[step_id] = {
            "attempt": attempt,
            "responses": attempt_responses,
        }

    mastery_rows = fetchall_dict(
        conn,
        """
        SELECT
            topic_mastery.mastery_id,
            topic_mastery.learner_id,
            topic_mastery.subject_id,
            topic_mastery.topic_id,
            t.topic_name,
            topic_mastery.mastery_probability,
            topic_mastery.last_assessed_score,
            topic_mastery.review_due_at,
            topic_mastery.last_practiced_at,
            topic_mastery.mastery_status,
            topic_mastery.created_at,
            topic_mastery.updated_at
        FROM topic_mastery
        LEFT JOIN topics t ON t.topic_id = topic_mastery.topic_id
        WHERE topic_mastery.learner_id = ? AND topic_mastery.subject_id = ?
        ORDER BY topic_mastery.mastery_probability ASC, topic_mastery.updated_at DESC
        """,
        (learner["learner_id"], active_subject["subject_id"]) if active_subject else (learner["learner_id"], None),
    ) if active_subject else []

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
    if current_step and active_subject and active_subject.get("active_path_id"):
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

    mastery_breakdown = build_mastery_breakdown(path_steps, latest_quiz, latest_quiz_responses)

    active_subject = {
        **active_subject,
        "active_path": active_path,
        "path_steps": path_steps,
        "latest_quiz": latest_quiz,
        "latest_quiz_responses": latest_quiz_responses,
        "step_quiz_map": step_quiz_map,
        "topic_mastery": mastery_rows,
        "mastery_breakdown": mastery_breakdown,
        "current_view": current_view,
        "current_step_content": current_step_content,
        "current_step": current_step,
    } if active_subject else None

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
        "selected_subject_id": active_subject["subject_id"] if active_subject else None,
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
        if path == "/api/diagnostic/submit":
            return self.submit_diagnostic(payload)
        if path == "/api/adaptive-quiz/start":
            return self.start_adaptive_quiz_request(payload)
        if path == "/api/adaptive-quiz/answer":
            return self.submit_adaptive_answer_request(payload)
        if path == "/api/mastery/update":
            return self.update_mastery_request(payload)

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

    def start_adaptive_quiz_request(self, payload):
        required_fields = [
            "learner_id",
            "subject_id",
            "path_id",
            "step_id",
        ]

        missing = [
            field
            for field in required_fields
            if not payload.get(field)
        ]

        if missing:
            return json_response(
                self,
                400,
                {
                    "error": (
                        "Missing required fields: "
                        + ", ".join(missing)
                    )
                },
            )

        try:
            with get_connection() as conn:
                result = start_adaptive_quiz(
                    conn=conn,
                    learner_id=payload["learner_id"],
                    subject_id=payload["subject_id"],
                    path_id=payload["path_id"],
                    step_id=payload["step_id"],
                )
                conn.commit()

        except ValueError as error:
            return json_response(
                self,
                400,
                {"error": str(error)},
            )

        except RuntimeError as error:
            return json_response(
                self,
                500,
                {"error": str(error)},
            )

        return json_response(self, 201, result)

    def submit_adaptive_answer_request(self, payload):
        required_fields = ["attempt_id", "question_id", "selected_answer"]
        missing = [field for field in required_fields if not payload.get(field)]
        if missing:
            return json_response(
                self,
                400,
                {"error": "Missing required fields: " + ", ".join(missing)},
            )

        try:
            with get_connection() as conn:
                result = submit_adaptive_answer(
                    conn=conn,
                    attempt_id=payload["attempt_id"],
                    question_id=payload["question_id"],
                    selected_answer=payload["selected_answer"],
                    time_taken_seconds=payload.get("time_taken_seconds"),
                )
                if result.get("quiz_complete"):
                    mastery_update = update_mastery_after_quiz(conn, payload["attempt_id"])
                    result["mastery_update"] = mastery_update
                conn.commit()
        except ValueError as error:
            return json_response(self, 400, {"error": str(error)})
        except RuntimeError as error:
            return json_response(self, 500, {"error": str(error)})
        except sqlite3.Error as error:
            return json_response(self, 500, {"error": f"Database error: {error}"})

        return json_response(self, 200, result)

    def update_mastery_request(self, payload):
        attempt_id = (payload.get("attempt_id") or "").strip()
        if not attempt_id:
            return json_response(self, 400, {"error": "attempt_id is required"})

        try:
            with get_connection() as conn:
                result = update_mastery_after_quiz(conn, attempt_id)
                conn.commit()
        except ValueError as error:
            return json_response(self, 400, {"error": str(error)})
        except sqlite3.Error as error:
            return json_response(self, 500, {"error": f"Database error: {error}"})

        return json_response(self, 200, result)


    def create_study_request(self, payload):
        topic = (payload.get("topic") or "").strip()
        if not topic:
            return json_response(self, 400, {"error": "topic is required"})

        study_flow = resolve_study_flow(payload.get("study_mode") or "roadmap")
        study_mode = study_flow["study_mode"]
        familiarity = payload.get("familiarity")
        level = familiarity_to_level(familiarity)
        learner_id = (payload.get("learner_id") or "").strip()
        learner_email = (payload.get("learner_email") or "").strip().lower()

        with get_connection() as conn:
            learner = None
            if learner_id:
                learner = conn.execute(
                    "SELECT learner_id, email, full_name FROM learners WHERE learner_id = ?",
                    (learner_id,),
                ).fetchone()
            if not learner and learner_email:
                learner = conn.execute(
                    "SELECT learner_id, email, full_name FROM learners WHERE email = ?",
                    (learner_email,),
                ).fetchone()
            if not learner:
                return json_response(self, 404, {"error": "learner not found"})

            learner_id = learner["learner_id"]
            subject_id, topic_id = upsert_subject_and_topic(
                conn,
                topic,
                f"Learning request created for {topic}",
            )
            profile_id = upsert_learner_subject_profile(
                conn,
                learner_id,
                subject_id,
                topic_id,
                level,
                study_mode,
                familiarity,
            )

            request_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO study_requests (request_id, learner_id, topic, study_mode, familiarity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    learner_id,
                    topic,
                    study_mode,
                    familiarity,
                ),
            )
            conn.commit()

        assessment_preview = None
        learning_path = None
        if study_flow["assessment_required"]:
            assessment_preview = build_assessment_preview(topic, level, familiarity, question_count=5)
        elif study_mode in {"quick_study", "quiz"}:
            learning_path = build_learning_path(
                conn,
                learner_id,
                subject_id,
                topic,
                None,
                study_mode="quick_study" if study_mode == "quick_study" else "roadmap",
            )
            if study_mode == "quick_study":
                assessment_preview = {
                    "topic": topic,
                    "level": level,
                    "mode": "quick_study",
                    "context_source": "learning_path quick study summary",
                    "context": learning_path["summary"] if learning_path else retrieve_context(topic),
                    "questions": [],
                    "learning_path": learning_path,
                }

        conn.commit()

        return json_response(
            self,
            201,
            {
                "message": "Study request created",
                "request_id": request_id,
                "learner_id": learner_id,
                "study_flow": study_flow,
                "subject": {
                    "subject_id": subject_id,
                    "subject_name": topic,
                    "topic_id": topic_id,
                    "profile_id": profile_id,
                    "level": level,
                },
                "assessment_preview": assessment_preview,
                "learning_path": learning_path,
            },
        )

    def submit_diagnostic(self, payload):
        email = (payload.get("email") or "").strip().lower()
        topic = (payload.get("topic") or "").strip()
        questions = payload.get("questions") or []
        answers = payload.get("answers") or []
        level = (payload.get("level") or "beginner").strip()

        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not topic:
            return json_response(self, 400, {"error": "topic is required"})
        if not questions:
            return json_response(self, 400, {"error": "questions are required"})

        with get_connection() as conn:
            learner = conn.execute(
                "SELECT learner_id FROM learners WHERE email = ?",
                (email,),
            ).fetchone()
            if not learner:
                return json_response(self, 404, {"error": "learner not found"})

            subject_id, topic_id = upsert_subject_and_topic(
                conn,
                topic,
                f"Diagnostic assessment subject for {topic}",
            )
            profile_id = upsert_learner_subject_profile(
                conn,
                learner["learner_id"],
                subject_id,
                topic_id,
                level,
                "roadmap",
                None,
            )
            result = save_diagnostic_attempt(
                conn,
                learner["learner_id"],
                subject_id,
                None,
                None,
                level,
                topic,
                questions,
                answers,
            )
            learning_path = build_learning_path(
                conn,
                learner["learner_id"],
                subject_id,
                topic,
                result,
                study_mode=payload.get("study_mode") or "roadmap",
            )
            conn.commit()

        preview = build_assessment_preview(topic, level, question_count=5)
        return json_response(
            self,
            200,
            {
                "message": "Diagnostic quiz saved",
                "learner_id": learner["learner_id"],
                "subject_id": subject_id,
                "topic_id": topic_id,
                "profile_id": profile_id,
                "result": result,
                "learning_path": learning_path,
                "preview": preview,
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
        selected_subject_id = (params.get("selected_subject_id", [""])[0]).strip() or None
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            summary = get_dashboard_summary(conn, email, selected_subject_id=selected_subject_id)

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
