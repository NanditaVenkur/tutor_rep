import os
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from dotenv import load_dotenv
import pandas as pd
from sentence_transformers import SentenceTransformer
import chromadb

load_dotenv()

# -----------------------------
# SCIQ DATASET LOADING
# -----------------------------

data_folder = Path("./sciq/data")
parquet_files = list(data_folder.glob("*.parquet"))

print(f"Found {len(parquet_files)} parquet files")

# Load all datasets
all_data = []
for parquet_path in parquet_files:
    print(f"Loading: {parquet_path.name}")
    df = pd.read_parquet(str(parquet_path))
    all_data.append((parquet_path.stem, df))

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

# Create combined text from each row
def create_document(row):
    """Combine question, support text, and answers into a single document"""
    doc = f"Question: {row['question']}\n\n"
    doc += f"Support: {row['support']}\n\n"
    doc += f"Correct Answer: {row['correct_answer']}\n"
    doc += f"Alternative Answers: {row['distractor1']}, {row['distractor2']}, {row['distractor3']}"
    return doc

# -----------------------------
# EMBEDDING MODEL
# -----------------------------

model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True)

# -----------------------------
# PERSISTENT CHROMADB
# -----------------------------

client = chromadb.PersistentClient(path="./chroma_db")

collection = client.get_or_create_collection(
    name="sciq_knowledge_base"
)

# -----------------------------
# PROCESS EACH DATASET
# -----------------------------

total_added = 0

for dataset_name, df in all_data:
    print(f"\nProcessing dataset: {dataset_name}")
    
    # Limit to first 3000 rows
    df_limited = df.head(3000)
    
    for idx, row in df_limited.iterrows():
        # Create combined document
        document = create_document(row)
        
        # Chunk the document
        chunks = chunk_text(document)
        
        # Generate embeddings for all chunks
        embeddings = model.encode(chunks)
        
        # Store in ChromaDB
        for chunk_idx, chunk in enumerate(chunks):
            doc_id = f"sciq_{dataset_name}_{idx}_{chunk_idx}"
            
            collection.add(
                ids=[doc_id],
                documents=[chunk],
                embeddings=[embeddings[chunk_idx].tolist()],
                metadatas=[{
                    "source": f"sciq_{dataset_name}",
                    "row_id": idx,
                    "chunk_id": chunk_idx,
                    "question": row['question'],
                    "correct_answer": row['correct_answer']
                }]
            )
            
            total_added += 1
        
        if (idx + 1) % 100 == 0:
            print(f"  Processed {idx + 1} rows...")
    
    print(f"  Completed {len(df_limited)} rows from {dataset_name}")

print(f"\nAll datasets processed successfully!")
print(f"Total documents added to ChromaDB: {total_added}")
