import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable
import re

try:
    import networkx as nx
except ImportError:
    nx = None

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return None

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

from agents.content_service import save_path_views


load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "backend" / "adaptive_tutor_v2.db"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
LLM = None
PREVIEW_TERMS_CACHE = {}
STEP_TITLES_CACHE = {}


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
            explanation_style,
            quiz_style,
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


def _extract_json_array(text: str) -> list:
    text = str(text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, flags=re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []


def _format_topic_context(topic_context) -> str:
    if not topic_context:
        return ""
    if isinstance(topic_context, str):
        return _normalize_text(topic_context)
    if not isinstance(topic_context, dict):
        return _normalize_text(str(topic_context))

    parts = []
    canonical = _normalize_text(topic_context.get("canonical_topic") or "")
    definition = _normalize_text(topic_context.get("definition") or "")
    learning_goal = _normalize_text(topic_context.get("learning_goal") or "")
    if canonical:
        parts.append(f"Confirmed topic: {canonical}")
    if learning_goal:
        parts.append(f"Learner goal: {learning_goal}")
    if definition:
        parts.append(f"Definition: {definition}")
    hints = topic_context.get("curriculum_hints") or []
    if isinstance(hints, list) and hints:
        parts.append("Required topic coverage: " + ", ".join(_normalize_text(item) for item in hints if _normalize_text(item)))
    for source in (topic_context.get("sources") or [])[:10]:
        if not isinstance(source, dict):
            continue
        title = _normalize_text(source.get("title") or "")
        snippet = _normalize_text(source.get("snippet") or "")
        excerpt = _normalize_text(source.get("content_excerpt") or "")
        if title or snippet:
            parts.append(f"Source: {title}. {snippet}".strip())
        if excerpt:
            parts.append(f"Source content excerpt: {excerpt[:3500]}")
    return "\n".join(parts)


def _step_node_prompt(
    topic: str,
    learner_level: str,
    score: float,
    weak_points: list[str],
    topic_context=None,
) -> str:
    weak_line = weak_points[:4] if weak_points else []
    context_text = _format_topic_context(topic_context)
    return (
        "You are generating a precise curriculum roadmap for the confirmed topic.\n"
        "Your job is to produce the sequence a knowledgeable tutor would actually teach.\n"
        "Each step must represent a real topic-specific concept group, not a generic learning activity.\n"
        "The roadmap must follow prerequisite order and must be grounded in the confirmed topic meaning.\n\n"
        "Return valid JSON only in this exact format:\n"
        "{\"steps\":[{\"id\":\"step_1\",\"title\":\"Concrete concept title\",\"description\":\"One short sentence describing what the learner studies in this step.\",\"prerequisites\":[]}]} \n\n"
        "Rules:\n"
        "- Generate 5 to 8 steps.\n"
        "- Each step must be a meaningful curriculum stage for this exact topic.\n"
        "- Titles must be concrete, domain-specific, and recognizable to someone who knows the topic.\n"
        "- Use real concept names, primitives, methods, structures, operations, formulas, protocols, APIs, tools, or techniques.\n"
        "- Descriptions must explain what the learner studies, not just say why the step matters.\n"
        "- The roadmap must be prerequisite-aware: definitions and architecture before internals, internals before building, building before production.\n"
        "- prerequisites must contain step ids from earlier steps only.\n"
        "- Do not create cycles.\n"
        "- Do not use generic titles such as Foundations, Basics, Key Concepts, Applications, Practice, Review, Summary, Overview, Step 1, or Step 2.\n"
        "- Do not create generic software/server/networking roadmaps unless those are the actual topic.\n"
        "- Do not overemphasize deployment, networking, scaling, or system administration before the topic's core primitives are taught.\n"
        "- If Required topic coverage is provided, the roadmap must include those ideas across the steps unless they are clearly irrelevant.\n"
        "- If source content excerpts are provided, use the concepts and section headings inside those excerpts as the main curriculum evidence.\n"
        "- Do not write quiz-like steps.\n"
        "- Do not copy question wording.\n"
        "- Do not add study advice like start here, build confidence, or quick recap.\n"
        "- Do not return subtopics, bullets, markdown, or explanations outside the JSON.\n"
        "- The output should read like a real curriculum.\n\n"
        "Quality target:\n"
        "- For a protocol or framework, include its architecture, core primitives, request lifecycle, implementation workflow, reliability/security, and production design.\n"
        "- For MCP / Model Context Protocol, a good roadmap would include concepts like clients, servers, tools, resources, prompts, JSON-RPC, transports, capability discovery, tool calls, and safe server design.\n"
        "- Adapt this pattern to the actual topic instead of copying it blindly.\n\n"
        f"Topic: {topic}\n"
        f"Confirmed grounding/context: {context_text or 'No external grounding was provided.'}\n"
        f"Learner level: {learner_level}\n"
        f"Diagnostic score: {round(score, 3)}\n"
        f"Weak areas: {json.dumps(weak_line, ensure_ascii=False)}\n\n"
        "Important:\n"
        "The learner context may affect pacing and emphasis, but it must not distort the core concept order.\n"
        "The roadmap must stay academically correct for the topic."
    )


def _normalize_graph_steps(raw_steps: list, topic: str) -> list[dict]:
    normalized = []
    seen_ids = set()
    fallback_steps = _build_roadmap_step_plan(topic, 0.0, [])

    for index, item in enumerate(raw_steps or [], start=1):
        if not isinstance(item, dict):
            continue
        step_id = _normalize_text(item.get("id") or f"step_{index}").replace(" ", "_").lower()
        if not step_id or step_id in seen_ids:
            step_id = f"step_{index}"
        seen_ids.add(step_id)
        title = _normalize_text(item.get("title") or "")
        description = _normalize_text(item.get("description") or "")
        prerequisites = [
            _normalize_text(value).replace(" ", "_").lower()
            for value in (item.get("prerequisites") or [])
            if _normalize_text(value)
        ]
        if not title:
            fallback = fallback_steps[min(index - 1, len(fallback_steps) - 1)]
            title = fallback["title"]
        if not description:
            fallback = fallback_steps[min(index - 1, len(fallback_steps) - 1)]
            description = fallback["description"]
        normalized.append(
            {
                "id": step_id,
                "title": title,
                "description": description,
                "prerequisites": prerequisites,
                "minutes": int(item.get("minutes") or 15),
            }
        )

    if normalized:
        return normalized[:10]

    fallback_generated = []
    for index, step in enumerate(fallback_steps, start=1):
        fallback_generated.append(
            {
                "id": f"step_{index}",
                "title": step["title"],
                "description": step["description"],
                "prerequisites": [f"step_{index - 1}"] if index > 1 else [],
                "minutes": int(step.get("minutes") or 15),
            }
        )
    return fallback_generated


def _generate_step_nodes(
    topic: str,
    learner_level: str,
    score: float,
    weak_points: list[str],
    topic_context=None,
) -> list[dict]:
    prompt = _step_node_prompt(topic, learner_level, score, weak_points, topic_context)
    try:
        response = _get_llm().invoke(prompt)
        payload = _extract_json_object(getattr(response, "content", response))
        raw_steps = payload.get("steps") if isinstance(payload, dict) else []
        return _normalize_graph_steps(raw_steps if isinstance(raw_steps, list) else [], topic)
    except Exception:
        return _normalize_graph_steps([], topic)


def _topologically_order_step_nodes(step_nodes: list[dict]) -> tuple[list[dict], bool]:
    if not step_nodes:
        return [], True

    lookup = {step["id"]: {**step} for step in step_nodes if step.get("id")}
    if not lookup:
        return [], True

    if nx is None:
        ordered = list(lookup.values())
        return ordered, False

    graph = nx.DiGraph()
    for node_id, step in lookup.items():
        graph.add_node(node_id, **step)

    for step in lookup.values():
        for prerequisite_id in step.get("prerequisites") or []:
            if prerequisite_id in lookup and prerequisite_id != step["id"]:
                graph.add_edge(prerequisite_id, step["id"])

    try:
        ordered_ids = list(nx.topological_sort(graph))
        return [dict(graph.nodes[node_id]) for node_id in ordered_ids], True
    except nx.NetworkXUnfeasible:
        return list(lookup.values()), False


def _get_llm():
    global LLM
    if LLM is None:
        if ChatGroq is None:
            raise RuntimeError("langchain_groq is not installed")
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        LLM = ChatGroq(model=GROQ_MODEL, api_key=api_key, temperature=0.2)
    return LLM


def _extract_json_object(text: str) -> dict:
    text = str(text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _fallback_preview_terms(topic: str, step: dict, order: int | None = None) -> list[str]:
    stopwords = {
        "and", "for", "the", "with", "from", "into", "this", "that", "your",
        "step", "topic", "basics", "examples", "guided", "walkthroughs",
        "practice", "checkpoint", "review", "next", "start", "learn",
        "cover", "needed", "understand", "short", "core", "ideas",
    }
    text = " ".join(
        [
            _normalize_text(topic),
            _normalize_text(step.get("step_title") or step.get("title") or ""),
            _normalize_text(step.get("step_description") or step.get("description") or ""),
        ]
    )
    words = [
        word for word in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]*", text)
        if len(word) > 2 and word.lower() not in stopwords
    ]
    terms = _unique_preserve_order(words)
    return terms[:4]


def build_path_preview_terms(topic: str, steps: list[dict], topic_context=None) -> list[list[str]]:
    prompt_steps = []
    for index, step in enumerate(steps, start=1):
        prompt_steps.append(
            {
                "order": step.get("step_order") or index,
                "title": step.get("step_title") or step.get("title") or "",
                "description": step.get("step_description") or step.get("description") or "",
            }
        )
    context_text = _format_topic_context(topic_context)
    cache_key = json.dumps([topic, prompt_steps, context_text], ensure_ascii=False, sort_keys=True)
    if cache_key in PREVIEW_TERMS_CACHE:
        return PREVIEW_TERMS_CACHE[cache_key]

    prompt = (
        "Generate learner-facing preview terms for an adaptive tutor roadmap.\n"
        "Return only valid JSON with this shape: "
        "{\"steps\":[{\"order\":1,\"terms\":[\"term\",\"term\",\"term\",\"term\"]}]}.\n"
        "Rules: exactly 4 concise terms per step; terms must be specific to the topic and stage; "
        "use the confirmed grounding/context as the source of truth when available; "
        "if source content excerpts are present, choose terms from the concepts and section headings inside those excerpts; "
        "avoid generic words like basics, examples, practice, review unless they are part of a real concept; "
        "use title case for terms; do not include explanations.\n\n"
        f"Topic: {topic}\n"
        f"Confirmed grounding/context: {context_text or 'No external grounding was provided.'}\n"
        f"Roadmap steps: {json.dumps(prompt_steps, ensure_ascii=False)}"
    )

    try:
        response = _get_llm().invoke(prompt)
        payload = _extract_json_object(getattr(response, "content", response))
        rows = payload.get("steps") if isinstance(payload, dict) else []
        by_order = {}
        for row in rows if isinstance(rows, list) else []:
            terms = row.get("terms") if isinstance(row, dict) else []
            if not isinstance(terms, list):
                continue
            try:
                order = int(row.get("order"))
            except (TypeError, ValueError):
                continue
            by_order[order] = _unique_preserve_order(str(term) for term in terms)[:4]

        generated = []
        for index, step in enumerate(steps, start=1):
            order = int(step.get("step_order") or index)
            terms = by_order.get(order) or _fallback_preview_terms(topic, step, order)
            generated.append(terms[:4])
        PREVIEW_TERMS_CACHE[cache_key] = generated
        return generated
    except Exception:
        generated = [
            _fallback_preview_terms(topic, step, step.get("step_order") or index)
            for index, step in enumerate(steps, start=1)
        ]
        PREVIEW_TERMS_CACHE[cache_key] = generated
        return generated


def build_step_preview_terms(topic: str, step: dict, order: int | None = None) -> list[str]:
    step_with_order = {**step, "step_order": order or step.get("step_order") or 1}
    return build_path_preview_terms(topic, [step_with_order])[0]


def _fallback_step_title(topic: str, terms: list[str], step: dict, order: int | None = None) -> str:
    clean_terms = _unique_preserve_order(str(term) for term in terms if _normalize_text(term))
    if len(clean_terms) >= 2:
        return f"{clean_terms[0]} and {clean_terms[1]}"
    if len(clean_terms) == 1:
        return f"{clean_terms[0]} in {topic}"
    return _normalize_text(step.get("step_title") or step.get("title") or topic)


def build_path_step_titles(topic: str, steps: list[dict], preview_terms_by_step: list[list[str]]) -> list[str]:
    prompt_steps = []
    for index, step in enumerate(steps, start=1):
        prompt_steps.append(
            {
                "order": step.get("step_order") or index,
                "current_title": step.get("step_title") or step.get("title") or "",
                "description": step.get("step_description") or step.get("description") or "",
                "preview_terms": preview_terms_by_step[index - 1] if index - 1 < len(preview_terms_by_step) else [],
            }
        )
    cache_key = json.dumps([topic, prompt_steps], ensure_ascii=False, sort_keys=True)
    if cache_key in STEP_TITLES_CACHE:
        return STEP_TITLES_CACHE[cache_key]

    prompt = (
        "Rewrite roadmap step headings for an adaptive tutor.\n"
        "Return only valid JSON with this shape: "
        "{\"steps\":[{\"order\":1,\"title\":\"Specific heading\"}]}.\n"
        "Rules: one short learner-facing heading per step; 3 to 7 words each; "
        "make each heading specific to the topic and preview terms; preserve step order and progression; "
        "avoid generic scaffolding like Foundations, Key ideas, Guided examples, Practice, Review, Basics, or Prerequisites "
        "unless the word is part of an actual domain concept; do not include numbering or status.\n\n"
        f"Topic: {topic}\n"
        f"Roadmap steps: {json.dumps(prompt_steps, ensure_ascii=False)}"
    )

    try:
        response = _get_llm().invoke(prompt)
        payload = _extract_json_object(getattr(response, "content", response))
        rows = payload.get("steps") if isinstance(payload, dict) else []
        by_order = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            title = _normalize_text(row.get("title") or "")
            if not title:
                continue
            try:
                order = int(row.get("order"))
            except (TypeError, ValueError):
                continue
            by_order[order] = title

        generated = []
        for index, step in enumerate(steps, start=1):
            order = int(step.get("step_order") or index)
            terms = preview_terms_by_step[index - 1] if index - 1 < len(preview_terms_by_step) else []
            generated.append(by_order.get(order) or _fallback_step_title(topic, terms, step, order))
        STEP_TITLES_CACHE[cache_key] = generated
        return generated
    except Exception:
        generated = [
            _fallback_step_title(
                topic,
                preview_terms_by_step[index - 1] if index - 1 < len(preview_terms_by_step) else [],
                step,
                step.get("step_order") or index,
            )
            for index, step in enumerate(steps, start=1)
        ]
        STEP_TITLES_CACHE[cache_key] = generated
        return generated


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
    topic_context=None,
) -> dict:
    topic = _normalize_text(topic)
    mode = _normalize_study_mode(study_mode)
    diagnostic_result = diagnostic_result or {}
    preferences = _load_learner_preferences(conn, learner_id)
    profile_snapshot = _load_active_profile(conn, learner_id, subject_id)

    score = float(diagnostic_result.get("score") or 0)
    weak_points = _extract_weak_points(diagnostic_result) if diagnostic_result else []
    learner_level = profile_snapshot.get("current_level") or "beginner"
    if mode == "roadmap":
        if score >= 0.8:
            learner_level = "advanced"
        elif score >= 0.4:
            learner_level = "intermediate"
        else:
            learner_level = "beginner"
    focus_line = ", ".join(weak_points[:3]) if weak_points else f"{topic} fundamentals"
    if mode == "roadmap":
        generated_nodes = _generate_step_nodes(topic, learner_level, score, weak_points, topic_context)
        steps, graph_valid = _topologically_order_step_nodes(generated_nodes)
    else:
        steps = _build_step_plan(topic, score, weak_points, mode, preferences)
        graph_valid = True
    estimated_total_minutes = sum(int(step.get("minutes", 0)) for step in steps)
    content_depth = "concise" if mode == "quick_study" else "detailed"
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

    step_rows = []
    preview_terms_by_step = build_path_preview_terms(topic, steps, topic_context=topic_context)
    step_titles = build_path_step_titles(topic, steps, preview_terms_by_step)
    step_id_lookup = {}
    topic_id_lookup = {}
    for order, step in enumerate(steps, start=1):
        graph_step_id = step.get("id") or f"step_{order}"
        step_title = step_titles[order - 1] if order - 1 < len(step_titles) else step["title"]
        step_id_lookup[graph_step_id] = str(uuid.uuid4())
        topic_id_lookup[graph_step_id] = _upsert_subject_topic(
            conn,
            subject_id,
            step_title,
            description=step["description"],
            prerequisite_topic_id=None,
            topic_order=order,
        )

    for order, step in enumerate(steps, start=1):
        graph_step_id = step.get("id") or f"step_{order}"
        step_title = step_titles[order - 1] if order - 1 < len(step_titles) else step["title"]
        preview_terms = preview_terms_by_step[order - 1] if order - 1 < len(preview_terms_by_step) else []
        prerequisite_ids = [prereq for prereq in (step.get("prerequisites") or []) if prereq in step_id_lookup]
        prerequisite_step_ids = [step_id_lookup[prereq] for prereq in prerequisite_ids]
        primary_prereq_topic_id = topic_id_lookup.get(prerequisite_ids[0]) if prerequisite_ids else root_topic_id

        conn.execute(
            """
            UPDATE topics
            SET prerequisite_topic_id = ?,
                topic_order = ?
            WHERE topic_id = ?
            """,
            (primary_prereq_topic_id, order, topic_id_lookup[graph_step_id]),
        )

        conn.execute(
            """
            INSERT INTO learning_path_steps (
                step_id, path_id, topic_id, resource_id, chunk_id,
                content_version, step_order, step_title, step_description, preview_terms,
                prerequisite_step_ids, step_status, estimated_minutes, actual_minutes,
                started_at, completed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 'not_started', ?, 0, NULL, NULL, datetime('now'), datetime('now'))
            """,
            (
                step_id_lookup[graph_step_id],
                path_id,
                topic_id_lookup[graph_step_id],
                None,
                None,
                order,
                step_title,
                step["description"],
                json.dumps(preview_terms),
                json.dumps(prerequisite_step_ids),
                step["minutes"],
            ),
        )
        step_rows.append(
            {
                "graph_step_id": graph_step_id,
                "path_id": path_id,
                "step_id": step_id_lookup[graph_step_id],
                "step_order": order,
                "step_title": step_title,
                "step_description": step["description"],
                "preview_terms": preview_terms,
                "prerequisite_step_ids": prerequisite_step_ids,
                "estimated_minutes": step["minutes"],
                "topic_id": topic_id_lookup[graph_step_id],
                "content_depth": content_depth,
                "step_focus": weak_points[order - 1] if order - 1 < len(weak_points) else topic,
            }
        )

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

    print({
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
        "prerequisite_graph_valid": graph_valid,
    })
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
        "prerequisite_graph_valid": graph_valid,
    }
