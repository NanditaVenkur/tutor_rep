import json
import math
import os
import random as _random
import re
import sqlite3
import uuid
from pathlib import Path

from dotenv import load_dotenv
from langchain_groq import ChatGroq

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "backend" / "adaptive_tutor_v2.db"
SCHEMA_PATH = PROJECT_ROOT / "data" / "sql" / "user_profile_schema.sql"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
DIFFICULTIES = ["easy", "medium", "hard"]
ADAPTIVE_QUIZ_LENGTH = 15
QUESTIONS_PER_PREVIEW_TERM = 5
PASS_THRESHOLD = 0.80
BKT_DEFAULT_PRIOR = 0.25
BKT_DEFAULT_TRANSIT = 0.12
BKT_DEFAULT_GUESS = 0.20
BKT_DEFAULT_SLIP = 0.10
BLOOM_SCORES = {
    "remember": 1,
    "understand": 2,
    "apply": 3,
    "analyze": 4,
    "evaluate": 4,
    "create": 4,
}
DIFFICULTY_SCORE_RANGES = {
    "easy": 1.5,
    "medium": 2.5,
    "hard": 3.5,
}

LLM = None


def _normalize_options(options):
    if isinstance(options, dict):
        return {str(key).strip().upper(): str(value).strip() for key, value in options.items() if str(value or "").strip()}
    if isinstance(options, list):
        letters = ["A", "B", "C", "D"]
        return {letters[i] if i < len(letters) else chr(65 + i): str(opt).strip() for i, opt in enumerate(options) if str(opt or "").strip()}
    if isinstance(options, str):
        return {"A": options}
    return {}


def _canonicalize_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalize_text(value):
    return " ".join(str(value or "").strip().split())


def _normalize_difficulty(value, default="medium"):
    normalized = _normalize_text(value).lower()
    return normalized if normalized in DIFFICULTIES else default


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _bkt_update(prior, is_correct, transit=BKT_DEFAULT_TRANSIT, guess=BKT_DEFAULT_GUESS, slip=BKT_DEFAULT_SLIP):
    prior = _clamp(prior)
    transit = _clamp(transit)
    guess = _clamp(guess, 0.01, 0.99)
    slip = _clamp(slip, 0.01, 0.99)

    if is_correct:
        numerator = prior * (1.0 - slip)
        denominator = numerator + ((1.0 - prior) * guess)
    else:
        numerator = prior * slip
        denominator = numerator + ((1.0 - prior) * (1.0 - guess))

    posterior_given_response = numerator / denominator if denominator else prior
    return _clamp(posterior_given_response + ((1.0 - posterior_given_response) * transit))


def calculate_difficulty_score(bloom_level, concept_count, reasoning_steps):
    bloom = BLOOM_SCORES.get(_normalize_text(bloom_level).lower(), 3)
    concepts = max(1, min(_safe_int(concept_count, 1), 4))
    reasoning = max(1, min(_safe_int(reasoning_steps, 1), 4))
    return round((0.40 * bloom) + (0.25 * concepts) + (0.35 * reasoning), 3)


def difficulty_label_from_score(score):
    score = float(score)
    if score <= 2.0:
        return "easy"
    if score <= 3.0:
        return "medium"
    return "hard"


def _parse_preview_terms(value) -> list[str]:
    if isinstance(value, list):
        return [_normalize_text(item) for item in value if _normalize_text(item)]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [_normalize_text(item) for item in parsed if _normalize_text(item)]


def _extract_json(raw):
    raw = getattr(raw, "content", str(raw)).strip()
    array_start = raw.find("[")
    array_end = raw.rfind("]")
    object_start = raw.find("{")
    object_end = raw.rfind("}")

    if array_start != -1 and array_end != -1 and (object_start == -1 or array_start < object_start):
        return json.loads(raw[array_start:array_end + 1])
    if object_start != -1 and object_end != -1:
        return json.loads(raw[object_start:object_end + 1])
    raise ValueError("LLM did not return JSON")


def _extract_option_key(raw_value):
    text = str(raw_value or "").strip().upper()
    match = re.search(r"\b([A-D])\b", text)
    if match:
        return match.group(1)
    if text in {"A", "B", "C", "D"}:
        return text
    return ""


def _match_option_key_from_text(candidate, options):
    candidate_text = _canonicalize_text(candidate)
    if not candidate_text:
        return ""
    normalized_options = _normalize_options(options)
    for key, value in normalized_options.items():
        option_text = _canonicalize_text(value)
        if not option_text:
            continue
        if option_text == candidate_text or option_text in candidate_text or candidate_text in option_text:
            return str(key).strip().upper()
    return ""


