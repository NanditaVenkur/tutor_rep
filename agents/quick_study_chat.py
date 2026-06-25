import io
import json
import os
import re
import uuid
from typing import Iterable

import numpy as np
from dotenv import load_dotenv
from pypdf import PdfReader


load_dotenv()

EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
EMBEDDING_MODEL = None
LLM = None


def _normalize_text(value) -> str:
    return " ".join(str(value or "").split())


def _get_embedding_model():
    global EMBEDDING_MODEL
    if EMBEDDING_MODEL is None:
        from sentence_transformers import SentenceTransformer

        EMBEDDING_MODEL = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return EMBEDDING_MODEL


def _get_llm():
    global LLM
    if LLM is None:
        from langchain_groq import ChatGroq

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY is not set")
        LLM = ChatGroq(model=GROQ_MODEL, api_key=api_key, temperature=0.2)
    return LLM


def _embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = _get_embedding_model().encode(texts, normalize_embeddings=True)
    return [vector.astype(float).tolist() for vector in vectors]


def _chunk_page_text(page_text: str, max_words: int = 220, overlap: int = 45) -> list[str]:
    text = _normalize_text(page_text)
    if not text:
        return []
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + max_words, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk) >= 80:
            chunks.append(chunk)
        if end == len(words):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _extract_pdf_pages(file_bytes: bytes) -> list[dict]:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = _normalize_text(page.extract_text() or "")
        if text:
            pages.append({"page_number": index, "text": text})
    return pages


def _parse_json_list(value) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _rows_to_messages(rows: Iterable) -> list[dict]:
    messages = []
    for row in rows:
        item = dict(row)
        item["sources"] = _parse_json_list(item.pop("sources_json", None))
        item["followups"] = _parse_json_list(item.pop("followups_json", None))
        messages.append(item)
    return messages


def create_quick_study_session(conn, learner_id: str, subject_id: str | None, topic: str, title: str | None = None) -> dict:
    topic = _normalize_text(topic) or "Quick study"
    title = _normalize_text(title) or f"Quick study: {topic}"
    session_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO quick_study_sessions (
            session_id, learner_id, subject_id, topic, title, last_accessed_at
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (session_id, learner_id, subject_id, topic, title),
    )
    return {
        "session_id": session_id,
        "learner_id": learner_id,
        "subject_id": subject_id,
        "topic": topic,
        "title": title,
        "document_count": 0,
        "message_count": 0,
    }


def list_quick_study_sessions(conn, learner_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            qss.session_id,
            qss.topic,
            qss.title,
            qss.session_status,
            qss.created_at,
            qss.updated_at,
            qss.last_accessed_at,
            COUNT(DISTINCT qsd.document_id) AS document_count,
            COUNT(DISTINCT qsm.message_id) AS message_count
        FROM quick_study_sessions qss
        LEFT JOIN quick_study_documents qsd ON qsd.session_id = qss.session_id
        LEFT JOIN quick_study_messages qsm ON qsm.session_id = qss.session_id
        WHERE qss.learner_id = ?
        GROUP BY qss.session_id
        ORDER BY COALESCE(qss.last_accessed_at, qss.updated_at, qss.created_at) DESC
        """,
        (learner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def load_quick_study_session(conn, learner_id: str, session_id: str) -> dict | None:
    session = conn.execute(
        """
        SELECT * FROM quick_study_sessions
        WHERE session_id = ? AND learner_id = ?
        """,
        (session_id, learner_id),
    ).fetchone()
    if not session:
        return None
    conn.execute(
        """
        UPDATE quick_study_sessions
        SET last_accessed_at = datetime('now'), updated_at = datetime('now')
        WHERE session_id = ?
        """,
        (session_id,),
    )
    documents = conn.execute(
        """
        SELECT document_id, file_name, file_size, page_count, upload_status, created_at
        FROM quick_study_documents
        WHERE session_id = ?
        ORDER BY created_at DESC
        """,
        (session_id,),
    ).fetchall()
    messages = conn.execute(
        """
        SELECT message_id, role, message_text, sources_json, followups_json, created_at
        FROM quick_study_messages
        WHERE session_id = ?
        ORDER BY created_at ASC
        """,
        (session_id,),
    ).fetchall()
    chunk_count = conn.execute(
        "SELECT COUNT(*) AS count FROM quick_study_chunks WHERE session_id = ?",
        (session_id,),
    ).fetchone()["count"]
    return {
        "session": dict(session),
        "documents": [dict(row) for row in documents],
        "messages": _rows_to_messages(messages),
        "chunk_count": chunk_count,
    }


def index_pdf_document(conn, learner_id: str, session_id: str, file_name: str, file_bytes: bytes) -> dict:
    session = conn.execute(
        "SELECT session_id FROM quick_study_sessions WHERE session_id = ? AND learner_id = ?",
        (session_id, learner_id),
    ).fetchone()
    if not session:
        raise ValueError("quick study session not found")

    pages = _extract_pdf_pages(file_bytes)
    document_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO quick_study_documents (
            document_id, session_id, file_name, file_size, page_count
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (document_id, session_id, file_name, len(file_bytes), len(pages)),
    )

    chunk_items = []
    for page in pages:
        for chunk in _chunk_page_text(page["text"]):
            chunk_items.append({"page_number": page["page_number"], "text": chunk})

    if chunk_items:
        embeddings = _embed_texts([item["text"] for item in chunk_items])
        for index, (item, embedding) in enumerate(zip(chunk_items, embeddings), start=1):
            conn.execute(
                """
                INSERT INTO quick_study_chunks (
                    chunk_id, session_id, document_id, chunk_order, page_number,
                    chunk_text, embedding_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    document_id,
                    index,
                    item["page_number"],
                    item["text"],
                    json.dumps(embedding),
                ),
            )

    conn.execute(
        """
        UPDATE quick_study_sessions
        SET updated_at = datetime('now'), last_accessed_at = datetime('now')
        WHERE session_id = ?
        """,
        (session_id,),
    )
    return {
        "document_id": document_id,
        "file_name": file_name,
        "file_size": len(file_bytes),
        "page_count": len(pages),
        "chunk_count": len(chunk_items),
    }


