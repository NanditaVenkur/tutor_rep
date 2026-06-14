import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
import re

from agents.content_service import save_path_views


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "backend" / "adaptive_tutor_v2.db"


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    seen = set()
    items = []
    for value in values:
        cleaned = _normalize_text(value)
        if not cleaned or cleaned.lower() in seen:
            continue
        seen.add(cleaned.lower())
        items.append(cleaned)
    return items


def _extract_weak_points(diagnostic_result: dict) -> list[str]:
    responses = diagnostic_result.get("responses") or []
    weak_points = []
    for item in responses:
        if not item.get("is_correct"):
            question = _normalize_text(item.get("question_text") or item.get("question") or "")
            if question:
                weak_points.append(question)
    return _unique_preserve_order(weak_points)


def _normalize_study_mode(study_mode: str | None) -> str:
    normalized = _normalize_text(study_mode).lower().replace(" ", "_")
    if normalized in {"quick_study", "quickstudy", "short_study"}:
        return "quick_study"
    return "roadmap"


def _parse_session_minutes(session_length: str | None, default_minutes: int) -> int:
    text = _normalize_text(session_length).lower()
    match = re.search(r"(\d+)", text)
    if match:
        return max(int(match.group(1)), 5)
    return default_minutes


def _pace_multiplier(learning_pace: str | None) -> float:
    text = _normalize_text(learning_pace).lower()
    if text in {"slow", "slower", "relaxed", "careful"}:
        return 1.15
    if text in {"fast", "faster", "quick", "rapid"}:
        return 0.85
    return 1.0


def _load_learner_preferences(conn, learner_id: str) -> dict:
    row = conn.execute(
        """
        SELECT
            learner_id,
            content_format,
            explanation_style,
            quiz_style,
            learning_pace,
            session_length,
            feedback_style,
            accessibility_notes
        FROM learner_preferences
        WHERE learner_id = ?
        """,
        (learner_id,),
    ).fetchone()
    return dict(row) if row else {}


def _load_active_profile(conn, learner_id: str, subject_id: str) -> dict:
    row = conn.execute(
        """
        SELECT profile_id, current_level, goal_type, target_level
        FROM learner_subject_profiles
        WHERE learner_id = ? AND subject_id = ?
        """,
        (learner_id, subject_id),
    ).fetchone()
    return dict(row) if row else {}


def _build_roadmap_step_plan(topic: str, score: float, weak_points: list[str]) -> list[dict]:
    topic = _normalize_text(topic)
    focus_line = ", ".join(weak_points[:3]) if weak_points else f"{topic} fundamentals"

    if score >= 0.8:
        return [
            {
                "title": f"Quick recap of {topic}",
                "description": f"Refresh the core ideas and verify you already know the basics of {topic}.",
                "minutes": 10,
            },
            {
                "title": f"Applied concepts in {topic}",
                "description": f"Work through applied examples and connect them to the main ideas in {topic}.",
                "minutes": 15,
            },
            {
                "title": f"Practice and review for {topic}",
                "description": f"Focus on the remaining weak points: {focus_line}.",
                "minutes": 20,
            },
        ]

    if score >= 0.4:
        return [
            {
                "title": f"Foundations of {topic}",
                "description": f"Build the essential base for {topic} before moving into harder material.",
                "minutes": 15,
            },
            {
                "title": f"Key ideas and prerequisites",
                "description": f"Cover the prerequisites and the ideas most related to {focus_line}.",
                "minutes": 20,
            },
            {
                "title": f"Guided examples for {topic}",
                "description": f"See worked examples that connect the topic to real use cases.",
                "minutes": 20,
            },
            {
                "title": f"Practice for {topic}",
                "description": f"Solve practice items that target the weak areas identified in the diagnostic quiz.",
                "minutes": 20,
            },
        ]

    return [
        {
            "title": f"Start with the basics of {topic}",
            "description": f"Learn the simplest version of {topic} before anything else.",
            "minutes": 15,
        },
        {
            "title": f"Prerequisites for {topic}",
            "description": f"Cover the background ideas needed to understand {topic}.",
            "minutes": 20,
        },
        {
            "title": f"Examples and guided walkthroughs",
            "description": f"Use examples to connect the topic to the weak areas: {focus_line}.",
            "minutes": 20,
        },
        {
            "title": f"Practice and checkpoint",
            "description": f"Do a short practice set and confirm the core ideas of {topic}.",
            "minutes": 20,
        },
        {
            "title": f"Review and next steps",
            "description": f"Summarize progress and identify what should be revised next.",
            "minutes": 10,
        },
    ]


