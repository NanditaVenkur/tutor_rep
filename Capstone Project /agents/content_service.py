import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime


def _now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _canonicalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _hash_content(*parts) -> str:
    payload = json.dumps([str(part or "") for part in parts], ensure_ascii=False, sort_keys=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_session_minutes(session_length: str | None, fallback: int = 30) -> int:
    text = _normalize_text(session_length).lower()
    match = re.search(r"(\d+)", text)
    if match:
        return max(int(match.group(1)), 5)
    return fallback


def _view_style(preferences: dict | None, mode: str) -> tuple[str, str]:
    preferences = preferences or {}
    explanation_style = _normalize_text(preferences.get("explanation_style") or "step_by_step")
    learning_pace = _normalize_text(preferences.get("learning_pace") or "normal")
    if mode == "quick_study":
        return "concise", learning_pace
    return explanation_style, learning_pace


def _fetch_current_step_source(conn, step_id: str) -> dict:
    row = conn.execute(
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
            lps.estimated_minutes,
            cr.title AS source_title,
            cr.source_name,
            cr.source_uri,
            cr.vector_collection,
            cr.vector_doc_id,
            cr.content_version AS resource_content_version,
            cc.chunk_text,
            cc.vector_chunk_id,
            cc.chunk_version
        FROM learning_path_steps lps
        LEFT JOIN content_resources cr ON cr.resource_id = lps.resource_id
        LEFT JOIN content_chunks cc ON cc.chunk_id = lps.chunk_id
        WHERE lps.step_id = ?
        LIMIT 1
        """,
        (step_id,),
    ).fetchone()
    return dict(row) if row else {}


def get_cached_step_view(conn, learner_id: str, step_id: str) -> dict | None:
    row = conn.execute(
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
        WHERE learner_id = ? AND step_id = ? AND view_status = 'active'
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (learner_id, step_id),
    ).fetchone()
    return dict(row) if row else None


def _build_rendered_content(step: dict, topic: str, preferences: dict, source_text: str, mode: str) -> dict:
    step_title = _normalize_text(step.get("step_title") or step.get("title") or topic)
    step_description = _normalize_text(step.get("step_description") or "")
    source_text = _normalize_text(source_text or step_description or topic)
    explanation_style, reading_level = _view_style(preferences, mode)
    session_minutes = _parse_session_minutes(preferences.get("session_length"), 30)

    if mode == "quick_study":
        rendered_summary = (
            f"Quick study summary for {topic}: {step_title}. Focus on the essentials and move on."
        )
        rendered_content = "\n".join(
            [
                f"# {step_title}",
                "",
                "## Quick summary",
                source_text,
                "",
                "## Key takeaways",
                f"- {step_description or source_text}",
                f"- Keep this as a {session_minutes}-minute review.",
                "",
                "## Remember",
                f"- {topic} should feel manageable after this step.",
            ]
        )
    else:
        rendered_summary = f"Detailed learning step for {topic}: {step_title}."
        rendered_content = "\n".join(
            [
                f"# {step_title}",
                "",
                "## Why this step matters",
                step_description or f"This step helps build your understanding of {topic}.",
                "",
                "## Explanation",
                source_text,
                "",
                "## Example",
                f"Think through how this idea appears in {topic} problems or examples.",
                "",
                "## Key takeaways",
                f"- {step_description or source_text}",
                f"- Study it using a {explanation_style} style.",
            ]
        )

    content_hash = _hash_content(
        topic,
        mode,
        step_title,
        step_description,
        source_text,
        explanation_style,
        reading_level,
    )
    rendered_format = "markdown"
    return {
        "rendered_title": step_title,
        "rendered_summary": rendered_summary,
        "rendered_content": rendered_content,
        "rendered_format": rendered_format,
        "reading_level": reading_level,
        "content_hash": content_hash,
    }


def save_step_view(
    conn,
    learner_id: str,
    topic: str,
    mode: str,
    preferences: dict | None,
    step: dict,
    source: dict | None = None,
) -> dict:
    source = source or {}
    step_id = str(step.get("step_id") or "")
    if not step_id:
        return {}

    existing = get_cached_step_view(conn, learner_id, step_id)
    source_text = source.get("chunk_text") or step.get("step_description") or topic
    rendered = _build_rendered_content(step, topic, preferences or {}, source_text, mode)

    source_resource_id = source.get("resource_id") or step.get("resource_id")
    source_chunk_id = source.get("chunk_id") or step.get("chunk_id")
    source_content_version = int(
        source.get("content_version")
        or source.get("chunk_version")
        or source.get("resource_content_version")
        or step.get("content_version")
        or 1
    )

    payload = {
        "path_id": step.get("path_id"),
        "step_id": step_id,
        "topic_id": step.get("topic_id"),
        "source_resource_id": source_resource_id,
        "source_chunk_id": source_chunk_id,
        "source_content_version": source_content_version,
        **rendered,
    }

    if existing and existing.get("content_hash") == payload["content_hash"]:
        return existing

    if existing:
        conn.execute(
            """
            UPDATE learner_content_views
            SET path_id = ?,
                topic_id = ?,
                source_resource_id = ?,
                source_chunk_id = ?,
                source_content_version = ?,
                rendered_title = ?,
                rendered_summary = ?,
                rendered_content = ?,
                rendered_format = ?,
                reading_level = ?,
                content_hash = ?,
                view_status = 'active',
                updated_at = datetime('now')
            WHERE view_id = ?
            """,
            (
                payload["path_id"],
                payload["topic_id"],
                payload["source_resource_id"],
                payload["source_chunk_id"],
                payload["source_content_version"],
                payload["rendered_title"],
                payload["rendered_summary"],
                payload["rendered_content"],
                payload["rendered_format"],
                payload["reading_level"],
                payload["content_hash"],
                existing["view_id"],
            ),
        )
        payload["view_id"] = existing["view_id"]
    else:
        view_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO learner_content_views (
                view_id, learner_id, path_id, step_id, topic_id,
                source_resource_id, source_chunk_id, source_content_version,
                rendered_title, rendered_summary, rendered_content,
                rendered_format, reading_level, content_hash, view_status,
                rendered_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', datetime('now'), datetime('now'))
            """,
            (
                view_id,
                learner_id,
                payload["path_id"],
                payload["step_id"],
                payload["topic_id"],
                payload["source_resource_id"],
                payload["source_chunk_id"],
                payload["source_content_version"],
                payload["rendered_title"],
                payload["rendered_summary"],
                payload["rendered_content"],
                payload["rendered_format"],
                payload["reading_level"],
                payload["content_hash"],
            ),
        )
        payload["view_id"] = view_id

    payload["rendered_at"] = _now_iso()
    payload["updated_at"] = _now_iso()
    return payload


def save_path_views(conn, learner_id: str, topic: str, mode: str, preferences: dict | None, steps: list[dict]) -> list[dict]:
    saved_views = []
    for step in steps:
        saved = save_step_view(conn, learner_id, topic, mode, preferences, step)
        if saved:
            saved_views.append(saved)
    return saved_views


def ensure_dashboard_step_view(
    conn,
    learner_id: str,
    topic: str,
    mode: str,
    preferences: dict | None,
    current_step_id: str,
) -> dict | None:
    existing = get_cached_step_view(conn, learner_id, current_step_id)
    if existing:
        return existing

    step = _fetch_current_step_source(conn, current_step_id)
    if not step:
        return None
    return save_step_view(conn, learner_id, topic, mode, preferences, step, source=step)
