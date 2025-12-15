import json
import requests
import sys
import numpy as np
import re
import os
from dotenv import load_dotenv

# --- 1. Load Environment Variables ---
load_dotenv()

# --- 2. Import Libraries ---
try:
    import chromadb # The Vector Database
    from sentence_transformers import SentenceTransformer
except ImportError:
    print("Error: Required libraries not found.")
    print("Please install them by running: pip install -r requirements.txt")
    sys.exit(1)

# --- 3. Define Paths ---
LOG_FILE = "diffs.log"
CHROMA_PATH = "./chroma_db" # Folder where the DB will live

# --- 4. Initialize Vector Database ---
print(f"-> Connecting to ChromaDB at '{CHROMA_PATH}'...")
chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

# Create or get a collection. Think of this as a "table" for your vectors.
# We call it "git_commits".
collection = chroma_client.get_or_create_collection(name="git_commits")


# --- 5. Parsing Function ---
# Robust regex from our previous fix
GIT_LOG_PATTERN = re.compile(
    r"COMMIT: (.*?)\n"
    r"AUTHOR: (.*?)\n"
    r"DATE:\s+(.*?)\n"
    r"MESSAGE: (.*?)"
    r"\n-{10,}\n+"
    r"(.*)",
    re.DOTALL
)

def parse_log_file(file_path):
    print(f"-> Parsing '{file_path}'...")
    ids = []
    documents = []
    metadatas = []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            full_log_content = f.read()
        
        commit_blocks = full_log_content.split("======================================================================")
        
        for block in commit_blocks:
            if not block.strip(): continue
            
            match = GIT_LOG_PATTERN.search(block)
            if match:
                commit, author, date, message, diff_data = match.groups()
                
                # The searchable text
                text = (
                    f"Commit: {commit.strip()}\n"
                    f"Author: {author.strip()}\n"
                    f"Date: {date.strip()}\n"
                    f"Message: {message.strip()}\n\n"
                    f"Changes:\n{diff_data.strip()}"
                )
                
                ids.append(commit.strip()) # Use Hash as Unique ID
                documents.append(text)
                metadatas.append({
                    "commit": commit.strip(),
                    "author": author.strip(),
                    "date": date.strip(),
                    "message": message.strip()
                })
        return ids, documents, metadatas
        
    except FileNotFoundError:
        print(f"Error: Could not find '{file_path}'")
        sys.exit(1)

# --- 6. Indexing (Populate the DB) ---
# Check if DB is already populated
if collection.count() == 0:
    print("-> Collection is empty. Indexing data...")
    
    # 1. Parse Data
    ids, documents, metadatas = parse_log_file(LOG_FILE)
    if not ids:
        print("Fatal Error: No commits found to index.")
        sys.exit(1)

    # 2. Load Embedding Model
    print("-> Loading embedding model...")
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # 3. Generate Embeddings
    print(f"-> Creating embeddings for {len(documents)} commits...")
    embeddings = model.encode(documents).tolist() # Chroma expects simple lists, not numpy/torch tensors
    
    # 4. Add to ChromaDB
    # Chroma handles the storage and indexing automatically
    print("-> Saving to Vector Database...")
    # Add in batches to avoid hitting limits
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        end = min(i + batch_size, len(ids))
        collection.add(
            ids=ids[i:end],
            documents=documents[i:end],
            embeddings=embeddings[i:end],
            metadatas=metadatas[i:end]
        )
    print("-> Indexing complete!")
else:
    print(f"-> Loaded {collection.count()} existing commits from Vector DB.")
    # We still need the model for querying
    print("-> Loading embedding model...")
    model = SentenceTransformer('all-MiniLM-L6-v2')


# --- 7. Retrieval System (Using Chroma) ---
def retrieve_context(query, top_k=3):
    print(f"-> Embedding query: '{query}'")
    query_embedding = model.encode([query]).tolist()
    
    # Query the DB
    results = collection.query(
        query_embeddings=query_embedding,
        n_results=top_k
    )
    
    # Chroma returns lists of lists (because you can provide multiple query embeddings)
    # We flatten this to make it easier to work with
    processed_results = []
    for i in range(len(results['ids'][0])):
        processed_results.append({
            "score": results['distances'][0][i], # Chroma returns distance (lower is better)
            "data": results['metadatas'][0][i]
        })
        
    return processed_results

# --- 8. Generation System ---
apiKey = os.getenv("GEMINI_API_KEY")
if not apiKey:
    print("WARNING: GEMINI_API_KEY not found in .env file.")
    sys.exit(1)

apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={apiKey}"

def generate_answer(query, context_list):
    context_str = "Here are the most relevant commits found:\n\n"
    for i, item in enumerate(context_list):
        context_str += f"Commit {i+1}:\n"
        context_str += f"Date: {item['data']['date']}\n"
        context_str += f"Author: {item['data']['author']}\n"
        context_str += f"Message: {item['data']['message']}\n\n"

    prompt = f"""
    You are a software engineering assistant analyzing a git log.
    Based ONLY on the provided context, answer the user's question.

    Context:
    {context_str}

    Question:
    "{query}"

    Answer:
    """
    
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    headers = {'Content-Type': 'application/json'}
    
    try:
        response = requests.post(apiUrl, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        return response.json()['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception as e:
        return f"Error generating answer: {e}"

# --- 9. Main Loop ---
def main():
    print("\n--- RAG with ChromaDB ---")
    print("Type 'quit' or 'exit' to stop.")
    
    while True:
        try:
            query = input("\nYour question: ")
            if query.lower() in ['quit', 'exit']:
                break
            if not query:
                continue

            contexts = retrieve_context(query)
            if contexts:
                print(f"-> Found {len(contexts)} relevant commits.")
                for i, item in enumerate(contexts):
                    msg_head = item['data']['message'].split('\n')[0][:60]
                    print(f"  {i+1}. [{item['data']['date'][:10]}] {msg_head}...")
            
            print("-> Generating answer...")
            answer = generate_answer(query, contexts)
            print("\n" + "="*20 + " Answer " + "="*20)
            print(answer)
            print("="*48)

        except (EOFError, KeyboardInterrupt):
            break

if __name__ == "__main__":
    main()