def _build_quick_study_step_plan(topic: str, weak_points: list[str], preferences: dict) -> list[dict]:
    topic = _normalize_text(topic)
    focus_line = ", ".join(weak_points[:2]) if weak_points else f"{topic} essentials"
    session_minutes = _parse_session_minutes(preferences.get("session_length"), 20)
    pace = _pace_multiplier(preferences.get("learning_pace"))
    total_minutes = max(12, min(int(session_minutes * 0.8 * pace), 25))

    step_templates = [
        {
            "title": f"Quick overview of {topic}",
            "description": f"Get the fastest possible picture of {topic} and how the main ideas fit together.",
            "minutes": 6,
        },
        {
            "title": f"Core ideas and examples",
            "description": f"Review the key terms, formulas, or patterns that matter most for {focus_line}.",
            "minutes": 8,
        },
        {
            "title": f"Short recap and next move",
            "description": f"Finish with a concise recap and a lightweight next step for continuing {topic}.",
            "minutes": 6,
        },
    ]

    if total_minutes <= 15:
        return step_templates[:2]
    return step_templates


def _apply_study_mode_scaling(steps: list[dict], preferences: dict, study_mode: str) -> list[dict]:
    session_minutes = _parse_session_minutes(preferences.get("session_length"), 30)
    pace = _pace_multiplier(preferences.get("learning_pace"))
    target_total = session_minutes if study_mode == "quick_study" else max(session_minutes * 2, 40)
    target_total = int(target_total * pace)
    current_total = sum(int(step.get("minutes", 0)) for step in steps) or 1
    scale = target_total / current_total

    if study_mode == "quick_study":
        scale = min(scale, 1.1)
    else:
        scale = max(min(scale, 1.3), 0.9)

    adjusted = []
    running_total = 0
    for index, step in enumerate(steps, start=1):
        minutes = max(5, int(round(step.get("minutes", 0) * scale)))
        if study_mode == "quick_study" and index == len(steps):
            minutes = max(5, min(minutes, max(target_total - running_total, 5)))
        running_total += minutes
        adjusted.append({**step, "minutes": minutes})
    return adjusted


def _build_step_plan(
    topic: str,
    score: float,
    weak_points: list[str],
    study_mode: str,
    preferences: dict,
) -> list[dict]:
    mode = _normalize_study_mode(study_mode)
    if mode == "quick_study":
        return _apply_study_mode_scaling(
            _build_quick_study_step_plan(topic, weak_points, preferences),
            preferences,
            mode,
        )
    return _apply_study_mode_scaling(
        _build_roadmap_step_plan(topic, score, weak_points),
        preferences,
        mode,
    )


def _upsert_subject_topic(conn, subject_id, topic_name, description=None, prerequisite_topic_id=None, topic_order=0):
    topic_name = _normalize_text(topic_name)
    row = conn.execute(
        """
        SELECT topic_id
        FROM topics
        WHERE subject_id = ? AND topic_name = ?
        """,
        (subject_id, topic_name),
    ).fetchone()

    if row:
        topic_id = row["topic_id"]
        conn.execute(
            """
            UPDATE topics
            SET topic_description = COALESCE(?, topic_description),
                prerequisite_topic_id = COALESCE(?, prerequisite_topic_id),
                topic_order = COALESCE(topic_order, ?)
            WHERE topic_id = ?
            """,
            (description, prerequisite_topic_id, topic_order, topic_id),
        )
    else:
        topic_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO topics (
                topic_id, subject_id, topic_name, topic_description,
                prerequisite_topic_id, topic_order, estimated_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                subject_id,
                topic_name,
                description,
                prerequisite_topic_id,
                topic_order,
                15,
            ),
        )

    return topic_id


