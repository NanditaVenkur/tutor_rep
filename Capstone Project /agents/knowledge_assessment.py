import json
import os
import sqlite3
import uuid
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
PASS_THRESHOLD = 0.80


@dataclass
class SessionState:
    learner_id: str | None = None
    email: str | None = None
    profile: dict | None = None
    preferences: dict | None = None
    current_topic: str | None = None
    current_level: str | None = None
    current_subject_id: str | None = None
    context: str = ""
    generated_quiz: list | None = None
    quiz_responses: list | None = None
    diagnostic_score: dict | None = None


SESSION = SessionState()
LLM = None


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


def _normalize_quiz_item(item):
    item["options"] = _normalize_options(item.get("options", {}))
    item["correct_answer"] = _normalize_correct_answer(item.get("correct_answer", ""), item["options"])
    return item


def _difficulty_plan_from_familiarity(familiarity: str | None) -> dict:
    normalized = _canonicalize_text(familiarity)
    if normalized in {"newtothis", "new", "beginner", "none"}:
        return {"easy": 5, "medium": 0, "hard": 0}
    if normalized in {"somefamiliarity", "some", "intermediate"}:
        return {"easy": 4, "medium": 1, "hard": 0}
    if normalized in {"comfortablewithbasics", "comfortable", "basics"}:
        return {"easy": 3, "medium": 2, "hard": 0}
    if normalized in {"alreadyadvanced", "advanced"}:
        return {"easy": 1, "medium": 2, "hard": 2}
    return {"easy": 5, "medium": 0, "hard": 0}


def _fallback_question_bank(topic: str, level: str, familiarity: str | None, context: str, n: int) -> list[dict]:
    topic = _normalize_text(topic) or "the topic"
    context = _normalize_text(context) or f"Topic focus: {topic}"
    difficulty_plan = _difficulty_plan_from_familiarity(familiarity)
    difficulty_order = (
        ["easy"] * difficulty_plan["easy"]
        + ["medium"] * difficulty_plan["medium"]
        + ["hard"] * difficulty_plan["hard"]
    ) or ["easy"] * n
    if len(difficulty_order) < n:
        difficulty_order.extend([difficulty_order[-1]] * (n - len(difficulty_order)))
    difficulty_order = difficulty_order[:n]

    question_templates = [
        (
            "What best describes {topic}?",
            {
                "A": f"A general idea that is unrelated to {topic}",
                "B": f"A concept or skill directly connected to {topic}",
                "C": f"Only a memorization task with no real use",
                "D": f"Something that cannot be studied or practiced",
            },
            "B",
        ),
        (
            "Which statement is most accurate about {topic}?",
            {
                "A": f"It is best learned by ignoring the main ideas of {topic}",
                "B": f"It is usually understood through core definitions and examples",
                "C": f"It never connects to problem solving",
                "D": f"It should always be treated as random information",
            },
            "B",
        ),
        (
            "What is a useful first step when studying {topic}?",
            {
                "A": f"Jump directly to the hardest ideas without basics",
                "B": f"Review the core definition, purpose, and simple examples",
                "C": f"Avoid examples completely",
                "D": f"Skip the topic summary and only memorize formulas",
            },
            "B",
        ),
        (
            "How should you approach a new example in {topic}?",
            {
                "A": "Guess the answer immediately",
                "B": "Identify the key idea and connect it to the concept",
                "C": "Ignore the example if it looks unfamiliar",
                "D": "Only focus on the last line of the example",
            },
            "B",
        ),
        (
            "Which option best helps you review {topic}?",
            {
                "A": "Reading unrelated material",
                "B": "Practicing the concept with a short explanation and example",
                "C": "Skipping review completely",
                "D": "Memorizing the title only",
            },
            "B",
        ),
    ]

    questions = []
    for idx in range(n):
        template_index = idx % len(question_templates)
        question_text, options, correct = question_templates[template_index]
        if idx == 0 and context:
            question_text = f"Based on the topic {topic}, what is the best interpretation of the core idea?"
        difficulty = difficulty_order[idx] if idx < len(difficulty_order) else "easy"
        questions.append(
            {
                "id": str(idx + 1),
                "question": question_text.format(topic=topic, level=level),
                "options": options,
                "correct_answer": correct,
                "difficulty": difficulty,
                "explanation": f"This checks your understanding of {topic}. The best answer is the one that connects directly to the topic context.",
            }
        )
    return questions


