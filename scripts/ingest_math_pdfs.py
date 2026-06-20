import argparse
import hashlib
import os
import sqlite3
import uuid
from pathlib import Path
import re

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from dotenv import load_dotenv
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import chromadb


load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF_DIR = PROJECT_ROOT / "data" / "math_pdfs"
DEFAULT_CHROMA_PATH = PROJECT_ROOT / "data" / "chroma_db"
DEFAULT_DB_PATH = PROJECT_ROOT / "backend" / "adaptive_tutor_v2.db"
DEFAULT_COLLECTION = "math_textbooks"
DEFAULT_SUBJECT_NAME = "Mathematics"
DEFAULT_CHUNKING_STRATEGY = "conceptual"
DEFAULT_MAX_CHARS = 1200
DEFAULT_OVERLAP_UNITS = 1


def get_connection(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def split_sentences(text: str) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [sentence.strip() for sentence in sentences if sentence.strip()]


def chunk_text(text: str, chunk_size: int = DEFAULT_MAX_CHARS, overlap: int = 200) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    step = max(chunk_size - overlap, 200)
    while start < len(text):
        chunk = text[start : start + chunk_size].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


HEADING_PREFIXES = (
    "chapter",
    "section",
    "unit",
    "lesson",
    "example",
    "exercise",
    "definition",
    "theorem",
    "lemma",
    "corollary",
    "proposition",
    "review",
    "summary",
)


def is_heading_line(line: str) -> bool:
    cleaned = normalize_text(line)
    if not cleaned:
        return False
    if len(cleaned) > 110:
        return False
    lower = cleaned.lower()
    if any(lower.startswith(prefix) for prefix in HEADING_PREFIXES):
        return True
    if re.match(r"^\d+(\.\d+)*\s+[A-Za-z]", cleaned):
        return True
    if cleaned.isupper() and len(cleaned) >= 4:
        return True
    if len(cleaned.split()) <= 10 and cleaned[0].isalpha() and cleaned[0].isupper() and not re.search(r"[.!?]$", cleaned):
        return True
    return False


def split_page_units(page_text: str) -> list[dict]:
    lines = [normalize_text(line) for line in page_text.splitlines()]
    units: list[dict] = []
    paragraph: list[str] = []

    def flush_paragraph():
        nonlocal paragraph
        if not paragraph:
            return
        paragraph_text = " ".join(paragraph).strip()
        if paragraph_text:
            units.append({"unit_type": "paragraph", "text": paragraph_text})
        paragraph = []

    for line in lines:
        if not line:
            flush_paragraph()
            continue
        if is_heading_line(line):
            flush_paragraph()
            units.append({"unit_type": "heading", "text": line})
            continue
        paragraph.append(line)

    flush_paragraph()
    return units


def split_long_text(text: str, max_chars: int) -> list[str]:
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    sentences = split_sentences(cleaned)
    if len(sentences) <= 1:
        return [cleaned[i : i + max_chars].strip() for i in range(0, len(cleaned), max_chars) if cleaned[i : i + max_chars].strip()]

    parts: list[str] = []
    buffer = ""
    for sentence in sentences:
        if not buffer:
            buffer = sentence
            continue
        candidate = f"{buffer} {sentence}".strip()
        if len(candidate) <= max_chars:
            buffer = candidate
        else:
            parts.append(buffer)
            buffer = sentence
    if buffer:
        parts.append(buffer)
    return parts


def pack_conceptual_chunks(
    pdf_path: Path,
    page_number: int,
    units: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_units: int = DEFAULT_OVERLAP_UNITS,
) -> list[dict]:
    if not units:
        return []

    chunks: list[dict] = []
    current_units: list[dict] = []
    current_len = 0

    def render_chunk(units_to_render: list[dict]) -> dict | None:
        if not units_to_render:
            return None
        heading = ""
        paragraph_texts: list[str] = []
        for unit in units_to_render:
            if unit["unit_type"] == "heading":
                heading = unit["text"]
                paragraph_texts.append(unit["text"])
            else:
                paragraph_texts.append(unit["text"])
        body = "\n\n".join(paragraph_texts).strip()
        if not body:
            return None
        header = f"Source: {pdf_path.name} | Page: {page_number + 1}"
        rendered_parts = [header]
        if heading:
            rendered_parts.append(f"Section: {heading}")
        rendered_parts.append(body)
        return {
            "chunk_text": "\n\n".join(rendered_parts).strip(),
            "section_title": heading or None,
            "unit_count": len(units_to_render),
        }

    def emit_current():
        chunk = render_chunk(current_units)
        if chunk:
            chunks.append(chunk)

    for unit in units:
        unit_text = unit["text"]
        if unit["unit_type"] == "paragraph" and len(unit_text) > max_chars:
            emit_current()
            current_units = current_units[-overlap_units:] if overlap_units > 0 else []
            current_len = sum(len(item["text"]) + 2 for item in current_units)
            for fragment in split_long_text(unit_text, max_chars):
                chunk = render_chunk([{"unit_type": "paragraph", "text": fragment}])
                if chunk:
                    chunks.append(chunk)
            continue

        if current_units and current_len + len(unit_text) + 2 > max_chars:
            emit_current()
            current_units = current_units[-overlap_units:] if overlap_units > 0 else []
            current_len = sum(len(item["text"]) + 2 for item in current_units)

        current_units.append(unit)
        current_len += len(unit_text) + 2

    emit_current()
    return chunks


def page_to_text(pdf_path: Path, page_number: int, page_text: str) -> str:
    cleaned = normalize_text(page_text)
    header = f"Source: {pdf_path.name} | Page: {page_number + 1}"
    return f"{header}\n\n{cleaned}" if cleaned else ""


def hash_text(*parts) -> str:
    payload = "\n".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ensure_subject(conn, subject_name: str) -> str:
    row = conn.execute(
        "SELECT subject_id FROM subjects WHERE subject_name = ?",
        (subject_name,),
    ).fetchone()
    if row:
        return row["subject_id"]
    subject_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO subjects (subject_id, subject_name, description) VALUES (?, ?, ?)",
        (subject_id, subject_name, f"Textbook collection for {subject_name}"),
    )
    return subject_id


