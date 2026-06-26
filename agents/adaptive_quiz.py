import json
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
PASS_THRESHOLD = 0.80

LLM = None


def _normalize_options(options):
    if isinstance(options, dict):
        return options
    if isinstance(options, list):
        letters = ["A", "B", "C", "D"]
        return {letters[i] if i < len(letters) else chr(65 + i): str(opt) for i, opt in enumerate(options)}
    if isinstance(options, str):
        return {"A": options}
    return {}


def _canonicalize_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalize_text(value):
    return " ".join(str(value or "").strip().split())


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


def _normalize_quiz_question_item(item):
    item["options"] = _normalize_options(item.get("options", {}))
    item["correct_answer"] = _normalize_correct_answer(item.get("correct_answer", ""), item["options"])
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
    normalized = str(difficulty or "medium").lower()
    return DIFFICULTIES.index(normalized) if normalized in DIFFICULTIES else 1


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
        "preview_terms": _parse_preview_terms(context.get("preview_terms")),
        "step_status": context.get("step_status"),
        "path_status": context.get("path_status"),
        "current_level": context.get("current_level") or "beginner",
        "mastery_score": context.get("mastery_score"),
        "mastery_probability": context.get("mastery_probability"),
        # Learner profile personalisation
        "learner_profile": {
            "full_name": context.get("full_name") or "",
            "age_group": context.get("age_group") or "",
            "role": context.get("role") or "",
            "quiz_style": context.get("quiz_style") or "mixed",
            "explanation_style": context.get("explanation_style") or "step_by_step",
            "feedback_style": context.get("feedback_style") or "immediate",
            "accessibility_notes": context.get("accessibility_notes") or "",
        },
    }


# ---------------------------------------------------------------------------
# Quiz format type definitions
# ---------------------------------------------------------------------------
_QUIZ_FORMAT_INSTRUCTIONS = {
    "mcq": {
        "label": "Multiple Choice (MCQ)",
        "prompt": (
            "Generate a standard multiple-choice question with exactly 4 options (A, B, C, D). "
            "One option must be definitively correct; the others should be plausible distractors. "
            "Return 'options' as a JSON object with keys A, B, C, D. "
            "Set 'correct_answer' to the single correct key letter (e.g. 'B')."
        ),
    },
    "true_false": {
        "label": "True or False",
        "prompt": (
            "Generate a True/False question. The question should be a clear declarative statement "
            "that is either definitively true or definitively false. "
            "Return 'options' as {\"A\": \"True\", \"B\": \"False\"}. "
            "Set 'correct_answer' to 'A' if the statement is true, or 'B' if it is false."
        ),
    },
    "statement_1_statement_2": {
        "label": "Statement 1 / Statement 2",
        "prompt": (
            "Generate a Statement 1 / Statement 2 question. Write two numbered statements about the topic. "
            "The question stem should be: 'Which of the following statements is/are correct?' "
            "Return 'options' as: "
            "{\"A\": \"Only Statement 1 is correct\", \"B\": \"Only Statement 2 is correct\", "
            "\"C\": \"Both statements are correct\", \"D\": \"Neither statement is correct\"}. "
            "Include both statements clearly in the question text. "
            "Set 'correct_answer' to the key matching the correct option."
        ),
    },
    "assertion_reasoning": {
        "label": "Assertion and Reasoning",
        "prompt": (
            "Generate an Assertion-Reasoning question. Write one Assertion (A) and one Reason (R) about the topic. "
            "The question stem should be: 'Consider the Assertion (A) and Reason (R) below.' "
            "Return 'options' as: "
            "{\"A\": \"Both A and R are true, and R is the correct explanation of A\", "
            "\"B\": \"Both A and R are true, but R is not the correct explanation of A\", "
            "\"C\": \"A is true but R is false\", "
            "\"D\": \"A is false but R is true\"}. "
            "Include the Assertion and Reason clearly in the question text. "
            "Set 'correct_answer' to the key matching the correct option."
        ),
    },
}