def _normalize_correct_answer(raw_correct_answer, options):
    if raw_correct_answer is None:
        return ""

    correct = str(raw_correct_answer).strip()
    if not correct:
        return ""

    normalized_options = _normalize_options(options)
    option_keys = {str(key).strip().upper(): str(key).strip().upper() for key in normalized_options.keys()}
    option_values = {_canonicalize_text(value): str(key).strip().upper() for key, value in normalized_options.items()}
    upper_correct = correct.upper()
    extracted_key = _extract_option_key(correct)

    if extracted_key and extracted_key in option_keys:
        return option_keys[extracted_key]
    if upper_correct in option_keys:
        return option_keys[upper_correct]
    if _canonicalize_text(correct) in option_values:
        return option_values[_canonicalize_text(correct)]
    matched_key = _match_option_key_from_text(correct, normalized_options)
    if matched_key:
        return matched_key
    return upper_correct


def _normalize_quiz_question_item(item, topic=None):
    item = dict(item or {})
    question_text = item.get("question_text") or item.get("question") or item.get("prompt")
    item["question"] = _normalize_text(question_text)
    item["options"] = _normalize_options(item.get("options", {}))
    item["correct_answer"] = _normalize_correct_answer(item.get("correct_answer", ""), item["options"])
    item["explanation"] = _normalize_text(item.get("explanation") or "Explanation not available.")
    item["concept"] = _normalize_text(item.get("concept") or item.get("concept_tag") or topic or "")
    return item


def _get_llm():
    global LLM
    if LLM is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        LLM = ChatGroq(model=GROQ_MODEL, api_key=api_key)
    return LLM


def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.commit()
    conn.close()


def difficulty_to_index(difficulty):
    normalized = _normalize_difficulty(difficulty)
    return DIFFICULTIES.index(normalized)


def adjust_difficulty(current_difficulty, is_correct):
    index = difficulty_to_index(current_difficulty)
    index += 1 if is_correct else -1
    index = max(0, min(index, len(DIFFICULTIES) - 1))
    return DIFFICULTIES[index]


def determine_starting_difficulty(profile, mastery=None):
    if mastery is not None:
        mastery = float(mastery)
        if mastery < 0.4:
            return "easy"
        if mastery < 0.8:
            return "medium"
        return "hard"

    level = str(profile.get("current_level") or "").lower()
    return {
        "beginner": "easy",
        "new": "easy",
        "intermediate": "medium",
        "advanced": "hard",
    }.get(level, "medium")


def load_adaptive_quiz_context(conn, learner_id, subject_id, path_id, step_id):
    row = conn.execute(
        """
        SELECT
            lp.path_id,
            lp.learner_id,
            lp.subject_id,
            lp.path_status,
            lp.total_steps,

            lps.step_id,
            lps.topic_id,
            lps.step_order,
            lps.step_title,
            lps.step_description,
            lps.preview_terms,
            lps.step_status,

            lsp.current_level,
            lsp.mastery_score,

            tm.mastery_probability,

            lcv.rendered_title,
            lcv.rendered_summary,
            lcv.rendered_content,

            l.age_group,
            l.role,
            l.full_name,

            lpr.quiz_style,
            lpr.explanation_style,
            lpr.feedback_style,
            lpr.accessibility_notes
        FROM learning_paths lp
        JOIN learning_path_steps lps ON lps.path_id = lp.path_id
        LEFT JOIN learner_subject_profiles lsp
            ON lsp.learner_id = lp.learner_id
            AND lsp.subject_id = lp.subject_id
        LEFT JOIN topic_mastery tm
            ON tm.learner_id = lp.learner_id
            AND tm.subject_id = lp.subject_id
            AND tm.topic_id = lps.topic_id
        LEFT JOIN learner_content_views lcv
            ON lcv.learner_id = lp.learner_id
            AND lcv.step_id = lps.step_id
            AND lcv.view_status = 'active'
        LEFT JOIN learners l ON l.learner_id = lp.learner_id
        LEFT JOIN learner_preferences lpr ON lpr.learner_id = lp.learner_id
        WHERE lp.path_id = ?
          AND lp.learner_id = ?
          AND lp.subject_id = ?
          AND lps.step_id = ?
        ORDER BY lcv.updated_at DESC
        LIMIT 1
        """,
        (path_id, learner_id, subject_id, step_id),
    ).fetchone()

    if not row:
        raise ValueError("The learner, subject, path and step do not belong together")

    context = dict(row)
    preview_terms = _parse_preview_terms(context.get("preview_terms"))
    if not preview_terms:
        preview_terms = [_normalize_text(context.get("step_title"))]
    return {
        "learner_id": context["learner_id"],
        "subject_id": context["subject_id"],
        "path_id": context["path_id"],
        "step_id": context["step_id"],
        "topic_id": context["topic_id"],
        "step_order": context.get("step_order"),
        "total_steps": context.get("total_steps"),
        "step_title": context["step_title"],
        "step_description": context.get("step_description") or "",
        "preview_terms": preview_terms,
        "step_status": context.get("step_status"),
        "path_status": context.get("path_status"),
        "current_level": context.get("current_level") or "beginner",
        "mastery_score": context.get("mastery_score"),
        "mastery_probability": context.get("mastery_probability"),
    }


