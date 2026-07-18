import uuid
from datetime import datetime, timedelta


MASTERED_THRESHOLD = 0.82
STRONG_THRESHOLD = 0.68
REVIEW_THRESHOLD = 0.5


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _normalize_score(value):
    if value is None:
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if score > 1.0:
        score /= 100.0
    return _clamp(score)


def _utc_now():
    return datetime.utcnow().replace(microsecond=0)


def _iso(dt):
    return dt.replace(microsecond=0).isoformat(sep=" ")


def _review_window_days(mastery_probability, score):
    if mastery_probability >= MASTERED_THRESHOLD and score >= 0.9:
        return 14
    if mastery_probability >= STRONG_THRESHOLD and score >= 0.8:
        return 7
    if mastery_probability >= REVIEW_THRESHOLD and score >= 0.65:
        return 3
    return 1


def _mastery_status(mastery_probability, score):
    if mastery_probability >= MASTERED_THRESHOLD and score >= 0.8:
        return "mastered"
    if mastery_probability >= STRONG_THRESHOLD:
        return "review_due"
    return "needs_review"


def _recommendation(mastery_probability, score):
    if mastery_probability >= STRONG_THRESHOLD and score >= 0.75:
        return "complete"
    return "retry"


def _step_concept_mastery_average(conn, learner_id, subject_id, step_id):
    if not step_id:
        return None
    rows = conn.execute(
        """
        SELECT mastery_probability
        FROM concept_mastery
        WHERE learner_id = ?
          AND subject_id = ?
          AND step_id = ?
        """,
        (learner_id, subject_id, step_id),
    ).fetchall()
    probabilities = [_normalize_score(row["mastery_probability"]) for row in rows]
    if not probabilities:
        return None
    return _clamp(sum(probabilities) / len(probabilities))


def _load_attempt_bundle(conn, attempt_id):
    attempt = conn.execute(
        """
        SELECT
            qa.attempt_id,
            qa.learner_id,
            qa.subject_id,
            qa.path_id,
            qa.step_id,
            qa.quiz_type,
            qa.score,
            qa.total_questions,
            qa.correct_answers,
            qa.completion_status,
            qa.mastery_delta,
            qa.started_at,
            qa.completed_at,
            lps.topic_id,
            lps.step_order,
            lps.step_title,
            lps.step_description,
            lps.step_status,
            lps.preview_terms,
            lsp.profile_id,
            lsp.active_path_id,
            lsp.current_topic_id,
            lsp.goal_type,
            lsp.current_level,
            lsp.target_level,
            lsp.status AS profile_status,
            lsp.last_assessed_score,
            lsp.mastery_score,
            lsp.confidence_score,
            lsp.path_completion_pct,
            lsp.completed_step_count,
            lsp.total_step_count,
            lsp.next_review_at,
            tm.mastery_id,
            tm.mastery_probability,
            tm.last_assessed_score AS topic_last_assessed_score,
            tm.review_due_at,
            tm.last_practiced_at,
            tm.mastery_status
        FROM quiz_attempts qa
        LEFT JOIN learning_path_steps lps
            ON lps.step_id = qa.step_id
        LEFT JOIN learner_subject_profiles lsp
            ON lsp.learner_id = qa.learner_id
           AND lsp.subject_id = qa.subject_id
        LEFT JOIN topic_mastery tm
            ON tm.learner_id = qa.learner_id
           AND tm.subject_id = qa.subject_id
           AND tm.topic_id = lps.topic_id
        WHERE qa.attempt_id = ?
        LIMIT 1
        """,
        (attempt_id,),
    ).fetchone()
    if not attempt:
        raise ValueError("Quiz attempt not found")
    return dict(attempt)