def upsert_learner_profile(input_str: str) -> str:
    parts = [p.strip() for p in input_str.split("|")]
    email = parts[0] if len(parts) > 0 else ""
    full_name = parts[1] if len(parts) > 1 else "Learner"
    age_group = parts[2] if len(parts) > 2 else None
    role = parts[3] if len(parts) > 3 else None
    preferred_language = parts[4] if len(parts) > 4 else "English"

    if not email:
        return json.dumps({"error": "email is required"})

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT learner_id, full_name, age_group, role, preferred_language FROM learners WHERE email = ?",
        (email,),
    ).fetchone()

    if row:
        learner_id = row[0]
        conn.execute(
            """UPDATE learners
               SET full_name = ?, age_group = ?, role = ?, preferred_language = ?, updated_at = datetime('now')
               WHERE learner_id = ?""",
            (full_name, age_group, role, preferred_language, learner_id),
        )
    else:
        learner_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO learners (learner_id, email, full_name, age_group, role, preferred_language)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (learner_id, email, full_name, age_group, role, preferred_language),
        )

    conn.commit()
    conn.close()

    SESSION.learner_id = learner_id
    SESSION.email = email
    SESSION.profile = {
        "learner_id": learner_id,
        "email": email,
        "full_name": full_name,
        "age_group": age_group,
        "role": role,
        "preferred_language": preferred_language,
    }
    return json.dumps(SESSION.profile)


def save_learner_preferences(input_str: str) -> str:
    parts = [p.strip() for p in input_str.split("|")]
    learner_id = parts[0]
    content_format = parts[1] if len(parts) > 1 else "mixed"
    explanation_style = parts[2] if len(parts) > 2 else "step_by_step"
    quiz_style = parts[3] if len(parts) > 3 else "mixed"
    learning_pace = parts[4] if len(parts) > 4 else "normal"
    session_length = parts[5] if len(parts) > 5 else "30_min"
    feedback_style = parts[6] if len(parts) > 6 else "immediate"
    accessibility_notes = parts[7] if len(parts) > 7 else None

    conn = sqlite3.connect(DB_PATH)
    existing = conn.execute(
        "SELECT preference_id FROM learner_preferences WHERE learner_id = ?",
        (learner_id,),
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE learner_preferences
               SET content_format = ?, explanation_style = ?, quiz_style = ?, learning_pace = ?, session_length = ?, feedback_style = ?, accessibility_notes = ?, updated_at = datetime('now')
               WHERE learner_id = ?""",
            (content_format, explanation_style, quiz_style, learning_pace, session_length, feedback_style, accessibility_notes, learner_id),
        )
    else:
        conn.execute(
            """INSERT INTO learner_preferences
               (preference_id, learner_id, content_format, explanation_style, quiz_style, learning_pace, session_length, feedback_style, accessibility_notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), learner_id, content_format, explanation_style, quiz_style, learning_pace, session_length, feedback_style, accessibility_notes),
        )

    conn.commit()
    conn.close()

    SESSION.preferences = {
        "content_format": content_format,
        "explanation_style": explanation_style,
        "quiz_style": quiz_style,
        "learning_pace": learning_pace,
        "session_length": session_length,
        "feedback_style": feedback_style,
        "accessibility_notes": accessibility_notes,
    }
    return json.dumps(SESSION.preferences)


def upsert_subject(subject_name: str, description: str | None = None) -> str:
    subject_name = subject_name.strip()
    if not subject_name:
        return json.dumps({"error": "subject_name is required"})

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT subject_id, subject_name, description FROM subjects WHERE subject_name = ?",
        (subject_name,),
    ).fetchone()

    if row:
        subject_id = row[0]
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

    conn.commit()
    conn.close()
    SESSION.current_subject_id = subject_id
    return json.dumps({"subject_id": subject_id, "subject_name": subject_name, "description": description})


