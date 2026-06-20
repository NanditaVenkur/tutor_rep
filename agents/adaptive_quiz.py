import json
import uuid
import sqlite3
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

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


def load_adaptive_quiz_context(
    conn,
    learner_id,
    subject_id,
    path_id,
    step_id,
):
    row = conn.execute(
        """
        SELECT
            lp.path_id,
            lp.learner_id,
            lp.subject_id,
            lp.path_status,

            lps.step_id,
            lps.topic_id,
            lps.step_title,
            lps.step_description,
            lps.step_status,

            lsp.current_level,
            lsp.mastery_score,

            tm.mastery_probability,

            lcv.rendered_title,
            lcv.rendered_summary,
            lcv.rendered_content
        FROM learning_paths lp

        JOIN learning_path_steps lps
            ON lps.path_id = lp.path_id

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

        WHERE lp.path_id = ?
          AND lp.learner_id = ?
          AND lp.subject_id = ?
          AND lps.step_id = ?

        ORDER BY lcv.updated_at DESC
        LIMIT 1
        """,
        (
            path_id,
            learner_id,
            subject_id,
            step_id,
        ),
    ).fetchone()

    if not row:
        raise ValueError(
            "The learner, subject, path and step do not belong together"
        )

    context = dict(row)

    return {
        "learner_id": context["learner_id"],
        "subject_id": context["subject_id"],
        "path_id": context["path_id"],
        "step_id": context["step_id"],
        "topic_id": context["topic_id"],
        "step_title": context["step_title"],
        "step_description": context.get("step_description") or "",
        "step_status": context.get("step_status"),
        "path_status": context.get("path_status"),
        "current_level": context.get("current_level") or "beginner",
        "mastery_score": context.get("mastery_score"),
        "mastery_probability": context.get("mastery_probability")
    }