def _load_responses(conn, attempt_id):
    return [
        dict(row)
        for row in conn.execute(
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
                question_difficulty,
                difficulty_after,
                explanation,
                created_at
            FROM quiz_responses
            WHERE attempt_id = ?
            ORDER BY created_at ASC
            """,
            (attempt_id,),
        ).fetchall()
    ]


def _subject_next_review_at(conn, learner_id, subject_id, fallback_due_at):
    row = conn.execute(
        """
        SELECT MIN(review_due_at) AS next_review_at
        FROM topic_mastery
        WHERE learner_id = ?
          AND subject_id = ?
          AND review_due_at IS NOT NULL
        """,
        (learner_id, subject_id),
    ).fetchone()
    next_review_at = row["next_review_at"] if row else None
    return next_review_at or fallback_due_at


def update_mastery_after_quiz(conn, attempt_id):
    bundle = _load_attempt_bundle(conn, attempt_id)
    responses = _load_responses(conn, attempt_id)
    if not responses:
        raise ValueError("No quiz responses were found for this attempt")

    topic_id = bundle.get("topic_id") or bundle.get("current_topic_id")
    if not topic_id:
        raise ValueError("Unable to determine the topic for this attempt")

    total_questions = int(bundle.get("total_questions") or len(responses) or 0)
    correct_answers = int(bundle.get("correct_answers") or 0)
    if not total_questions:
        total_questions = len(responses)
    if not correct_answers:
        correct_answers = sum(1 for response in responses if int(response.get("is_correct") or 0))

    score = _normalize_score(bundle.get("score"))
    accuracy = _clamp(correct_answers / total_questions) if total_questions else score
    observed_score = score if score else accuracy

    current_mastery = bundle.get("mastery_probability")
    if current_mastery is None:
        current_mastery = bundle.get("mastery_score")
    current_mastery = _normalize_score(current_mastery)

    concept_mastery_average = _step_concept_mastery_average(
        conn,
        bundle["learner_id"],
        bundle["subject_id"],
        bundle.get("step_id"),
    )
    if concept_mastery_average is not None:
        updated_mastery = concept_mastery_average
    else:
        blended_score = (observed_score * 0.7) + (accuracy * 0.3)
        updated_mastery = _clamp((current_mastery * 0.55) + (blended_score * 0.45))
        if accuracy >= 0.9:
            updated_mastery = _clamp(updated_mastery + 0.04)
        elif accuracy < 0.6:
            updated_mastery = _clamp(updated_mastery - 0.06)

    review_days = _review_window_days(updated_mastery, accuracy)
    review_due_at = _iso(_utc_now() + timedelta(days=review_days))
    mastery_status = _mastery_status(updated_mastery, accuracy)
    recommendation = _recommendation(updated_mastery, accuracy)
    review_flagged = recommendation == "retry"

    confidence_score = bundle.get("confidence_score")
    confidence_score = _normalize_score(confidence_score) if confidence_score is not None else 0.5
    updated_confidence = _clamp((confidence_score * 0.5) + (accuracy * 0.5))
    mastery_delta = round(updated_mastery - current_mastery, 3)

    profile_mastery = bundle.get("mastery_score")
    profile_mastery = _normalize_score(profile_mastery) if profile_mastery is not None else updated_mastery
    updated_profile_mastery = _clamp((profile_mastery * 0.65) + (updated_mastery * 0.35))

    next_review_at = _subject_next_review_at(conn, bundle["learner_id"], bundle["subject_id"], review_due_at)

    mastery_row = conn.execute(
        """
        SELECT mastery_id
        FROM topic_mastery
        WHERE learner_id = ? AND topic_id = ?
        LIMIT 1
        """,
        (bundle["learner_id"], topic_id),
    ).fetchone()
    mastery_id = mastery_row["mastery_id"] if mastery_row else str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO topic_mastery (
            mastery_id, learner_id, subject_id, topic_id,
            mastery_probability, last_assessed_score, review_due_at,
            last_practiced_at, mastery_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(learner_id, topic_id) DO UPDATE SET
            subject_id = excluded.subject_id,
            mastery_probability = excluded.mastery_probability,
            last_assessed_score = excluded.last_assessed_score,
            review_due_at = excluded.review_due_at,
            last_practiced_at = excluded.last_practiced_at,
            mastery_status = excluded.mastery_status,
            updated_at = datetime('now')
        """,
        (
            mastery_id,
            bundle["learner_id"],
            bundle["subject_id"],
            topic_id,
            updated_mastery,
            accuracy,
            review_due_at,
            _iso(_utc_now()),
            mastery_status,
        ),
    )

    if bundle.get("step_id"):
        conn.execute(
            """
            UPDATE learning_path_steps
            SET step_status = ?,
                updated_at = datetime('now')
            WHERE step_id = ?
            """,
            ("needs_review" if review_flagged else "completed", bundle["step_id"]),
        )

    profile_row = conn.execute(
        """
        SELECT profile_id
        FROM learner_subject_profiles
        WHERE learner_id = ? AND subject_id = ?
        LIMIT 1
        """,
        (bundle["learner_id"], bundle["subject_id"]),
    ).fetchone()
    profile_id = profile_row["profile_id"] if profile_row else str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO learner_subject_profiles (
            profile_id, learner_id, subject_id, active_path_id, current_topic_id,
            goal_type, current_level, target_level, status,
            last_assessed_score, mastery_score, confidence_score,
            path_completion_pct, completed_step_count, total_step_count,
            next_review_at, last_activity_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), datetime('now'))
        ON CONFLICT(learner_id, subject_id) DO UPDATE SET
            active_path_id = excluded.active_path_id,
            current_topic_id = excluded.current_topic_id,
            goal_type = excluded.goal_type,
            current_level = excluded.current_level,
            target_level = excluded.target_level,
            status = excluded.status,
            last_assessed_score = excluded.last_assessed_score,
            mastery_score = excluded.mastery_score,
            confidence_score = excluded.confidence_score,
            path_completion_pct = excluded.path_completion_pct,
            completed_step_count = excluded.completed_step_count,
            total_step_count = excluded.total_step_count,
            next_review_at = excluded.next_review_at,
            last_activity_at = excluded.last_activity_at,
            updated_at = datetime('now')
        """,
        (
            profile_id,
            bundle["learner_id"],
            bundle["subject_id"],
            bundle.get("active_path_id") or bundle.get("path_id"),
            topic_id,
            bundle.get("goal_type"),
            bundle.get("current_level"),
            bundle.get("target_level"),
            bundle.get("profile_status") or "active",
            accuracy,
            updated_profile_mastery,
            updated_confidence,
            bundle.get("path_completion_pct") or 0,
            bundle.get("completed_step_count") or 0,
            bundle.get("total_step_count") or 0,
            next_review_at,
        ),
    )

    conn.execute(
        """
        UPDATE quiz_attempts
        SET mastery_delta = ?,
            completed_at = COALESCE(completed_at, datetime('now'))
        WHERE attempt_id = ?
        """,
        (mastery_delta, bundle["attempt_id"]),
    )

    return {
        "attempt_id": bundle["attempt_id"],
        "learner_id": bundle["learner_id"],
        "subject_id": bundle["subject_id"],
        "topic_id": topic_id,
        "step_id": bundle.get("step_id"),
        "score": accuracy,
        "correct_answers": correct_answers,
        "total_questions": total_questions,
        "updated_mastery_probability": updated_mastery,
        "updated_mastery_score": updated_profile_mastery,
        "updated_confidence_score": updated_confidence,
        "mastery_delta": mastery_delta,
        "review_due_at": review_due_at,
        "next_review_at": next_review_at,
        "mastery_status": mastery_status,
        "recommendation": recommendation,
        "review_flagged": review_flagged,
        "responses": responses,
    }