def build_learning_path(
    conn,
    learner_id: str,
    subject_id: str,
    topic: str,
    diagnostic_result: dict | None = None,
    study_mode: str = "roadmap",
) -> dict:
    topic = _normalize_text(topic)
    mode = _normalize_study_mode(study_mode)
    diagnostic_result = diagnostic_result or {}
    preferences = _load_learner_preferences(conn, learner_id)
    profile_snapshot = _load_active_profile(conn, learner_id, subject_id)

    score = float(diagnostic_result.get("score") or 0)
    weak_points = _extract_weak_points(diagnostic_result) if diagnostic_result else []
    steps = _build_step_plan(topic, score, weak_points, mode, preferences)
    estimated_total_minutes = sum(int(step.get("minutes", 0)) for step in steps)
    content_depth = "concise" if mode == "quick_study" else "detailed"
    focus_line = ", ".join(weak_points[:3]) if weak_points else f"{topic} fundamentals"
    learner_level = profile_snapshot.get("current_level") or "beginner"
    if mode == "roadmap":
        if score >= 0.8:
            learner_level = "advanced"
        elif score >= 0.4:
            learner_level = "intermediate"
        else:
            learner_level = "beginner"
    target_outcome = (
        "Complete a concise study sprint with the essentials"
        if mode == "quick_study"
        else "Build confidence and complete the topic roadmap"
    )
    path_title = f"Quick study: {topic}" if mode == "quick_study" else f"{topic} learning path"
    summary = (
        f"A concise study sprint for {topic} focused on {focus_line}."
        if mode == "quick_study"
        else f"A personalized roadmap for {topic} that starts from {learner_level} and focuses on {focus_line}."
    )

    conn.execute(
        """
        UPDATE learning_paths
        SET path_status = 'archived', updated_at = datetime('now')
        WHERE learner_id = ? AND subject_id = ? AND path_status = 'active'
        """,
        (learner_id, subject_id),
    )

    root_topic_id = _upsert_subject_topic(
        conn,
        subject_id,
        topic,
        description=f"Learning path root for {topic}",
        prerequisite_topic_id=None,
        topic_order=0,
    )

    path_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO learning_paths (
            path_id, learner_id, subject_id, root_topic_id, path_title,
            path_status, target_outcome, total_steps, completed_steps,
            created_at, updated_at, last_accessed_at
        )
        VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 0, datetime('now'), datetime('now'), datetime('now'))
        """,
        (
            path_id,
            learner_id,
            subject_id,
            root_topic_id,
            path_title,
            target_outcome,
            len(steps),
        ),
    )

    prev_topic_id = root_topic_id
    step_rows = []
    for order, step in enumerate(steps, start=1):
        step_topic_id = _upsert_subject_topic(
            conn,
            subject_id,
            step["title"],
            description=step["description"],
            prerequisite_topic_id=prev_topic_id,
            topic_order=order,
        )
        step_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO learning_path_steps (
                step_id, path_id, topic_id, resource_id, chunk_id,
                content_version, step_order, step_title, step_description,
                step_status, estimated_minutes, actual_minutes,
                started_at, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, 'not_started', ?, 0, NULL, NULL, datetime('now'), datetime('now'))
            """,
            (
                step_id,
                path_id,
                step_topic_id,
                None,
                None,
                order,
                step["title"],
                step["description"],
                step["minutes"],
            ),
        )
        step_rows.append(
            {
                "path_id": path_id,
                "step_id": step_id,
                "step_order": order,
                "step_title": step["title"],
                "step_description": step["description"],
                "estimated_minutes": step["minutes"],
                "topic_id": step_topic_id,
                "content_depth": content_depth,
                "step_focus": weak_points[order - 1] if order - 1 < len(weak_points) else topic,
            }
        )
        prev_topic_id = step_topic_id

    cached_views = save_path_views(
        conn,
        learner_id,
        topic,
        mode,
        preferences,
        step_rows,
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
            SET active_path_id = ?,
                current_topic_id = ?,
                goal_type = ?,
                current_level = ?,
                target_level = ?,
                status = 'active',
                path_completion_pct = 0,
                completed_step_count = 0,
                total_step_count = ?,
                last_activity_at = datetime('now'),
                updated_at = datetime('now')
            WHERE learner_id = ? AND subject_id = ?
            """,
            (
                path_id,
                root_topic_id,
                mode,
                learner_level,
                "mastery" if mode == "roadmap" else "understanding",
                len(steps),
                learner_id,
                subject_id,
            ),
        )
    else:
        profile_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO learner_subject_profiles (
                profile_id, learner_id, subject_id, active_path_id, current_topic_id,
                goal_type, current_level, target_level, status, last_assessed_score,
                mastery_score, confidence_score, path_completion_pct, completed_step_count,
                total_step_count, last_activity_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 0, 0, ?, datetime('now'))
            """,
            (
                profile_id,
                learner_id,
                subject_id,
                path_id,
                root_topic_id,
                mode,
                learner_level,
                "mastery" if mode == "roadmap" else "understanding",
                score,
                score,
                min(score + 0.1, 1.0),
                len(steps),
            ),
        )

    return {
        "path_id": path_id,
        "path_title": path_title,
        "mode": mode,
        "root_topic_id": root_topic_id,
        "total_steps": len(step_rows),
        "estimated_total_minutes": estimated_total_minutes,
        "summary": summary,
        "score": score,
        "weak_points": weak_points,
        "steps": step_rows,
        "cached_views": cached_views,
        "created_at": _now_iso(),
        "content_depth": content_depth,
        "target_outcome": target_outcome,
    }