def fetch_learner_dashboard(email: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    learner = conn.execute("SELECT * FROM learners WHERE email = ?", (email.strip(),)).fetchone()
    if not learner:
        conn.close()
        return json.dumps({"error": "learner not found"})

    prefs = conn.execute("SELECT * FROM learner_preferences WHERE learner_id = ?", (learner["learner_id"],)).fetchone()
    subject = conn.execute(
        """SELECT * FROM learner_subject_profiles
           WHERE learner_id = ? AND is_active = 1
           ORDER BY updated_at DESC LIMIT 1""",
        (learner["learner_id"],),
    ).fetchone()
    conn.close()

    return json.dumps(
        {
            "learner": dict(learner),
            "preferences": dict(prefs) if prefs else None,
            "active_subject_profile": dict(subject) if subject else None,
        },
        default=str,
    )


def retrieve_context(topic: str) -> str:
    topic = topic.strip()
    normalized = topic.lower()
    fallback_contexts = {
        "financial statements": (
            "Financial statements include the balance sheet, income statement, cash flow statement, and statement of changes in equity. "
            "They help learners understand financial position, performance, and liquidity."
        ),
        "balance sheet": (
            "A balance sheet shows assets, liabilities, and equity at a point in time. "
            "It follows the accounting equation: Assets = Liabilities + Equity."
        ),
        "finance": "Finance covers budgeting, investments, capital structure, risk, and financial analysis.",
    }
    ctx = fallback_contexts.get(normalized)
    if not ctx:
        ctx = f"Topic focus: {topic}. Generate explanations and diagnostic questions directly about this topic."
    SESSION.context = ctx
    return ctx


def build_assessment_preview(
    topic: str,
    level: str = "beginner",
    familiarity: str | None = None,
    question_count: int = 5,
) -> dict:
    """Build a non-interactive assessment preview for UI/backend callers."""
    SESSION.current_topic = topic
    SESSION.current_level = level
    ctx = retrieve_context(topic)
    quiz_raw = generate_diagnostic_questions(f"{topic}|{level}|{familiarity or ''}|{question_count}")
    quiz = json.loads(quiz_raw)
    if isinstance(quiz, dict):
        quiz = quiz.get("questions") or []
    if not isinstance(quiz, list):
        quiz = []
    return {
        "topic": topic,
        "level": level,
        "familiarity": familiarity,
        "difficulty_plan": _difficulty_plan_from_familiarity(familiarity),
        "context_source": "fallback_contexts in agents/knowledge_assessment.py",
        "context": ctx,
        "questions": quiz,
    }


def generate_diagnostic_questions(input_str: str) -> str:
    parts = [p.strip() for p in input_str.split("|")]
    topic = parts[0] if len(parts) > 0 else "General Knowledge"
    level = parts[1] if len(parts) > 1 else "beginner"
    SESSION.current_level = level
    familiarity = parts[2] if len(parts) > 2 else None
    n = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 5
    difficulty_plan = _difficulty_plan_from_familiarity(familiarity)
    difficulty_mix = ", ".join(
        [f"{key}: {value}" for key, value in difficulty_plan.items()]
    )

    ctx = SESSION.context or retrieve_context(topic)
    prompt = f"""You are a diagnostic quiz writer.
Create exactly {n} MCQ questions for the topic: {topic}
Learner level: {level}
Learner familiarity: {familiarity or "unknown"}
Use only the topic and context below. Do not introduce unrelated domains.
Return valid JSON only.
Each item should contain: id, question, options, correct_answer, explanation.
The correct_answer must be exactly one of the option keys such as A, B, C, or D.
Each item should also include a difficulty field with one of: easy, medium, hard.

Context:
{ctx[:7000]}

Rules:
- Every question must directly test the entered topic: {topic}
- Do not use finance, accounting, or any unrelated subject unless the topic itself is about that subject
- Keep the questions aligned to {level} difficulty
- Set correct_answer to the key of the right option, not the full option text
- Match this difficulty mix as closely as possible: {difficulty_mix}
"""

    try:
        resp = _get_llm().invoke(prompt)
        raw = getattr(resp, "content", str(resp)).strip()
        start, end = raw.find("["), raw.rfind("]")
        if start != -1 and end != -1:
            raw = raw[start : end + 1]
        quiz = json.loads(raw)
        for idx, item in enumerate(quiz, start=1):
            item["id"] = str(item.get("id") or idx)
            _normalize_quiz_item(item)
            item["difficulty"] = _normalize_text(item.get("difficulty") or "easy").lower()
        SESSION.generated_quiz = quiz
        return json.dumps(quiz)
    except Exception as exc:
        fallback_quiz = _fallback_question_bank(topic, level, familiarity, ctx, n)
        for idx, item in enumerate(fallback_quiz, start=1):
            item["id"] = str(item.get("id") or idx)
            _normalize_quiz_item(item)
            item["difficulty"] = _normalize_text(item.get("difficulty") or "easy").lower()
        SESSION.generated_quiz = fallback_quiz
        SESSION.context = ctx
        return json.dumps(
            {
                "warning": "LLM generation failed; using local fallback questions.",
                "error": str(exc),
                "questions": fallback_quiz,
            }
        )


def run_diagnostic_quiz(_: str = "", answers: Optional[list[str]] = None) -> str:
    quiz = SESSION.generated_quiz or []
    if not quiz:
        return "ERROR: generate_diagnostic_questions first."

    responses = []
    answer_iter = iter(answers) if answers is not None else None
    for q in quiz:
        _normalize_quiz_item(q)
        options = q["options"]
        print(f"\nQ{q.get('id')}: {q.get('question')}")
        for key, value in options.items():
            print(f"  {key}) {value}")
        if answer_iter is not None:
            try:
                ans = next(answer_iter)
                print(f"Your answer (A/B/C/D): {ans}")
            except StopIteration:
                ans = ""
        else:
            ans = input("Your answer (A/B/C/D): ")
        ans = ans.strip().upper()
        is_correct = ans == str(q.get("correct_answer", "")).strip().upper()
        responses.append(
            {
                "question_id": q.get("id"),
                "question": q.get("question"),
                "user_answer": ans,
                "correct_answer": q.get("correct_answer"),
                "is_correct": is_correct,
                "explanation": q.get("explanation", ""),
            }
        )

    SESSION.quiz_responses = responses
    return f"Quiz complete: {len(responses)} responses recorded."


def score_and_save_attempt(input_str: str) -> str:
    parts = [p.strip() for p in input_str.split("|")]
    topic = parts[0] if len(parts) > 0 else "General Knowledge"
    session_type = parts[1] if len(parts) > 1 else "diagnostic"

    responses = SESSION.quiz_responses or []
    if not responses:
        return json.dumps({"error": "no quiz responses in session"})

    total = len(responses)
    correct = sum(1 for r in responses if r.get("is_correct"))
    score_pct = round(correct / total if total else 0.0, 3)
    passed = score_pct >= PASS_THRESHOLD

    learner_id = SESSION.learner_id
    if learner_id:
        attempt_id = str(uuid.uuid4())
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            """INSERT INTO quiz_attempts
               (attempt_id, learner_id, subject_id, path_id, step_id, quiz_type, difficulty_level, score, total_questions, correct_answers, completion_status, mastery_delta, started_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (
                attempt_id,
                learner_id,
                SESSION.current_subject_id,
                None,
                None,
                session_type,
                SESSION.current_level or "beginner",
                score_pct,
                total,
                correct,
                "completed",
                0.0,
            ),
        )
        for item in responses:
            conn.execute(
                """INSERT INTO quiz_responses
                   (response_id, attempt_id, question_id, question_text, selected_answer, correct_answer, is_correct, time_taken_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()),
                    attempt_id,
                    str(item.get("question_id", "")),
                    item.get("question", ""),
                    item.get("user_answer", ""),
                    item.get("correct_answer", ""),
                    int(item.get("is_correct", False)),
                    None,
                ),
            )
        conn.commit()
        conn.close()

    result = {
        "topic": topic,
        "total": total,
        "correct": correct,
        "score_pct": score_pct,
        "passed": passed,
    }
    SESSION.diagnostic_score = result
    return json.dumps(result)