def generate_adaptive_question(
    topic,
    step_title,
    step_description,
    difficulty,
    previous_questions=None,
):
    prompt = f"""
        Generate one MCQ for the supplied learning-path step.
        Difficulty: {difficulty}
        Topic: {topic}
        Step: {step_title}
        Step discription: {step_description}

        Return JSON containing:
        - id
        - question
        - options
        - correct_answer
        - explanation
        - difficulty

        Rules:
        - Question must directly test the entered topic: {topic}
        - Do not use finance, accounting, or any unrelated subject unless the topic itself is about that subject
        - Keep the questions aligned to {difficulty} difficulty
        - Set correct_answer to the key of the right option, not the full option text
        - Question must align to the sub topic {step_title} with the Step direction: {step_description} used as guideline

        The question must be answerable from the supplied content.
        Do not repeat these questions: {previous_questions} 
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
        question_item.get("explanation")
        or "Explanation not available."
    )
    generated_difficulty = _normalize_text(
        question_item.get("difficulty")
    ).lower()
    
    question_item["difficulty"] = (
        generated_difficulty
        if generated_difficulty in DIFFICULTIES
        else difficulty
    )
    normalized_question_item = _normalize_quiz_question_item(question_item)

    return normalized_question_item


def save_generated_question(
    conn,
    attempt_id,
    question,
    difficulty,
):
    question_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO quiz_questions (
            question_id,
            attempt_id,
            question_text,
            options_json,
            correct_answer,
            explanation,
            difficulty_level
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


def start_adaptive_quiz(
    conn,
    learner_id,
    subject_id,
    path_id,
    step_id,
):
    # 1. Validate relationships and load context.
    context = load_adaptive_quiz_context(
        conn=conn,
        learner_id=learner_id,
        subject_id=subject_id,
        path_id=path_id,
        step_id=step_id,
    )

    # 2. Prevent multiple unfinished attempts for the same step.
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
        (
            learner_id,
            subject_id,
            path_id,
            step_id,
        ),
    ).fetchone()

    if existing_attempt:
        raise ValueError(
            "An adaptive quiz is already in progress for this step"
        )

    # 3. Determine starting difficulty.
    mastery = context.get("mastery_probability")

    if mastery is None:
        mastery = context.get("mastery_score")

    starting_difficulty = determine_starting_difficulty(
        {"current_level": context.get("current_level")},
        mastery,
    )

    # 4. Create the attempt.
    attempt_id = str(uuid.uuid4())

    conn.execute(
        """
        INSERT INTO quiz_attempts (
            attempt_id,
            learner_id,
            subject_id,
            path_id,
            step_id,
            quiz_type,
            difficulty_level,
            starting_difficulty,
            score,
            total_questions,
            correct_answers,
            completion_status
        )
        VALUES (?, ?, ?, ?, ?, 'adaptive', ?, ?, 0, 0, 0, 'in_progress')
        """,
        (
            attempt_id,
            learner_id,
            subject_id,
            path_id,
            step_id,
            starting_difficulty,
            starting_difficulty,
        ),
    )

    # 5. Generate the first question.
    question = generate_adaptive_question(
        topic=context["step_title"],
        step_title=context["step_title"],
        step_description=context["step_description"],
        difficulty=starting_difficulty,
        previous_questions=[],
    )

    if not question:
        raise RuntimeError("Failed to generate the first adaptive question")

    if not question.get("question"):
        raise RuntimeError("Generated question has no question text")

    if not question.get("options"):
        raise RuntimeError("Generated question has no answer options")

    if not question.get("correct_answer"):
        raise RuntimeError("Generated question has no correct answer")

    # 6. Save the private question and answer.
    question_id = save_generated_question(
        conn=conn,
        attempt_id=attempt_id,
        question=question,
        difficulty=starting_difficulty,
    )

    # 7. Return only learner-safe information.
    return {
        "attempt_id": attempt_id,
        "quiz_type": "adaptive",
        "step": {
            "step_id": context["step_id"],
            "step_title": context["step_title"],
            "step_description": context.get("step_description"),
        },
        "starting_difficulty": starting_difficulty,
        "questions_answered": 0,
        "question": public_question(
            question_id=question_id,
            question=question,
            difficulty=starting_difficulty,
        ),
    }


def submit_adaptive_answer(
    conn,
    attempt_id,
    question_id,
    selected_answer,
    time_taken_seconds=None,
):
    """Grade one adaptive response and return feedback plus the next question."""
    attempt = conn.execute(
        """
        SELECT
            attempt_id,
            learner_id,
            subject_id,
            path_id,
            step_id,
            quiz_type,
            difficulty_level,
            total_questions,
            correct_answers,
            completion_status
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
        SELECT
            question_id,
            attempt_id,
            question_text,
            options_json,
            correct_answer,
            explanation,
            difficulty_level
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
    status = "passed" if quiz_complete and score >= PASS_THRESHOLD else (
        "needs_review" if quiz_complete else "in_progress"
    )

    conn.execute(
        """
        INSERT INTO quiz_responses (
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
            explanation
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
        conn.execute(
            """
            UPDATE quiz_attempts
            SET difficulty_level = ?,
                ending_difficulty = ?,
                score = ?,
                total_questions = ?,
                correct_answers = ?,
                completion_status = ?,
                completed_at = datetime('now')
            WHERE attempt_id = ?
            """,
            (
                next_difficulty,
                next_difficulty,
                score,
                total_questions,
                correct_answers,
                status,
                attempt_id,
            ),
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
            "next_question": None,
        }

    conn.execute(
        """
        UPDATE quiz_attempts
        SET difficulty_level = ?,
            score = ?,
            total_questions = ?,
            correct_answers = ?
        WHERE attempt_id = ?
        """,
        (
            next_difficulty,
            score,
            total_questions,
            correct_answers,
            attempt_id,
        ),
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
        step_description=context['step_description'],
        difficulty=next_difficulty,
        previous_questions=previous_questions,
    )

    if not next_question or not next_question.get("question"):
        raise RuntimeError("Failed to generate the next adaptive question")
    if not next_question.get("options") or not next_question.get("correct_answer"):
        raise RuntimeError("Generated adaptive question is incomplete")

    next_question_id = save_generated_question(
        conn=conn,
        attempt_id=attempt_id,
        question=next_question,
        difficulty=next_difficulty,
    )

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
        "next_question": public_question(
            question_id=next_question_id,
            question=next_question,
            difficulty=next_difficulty,
        ),
    }
    