def generate_questions_for_subtopic(context, subtopic, question_count=QUESTIONS_PER_PREVIEW_TERM):
    prompt = f"""
        Generate {question_count} diverse multiple-choice questions for this subtopic.
        Cover different concepts.
        Cover different Bloom levels.
        Do NOT assign difficulty.
        Return JSON only.

        Subject/step context:
        Step title: {context['step_title']}
        Step description: {context.get('step_description') or ''}
        Subtopic: {subtopic}

        Return a JSON array. Each item must contain:
        - question_text
        - options as an object with A, B, C, D
        - correct_answer as the option key only
        - explanation
        - concept

        Rules:
        - Questions must be answerable from the step title, description, and subtopic.
        - Do not include difficulty.
        - Avoid unrelated domains unless they are part of the subtopic.
    """
    items = _extract_json(_get_llm().invoke(prompt))
    if isinstance(items, dict):
        items = items.get("questions", [])
    if not isinstance(items, list):
        raise ValueError("LLM did not return a question array")

    questions = []
    for item in items[:question_count]:
        question = _normalize_quiz_question_item(item, topic=subtopic)
        if question.get("question") and question.get("options") and question.get("correct_answer"):
            questions.append(question)
    if not questions:
        raise RuntimeError(f"Failed to generate adaptive quiz questions for {subtopic}")
    return questions


def calibrate_question_difficulty(context, subtopic, question):
    prompt = f"""
        Calibrate this multiple-choice question for an adaptive quiz.
        Return JSON only in this exact shape:
        {{
          "bloom": "Apply",
          "concepts": 2,
          "reasoning_steps": 2
        }}

        Bloom must be one of Remember, Understand, Apply, Analyze, Evaluate, Create.
        concepts is the number of distinct concepts required, from 1 to 4.
        reasoning_steps is the number of reasoning steps required, from 1 to 4.
        Do not assign a difficulty label; it is calculated deterministically.

        Step title: {context['step_title']}
        Subtopic: {subtopic}
        Concept: {question.get('concept')}
        Question: {question['question']}
        Options: {json.dumps(question['options'])}
        Correct answer: {question['correct_answer']}
    """
    try:
        data = _extract_json(_get_llm().invoke(prompt))
    except Exception:
        data = {}
    bloom_level = _normalize_text(data.get("bloom") if isinstance(data, dict) else "") or "Apply"
    concept_count = max(1, min(_safe_int(data.get("concepts") if isinstance(data, dict) else None, 1), 4))
    reasoning_steps = max(1, min(_safe_int(data.get("reasoning_steps") if isinstance(data, dict) else None, 1), 4))
    difficulty_score = calculate_difficulty_score(bloom_level, concept_count, reasoning_steps)
    return {
        "bloom_level": bloom_level,
        "concept_count": concept_count,
        "reasoning_steps": reasoning_steps,
        "difficulty_score": difficulty_score,
        "difficulty": difficulty_label_from_score(difficulty_score),
    }


def save_bank_question(conn, context, topic, question, calibration):
    question_id = str(uuid.uuid4())
    difficulty = calibration["difficulty"]
    conn.execute(
        """
        INSERT INTO quiz_questions (
            question_id, attempt_id, subject_id, path_id, step_id, topic, concept,
            question_text, options_json, correct_answer, explanation, difficulty_level,
            bloom_level, concept_count, reasoning_steps, difficulty_score, calibrated_difficulty,
            question_source, is_active
        )
        VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'adaptive_bank', 1)
        """,
        (
            question_id,
            context["subject_id"],
            context["path_id"],
            context["step_id"],
            topic,
            question.get("concept") or topic,
            question["question"],
            json.dumps(question["options"]),
            question["correct_answer"],
            question.get("explanation"),
            difficulty,
            calibration.get("bloom_level"),
            calibration.get("concept_count"),
            calibration.get("reasoning_steps"),
            calibration.get("difficulty_score"),
            difficulty,
        ),
    )
    return question_id


