# from dotenv import load_dotenv
# from pypdf import PdfReader
# from sentence_transformers import SentenceTransformer
# import chromadb

# load_dotenv()
# # -----------------------------
# # LOAD PDF
# # -----------------------------

# reader = PdfReader(
#     "./datasources/leac203.pdf"
# )

# text = ""

# for page in reader.pages:
#     extracted = page.extract_text()

#     if extracted:
#         text += extracted

# print(f"Extracted text length: {len(text)} characters")

# # -----------------------------
# # CHUNKING
# # -----------------------------

# # def chunk_text(text, chunk_size=1000):
# #     chunks = []

# #     for i in range(0, len(text), chunk_size):
# #         chunks.append(text[i:i+chunk_size])

# #     return chunks

# def chunk_text(text, chunk_size=1000, overlap=200):

#     chunks = []

#     start = 0

#     while start < len(text):

#         end = start + chunk_size

#         chunk = text[start:end]

#         chunks.append(chunk)

#         start += chunk_size - overlap

#     return chunks

# chunks = chunk_text(text)

# print(f"Total chunks created: {len(chunks)}")

# # -----------------------------
# # EMBEDDING MODEL
# # -----------------------------

# model = SentenceTransformer(
#     "all-MiniLM-L6-v2"
# )

# print("Generating embeddings...")

# embeddings = model.encode(chunks)

# # -----------------------------
# # PERSISTENT CHROMADB
# # -----------------------------

# client = chromadb.PersistentClient(
#     path="./chroma_db"
# )

# collection = client.get_or_create_collection(
#     name="knowledge_base"
# )

# print("Adding chunks to ChromaDB...")

# for i, chunk in enumerate(chunks):

#     collection.add(
#         ids=[f"finance_chunk_{i}"],
#         documents=[chunk],
#         embeddings=[embeddings[i].tolist()],
#         metadatas=[{
#             "source": "leac203.pdf",
#             "chunk_id": i
#         }]
#     )

# print("Embeddings stored successfully.")

from dotenv import load_dotenv
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
import chromadb
from pathlib import Path

load_dotenv()

# -----------------------------
# PDF FOLDER
# -----------------------------

pdf_folder = Path("./datasources")

pdf_files = list(pdf_folder.glob("*.pdf"))

print(f"Found {len(pdf_files)} PDF files")

# -----------------------------
# CHUNKING FUNCTION
# -----------------------------

def chunk_text(text, chunk_size=1000, overlap=200):

    chunks = []

    start = 0

    while start < len(text):

        end = start + chunk_size

        chunk = text[start:end]

        chunks.append(chunk)

        start += chunk_size - overlap

    return chunks

# -----------------------------
# EMBEDDING MODEL
# -----------------------------

model = SentenceTransformer(
    "all-MiniLM-L6-v2"
)

# -----------------------------
# PERSISTENT CHROMADB
# -----------------------------

client = chromadb.PersistentClient(
    path="./chroma_db"
)

collection = client.get_or_create_collection(
    name="knowledge_base"
)

# -----------------------------
# PROCESS EACH PDF
# -----------------------------

for pdf_path in pdf_files:

    print(f"\nProcessing: {pdf_path.name}")

    reader = PdfReader(str(pdf_path))

    text = ""

    # Extract text
    for page in reader.pages:

        extracted = page.extract_text()

        if extracted:
            text += extracted

    print(f"Extracted text length: {len(text)} characters")

    # Chunking
    chunks = chunk_text(text)

    print(f"Total chunks created: {len(chunks)}")

    # Generate embeddings
    print("Generating embeddings...")

    embeddings = model.encode(chunks)

    # Store in ChromaDB
    print("Adding chunks to ChromaDB...")

    for i, chunk in enumerate(chunks):

        collection.add(
            ids=[f"{pdf_path.stem}_chunk_{i}"],
            documents=[chunk],
            embeddings=[embeddings[i].tolist()],
            metadatas=[{
                "source": pdf_path.name,
                "chunk_id": i
            }]
        )

    print(f"{pdf_path.name} stored successfully.")

print("\nAll PDFs processed successfully.")

