import argparse
import hashlib
import os
import sqlite3
import uuid
from pathlib import Path

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


def get_connection(db_path: Path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
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


def extract_pdf_chunks(pdf_path: Path) -> list[dict]:
    reader = PdfReader(str(pdf_path))
    extracted = []
    for page_index, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if not normalize_text(page_text):
            continue
        content = page_to_text(pdf_path, page_index, page_text)
        if not content:
            continue
        for chunk_index, chunk in enumerate(chunk_text(content)):
            extracted.append(
                {
                    "page_number": page_index + 1,
                    "chunk_index": chunk_index,
                    "chunk_text": chunk,
                    "chunk_hash": hash_text(pdf_path.name, page_index + 1, chunk_index, chunk),
                }
            )
    return extracted


def ingest_pdf_folder(pdf_dir: Path, db_path: Path, chroma_path: Path, collection_name: str, subject_name: str):
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

            chunks = extract_pdf_chunks(pdf_path)
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
    args = parser.parse_args()

    ingest_pdf_folder(
        pdf_dir=args.pdf_dir,
        db_path=args.db_path,
        chroma_path=args.chroma_path,
        collection_name=args.collection,
        subject_name=args.subject,
    )


if __name__ == "__main__":
    main()
