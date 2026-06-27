import json
import os
import sqlite3
import uuid
import sys
import re
import cgi
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


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
from agents.quick_study_chat import (
    answer_quick_study_question,
    create_quick_study_session,
    index_pdf_document,
    list_quick_study_sessions,
    load_quick_study_session,
)

DB_PATH = BASE_DIR / "adaptive_tutor_v2.db"
ROOT_SCHEMA_PATH = BASE_DIR.parent / "data" / "sql" / "user_profile_schema.sql"


def is_provider_rate_limit_error(error):
    error_name = error.__class__.__name__.lower()
    error_text = str(error).lower()
    return (
        "ratelimit" in error_name
        or "rate limit" in error_text
        or "rate_limit_exceeded" in error_text
        or "error code: 429" in error_text
    )


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
        conn.commit()


def ensure_runtime_migrations(conn):
    step_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(learning_path_steps)").fetchall()
    }
    if "preview_terms" not in step_columns:
        conn.execute("ALTER TABLE learning_path_steps ADD COLUMN preview_terms TEXT")

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
            conn.execute(f"ALTER TABLE quiz_responses ADD COLUMN {column_name} {column_type}")

    migrate_quiz_questions_table(conn)


def migrate_quiz_questions_table(conn):
    table_info = conn.execute("PRAGMA table_info(quiz_questions)").fetchall()
    if not table_info:
        return

    columns = {row["name"] for row in table_info}
    attempt_id_is_not_null = any(row["name"] == "attempt_id" and int(row["notnull"] or 0) == 1 for row in table_info)
    required_columns = {
        "subject_id": "TEXT",
        "path_id": "TEXT",
        "step_id": "TEXT",
        "topic": "TEXT",
        "concept": "TEXT",
        "bloom_level": "TEXT",
        "concept_count": "INTEGER",
        "reasoning_steps": "INTEGER",
        "difficulty_score": "REAL",
        "calibrated_difficulty": "TEXT",
        "question_source": "TEXT NOT NULL DEFAULT 'adaptive_bank'",
        "is_active": "INTEGER NOT NULL DEFAULT 1",
    }

    if attempt_id_is_not_null:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("ALTER TABLE quiz_questions RENAME TO quiz_questions_old")
        conn.execute(
            """
            CREATE TABLE quiz_questions (
                question_id TEXT PRIMARY KEY NOT NULL,
                attempt_id TEXT,
                subject_id TEXT,
                path_id TEXT,
                step_id TEXT,
                topic TEXT,
                concept TEXT,
                question_text TEXT NOT NULL,
                options_json TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                explanation TEXT,
                difficulty_level TEXT NOT NULL,
                bloom_level TEXT,
                concept_count INTEGER,
                reasoning_steps INTEGER,
                difficulty_score REAL,
                calibrated_difficulty TEXT,
                question_source TEXT NOT NULL DEFAULT 'adaptive_bank',
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (attempt_id)
                    REFERENCES quiz_attempts(attempt_id)
                    ON DELETE CASCADE
            )
            """
        )
        old_columns = {row["name"] for row in conn.execute("PRAGMA table_info(quiz_questions_old)").fetchall()}
        selectable_columns = [
            "question_id",
            "attempt_id",
            "question_text",
            "options_json",
            "correct_answer",
            "explanation",
            "difficulty_level",
            "created_at",
        ]
        insert_columns = [column for column in selectable_columns if column in old_columns]
        conn.execute(
            f"""
            INSERT INTO quiz_questions ({', '.join(insert_columns)})
            SELECT {', '.join(insert_columns)}
            FROM quiz_questions_old
            """
        )
        conn.execute(
            """
            UPDATE quiz_questions
            SET calibrated_difficulty = COALESCE(calibrated_difficulty, difficulty_level),
                question_source = COALESCE(question_source, 'attempt_generated'),
                is_active = COALESCE(is_active, 1)
            """
        )
        conn.execute("DROP TABLE quiz_questions_old")
        conn.execute("PRAGMA foreign_keys = ON")
        return

    for column_name, column_type in required_columns.items():
        if column_name not in columns:
            conn.execute(f"ALTER TABLE quiz_questions ADD COLUMN {column_name} {column_type}")

    conn.execute(
        """
        UPDATE quiz_questions
        SET calibrated_difficulty = COALESCE(calibrated_difficulty, difficulty_level),
            question_source = COALESCE(question_source, 'adaptive_bank'),
            is_active = COALESCE(is_active, 1)
        """
    )
    existing_response_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(quiz_responses)")
    }
    for column_name, column_type in (
        ("question_difficulty", "TEXT"),
        ("difficulty_after", "TEXT"),
        ("explanation", "TEXT"),
        ("options_json", "TEXT"),
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
    if "prerequisite_step_ids" not in columns:
        conn.execute("ALTER TABLE learning_path_steps ADD COLUMN prerequisite_step_ids TEXT")

    concept_mastery_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(concept_mastery)").fetchall()
    }
    for column_name, column_type in (
        ("bkt_prior", "REAL NOT NULL DEFAULT 0.25"),
        ("bkt_transit", "REAL NOT NULL DEFAULT 0.12"),
        ("bkt_guess", "REAL NOT NULL DEFAULT 0.2"),
        ("bkt_slip", "REAL NOT NULL DEFAULT 0.1"),
    ):
        if column_name not in concept_mastery_columns:
            conn.execute(f"ALTER TABLE concept_mastery ADD COLUMN {column_name} {column_type}")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS quick_study_sessions (
            session_id TEXT PRIMARY KEY NOT NULL,
            learner_id TEXT NOT NULL,
            subject_id TEXT,
            topic TEXT NOT NULL,
            title TEXT NOT NULL,
            session_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_accessed_at TEXT,
            FOREIGN KEY (learner_id) REFERENCES learners(learner_id) ON DELETE CASCADE,
            FOREIGN KEY (subject_id) REFERENCES subjects(subject_id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_quick_study_sessions_learner
        ON quick_study_sessions (learner_id, updated_at);

        CREATE TABLE IF NOT EXISTS quick_study_documents (
            document_id TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 0,
            upload_status TEXT NOT NULL DEFAULT 'indexed',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES quick_study_sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_quick_study_documents_session
        ON quick_study_documents (session_id);

        CREATE TABLE IF NOT EXISTS quick_study_chunks (
            chunk_id TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chunk_order INTEGER NOT NULL,
            page_number INTEGER,
            chunk_text TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES quick_study_sessions(session_id) ON DELETE CASCADE,
            FOREIGN KEY (document_id) REFERENCES quick_study_documents(document_id) ON DELETE CASCADE,
            UNIQUE (document_id, chunk_order)
        );

        CREATE INDEX IF NOT EXISTS idx_quick_study_chunks_session
        ON quick_study_chunks (session_id, chunk_order);

        CREATE TABLE IF NOT EXISTS quick_study_messages (
            message_id TEXT PRIMARY KEY NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            message_text TEXT NOT NULL,
            sources_json TEXT,
            followups_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (session_id) REFERENCES quick_study_sessions(session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_quick_study_messages_session
        ON quick_study_messages (session_id, created_at);
        """
    )


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


def text_response(handler, status_code, body, content_type):
    payload = body.encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
    handler.end_headers()
    handler.wfile.write(payload)


def read_json(handler):
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length).decode("utf-8") if length else "{}"
    return json.loads(raw or "{}")


def read_multipart(handler):
    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": handler.headers.get("Content-Type"),
        },
    )
    fields = {}
    files = []
    for key in form.keys():
        item = form[key]
        items = item if isinstance(item, list) else [item]
        for part in items:
            if part.filename:
                files.append(
                    {
                        "field": key,
                        "filename": os.path.basename(part.filename),
                        "content": part.file.read(),
                    }
                )
            else:
                fields[key] = part.value
    return fields, files


def fetchone_dict(conn, query, params=()):
    row = conn.execute(query, params).fetchone()
    return dict(row) if row else None


def fetchall_dict(conn, query, params=()):
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def find_learner_by_email(conn, email):
    email = normalize_text_value(email).lower()
    if not email:
        return None
    return conn.execute(
        "SELECT learner_id, email, full_name FROM learners WHERE email = ?",
        (email,),
    ).fetchone()


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


def parse_json_object(value):
    if isinstance(value, dict):
        return {str(key): value[key] for key in value}
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def normalize_quiz_style_value(value):
    return "mcq"


def normalize_text_value(value, fallback=""):
    if value is None:
        return fallback
    if isinstance(value, list):
        cleaned = [str(item).strip() for item in value if str(item or "").strip()]
        return ", ".join(cleaned) if cleaned else fallback
    cleaned = str(value).strip()
    return cleaned or fallback


def normalize_answer_value(value):
    return str(value or "").strip().upper()


def canonicalize_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def normalize_topic_text(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]+", " ", str(value or "").strip().lower())).strip()


def _serpapi_key():
    return (
        os.environ.get("SERPAPI_API_KEY")
        or os.environ.get("SERP_API_KEY")
        or os.environ.get("SERPAPI_KEY")
        or ""
    ).strip()


class _ReadableHTMLParser(HTMLParser):
    BLOCK_TAGS = {"h1", "h2", "h3", "h4", "p", "li"}
    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self._skip_depth = 0
        self._current_tag = None
        self._current_parts = []
        self.lines = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self.BLOCK_TAGS:
            self._flush()
            self._current_tag = tag

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == self._current_tag:
            self._flush()

    def handle_data(self, data):
        if self._skip_depth or self._current_tag is None:
            return
        cleaned = normalize_text_value(data)
        if cleaned:
            self._current_parts.append(cleaned)

    def _flush(self):
        if not self._current_parts:
            self._current_tag = None
            return
        text = normalize_text_value(" ".join(self._current_parts))
        if len(text) >= 18:
            self.lines.append(text)
        self._current_parts = []
        self._current_tag = None


def _fetch_source_excerpt(link, max_chars=4500):
    parsed = urlparse(link or "")
    if parsed.scheme not in {"http", "https"} or parsed.path.lower().endswith(".pdf"):
        return ""

    request = Request(
        link,
        headers={
            "User-Agent": "Mozilla/5.0 AdaptiveTutor/1.0; topic-grounding",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return ""
            raw = response.read(300_000)
    except (OSError, URLError, TimeoutError):
        return ""

    parser = _ReadableHTMLParser()
    parser.feed(raw.decode("utf-8", errors="ignore"))
    parser.close()

    unique_lines = []
    seen = set()
    for line in parser.lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_lines.append(line)
        if sum(len(item) for item in unique_lines) >= max_chars:
            break
    return "\n".join(unique_lines)[:max_chars]


def _fetch_serpapi_context(topic):
    api_key = _serpapi_key()
    if not api_key:
        return [], "SERP API key is not configured."

    normalized = normalize_topic_text(topic)
    query = f"{topic} meaning tutorial learning topic"
    if "mcp" in normalized.split() or normalized.startswith("mcp "):
        query = (
            "MCP servers Model Context Protocol AI tools resources prompts "
            "JSON-RPC transport SSE initialization capability discovery"
        )
    if "graph rag" in normalized or "graphrag" in normalized:
        query = (
            "GraphRAG tutorial knowledge graph RAG vector search graph database "
            "architecture retrieval Cypher LangChain Neo4j"
        )
    params = urlencode({"engine": "google", "q": query, "num": 10, "api_key": api_key})
    url = f"https://serpapi.com/search.json?{params}"
    try:
        with urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
        return [], f"SERP lookup failed: {error}"

    sources = []
    readable_count = 0
    for item in payload.get("organic_results", [])[:10]:
        title = normalize_text_value(item.get("title"))
        snippet = normalize_text_value(item.get("snippet"))
        link = normalize_text_value(item.get("link"))
        if title or snippet:
            source = {"title": title, "snippet": snippet, "link": link}
            if readable_count < 3 and link:
                excerpt = _fetch_source_excerpt(link)
                if excerpt:
                    source["content_excerpt"] = excerpt
                    readable_count += 1
            sources.append(source)
    return sources, ""


def _extract_grounding_hints(topic, evidence_text, sources=None):
    normalized = normalize_topic_text(topic)
    evidence_lower = evidence_text.lower()
    hints = []
    if "mcp" in normalized.split() or normalized.startswith("mcp ") or "model context protocol" in evidence_lower:
        hints = [
            "MCP purpose and AI context integration",
            "Client-server architecture",
            "Tools, resources, and prompts",
            "JSON-RPC request and response lifecycle",
            "Initialization and capability discovery",
            "STDIO, HTTP, and SSE transports",
            "Building tool, resource, and prompt servers",
            "MCP server configuration",
            "Security boundaries and permission design",
            "Logging, monitoring, deployment, and multi-server orchestration",
        ]
    if "graph rag" in normalized or "graphrag" in normalized or "graphrag" in evidence_lower:
        hints = [
            "Basic RAG process: retrieval, augmentation, and generation",
            "Core components of a RAG architecture",
            "Knowledge graphs in retrieval",
            "GraphRAG architecture overview",
            "Why graph retrieval goes beyond vector-only RAG",
            "Preparing and modeling the dataset",
            "Neo4j environment setup",
            "Vector indexes and embeddings",
            "Graph Cypher search",
            "LangChain integration",
            "Best practices and common pitfalls",
        ]
    if hints:
        return hints

    phrases = []
    source_text = "\n".join(
        source.get("content_excerpt", "") for source in (sources or []) if isinstance(source, dict)
    )
    for line in source_text.splitlines():
        cleaned = normalize_text_value(line)
        if 8 <= len(cleaned) <= 90 and not cleaned.endswith("."):
            phrases.append(cleaned)
    for match in re.finditer(r"\b[A-Z][A-Za-z0-9+#.-]*(?:\s+[A-Z][A-Za-z0-9+#.-]*){0,3}\b", evidence_text):
        phrase = normalize_text_value(match.group(0))
        if phrase and phrase.lower() not in {"google", "youtube", "wikipedia"}:
            phrases.append(phrase)
    return list(dict.fromkeys(phrases))[:10]


def ground_topic(topic):
    raw_topic = normalize_text_value(topic)
    if not raw_topic:
        raise ValueError("topic is required")

    sources, lookup_note = _fetch_serpapi_context(raw_topic)
    evidence_text = " ".join(
        f"{source.get('title', '')} {source.get('snippet', '')}" for source in sources
    )
    evidence_lower = evidence_text.lower()
    normalized = normalize_topic_text(raw_topic)

    canonical_topic = raw_topic
    definition = f"Study material and quiz questions should focus directly on {raw_topic}."
    confidence = 0.68

    if "mcp" in normalized.split() or normalized.startswith("mcp "):
        if "model context protocol" in evidence_lower or not sources:
            canonical_topic = "Model Context Protocol servers"
            definition = (
                "MCP servers are Model Context Protocol servers that expose tools, data, "
                "and resources so AI applications can use external context safely."
            )
            confidence = 0.88 if sources else 0.78
        else:
            confidence = 0.52
            definition = (
                "MCP can mean different things. Please confirm the intended meaning before "
                "the diagnostic quiz is generated."
            )
    elif sources:
        best = sources[0]
        definition = best.get("snippet") or definition
        confidence = 0.76
        title = best.get("title", "")
        if title and topic_similarity(raw_topic, title) > 0:
            canonical_topic = raw_topic

    return {
        "raw_topic": raw_topic,
        "canonical_topic": canonical_topic,
        "definition": definition,
        "confidence": confidence,
        "needs_confirmation": True,
        "used_serpapi": bool(sources),
        "lookup_note": lookup_note,
        "curriculum_hints": _extract_grounding_hints(raw_topic, evidence_text, sources),
        "sources": sources,
    }


def tokenize_topic(value):
    return [token for token in normalize_topic_text(value).split(" ") if len(token) > 2]


def topic_similarity(left, right):
    left_tokens = tokenize_topic(left)
    right_tokens = tokenize_topic(right)
    if not left_tokens or not right_tokens:
        return 0.0
    right_set = set(right_tokens)
    score = 0.0
    for token in left_tokens:
        if token in right_set:
            score += 3.0
            continue
        partial_match = next(
            (candidate for candidate in right_tokens if candidate.startswith(token) or token.startswith(candidate)),
            None,
        )
        if partial_match:
            score += 1.0
    return score


def escape_xml(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def resolve_answer_text(answer_key, options):
    normalized_options = parse_json_object(options)
    cleaned_key = str(answer_key or "").strip().upper()
    if cleaned_key and cleaned_key in normalized_options:
        return str(normalized_options[cleaned_key])
    return cleaned_key


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
    response_topic = response.get("topic")
    response_concept = response.get("concept")
    for candidate in (response_topic, response_concept):
        for term in preview_terms:
            if _term_matches_text(term, candidate):
                return term

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


def _clamp_probability(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def _bkt_preview_update(prior, is_correct, transit=0.12, guess=0.20, slip=0.10):
    prior = _clamp_probability(prior)
    transit = _clamp_probability(transit)
    guess = _clamp_probability(guess, 0.01, 0.99)
    slip = _clamp_probability(slip, 0.01, 0.99)

    if is_correct:
        numerator = prior * (1.0 - slip)
        denominator = numerator + ((1.0 - prior) * guess)
    else:
        numerator = prior * slip
        denominator = numerator + ((1.0 - prior) * (1.0 - guess))

    posterior = numerator / denominator if denominator else prior
    return _clamp_probability(posterior + ((1.0 - posterior) * transit))


def _timeline_point(index, response, prior, posterior):
    return {
        "question_number": index + 1,
        "question_text": response.get("question_text"),
        "concept": response.get("concept"),
        "is_correct": bool(int(response.get("is_correct") or 0)),
        "prior": round(prior * 100, 1),
        "after": round(posterior * 100, 1),
    }


def build_mastery_breakdown(conn, path_steps, latest_quiz, latest_quiz_responses):
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
            term: {"answered": 0, "correct": 0, "mastery": 0.25, "timeline": []}
            for term in preview_terms
        }

        for index, response in enumerate(latest_quiz_responses or []):
            assigned_term = _assign_term_to_response(response, preview_terms, index)
            if not assigned_term:
                continue
            bucket = term_stats.setdefault(assigned_term, {"answered": 0, "correct": 0, "mastery": 0.25, "timeline": []})
            prior = bucket["mastery"]
            posterior = _bkt_preview_update(prior, int(response.get("is_correct") or 0) == 1)
            bucket["answered"] += 1
            if int(response.get("is_correct") or 0):
                bucket["correct"] += 1
            bucket["mastery"] = posterior
            bucket["timeline"].append(_timeline_point(index, response, prior, posterior))

        answer_count = len(latest_quiz_responses or [])
        correct_count = sum(1 for response in latest_quiz_responses or [] if int(response.get("is_correct") or 0))
        accuracy = round((correct_count / answer_count) * 100, 1) if answer_count else None

        subtopics = []
        mastery_values = []
        for term in preview_terms:
            stats = term_stats.get(term, {"answered": 0, "correct": 0, "mastery": 0.25, "timeline": []})
            answered = int(stats["answered"])
            correct = int(stats["correct"])
            bkt_mastery = round(float(stats["mastery"]) * 100, 1) if answered else None
            term_accuracy = round((correct / answered) * 100, 1) if answered else None
            display_mastery = bkt_mastery if bkt_mastery is not None else term_accuracy
            if display_mastery is not None:
                mastery_values.append(display_mastery)

            if display_mastery is not None and display_mastery >= 75:
                bucket = "strong"
            elif display_mastery is not None and display_mastery < 60:
                bucket = "review"
            elif display_mastery is not None:
                bucket = "practice"
            else:
                bucket = "untried"
            subtopics.append({
                "topic": term,
                "answered": answered,
                "correct": correct,
                "accuracy": term_accuracy,
                "mastery": bkt_mastery,
                "mastery_source": "preview_term_bkt" if bkt_mastery is not None else "accuracy",
                "mastery_timeline": stats["timeline"],
                "bucket": bucket,
            })

        step_mastery = round(sum(mastery_values) / len(mastery_values), 1) if mastery_values else None
        step_summary["subtopics"] = subtopics
        step_summary["answered_questions"] = answer_count
        step_summary["correct_answers"] = correct_count
        step_summary["accuracy"] = accuracy
        step_summary["mastery"] = step_mastery
        step_summary["mastery_source"] = "bkt" if any(item.get("mastery") is not None for item in subtopics) else "accuracy"
        step_summary["topics_strong"] = [item["topic"] for item in subtopics if item["bucket"] == "strong"]
        step_summary["topics_to_review"] = [item["topic"] for item in subtopics if item["bucket"] == "review"]
        step_summary["topics_to_practice"] = [item["topic"] for item in subtopics if item["bucket"] == "practice"]

        if step_mastery is None and accuracy is None:
            step_summary["summary"] = "No answered questions yet."
        elif step_mastery is not None and step_mastery >= 75:
            step_summary["summary"] = "Strong BKT mastery on this step."
        elif step_mastery is not None and step_mastery >= 60:
            step_summary["summary"] = "Mixed BKT mastery. Some subtopics need another pass."
        elif step_mastery is not None:
            step_summary["summary"] = "Needs review. Focus on the low-mastery subtopics."
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
                "options_json": json.dumps(options or {}),
                "explanation": question.get("explanation") or "",
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
                selected_answer, correct_answer, is_correct, time_taken_seconds,
                explanation, options_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                row["explanation"],
                row["options_json"],
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
            explanation_style,
            quiz_style,
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
            prerequisite_step_ids,
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
    stored_prereqs_by_step = [parse_json_list(step.get("prerequisite_step_ids")) for step in path_steps]
    missing_preview_terms = any(not terms for terms in stored_terms_by_step)
    generated_terms_by_step = (
        build_path_preview_terms(active_subject["subject_name"], path_steps)
        if active_subject and path_steps and missing_preview_terms
        else []
    )
    preview_terms_by_step = []
    for index, step in enumerate(path_steps):
        terms = stored_terms_by_step[index] if index < len(stored_terms_by_step) else []
        step["prerequisite_step_ids"] = stored_prereqs_by_step[index] if index < len(stored_prereqs_by_step) else []
        if not terms and index < len(generated_terms_by_step):
            terms = generated_terms_by_step[index]
            try:
                conn.execute(
                    """
                    UPDATE learning_path_steps
                    SET preview_terms = ?,
                        updated_at = datetime('now')
                    WHERE step_id = ?
                    """,
                    (dump_json_list(terms), step["step_id"]),
                )
            except sqlite3.Error:
                pass
        step["preview_terms"] = terms
        preview_terms_by_step.append(terms)

    display_titles = build_path_step_titles(active_subject["subject_name"], path_steps, preview_terms_by_step) if active_subject and path_steps else []
    for index, step in enumerate(path_steps):
        refined_title = display_titles[index] if index < len(display_titles) else step["step_title"]
        if refined_title and refined_title != step["step_title"]:
            try:
                conn.execute(
                    """
                    UPDATE learning_path_steps
                    SET step_title = ?,
                        updated_at = datetime('now')
                    WHERE step_id = ?
                    """,
                    (refined_title, step["step_id"]),
                )
            except sqlite3.Error:
                pass
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
            quiz_responses.response_id,
            quiz_responses.attempt_id,
            quiz_responses.question_id,
            quiz_responses.question_text,
            quiz_responses.selected_answer,
            quiz_responses.correct_answer,
            quiz_responses.is_correct,
            quiz_responses.time_taken_seconds,
            qq.topic,
            qq.concept,
            quiz_responses.created_at
        FROM quiz_responses
        LEFT JOIN quiz_questions qq ON qq.question_id = quiz_responses.question_id
        WHERE quiz_responses.attempt_id = ?
        ORDER BY quiz_responses.created_at ASC
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
                quiz_responses.response_id,
                quiz_responses.attempt_id,
                quiz_responses.question_id,
                quiz_responses.question_text,
                quiz_responses.selected_answer,
                quiz_responses.correct_answer,
                quiz_responses.is_correct,
                quiz_responses.time_taken_seconds,
                qq.topic,
                qq.concept,
                quiz_responses.created_at
            FROM quiz_responses
            LEFT JOIN quiz_questions qq ON qq.question_id = quiz_responses.question_id
            WHERE quiz_responses.attempt_id = ?
            ORDER BY quiz_responses.created_at ASC
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

    mastery_breakdown = build_mastery_breakdown(conn, path_steps, latest_quiz, latest_quiz_responses)
    quick_study_sessions = fetchall_dict(
        conn,
        """
        SELECT
            qss.session_id,
            qss.learner_id,
            qss.subject_id,
            qss.topic,
            qss.title,
            qss.session_status,
            qss.created_at,
            qss.updated_at,
            qss.last_accessed_at,
            COUNT(DISTINCT qsd.document_id) AS document_count,
            COUNT(DISTINCT qsm.message_id) AS message_count,
            COUNT(DISTINCT qsc.chunk_id) AS chunk_count
        FROM quick_study_sessions qss
        LEFT JOIN quick_study_documents qsd ON qsd.session_id = qss.session_id
        LEFT JOIN quick_study_messages qsm ON qsm.session_id = qss.session_id
        LEFT JOIN quick_study_chunks qsc ON qsc.session_id = qss.session_id
        WHERE qss.learner_id = ? AND qss.subject_id = ?
        GROUP BY qss.session_id
        ORDER BY COALESCE(qss.last_accessed_at, qss.updated_at, qss.created_at) DESC
        """,
        (learner["learner_id"], active_subject["subject_id"]) if active_subject else (learner["learner_id"], None),
    ) if active_subject else []

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
        "quick_study_sessions": quick_study_sessions,
        "latest_quick_study_session": quick_study_sessions[0] if quick_study_sessions else None,
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


def get_roadmap_graph_summary(conn, email, selected_subject_id=None):
    summary = get_dashboard_summary(conn, email, selected_subject_id=selected_subject_id)
    if not summary:
        return None

    active_subject = summary.get("active_subject") or {}
    active_path = active_subject.get("active_path") or {}
    path_steps = active_subject.get("path_steps") or []
    step_lookup = {step.get("step_id"): step for step in path_steps if step.get("step_id")}

    nodes = []
    edges = []
    for step in path_steps:
        step_id = step.get("step_id")
        nodes.append({
            "step_id": step_id,
            "step_order": step.get("step_order"),
            "step_title": step.get("step_title"),
            "step_status": step.get("step_status"),
            "preview_terms": step.get("preview_terms") or [],
            "prerequisite_step_ids": step.get("prerequisite_step_ids") or [],
        })
        for prerequisite_step_id in step.get("prerequisite_step_ids") or []:
            edges.append({
                "from_step_id": prerequisite_step_id,
                "to_step_id": step_id,
                "from_step_title": (step_lookup.get(prerequisite_step_id) or {}).get("step_title"),
                "to_step_title": step.get("step_title"),
            })

    networkx_available = False
    graph_valid = True
    topological_order = [step.get("step_id") for step in path_steps if step.get("step_id")]
    try:
        import networkx as nx
        networkx_available = True
        graph = nx.DiGraph()
        for node in nodes:
            graph.add_node(node["step_id"], **node)
        for edge in edges:
            graph.add_edge(edge["from_step_id"], edge["to_step_id"])
        topological_order = list(nx.topological_sort(graph))
    except ImportError:
        networkx_available = False
    except Exception:
        graph_valid = False

    return {
        "learner": {
            "learner_id": summary["learner"].get("learner_id"),
            "email": summary["learner"].get("email"),
        },
        "subject": {
            "subject_id": active_subject.get("subject_id"),
            "subject_name": active_subject.get("subject_name"),
            "current_topic_name": active_subject.get("current_topic_name"),
        },
        "path": {
            "path_id": active_path.get("path_id"),
            "path_title": active_path.get("path_title"),
            "path_status": active_path.get("path_status"),
            "total_steps": active_path.get("total_steps"),
            "completed_steps": active_path.get("completed_steps"),
        },
        "graph": {
            "networkx_available": networkx_available,
            "graph_valid": graph_valid,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "nodes": nodes,
            "edges": edges,
            "topological_order": topological_order,
        },
    }


def build_preview_topic_graph(graph_summary):
    graph = (graph_summary or {}).get("graph") or {}
    step_nodes = list(graph.get("nodes") or [])
    step_edges = list(graph.get("edges") or [])
    order_lookup = {
        step_id: index
        for index, step_id in enumerate(graph.get("topological_order") or [])
    }
    ordered_steps = sorted(
        step_nodes,
        key=lambda node: (
            order_lookup.get(node.get("step_id"), 10**6),
            int(node.get("step_order") or 0),
        ),
    )
    step_count = len(ordered_steps) or 1
    topic_nodes = []
    nodes_by_step = {}
    for index, step in enumerate(ordered_steps):
        topics = parse_json_list(step.get("preview_terms")) or [step.get("step_title") or f"Step {index + 1}"]
        tier = min(2, int((index / step_count) * 3))
        current_nodes = []
        for topic_index, topic in enumerate(topics):
            node = {
                "id": f"{step.get('step_id')}::{topic_index}",
                "label": topic,
                "step_id": step.get("step_id"),
                "step_title": step.get("step_title"),
                "step_order": step.get("step_order"),
                "tier": tier,
            }
            current_nodes.append(node)
            topic_nodes.append(node)
        nodes_by_step[step.get("step_id")] = current_nodes

    topic_edges = []
    edge_keys = set()

    def add_edge(from_node, to_node, reason):
        if not from_node or not to_node or from_node["id"] == to_node["id"]:
            return
        edge_key = (from_node["id"], to_node["id"])
        if edge_key in edge_keys:
            return
        edge_keys.add(edge_key)
        topic_edges.append(
            {
                "from": from_node["id"],
                "to": to_node["id"],
                "from_label": from_node["label"],
                "to_label": to_node["label"],
                "reason": reason,
            }
        )

    for step_id, nodes in nodes_by_step.items():
        for index in range(1, len(nodes)):
            add_edge(nodes[index - 1], nodes[index], "within-step flow")

    for step_edge in step_edges:
        from_nodes = nodes_by_step.get(step_edge.get("from_step_id")) or []
        to_nodes = nodes_by_step.get(step_edge.get("to_step_id")) or []
        if not from_nodes or not to_nodes:
            continue

        matched = False
        for target_index, target_node in enumerate(to_nodes):
            best_source = None
            best_score = -1.0
            for source_index, source_node in enumerate(from_nodes):
                fallback_bias = 0.25 if source_index == len(from_nodes) - 1 else 0.0
                score = topic_similarity(source_node["label"], target_node["label"]) + fallback_bias
                if score > best_score:
                    best_score = score
                    best_source = source_node
            if best_source and (best_score > 0 or target_index == 0):
                add_edge(best_source, target_node, "cross-step dependency" if best_score > 0 else "step prerequisite")
                matched = True

        if not matched:
            add_edge(from_nodes[-1], to_nodes[0], "step prerequisite")

    return {
        "nodes": topic_nodes,
        "edges": topic_edges,
    }


def render_preview_topic_graph_svg(graph_summary):
    topic_graph = build_preview_topic_graph(graph_summary)
    nodes = topic_graph["nodes"]
    edges = topic_graph["edges"]
    if not nodes:
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="200" viewBox="0 0 960 200">'
            '<text x="40" y="100" fill="#5f7682" font-size="18" font-family="Arial, sans-serif">'
            "No graph nodes available."
            "</text></svg>"
        )

    tier_labels = ["Foundational", "Core build", "Advanced application"]
    tier_columns = {0: [], 1: [], 2: []}
    for node in nodes:
        tier_columns.get(int(node.get("tier", 0)), tier_columns[0]).append(node)

    tier_width = 300
    node_width = 196
    node_height = 60
    row_gap = 34
    margin_x = 44
    margin_y = 42
    header_height = 68
    max_rows = max(len(column) for column in tier_columns.values()) or 1
    width = max(1040, margin_x * 2 + tier_width * 3)
    height = margin_y * 2 + header_height + max_rows * node_height + max(0, max_rows - 1) * row_gap

    positions = {}
    for tier in range(3):
        for row_index, node in enumerate(tier_columns[tier]):
            x = margin_x + (tier * tier_width)
            y = margin_y + header_height + row_index * (node_height + row_gap)
            positions[node["id"]] = {
                "x": x,
                "y": y,
                "center_x": x + node_width / 2,
                "center_y": y + node_height / 2,
            }

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="graphArrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#0f766e"></path>',
        "</marker>",
        "</defs>",
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"></rect>',
    ]

    for tier, label in enumerate(tier_labels):
        x = margin_x + tier * tier_width
        svg.append(
            f'<text x="{x + 14}" y="{margin_y + 18}" fill="#4b5563" font-size="14" font-weight="700" font-family="Arial, sans-serif">{escape_xml(label)}</text>'
        )
        svg.append(
            f'<line x1="{x}" y1="{margin_y + 30}" x2="{x + tier_width - 26}" y2="{margin_y + 30}" stroke="#d5e3fc" stroke-width="2"></line>'
        )

    for edge in edges:
        from_pos = positions.get(edge["from"])
        to_pos = positions.get(edge["to"])
        if not from_pos or not to_pos:
            continue
        start_x = from_pos["x"] + node_width
        start_y = from_pos["center_y"]
        end_x = to_pos["x"]
        end_y = to_pos["center_y"]
        mid_x = start_x + (end_x - start_x) / 2
        stroke = "#94a3b8" if edge["reason"] == "within-step flow" else "#0f766e"
        stroke_width = 2 if edge["reason"] == "within-step flow" else 3
        opacity = "0.72" if edge["reason"] == "within-step flow" else "0.92"
        svg.append(
            f'<path d="M {start_x} {start_y} C {mid_x} {start_y}, {mid_x} {end_y}, {end_x} {end_y}" '
            f'fill="none" stroke="{stroke}" stroke-width="{stroke_width}" stroke-linecap="round" '
            f'marker-end="url(#graphArrow)" opacity="{opacity}"></path>'
        )

    tier_fills = {0: "#e7faf3", 1: "#eef2ff", 2: "#fff4ed"}
    tier_strokes = {0: "#9ad8bd", 1: "#c7d2fe", 2: "#fdba74"}
    for node in nodes:
        pos = positions.get(node["id"])
        if not pos:
            continue
        tier = int(node.get("tier", 0))
        fill = tier_fills.get(tier, "#f7fcfb")
        stroke = tier_strokes.get(tier, "#c7ded8")
        sub_label = f"Step {node.get('step_order') or '?'}"
        svg.append(
            f'<rect x="{pos["x"]}" y="{pos["y"]}" width="{node_width}" height="{node_height}" rx="18" ry="18" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="2"></rect>'
        )
        svg.append(
            f'<text x="{pos["x"] + 14}" y="{pos["y"] + 22}" fill="#6b7280" font-size="11" font-weight="700" font-family="Arial, sans-serif">{escape_xml(sub_label)}</text>'
        )
        svg.append(
            f'<text x="{pos["x"] + 14}" y="{pos["y"] + 42}" fill="#16323b" font-size="14" font-weight="700" font-family="Arial, sans-serif">{escape_xml(node["label"])}</text>'
        )

    svg.append("</svg>")
    return "".join(svg)


def get_quiz_attempt_summary(conn, attempt_id):
    attempt = fetchone_dict(
        conn,
        """
        SELECT
            qa.attempt_id,
            qa.learner_id,
            qa.subject_id,
            qa.path_id,
            qa.step_id,
            qa.quiz_type,
            qa.difficulty_level,
            qa.score,
            qa.total_questions,
            qa.correct_answers,
            qa.completion_status,
            qa.mastery_delta,
            qa.started_at,
            qa.completed_at,
            s.subject_name,
            lps.step_title,
            lps.step_order
        FROM quiz_attempts qa
        JOIN subjects s ON s.subject_id = qa.subject_id
        LEFT JOIN learning_path_steps lps ON lps.step_id = qa.step_id
        WHERE qa.attempt_id = ?
        """,
        (attempt_id,),
    )
    if not attempt:
        return None

    responses = fetchall_dict(
        conn,
        """
        SELECT
            qr.response_id,
            qr.attempt_id,
            qr.question_id,
            COALESCE(qr.question_text, qq.question_text) AS question_text,
            qr.selected_answer,
            qr.correct_answer,
            qr.is_correct,
            qr.time_taken_seconds,
            qr.created_at,
            qr.question_difficulty,
            qr.difficulty_after,
            COALESCE(qr.explanation, qq.explanation) AS explanation,
            COALESCE(qr.options_json, qq.options_json) AS options_json
        FROM quiz_responses qr
        LEFT JOIN quiz_questions qq
            ON qq.question_id = qr.question_id
            AND qq.attempt_id = qr.attempt_id
        WHERE qr.attempt_id = ?
        ORDER BY qr.created_at ASC
        """,
        (attempt_id,),
    )

    normalized_responses = []
    incorrect_questions = []
    for index, response in enumerate(responses, start=1):
        options = parse_json_object(response.get("options_json"))
        selected_answer = str(response.get("selected_answer") or "").strip().upper()
        correct_answer = str(response.get("correct_answer") or "").strip().upper()
        response_payload = {
            "index": index,
            "response_id": response.get("response_id"),
            "question_id": response.get("question_id"),
            "question_text": response.get("question_text") or "Question unavailable",
            "selected_answer": selected_answer,
            "selected_answer_text": resolve_answer_text(selected_answer, options),
            "correct_answer": correct_answer,
            "correct_answer_text": resolve_answer_text(correct_answer, options),
            "is_correct": bool(int(response.get("is_correct") or 0)),
            "time_taken_seconds": response.get("time_taken_seconds"),
            "question_difficulty": response.get("question_difficulty"),
            "difficulty_after": response.get("difficulty_after"),
            "explanation": response.get("explanation") or "",
            "options": options,
        }
        if not response_payload["is_correct"]:
            incorrect_questions.append(index)
        normalized_responses.append(response_payload)

    score_ratio = float(attempt.get("score") or 0)
    total_questions = int(attempt.get("total_questions") or len(normalized_responses) or 0)
    correct_answers = int(attempt.get("correct_answers") or 0)
    if total_questions == 0 and normalized_responses:
        total_questions = len(normalized_responses)
    if correct_answers == 0 and normalized_responses:
        correct_answers = sum(1 for item in normalized_responses if item["is_correct"])

    return {
        "attempt": attempt,
        "summary": {
            "score_ratio": score_ratio,
            "score_percent": round(score_ratio * 100),
            "total_questions": total_questions,
            "correct_answers": correct_answers,
            "incorrect_questions": incorrect_questions,
        },
        "responses": normalized_responses,
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
        if path == "/api/roadmap-graph":
            return self.get_roadmap_graph()
        if path == "/api/roadmap-graph-image":
            return self.get_roadmap_graph_image()
        if path == "/api/quiz-summary":
            return self.get_quiz_summary()
        if path == "/api/quick-study/sessions":
            return self.list_quick_study_sessions_request()
        if path == "/api/quick-study/session":
            return self.get_quick_study_session_request()
        return json_response(self, 404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/quick-study/upload":
            return self.upload_quick_study_documents_request()

        try:
            payload = read_json(self)
        except json.JSONDecodeError:
            return json_response(self, 400, {"error": "Invalid JSON"})

        if path == "/api/onboarding":
            return self.create_learner(payload)
        if path == "/api/topic/ground":
            return self.ground_topic_request(payload)
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
        if path == "/api/quick-study/session":
            return self.create_quick_study_session_request(payload)
        if path == "/api/quick-study/chat":
            return self.quick_study_chat_request(payload)

        return json_response(self, 404, {"error": "Not found"})

    def ground_topic_request(self, payload):
        try:
            result = ground_topic(payload.get("topic"))
        except ValueError as error:
            return json_response(self, 400, {"error": str(error)})
        return json_response(self, 200, {"grounding": result})

    def create_learner(self, payload):
        email = normalize_text_value(payload.get("email")).lower()
        full_name = normalize_text_value(payload.get("full_name"))
        age_group = normalize_text_value(payload.get("age_group")) or None
        role = normalize_text_value(payload.get("role")) or None
        explanation_style = normalize_text_value(payload.get("explanation_style"), "step_by_step")
        feedback_style = normalize_text_value(payload.get("feedback_style"), "immediate")
        accessibility_notes = normalize_text_value(payload.get("accessibility_notes")) or None
        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not full_name:
            full_name = email.split("@", 1)[0] or "Learner"

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
                    SET full_name = ?, age_group = ?, role = ?, updated_at = datetime('now')
                    WHERE learner_id = ?
                    """,
                    (
                        full_name,
                        age_group,
                        role,
                        learner_id,
                    ),
                )
            else:
                learner_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO learners (learner_id, email, full_name, age_group, role)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        learner_id,
                        email,
                        full_name,
                        age_group,
                        role,
                    ),
                )

            conn.execute(
                """
                INSERT INTO learner_preferences (
                    preference_id, learner_id, explanation_style, quiz_style, feedback_style, accessibility_notes
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(learner_id) DO UPDATE SET
                    explanation_style = excluded.explanation_style,
                    quiz_style = excluded.quiz_style,
                    feedback_style = excluded.feedback_style,
                    accessibility_notes = excluded.accessibility_notes,
                    updated_at = datetime('now')
                """,
                (
                    str(uuid.uuid4()),
                    learner_id,
                    explanation_style,
                    normalize_quiz_style_value(payload.get("quiz_style")),
                    feedback_style,
                    accessibility_notes,
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

        except Exception as error:
            if is_provider_rate_limit_error(error):
                return json_response(
                    self,
                    429,
                    {
                        "error": (
                            "The quiz generator hit the Groq rate limit. "
                            "Please wait a minute and try again, or switch to a model/API key with more quota."
                        )
                    },
                )
            return json_response(
                self,
                500,
                {"error": f"Failed to start adaptive quiz: {error}"},
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

    def list_quick_study_sessions_request(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            learner = find_learner_by_email(conn, email)
            if not learner:
                return json_response(self, 404, {"error": "learner not found"})
            sessions = list_quick_study_sessions(conn, learner["learner_id"])

        return json_response(self, 200, {"sessions": sessions})

    def get_quick_study_session_request(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        session_id = (params.get("session_id", [""])[0]).strip()
        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not session_id:
            return json_response(self, 400, {"error": "session_id is required"})

        with get_connection() as conn:
            learner = find_learner_by_email(conn, email)
            if not learner:
                return json_response(self, 404, {"error": "learner not found"})
            detail = load_quick_study_session(conn, learner["learner_id"], session_id)
            if not detail:
                return json_response(self, 404, {"error": "quick study session not found"})
            conn.commit()

        return json_response(self, 200, detail)

    def create_quick_study_session_request(self, payload):
        email = (payload.get("email") or payload.get("learner_email") or "").strip().lower()
        topic = normalize_text_value(payload.get("topic"), "Quick study")
        title = normalize_text_value(payload.get("title")) or None
        subject_id = normalize_text_value(payload.get("subject_id")) or None
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            learner = find_learner_by_email(conn, email)
            if not learner:
                return json_response(self, 404, {"error": "learner not found"})
            session = create_quick_study_session(
                conn,
                learner["learner_id"],
                subject_id,
                topic,
                title,
            )
            conn.commit()

        return json_response(self, 201, {"session": session})

    def upload_quick_study_documents_request(self):
        try:
            fields, files = read_multipart(self)
        except Exception as error:
            return json_response(self, 400, {"error": f"Invalid upload: {error}"})

        email = normalize_text_value(fields.get("email")).lower()
        session_id = normalize_text_value(fields.get("session_id"))
        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not session_id:
            return json_response(self, 400, {"error": "session_id is required"})
        pdfs = [file for file in files if file["filename"].lower().endswith(".pdf")]
        if not pdfs:
            return json_response(self, 400, {"error": "Upload at least one PDF"})

        try:
            with get_connection() as conn:
                learner = find_learner_by_email(conn, email)
                if not learner:
                    return json_response(self, 404, {"error": "learner not found"})
                documents = [
                    index_pdf_document(
                        conn,
                        learner["learner_id"],
                        session_id,
                        file["filename"],
                        file["content"],
                    )
                    for file in pdfs
                ]
                detail = load_quick_study_session(conn, learner["learner_id"], session_id)
                conn.commit()
        except ValueError as error:
            return json_response(self, 400, {"error": str(error)})
        except Exception as error:
            return json_response(self, 500, {"error": f"Failed to index PDF: {error}"})

        return json_response(self, 201, {"documents": documents, "session_detail": detail})

    def quick_study_chat_request(self, payload):
        email = (payload.get("email") or "").strip().lower()
        session_id = normalize_text_value(payload.get("session_id"))
        message = normalize_text_value(payload.get("message"))
        if not email:
            return json_response(self, 400, {"error": "email is required"})
        if not session_id:
            return json_response(self, 400, {"error": "session_id is required"})
        if not message:
            return json_response(self, 400, {"error": "message is required"})

        try:
            with get_connection() as conn:
                learner = find_learner_by_email(conn, email)
                if not learner:
                    return json_response(self, 404, {"error": "learner not found"})
                result = answer_quick_study_question(
                    conn,
                    learner["learner_id"],
                    session_id,
                    message,
                )
                detail = load_quick_study_session(conn, learner["learner_id"], session_id)
                conn.commit()
        except ValueError as error:
            return json_response(self, 400, {"error": str(error)})
        except Exception as error:
            return json_response(self, 500, {"error": f"Quick study chat failed: {error}"})

        return json_response(self, 200, {**result, "session_detail": detail})


    def create_study_request(self, payload):
        raw_topic = (payload.get("raw_topic") or payload.get("topic") or "").strip()
        topic = (payload.get("canonical_topic") or payload.get("topic") or "").strip()
        if not topic:
            return json_response(self, 400, {"error": "topic is required"})

        study_flow = resolve_study_flow(payload.get("study_mode") or "roadmap")
        study_mode = study_flow["study_mode"]
        familiarity = payload.get("familiarity")
        level = familiarity_to_level(familiarity)
        learner_id = (payload.get("learner_id") or "").strip()
        learner_email = (payload.get("learner_email") or "").strip().lower()
        topic_grounding = payload.get("topic_grounding")

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
            assessment_preview = None
            learning_path = None
            quick_study_session = None
            if study_flow["assessment_required"]:
                assessment_preview = build_assessment_preview(
                    topic,
                    level,
                    familiarity,
                    question_count=5,
                    topic_context=topic_grounding,
                )
            elif study_mode in {"quick_study", "quiz"}:
                if study_mode == "quick_study":
                    quick_study_session = create_quick_study_session(
                        conn,
                        learner_id,
                        subject_id,
                        topic,
                        f"Quick study: {topic}",
                    )
                    assessment_preview = {
                        "topic": topic,
                        "level": level,
                        "mode": "quick_study",
                        "context_source": "quick study document chat",
                        "context": f"Upload PDFs to study {topic} with a persistent RAG chat.",
                        "questions": [],
                        "quick_study_session": quick_study_session,
                    }
                else:
                    learning_path = build_learning_path(
                        conn,
                        learner_id,
                        subject_id,
                        topic,
                        None,
                        study_mode="roadmap",
                        topic_context=topic_grounding,
                    )

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
                    "raw_topic": raw_topic,
                    "topic_id": topic_id,
                    "profile_id": profile_id,
                    "level": level,
                },
                "topic_grounding": topic_grounding,
                "assessment_preview": assessment_preview,
                "learning_path": learning_path,
                "quick_study_session": quick_study_session,
            },
        )

    def submit_diagnostic(self, payload):
        email = (payload.get("email") or "").strip().lower()
        topic = (payload.get("topic") or "").strip()
        questions = payload.get("questions") or []
        answers = payload.get("answers") or []
        level = (payload.get("level") or "beginner").strip()
        topic_grounding = payload.get("topic_grounding")

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
                topic_context=topic_grounding,
            )
            conn.commit()

        preview = build_assessment_preview(topic, level, question_count=5, topic_context=topic_grounding)
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
                    p.explanation_style,
                    p.quiz_style,
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

    def get_roadmap_graph(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        selected_subject_id = (params.get("subject_id", [""])[0]).strip() or None
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            summary = get_roadmap_graph_summary(conn, email, selected_subject_id=selected_subject_id)

        if not summary:
            return json_response(self, 404, {"error": "learner not found"})
        if not summary.get("subject", {}).get("subject_id"):
            return json_response(self, 404, {"error": "subject not found"})

        return json_response(self, 200, summary)

    def get_roadmap_graph_image(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        email = (params.get("email", [""])[0]).strip().lower()
        selected_subject_id = (params.get("subject_id", [""])[0]).strip() or None
        if not email:
            return json_response(self, 400, {"error": "email is required"})

        with get_connection() as conn:
            summary = get_roadmap_graph_summary(conn, email, selected_subject_id=selected_subject_id)

        if not summary:
            return json_response(self, 404, {"error": "learner not found"})
        if not summary.get("subject", {}).get("subject_id"):
            return json_response(self, 404, {"error": "subject not found"})

        svg = render_preview_topic_graph_svg(summary)
        return text_response(self, 200, svg, "image/svg+xml; charset=utf-8")

    def get_quiz_summary(self):
        query = urlparse(self.path).query
        params = parse_qs(query)
        attempt_id = (params.get("attempt_id", [""])[0]).strip()
        if not attempt_id:
            return json_response(self, 400, {"error": "attempt_id is required"})

        with get_connection() as conn:
            summary = get_quiz_attempt_summary(conn, attempt_id)

        if not summary:
            return json_response(self, 404, {"error": "quiz attempt not found"})

        return json_response(self, 200, summary)


def main():
    init_db()
    port = int(os.environ.get("PORT", "8001"))
    server = HTTPServer(("0.0.0.0", port), RequestHandler)
    print(f"SQLite API running on http://localhost:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