def ensure_topic(conn, subject_id: str, topic_name: str, description: str | None = None) -> str:
    row = conn.execute(
        """
        SELECT topic_id
        FROM topics
        WHERE subject_id = ? AND topic_name = ?
        """,
        (subject_id, topic_name),
    ).fetchone()
    if row:
        return row["topic_id"]
    topic_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO topics (
            topic_id, subject_id, topic_name, topic_description, topic_order, estimated_minutes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (topic_id, subject_id, topic_name, description, 0, 15),
    )
    return topic_id


def upsert_resource(conn, subject_id: str, topic_id: str, pdf_path: Path, source_name: str) -> str:
    doc_hash = hash_text(pdf_path.name, source_name)
    existing = conn.execute(
        """
        SELECT resource_id
        FROM content_resources
        WHERE subject_id = ? AND source_uri = ? AND source_name = ?
        """,
        (subject_id, str(pdf_path), source_name),
    ).fetchone()

    if existing:
        resource_id = existing["resource_id"]
        conn.execute(
            """
            UPDATE content_resources
            SET title = ?, content_hash = ?, source_revision = ?, is_active = 1, updated_at = datetime('now')
            WHERE resource_id = ?
            """,
            (pdf_path.stem, doc_hash, pdf_path.stat().st_mtime, resource_id),
        )
        conn.execute(
            "DELETE FROM content_chunks WHERE resource_id = ?",
            (resource_id,),
        )
        return resource_id

    resource_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO content_resources (
            resource_id, subject_id, topic_id, resource_type, title, source_name,
            source_uri, vector_collection, vector_doc_id, content_version,
            content_hash, source_revision, is_active, embedding_ref
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            resource_id,
            subject_id,
            topic_id,
            "textbook",
            pdf_path.stem,
            source_name,
            str(pdf_path),
            DEFAULT_COLLECTION,
            None,
            1,
            doc_hash,
            str(pdf_path.stat().st_mtime),
            None,
        ),
    )
    return resource_id