def _parse_quiz_styles(raw_quiz_style):
    """Return a list of enabled quiz style keys from the stored value.

    The backend stores quiz_style as either:
    - a JSON array string: '["mcq","true_false"]'
    - a plain string: 'mcq'
    """
    if not raw_quiz_style:
        return ["mcq"]
    if isinstance(raw_quiz_style, list):
        return [str(s).strip() for s in raw_quiz_style if str(s or "").strip()] or ["mcq"]
    raw = str(raw_quiz_style).strip()
    # Try to parse as JSON array
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s or "").strip()] or ["mcq"]
        except (json.JSONDecodeError, TypeError):
            pass
    return [raw] if raw else ["mcq"]


def generate_adaptive_question(
    topic,
    step_title,
    step_description,
    preview_terms,
    difficulty,
    previous_questions=None,
    learner_profile=None,
):
    preview_terms = preview_terms or []
    preview_terms_text = ", ".join(preview_terms) if preview_terms else step_title
    learner_profile = learner_profile or {}

    # --- Learner profile fields ---
    role = (learner_profile.get("role") or "").strip()
    age_group = (learner_profile.get("age_group") or "").strip()
    explanation_style = (learner_profile.get("explanation_style") or "step_by_step").strip()
    accessibility_notes = (learner_profile.get("accessibility_notes") or "").strip()
    raw_quiz_style = learner_profile.get("quiz_style")

    # Parse quiz_style: stored as JSON array string or plain string
    enabled_formats = _parse_quiz_styles(raw_quiz_style)

    # Pick a random format from what the learner enabled this question
    chosen_format_key = _random.choice(enabled_formats)
    format_info = _QUIZ_FORMAT_INSTRUCTIONS.get(
        chosen_format_key,
        _QUIZ_FORMAT_INSTRUCTIONS["mcq"],
    )
    format_label = format_info["label"]
    format_instructions = format_info["prompt"]

    # --- Build learner context block ---
    learner_context_lines = []
    if role:
        learner_context_lines.append(f"- Profession / role: {role}")
    if age_group:
        learner_context_lines.append(f"- Age group: {age_group}")
    if explanation_style:
        learner_context_lines.append(f"- Preferred explanation style: {explanation_style}")
    if accessibility_notes:
        learner_context_lines.append(f"- Special instruction from learner: {accessibility_notes}")

    learner_context_section = (
        "Learner profile (personalise the question to this learner):\n"
        + ("\n".join(learner_context_lines) if learner_context_lines else "  (no additional profile information)")
    )

    prompt = f"""
        Generate one question of type "{format_label}" for the supplied learning-path step.

        === FORMAT INSTRUCTIONS ===
        {format_instructions}

        === CONTENT CONTEXT ===
        Difficulty: {difficulty}
        Overall topic: {topic}
        Current roadmap step: {step_title}
        Step description: {step_description}
        Required subtopics for this step: {preview_terms_text}

        === LEARNER PROFILE ===
        {learner_context_section}

        === OUTPUT FORMAT ===
        Return a single JSON object containing:
        - id         (a short unique identifier string)
        - question   (the full question text, including any statements/assertions as required by the format)
        - options    (a JSON object with keys and values exactly as specified in the format instructions)
        - correct_answer  (the key of the correct option, e.g. "B")
        - explanation     (a concise explanation of why the correct answer is right)
        - difficulty      (must be one of: easy, medium, hard)

        === RULES ===
        - The question must directly test the current roadmap step: {step_title}
        - Base the question on one or more of these step subtopics: {preview_terms_text}
        - Do not ask about preview terms from other roadmap steps
        - Do not introduce unrelated subjects (e.g. finance, cooking) unless the topic itself covers them
        - Keep difficulty aligned to: {difficulty}
        - Set correct_answer to the option KEY only (not the full option text)
        - The question must be answerable from the supplied step title, description, and subtopics
        - If the learner has a known profession, make the context relatable to that profession where natural
        - Adjust wording complexity to match the learner's age group if provided
        - If the learner has a special instruction (e.g. "use rap references"), honour it in the question wording and explanation
        - Do not repeat these questions: {previous_questions}
        """

    response = _get_llm().invoke(prompt)
    raw = getattr(response, "content", str(response)).strip()
    start = raw.find("{")
    end = raw.rfind("}")

    if start == -1 or end == -1:
        raise ValueError("LLM did not return a JSON object")

    raw = raw[start:end + 1]
    question_item = json.loads(raw)

    question_item["explanation"] = _normalize_text(
        question_item.get("explanation") or "Explanation not available."
    )
    generated_difficulty = _normalize_text(question_item.get("difficulty")).lower()
    question_item["difficulty"] = generated_difficulty if generated_difficulty in DIFFICULTIES else difficulty
    return _normalize_quiz_question_item(question_item)