def retrieve_session_chunks(conn, session_id: str, query: str, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            qsc.chunk_id,
            qsc.document_id,
            qsd.file_name,
            qsc.page_number,
            qsc.chunk_text,
            qsc.embedding_json
        FROM quick_study_chunks qsc
        JOIN quick_study_documents qsd ON qsd.document_id = qsc.document_id
        WHERE qsc.session_id = ?
        """,
        (session_id,),
    ).fetchall()
    if not rows:
        return []

    query_vector = np.array(_embed_texts([query])[0], dtype=float)
    ranked = []
    for row in rows:
        embedding = np.array(json.loads(row["embedding_json"]), dtype=float)
        score = float(np.dot(query_vector, embedding))
        ranked.append({**dict(row), "score": score})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    for item in ranked:
        item.pop("embedding_json", None)
    return ranked[:limit]


def _extract_json_object(text: str) -> dict:
    text = str(text or "").strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}


def answer_quick_study_question(conn, learner_id: str, session_id: str, question: str) -> dict:
    question = _normalize_text(question)
    if not question:
        raise ValueError("message is required")

    session = conn.execute(
        "SELECT * FROM quick_study_sessions WHERE session_id = ? AND learner_id = ?",
        (session_id, learner_id),
    ).fetchone()
    if not session:
        raise ValueError("quick study session not found")

    chunks = retrieve_session_chunks(conn, session_id, question, limit=5)
    if not chunks:
        answer = "Upload one or more PDFs first so I can answer from your study material."
        followups = ["Upload a PDF", "What topic should this session focus on?", "Create a study checklist"]
        sources = []
    else:
        context = "\n\n".join(
            f"[Source {index}] {chunk['file_name']} page {chunk.get('page_number') or '?'}\n{chunk['chunk_text']}"
            for index, chunk in enumerate(chunks, start=1)
        )
        prompt = f"""You are a study assistant answering only from uploaded PDF excerpts.
Session topic: {session['topic']}
Learner question: {question}

Retrieved PDF context:
{context[:9000]}

Return valid JSON only with this shape:
{{"answer":"clear answer grounded in the excerpts","followups":["question","question","question"]}}

Rules:
- Answer from the retrieved excerpts. If the excerpts are insufficient, say what is missing.
- Keep the answer practical and learner-facing.
- Mention source numbers naturally, like "Source 1".
- Generate exactly 3 short follow-up questions the learner could ask next.
"""
        try:
            response = _get_llm().invoke(prompt)
            payload = _extract_json_object(getattr(response, "content", response))
            answer = _normalize_text(payload.get("answer") or "")
            followups = payload.get("followups") if isinstance(payload.get("followups"), list) else []
        except Exception as error:
            answer = (
                "I found relevant PDF excerpts, but the LLM answer step failed. "
                f"Try again after checking the Groq API key. Error: {error}"
            )
            followups = []
        if not answer:
            answer = "I found relevant PDF excerpts, but could not generate a grounded answer from them."
        followups = [_normalize_text(item) for item in followups if _normalize_text(item)][:3]
        if len(followups) < 3:
            followups.extend(
                [
                    "Can you quiz me on this?",
                    "What are the key terms here?",
                    "Can you explain this with an example?",
                ][: 3 - len(followups)]
            )
        sources = [
            {
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk["document_id"],
                "file_name": chunk["file_name"],
                "page_number": chunk.get("page_number"),
                "score": round(chunk["score"], 4),
                "snippet": chunk["chunk_text"][:420],
            }
            for chunk in chunks
        ]

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO quick_study_messages (message_id, session_id, role, message_text)
        VALUES (?, ?, 'user', ?)
        """,
        (user_message_id, session_id, question),
    )
    conn.execute(
        """
        INSERT INTO quick_study_messages (
            message_id, session_id, role, message_text, sources_json, followups_json
        )
        VALUES (?, ?, 'assistant', ?, ?, ?)
        """,
        (assistant_message_id, session_id, answer, json.dumps(sources), json.dumps(followups)),
    )
    conn.execute(
        """
        UPDATE quick_study_sessions
        SET updated_at = datetime('now'), last_accessed_at = datetime('now')
        WHERE session_id = ?
        """,
        (session_id,),
    )
    return {
        "user_message": {"message_id": user_message_id, "role": "user", "message_text": question},
        "assistant_message": {
            "message_id": assistant_message_id,
            "role": "assistant",
            "message_text": answer,
            "sources": sources,
            "followups": followups,
        },
    }