def extract_pdf_chunks(
    pdf_path: Path,
    chunking_strategy: str = DEFAULT_CHUNKING_STRATEGY,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_units: int = DEFAULT_OVERLAP_UNITS,
) -> list[dict]:
    reader = PdfReader(str(pdf_path))
    extracted = []
    for page_index, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if not normalize_text(page_text):
            continue
        if chunking_strategy == "fixed":
            content = page_to_text(pdf_path, page_index, page_text)
            if not content:
                continue
            for chunk_index, chunk in enumerate(chunk_text(content, chunk_size=max_chars, overlap=min(200, max_chars // 6))):
                extracted.append(
                    {
                        "page_number": page_index + 1,
                        "chunk_index": chunk_index,
                        "chunk_text": chunk,
                        "section_title": None,
                        "unit_count": 1,
                        "chunk_hash": hash_text(pdf_path.name, page_index + 1, chunk_index, chunk),
                    }
                )
            continue

        units = split_page_units(page_text)
        if not units:
            continue
        for chunk_index, item in enumerate(pack_conceptual_chunks(pdf_path, page_index, units, max_chars=max_chars, overlap_units=overlap_units)):
            item["page_number"] = page_index + 1
            item["chunk_index"] = chunk_index
            item["chunk_hash"] = hash_text(
                pdf_path.name,
                page_index + 1,
                chunk_index,
                item["chunk_text"],
                item.get("section_title"),
            )
            extracted.append(item)
    return extracted


def ingest_pdf_folder(
    pdf_dir: Path,
    db_path: Path,
    chroma_path: Path,
    collection_name: str,
    subject_name: str,
    chunking_strategy: str = DEFAULT_CHUNKING_STRATEGY,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_units: int = DEFAULT_OVERLAP_UNITS,
):
    pdf_files = sorted([path for path in pdf_dir.glob("*.pdf") if path.is_file()])
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in {pdf_dir}")

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection = client.get_or_create_collection(name=collection_name)

    with get_connection(db_path) as conn:
        subject_id = ensure_subject(conn, subject_name)
        total_chunks = 0

        for pdf_path in pdf_files:
            print(f"Ingesting {pdf_path.name}")
            topic_id = ensure_topic(conn, subject_id, pdf_path.stem, description=f"Textbook content from {pdf_path.name}")
            resource_id = upsert_resource(conn, subject_id, topic_id, pdf_path, source_name=pdf_path.name)

            try:
                collection.delete(where={"resource_id": resource_id})
            except Exception:
                pass

            chunks = extract_pdf_chunks(
                pdf_path,
                chunking_strategy=chunking_strategy,
                max_chars=max_chars,
                overlap_units=overlap_units,
            )
            if not chunks:
                print(f"  No extractable text found in {pdf_path.name}")
                continue

            embeddings = model.encode([item["chunk_text"] for item in chunks], normalize_embeddings=True)

            for idx, item in enumerate(chunks, start=1):
                chunk_id = str(uuid.uuid4())
                vector_chunk_id = f"{pdf_path.stem}_{item['page_number']}_{item['chunk_index']}"
                chunk_version = 1
                conn.execute(
                    """
                    INSERT INTO content_chunks (
                        chunk_id, resource_id, chunk_order, chunk_text, vector_chunk_id,
                        chunk_hash, chunk_version, embedding_ref
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk_id,
                        resource_id,
                        idx,
                        item["chunk_text"],
                        vector_chunk_id,
                        item["chunk_hash"],
                        chunk_version,
                        vector_chunk_id,
                    ),
                )
                collection.add(
                    ids=[vector_chunk_id],
                    documents=[item["chunk_text"]],
                    embeddings=[embeddings[idx - 1].tolist()],
                    metadatas=[{
                        "subject_name": subject_name,
                        "subject_id": subject_id,
                        "topic_id": topic_id,
                        "resource_id": resource_id,
                        "chunk_id": chunk_id,
                        "chunk_order": idx,
                        "page_number": item["page_number"],
                        "source_file": pdf_path.name,
                        "source_path": str(pdf_path),
                        "content_hash": item["chunk_hash"],
                        "source_type": "textbook_pdf",
                        "section_title": item.get("section_title"),
                        "unit_count": item.get("unit_count"),
                    }],
                )
                total_chunks += 1

        conn.commit()

    print(f"Done. Added {total_chunks} chunks to Chroma collection '{collection_name}'.")


def main():
    parser = argparse.ArgumentParser(description="Ingest math textbook PDFs into ChromaDB and SQLite metadata.")
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chroma-path", type=Path, default=DEFAULT_CHROMA_PATH)
    parser.add_argument("--collection", type=str, default=DEFAULT_COLLECTION)
    parser.add_argument("--subject", type=str, default=DEFAULT_SUBJECT_NAME)
    parser.add_argument("--chunking-strategy", type=str, default=DEFAULT_CHUNKING_STRATEGY, choices=["conceptual", "fixed"])
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS)
    parser.add_argument("--overlap-units", type=int, default=DEFAULT_OVERLAP_UNITS)
    args = parser.parse_args()

    ingest_pdf_folder(
        pdf_dir=args.pdf_dir,
        db_path=args.db_path,
        chroma_path=args.chroma_path,
        collection_name=args.collection,
        subject_name=args.subject,
        chunking_strategy=args.chunking_strategy,
        max_chars=args.max_chars,
        overlap_units=args.overlap_units,
    )


if __name__ == "__main__":
    main()