def save_generated_question(conn, attempt_id, question, difficulty):
    question_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO quiz_questions (
            question_id, attempt_id, question_text, options_json,
            correct_answer, explanation, difficulty_level
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            question_id,
            attempt_id,
            question["question"],
            json.dumps(question["options"]),
            question["correct_answer"],
            question.get("explanation"),
            difficulty,
        ),
    )
    return question_id


def public_question(question_id, question, difficulty):
    return {
        "question_id": question_id,
        "question": question["question"],
        "options": question["options"],
        "difficulty": difficulty,
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
        raise ValueError("An adaptive quiz is already in progress for this step")

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

    question = generate_adaptive_question(
        topic=context["step_title"],
        step_title=context["step_title"],
        step_description=context["step_description"],
        preview_terms=context.get("preview_terms") or [],
        difficulty=starting_difficulty,
        previous_questions=[],
        learner_profile=context.get("learner_profile"),
    )
    if not question or not question.get("question"):
        raise RuntimeError("Failed to generate the first adaptive question")
    if not question.get("options") or not question.get("correct_answer"):
        raise RuntimeError("Generated adaptive question is incomplete")

    question_id = save_generated_question(conn, attempt_id, question, starting_difficulty)
    return {
        "attempt_id": attempt_id,
        "quiz_type": "adaptive",
        "step": _step_payload(context),
        "quiz_length": ADAPTIVE_QUIZ_LENGTH,
        "starting_difficulty": starting_difficulty,
        "questions_answered": 0,
        "question": public_question(question_id, question, starting_difficulty),
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
        SELECT question_id, attempt_id, question_text, options_json,
               correct_answer, explanation, difficulty_level
        FROM quiz_questions
        WHERE question_id = ? AND attempt_id = ?
        """,
        (question_id, attempt_id),
    ).fetchone()
    if not question:
        raise ValueError("Question does not belong to this adaptive attempt")

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
    current_difficulty = question.get("difficulty_level") or attempt.get("difficulty_level") or "medium"
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
    next_question = generate_adaptive_question(
        topic=context["step_title"],
        step_title=context["step_title"],
        step_description=context["step_description"],
        preview_terms=context.get("preview_terms") or [],
        difficulty=next_difficulty,
        previous_questions=previous_questions,
        learner_profile=context.get("learner_profile"),
    )
    if not next_question or not next_question.get("question"):
        raise RuntimeError("Failed to generate the next adaptive question")
    if not next_question.get("options") or not next_question.get("correct_answer"):
        raise RuntimeError("Generated adaptive question is incomplete")

    next_question_id = save_generated_question(conn, attempt_id, next_question, next_difficulty)
    return {
        "attempt_id": attempt_id,
        "feedback": {
            "is_correct": is_correct,
            "correct_answer": correct,
            "explanation": question.get("explanation"),
        },
        "difficulty_after": next_difficulty,
        "quiz_complete": False,
        "progress": {
            "questions_answered": total_questions,
            "correct_answers": correct_answers,
            "quiz_length": ADAPTIVE_QUIZ_LENGTH,
        },
        "step": _step_payload(context),
        "next_question": public_question(next_question_id, next_question, next_difficulty),
    }