def existing_bank_count(conn, step_id, topic=None):
    if topic:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM quiz_questions
            WHERE step_id = ?
              AND topic = ?
              AND question_source = 'adaptive_bank'
              AND is_active = 1
            """,
            (step_id, topic),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM quiz_questions
            WHERE step_id = ?
              AND question_source = 'adaptive_bank'
              AND is_active = 1
            """,
            (step_id,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def ensure_calibrated_question_bank(conn, context):
    topics = context.get("preview_terms") or [context["step_title"]]
    questions_per_topic = max(
        QUESTIONS_PER_PREVIEW_TERM,
        math.ceil(ADAPTIVE_QUIZ_LENGTH / max(len(topics), 1)),
    )
    created = 0
    for topic in topics:
        existing = existing_bank_count(conn, context["step_id"], topic)
        missing = max(0, questions_per_topic - existing)
        if missing <= 0:
            continue
        questions = generate_questions_for_subtopic(context, topic, missing)
        for question in questions[:missing]:
            calibration = calibrate_question_difficulty(context, topic, question)
            save_bank_question(conn, context, topic, question, calibration)
            created += 1
    return created


def _difficulty_order_for(target_difficulty):
    target_index = difficulty_to_index(target_difficulty)
    return sorted(DIFFICULTIES, key=lambda difficulty: abs(difficulty_to_index(difficulty) - target_index))


def _concept_key(row):
    return _normalize_text(row.get("concept") or row.get("topic") or "").lower()


def _load_attempt_concept_state(conn, attempt_id):
    attempt = conn.execute(
        """
        SELECT qa.learner_id, qa.subject_id, qa.step_id,
               COALESCE(tm.mastery_probability, lsp.mastery_score) AS baseline_mastery
        FROM quiz_attempts qa
        LEFT JOIN learning_path_steps lps ON lps.step_id = qa.step_id
        LEFT JOIN topic_mastery tm
          ON tm.learner_id = qa.learner_id
         AND tm.subject_id = qa.subject_id
         AND tm.topic_id = lps.topic_id
        LEFT JOIN learner_subject_profiles lsp
          ON lsp.learner_id = qa.learner_id
         AND lsp.subject_id = qa.subject_id
        WHERE qa.attempt_id = ?
        LIMIT 1
        """,
        (attempt_id,),
    ).fetchone()
    if not attempt:
        return {}, {}, 0.5

    baseline = attempt["baseline_mastery"]
    baseline = _clamp(baseline) if baseline is not None else 0.5
    mastery = {}
    for row in conn.execute(
        """
        SELECT concept, mastery_probability
        FROM concept_mastery
        WHERE learner_id = ? AND subject_id = ? AND step_id = ?
        """,
        (attempt["learner_id"], attempt["subject_id"], attempt["step_id"]),
    ).fetchall():
        mastery[_normalize_text(row["concept"]).lower()] = _clamp(row["mastery_probability"])

    exposure = {}
    for row in conn.execute(
        """
        SELECT qq.concept, qq.topic, COUNT(*) AS response_count
        FROM quiz_responses qr
        JOIN quiz_questions qq ON qq.question_id = qr.question_id
        WHERE qr.attempt_id = ?
        GROUP BY LOWER(COALESCE(qq.concept, qq.topic, ''))
        """,
        (attempt_id,),
    ).fetchall():
        key = _normalize_text(row["concept"] or row["topic"] or "").lower()
        exposure[key] = int(row["response_count"] or 0)
    return mastery, exposure, baseline


def _question_information_score(row, target_difficulty, mastery, exposure, baseline):
    key = _concept_key(row)
    concept_mastery = mastery.get(key, baseline)
    difficulty_score = row.get("difficulty_score")
    if difficulty_score is None:
        label = _normalize_difficulty(row.get("calibrated_difficulty") or row.get("difficulty_level"))
        difficulty_score = DIFFICULTY_SCORE_RANGES[label]
    difficulty_score = float(difficulty_score)

    learner_ability = 1.0 + (3.0 * concept_mastery)
    probability_correct = 1.0 / (1.0 + math.exp(-1.7 * (learner_ability - difficulty_score)))
    response_information = 4.0 * probability_correct * (1.0 - probability_correct)
    mastery_uncertainty = 4.0 * concept_mastery * (1.0 - concept_mastery)
    weakness_priority = 1.0 - concept_mastery
    coverage = 1.0 / (1.0 + exposure.get(key, 0))
    target_score = DIFFICULTY_SCORE_RANGES[_normalize_difficulty(target_difficulty)]
    difficulty_fit = max(0.0, 1.0 - (abs(difficulty_score - target_score) / 3.0))

    return (
        (0.45 * response_information)
        + (0.25 * mastery_uncertainty)
        + (0.15 * weakness_priority)
        + (0.10 * coverage)
        + (0.05 * difficulty_fit)
    )


def select_next_bank_question(conn, attempt_id, step_id, target_difficulty):
    rows = conn.execute(
        """
        SELECT question_id, question_text, options_json, correct_answer, explanation,
               difficulty_level, calibrated_difficulty, topic, concept, bloom_level,
               concept_count, reasoning_steps, difficulty_score
        FROM quiz_questions
        WHERE step_id = ?
          AND question_source = 'adaptive_bank'
          AND is_active = 1
          AND question_id NOT IN (
              SELECT question_id
              FROM quiz_responses
              WHERE attempt_id = ? AND question_id IS NOT NULL
          )
        """,
        (step_id, attempt_id),
    ).fetchall()
    if not rows:
        return None

    mastery, exposure, baseline = _load_attempt_concept_state(conn, attempt_id)
    candidates = [dict(row) for row in rows]
    return max(
        candidates,
        key=lambda row: (
            _question_information_score(row, target_difficulty, mastery, exposure, baseline),
            -abs(
                float(row.get("difficulty_score") or DIFFICULTY_SCORE_RANGES[
                    _normalize_difficulty(row.get("calibrated_difficulty") or row.get("difficulty_level"))
                ])
                - DIFFICULTY_SCORE_RANGES[_normalize_difficulty(target_difficulty)]
            ),
            row["question_id"],
        ),
    )


def update_concept_mastery_after_response(conn, attempt, question, is_correct):
    concept = _normalize_text(question.get("concept") or question.get("topic") or "")
    if not concept:
        return None

    row = conn.execute(
        """
        SELECT mastery_id, mastery_probability, evidence_count,
               bkt_prior, bkt_transit, bkt_guess, bkt_slip
        FROM concept_mastery
        WHERE learner_id = ? AND subject_id = ? AND step_id = ? AND concept = ?
        LIMIT 1
        """,
        (attempt["learner_id"], attempt["subject_id"], attempt["step_id"], concept),
    ).fetchone()
    if row:
        prior = _clamp(row["mastery_probability"])
        mastery_id = row["mastery_id"]
        evidence_count = int(row["evidence_count"] or 0)
        bkt_prior = _clamp(row["bkt_prior"] if row["bkt_prior"] is not None else BKT_DEFAULT_PRIOR)
        bkt_transit = _clamp(row["bkt_transit"] if row["bkt_transit"] is not None else BKT_DEFAULT_TRANSIT)
        bkt_guess = _clamp(row["bkt_guess"] if row["bkt_guess"] is not None else BKT_DEFAULT_GUESS)
        bkt_slip = _clamp(row["bkt_slip"] if row["bkt_slip"] is not None else BKT_DEFAULT_SLIP)
    else:
        prior = BKT_DEFAULT_PRIOR
        mastery_id = str(uuid.uuid4())
        evidence_count = 0
        bkt_prior = prior
        bkt_transit = BKT_DEFAULT_TRANSIT
        bkt_guess = BKT_DEFAULT_GUESS
        bkt_slip = BKT_DEFAULT_SLIP

    posterior = round(_bkt_update(prior, is_correct, bkt_transit, bkt_guess, bkt_slip), 4)
    conn.execute(
        """
        INSERT INTO concept_mastery (
            mastery_id, learner_id, subject_id, step_id, concept,
            mastery_probability, evidence_count, correct_count,
            bkt_prior, bkt_transit, bkt_guess, bkt_slip, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(learner_id, subject_id, step_id, concept) DO UPDATE SET
            mastery_probability = excluded.mastery_probability,
            evidence_count = concept_mastery.evidence_count + 1,
            correct_count = concept_mastery.correct_count + excluded.correct_count,
            bkt_prior = excluded.bkt_prior,
            bkt_transit = excluded.bkt_transit,
            bkt_guess = excluded.bkt_guess,
            bkt_slip = excluded.bkt_slip,
            updated_at = datetime('now')
        """,
        (
            mastery_id,
            attempt["learner_id"],
            attempt["subject_id"],
            attempt["step_id"],
            concept,
            posterior,
            int(is_correct),
            bkt_prior,
            bkt_transit,
            bkt_guess,
            bkt_slip,
        ),
    )
    return {
        "concept": concept,
        "prior_mastery_probability": round(prior, 4),
        "mastery_probability": posterior,
        "evidence_count": evidence_count + 1,
        "bkt_parameters": {
            "prior": round(bkt_prior, 4),
            "transit": round(bkt_transit, 4),
            "guess": round(bkt_guess, 4),
            "slip": round(bkt_slip, 4),
        },
    }


def public_question_from_row(row):
    options = _normalize_options(json.loads(row["options_json"]))
    difficulty = _normalize_difficulty(row.get("calibrated_difficulty") or row.get("difficulty_level"))
    return {
        "question_id": row["question_id"],
        "question": row["question_text"],
        "question_text": row["question_text"],
        "options": options,
        "difficulty": difficulty,
        "topic": row.get("topic"),
        "concept": row.get("concept"),
        "bloom_level": row.get("bloom_level"),
        "difficulty_score": row.get("difficulty_score"),
    }


def _step_payload(context):
    return {
        "step_id": context["step_id"],
        "step_order": context.get("step_order"),
        "total_steps": context.get("total_steps"),
        "step_title": context["step_title"],
        "step_description": context.get("step_description"),
        "preview_terms": context.get("preview_terms") or [],
    }


def complete_step_and_advance(conn, learner_id, subject_id, path_id, step_id):
    current = conn.execute(
        """
        SELECT step_id, step_order
        FROM learning_path_steps
        WHERE path_id = ? AND step_id = ?
        """,
        (path_id, step_id),
    ).fetchone()
    if not current:
        raise ValueError("Completed step was not found in the roadmap")

    conn.execute(
        """
        UPDATE learning_path_steps
        SET step_status = 'completed',
            actual_minutes = COALESCE(actual_minutes, 0),
            completed_at = COALESCE(completed_at, datetime('now')),
            updated_at = datetime('now')
        WHERE step_id = ?
        """,
        (step_id,),
    )

    next_step = conn.execute(
        """
        SELECT step_id, step_order, step_title, step_description, preview_terms
        FROM learning_path_steps
        WHERE path_id = ?
          AND step_order > ?
          AND step_status != 'completed'
        ORDER BY step_order ASC
        LIMIT 1
        """,
        (path_id, current["step_order"]),
    ).fetchone()

    if next_step:
        conn.execute(
            """
            UPDATE learning_path_steps
            SET step_status = 'in_progress',
                started_at = COALESCE(started_at, datetime('now')),
                updated_at = datetime('now')
            WHERE step_id = ? AND step_status = 'not_started'
            """,
            (next_step["step_id"],),
        )

    counts = conn.execute(
        """
        SELECT
            COUNT(*) AS total_steps,
            SUM(CASE WHEN step_status = 'completed' THEN 1 ELSE 0 END) AS completed_steps
        FROM learning_path_steps
        WHERE path_id = ?
        """,
        (path_id,),
    ).fetchone()
    total_steps = int(counts["total_steps"] or 0)
    completed_steps = int(counts["completed_steps"] or 0)
    completion_pct = round((completed_steps / total_steps) * 100, 3) if total_steps else 0
    path_status = "completed" if total_steps and completed_steps >= total_steps else "active"

    conn.execute(
        """
        UPDATE learning_paths
        SET completed_steps = ?,
            path_status = ?,
            updated_at = datetime('now'),
            last_accessed_at = datetime('now')
        WHERE path_id = ?
        """,
        (completed_steps, path_status, path_id),
    )
    conn.execute(
        """
        UPDATE learner_subject_profiles
        SET completed_step_count = ?,
            total_step_count = ?,
            path_completion_pct = ?,
            status = 'active',
            last_activity_at = datetime('now'),
            updated_at = datetime('now')
        WHERE learner_id = ? AND subject_id = ?
        """,
        (completed_steps, total_steps, completion_pct, learner_id, subject_id),
    )

    return {
        "completed_step_id": step_id,
        "next_step": dict(next_step) if next_step else None,
        "completed_steps": completed_steps,
        "total_steps": total_steps,
        "path_completion_pct": completion_pct,
        "path_status": path_status,
    }


def start_adaptive_quiz(conn, learner_id, subject_id, path_id, step_id):
    context = load_adaptive_quiz_context(conn, learner_id, subject_id, path_id, step_id)

    existing_attempt = conn.execute(
        """
        SELECT attempt_id
        FROM quiz_attempts
        WHERE learner_id = ?
          AND subject_id = ?
          AND path_id = ?
          AND step_id = ?
          AND quiz_type = 'adaptive'
          AND completion_status = 'in_progress'
        ORDER BY started_at DESC
        LIMIT 1
        """,
        (learner_id, subject_id, path_id, step_id),
    ).fetchone()
    if existing_attempt:
        attempt_id = existing_attempt["attempt_id"]
        attempt = conn.execute(
            """
            SELECT difficulty_level, total_questions
            FROM quiz_attempts
            WHERE attempt_id = ?
            LIMIT 1
            """,
            (attempt_id,),
        ).fetchone()
        target_difficulty = _normalize_difficulty(
            attempt["difficulty_level"] if attempt else None,
            determine_starting_difficulty(
                {"current_level": context.get("current_level")},
                context.get("mastery_probability") if context.get("mastery_probability") is not None else context.get("mastery_score"),
            ),
        )
        question = select_next_bank_question(conn, attempt_id, step_id, target_difficulty)
        if not question:
            raise RuntimeError("No unused calibrated adaptive quiz questions are available for this in-progress quiz")
        return {
            "attempt_id": attempt_id,
            "quiz_type": "adaptive",
            "step": _step_payload(context),
            "quiz_length": ADAPTIVE_QUIZ_LENGTH,
            "starting_difficulty": target_difficulty,
            "questions_answered": int(attempt["total_questions"] or 0) if attempt else 0,
            "question": public_question_from_row(question),
            "resumed": True,
        }

    ensure_calibrated_question_bank(conn, context)

    if context.get("step_status") == "not_started":
        conn.execute(
            """
            UPDATE learning_path_steps
            SET step_status = 'in_progress',
                started_at = COALESCE(started_at, datetime('now')),
                updated_at = datetime('now')
            WHERE step_id = ?
            """,
            (step_id,),
        )

    mastery = context.get("mastery_probability")
    if mastery is None:
        mastery = context.get("mastery_score")
    starting_difficulty = determine_starting_difficulty({"current_level": context.get("current_level")}, mastery)

    attempt_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO quiz_attempts (
            attempt_id, learner_id, subject_id, path_id, step_id,
            quiz_type, difficulty_level, starting_difficulty, score,
            total_questions, correct_answers, completion_status
        )
        VALUES (?, ?, ?, ?, ?, 'adaptive', ?, ?, 0, 0, 0, 'in_progress')
        """,
        (attempt_id, learner_id, subject_id, path_id, step_id, starting_difficulty, starting_difficulty),
    )

    # question = generate_adaptive_question(
    #     topic=context["step_title"],
    #     step_title=context["step_title"],
    #     step_description=context["step_description"],
    #     preview_terms=context.get("preview_terms") or [],
    #     difficulty=starting_difficulty,
    #     previous_questions=[],
    # )
    # if not question or not question.get("question"):
    #     raise RuntimeError("Failed to generate the first adaptive question")
    # if not question.get("options") or not question.get("correct_answer"):
    #     raise RuntimeError("Generated adaptive question is incomplete")

    # question_id = save_generated_question(conn, attempt_id, question, starting_difficulty)
    # return {
    #     "attempt_id": attempt_id,
    #     "quiz_type": "adaptive",
    #     "step": _step_payload(context),
    #     "quiz_length": ADAPTIVE_QUIZ_LENGTH,
    #     "starting_difficulty": starting_difficulty,
    #     "questions_answered": 0,
    #     "question": public_question_from_row(question),
    # }
    question = select_next_bank_question(conn, attempt_id, step_id, starting_difficulty)
    if not question:
        raise RuntimeError("No calibrated adaptive quiz questions are available for this step")

    return {
        "attempt_id": attempt_id,
        "quiz_type": "adaptive",
        "step": _step_payload(context),
        "quiz_length": ADAPTIVE_QUIZ_LENGTH,
        "starting_difficulty": starting_difficulty,
        "questions_answered": 0,
        "question": public_question_from_row(question),
    }


def submit_adaptive_answer(conn, attempt_id, question_id, selected_answer, time_taken_seconds=None):
    attempt = conn.execute(
        """
        SELECT attempt_id, learner_id, subject_id, path_id, step_id, quiz_type,
               difficulty_level, total_questions, correct_answers, completion_status
        FROM quiz_attempts
        WHERE attempt_id = ?
        """,
        (attempt_id,),
    ).fetchone()
    if not attempt:
        raise ValueError("Adaptive quiz attempt not found")

    attempt = dict(attempt)
    if attempt["quiz_type"] != "adaptive":
        raise ValueError("The supplied attempt is not an adaptive quiz")
    if attempt["completion_status"] != "in_progress":
        raise ValueError("This adaptive quiz is already complete")

    question = conn.execute(
        """
        SELECT question_id, question_text, options_json, correct_answer, explanation,
               difficulty_level, calibrated_difficulty, topic, concept, bloom_level,
               concept_count, reasoning_steps, difficulty_score
        FROM quiz_questions
        WHERE question_id = ?
          AND step_id = ?
          AND question_source = 'adaptive_bank'
          AND is_active = 1
        """,
        (question_id, attempt["step_id"]),
    ).fetchone()
    if not question:
        raise ValueError("Question does not belong to this adaptive step")

    duplicate = conn.execute(
        """
        SELECT response_id
        FROM quiz_responses
        WHERE attempt_id = ? AND question_id = ?
        LIMIT 1
        """,
        (attempt_id, question_id),
    ).fetchone()
    if duplicate:
        raise ValueError("This question has already been answered")

    question = dict(question)
    try:
        options = _normalize_options(json.loads(question["options_json"]))
    except (TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("Stored question options are invalid") from error

    selected = _normalize_correct_answer(selected_answer, options)
    option_keys = {str(key).strip().upper() for key in options}
    if not selected or selected not in option_keys:
        raise ValueError("selected_answer must identify one of the question options")

    correct = _normalize_correct_answer(question["correct_answer"], options)
    is_correct = selected == correct
    current_difficulty = _normalize_difficulty(question.get("calibrated_difficulty") or question.get("difficulty_level") or attempt.get("difficulty_level"))
    next_difficulty = adjust_difficulty(current_difficulty, is_correct)

    total_questions = int(attempt.get("total_questions") or 0) + 1
    correct_answers = int(attempt.get("correct_answers") or 0) + int(is_correct)
    score = round(correct_answers / total_questions, 3)
    quiz_complete = total_questions >= ADAPTIVE_QUIZ_LENGTH
    status = "passed" if quiz_complete and score >= PASS_THRESHOLD else ("needs_review" if quiz_complete else "in_progress")

    conn.execute(
        """
        INSERT INTO quiz_responses (
            response_id, attempt_id, question_id, question_text, selected_answer,
            correct_answer, is_correct, time_taken_seconds, question_difficulty,
            difficulty_after, explanation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            attempt_id,
            question_id,
            question["question_text"],
            selected,
            correct,
            int(is_correct),
            time_taken_seconds,
            current_difficulty,
            next_difficulty,
            question.get("explanation"),
        ),
    )
    concept_mastery = update_concept_mastery_after_response(conn, attempt, question, is_correct)

    if quiz_complete:
        roadmap = complete_step_and_advance(
            conn=conn,
            learner_id=attempt["learner_id"],
            subject_id=attempt["subject_id"],
            path_id=attempt["path_id"],
            step_id=attempt["step_id"],
        )
        conn.execute(
            """
            UPDATE quiz_attempts
            SET difficulty_level = ?, ending_difficulty = ?, score = ?,
                total_questions = ?, correct_answers = ?, completion_status = ?,
                completed_at = datetime('now')
            WHERE attempt_id = ?
            """,
            (next_difficulty, next_difficulty, score, total_questions, correct_answers, status, attempt_id),
        )
        return {
            "attempt_id": attempt_id,
            "feedback": {
                "is_correct": is_correct,
                "correct_answer": correct,
                "explanation": question.get("explanation"),
            },
            "difficulty_after": next_difficulty,
            "concept_mastery": concept_mastery,
            "quiz_complete": True,
            "result": {
                "score": score,
                "total_questions": total_questions,
                "correct_answers": correct_answers,
                "status": status,
                "ending_difficulty": next_difficulty,
            },
            "roadmap": roadmap,
            "next_question": None,
        }

    conn.execute(
        """
        UPDATE quiz_attempts
        SET difficulty_level = ?, score = ?, total_questions = ?, correct_answers = ?
        WHERE attempt_id = ?
        """,
        (next_difficulty, score, total_questions, correct_answers, attempt_id),
    )

    context = load_adaptive_quiz_context(
        conn=conn,
        learner_id=attempt["learner_id"],
        subject_id=attempt["subject_id"],
        path_id=attempt["path_id"],
        step_id=attempt["step_id"],
    )
    previous_questions = [
        row["question_text"]
        for row in conn.execute(
            """
            SELECT question_text
            FROM quiz_questions
            WHERE attempt_id = ?
            ORDER BY created_at ASC
            """,
            (attempt_id,),
        ).fetchall()
    ]
    # next_question = generate_adaptive_question(
    #     topic=context["step_title"],
    #     step_title=context["step_title"],
    #     step_description=context["step_description"],
    #     preview_terms=context.get("preview_terms") or [],
    #     difficulty=next_difficulty,
    #     previous_questions=previous_questions,
    # )
    # if not next_question or not next_question.get("question"):
    #     raise RuntimeError("Failed to generate the next adaptive question")git chec
    # if not next_question.get("options") or not next_question.get("correct_answer"):
    #     raise RuntimeError("Generated adaptive question is incomplete")

    next_question = select_next_bank_question(conn, attempt_id, attempt["step_id"], next_difficulty)
    if not next_question:
        raise RuntimeError("No unused calibrated adaptive quiz questions are available for this step")

    return {
        "attempt_id": attempt_id,
        "feedback": {
            "is_correct": is_correct,
            "correct_answer": correct,
            "explanation": question.get("explanation"),
        },
        "difficulty_after": next_difficulty,
        "concept_mastery": concept_mastery,
        "quiz_complete": False,
        "progress": {
            "questions_answered": total_questions,
            "correct_answers": correct_answers,
            "quiz_length": ADAPTIVE_QUIZ_LENGTH,
        },
        "step": _step_payload(context),
        "next_question": public_question_from_row(next_question),
    }
