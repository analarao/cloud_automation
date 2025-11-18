import json
import requests
import sys
import numpy as np
import re
import os
import pickle
from dotenv import load_dotenv

# --- 1. Load Environment Variables ---
load_dotenv()

# --- 2. Import ML Libraries ---
try:
    from sentence_transformers import SentenceTransformer, util
    import torch
except ImportError:
    print("Error: Required libraries not found.")
    print("Please install them by running: pip install -r requirements.txt")
    sys.exit(1)

# --- 3. Define File Paths ---
LOG_FILE = "diffs.log"
EMBEDDING_CACHE = "embeddings.pkl"

# --- 4. Parse the Git Log File ---
#
# --- REGEX FIX EXPLAINED ---
# We use '\s+?' (non-greedy whitespace) between MESSAGE and the separator lines.
# This handles cases where there is 1 newline, 2 newlines, or weird spacing.
#
GIT_LOG_PATTERN = re.compile(
    r"COMMIT: (.*?)\n"           # Group 1: Commit Hash
    r"AUTHOR: (.*?)\n"          # Group 2: Author
    r"DATE:\s+(.*?)\n"          # Group 3: Date
    r"MESSAGE: (.*?)"           # Group 4: Message content
    r"\n-{10,}\n+"              # Separator: Newline, 10+ dashes, newlines
    r"(.*)",                    # Group 5: The rest (Diff data)
    re.DOTALL                   # DOTALL allows (.) to match newlines inside the message/diff
)

corpus = []
metadata = []

print(f"-> Loading and parsing '{LOG_FILE}'...")
try:
    with open(LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        full_log_content = f.read()
    
    # Split by the main separator
    commit_blocks = full_log_content.split("======================================================================")
    
    for block in commit_blocks:
        if not block.strip():
            continue
            
        match = GIT_LOG_PATTERN.search(block)
        if match:
            commit, author, date, message, diff_data = match.groups()
            
            commit = commit.strip()
            author = author.strip()
            date = date.strip()
            message = message.strip()
            diff_data = diff_data.strip()
            
            # Create the comprehensive document for searching
            searchable_text = (
                f"Commit: {commit}\n"
                f"Author: {author}\n"
                f"Date: {date}\n"
                f"Message: {message}\n\n"
                f"Changes:\n{diff_data}"
            )
            corpus.append(searchable_text)
            
            metadata.append({
                "commit": commit,
                "author": author,
                "date": date,
                "message": message,
            })
    
    if not corpus:
        print(f"Fatal Error: Could not parse any commit blocks from '{LOG_FILE}'.")
        print("Please check the regex pattern.")
        sys.exit(1)
        
    print(f"-> Successfully parsed {len(corpus)} commits.")

except FileNotFoundError:
    print(f"Error: Could not find '{LOG_FILE}'.")
    sys.exit(1)
except Exception as e:
    print(f"Error parsing log file: {e}")
    sys.exit(1)


# --- 5. Load Embedding Model ---
print("-> Loading embedding model (all-MiniLM-L6-v2)...")
try:
    embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    device = "cuda" if torch.cuda.is_available() else "cpu"
    embedding_model.to(device)
    print(f"-> Model loaded successfully on {device}.")
except Exception as e:
    print(f"Error loading SentenceTransformer model: {e}")
    sys.exit(1)

# --- 6. Indexing with Caching ---
# We perform a check: If the cache exists but has a different number of items
# than our parsed corpus, we assume the log file changed and re-index.
needs_indexing = True

if os.path.exists(EMBEDDING_CACHE):
    print(f"-> Found cache file '{EMBEDDING_CACHE}'. Verifying...")
    try:
        with open(EMBEDDING_CACHE, 'rb') as f:
            chunk_embeddings = pickle.load(f)
        
        if len(chunk_embeddings) == len(corpus):
            print("-> Cache verified. Loading embeddings...")
            needs_indexing = False
        else:
            print(f"-> Cache mismatch ({len(chunk_embeddings)} embeddings vs {len(corpus)} commits). Re-indexing...")
    except Exception as e:
        print(f"-> Error reading cache: {e}. Re-indexing...")

if needs_indexing:
    print(f"-> Creating new embeddings for {len(corpus)} commits...")
    try:
        chunk_embeddings = embedding_model.encode(
            corpus, 
            convert_to_tensor=True, 
            show_progress_bar=True
        )
        print("-> Indexing complete.")
        
        # Save to cache
        with open(EMBEDDING_CACHE, 'wb') as f:
            pickle.dump(chunk_embeddings, f)
        print(f"-> Embeddings saved to '{EMBEDDING_CACHE}'.")
        
    except Exception as e:
        print(f"Error during embedding: {e}")
        sys.exit(1)

# --- 7. Retrieval System ---
def retrieve_context(query, top_k=3):
    print(f"-> Embedding query: '{query}'")
    query_embedding = embedding_model.encode(query, convert_to_tensor=True)
    cos_scores = util.pytorch_cos_sim(query_embedding, chunk_embeddings)[0]
    
    # Get top results
    top_results = torch.topk(cos_scores, k=min(top_k, len(corpus)))
    
    retrieved_contexts = []
    for score, idx in zip(top_results[0], top_results[1]):
        retrieved_contexts.append({
            "score": score.item(),
            "data": metadata[idx.item()]
        })
    return retrieved_contexts

# --- 8. Generation System ---
apiKey = os.getenv("GEMINI_API_KEY")
if not apiKey:
    print("\n" + "*"*50)
    print("WARNING: GEMINI_API_KEY not found in .env file.")
    sys.exit(1)

apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={apiKey}"

def generate_answer(query, context_list):
    # Prepare context for LLM
    context_str = "Here are the most relevant commits found in the log:\n\n"
    for i, item in enumerate(context_list):
        # Clean up message for display
        msg_preview = item['data']['message'].replace('\n', ' ')[:200]
        context_str += f"Commit {i+1} (Score: {item['score']:.2f}):\n"
        context_str += f"Hash: {item['data']['commit']}\n"
        context_str += f"Author: {item['data']['author']}\n"
        context_str += f"Date: {item['data']['date']}\n"
        context_str += f"Message: {item['data']['message']}\n\n"

    prompt = f"""
    You are a software engineering assistant analyzing a git log.
    Based ONLY on the provided context, answer the user's question.
    
    If the user asks about a specific date, check the 'Date' fields in the context.
    If the user asks about a version (like v2.32.4), check the 'Message' fields.

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
        result = response.json()
        return result['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception as e:
        return f"Error generating answer: {e}"

# --- 9. Main Loop ---
def main():
    print(f"\n--- RAG on Git Diff Log (Final) ---")
    print(f"Loaded {len(corpus)} commits.")
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
                    print(f"  {i+1}. [{item['data']['date'][:10]}] {item['data']['message'].splitlines()[0][:60]}...")
            
            print("-> Generating answer...")
            answer = generate_answer(query, contexts)
            print("\n" + "="*20 + " Answer " + "="*20)
            print(answer)
            print("="*48)

        except (EOFError, KeyboardInterrupt):
            break

if __name__ == "__main__":
    main()