def run_knowledge_assessment(
    email: str,
    full_name: str,
    topic: str,
    level: str = "beginner",
    answers: Optional[list[str]] = None,
) -> dict:
    SESSION.current_topic = topic
    SESSION.current_level = level

    print("1) Upserting learner profile")
    profile = json.loads(upsert_learner_profile(f"{email}|{full_name}|None|student|English"))
    print(profile)

    print("2) Saving preferences")
    prefs = json.loads(
        save_learner_preferences(
            f"{profile['learner_id']}|mixed|step_by_step|mixed|normal|30_min|immediate|None"
        )
    )
    print(prefs)

    print("3) Upserting subject")
    subject = json.loads(upsert_subject(topic, f"Diagnostic assessment subject for {topic}"))
    print(subject)

    print("4) Retrieving context")
    preview = build_assessment_preview(topic, level, question_count=5)
    ctx = preview["context"]
    print(ctx)

    print("5) Generating diagnostic questions")
    quiz = preview["questions"]
    print(f"Generated {len(quiz)} questions")

    print("6) Running diagnostic quiz")
    quiz_result = run_diagnostic_quiz("", answers=answers)
    print(quiz_result)

    print("7) Scoring and saving attempt")
    score = json.loads(score_and_save_attempt(f"{topic}|diagnostic"))
    print(score)

    return {
        "profile": profile,
        "preferences": prefs,
        "subject": subject,
        "preview": preview,
        "score": score,
    }


init_db()
