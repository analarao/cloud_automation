import json
import requests
import sys
import numpy as np
import re  # <-- We'll use Regular Expressions to parse the log
import os
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

# --- 3. Parse the Git Log File (NEW PARSER) ---
LOG_FILE = "diffs.log"  # Hard-coded to your file name

# This regex will parse the header of each commit block
HEADER_PATTERN = re.compile(
    r"COMMIT: (.*?)\n"
    r"AUTHOR: (.*?)\n"
    r"DATE:\s+(.*?)\n"
    r"MESSAGE: (.*?)\n"
    r"----------------------------------------------------------------------\n\n"
    r"(.*)", # The rest of the block is the diff data
    re.DOTALL # Make '.' match newlines for the MESSAGE and diff data
)

corpus = []
metadata = []

print(f"-> Loading and parsing '{LOG_FILE}'...")
try:
    with open(LOG_FILE, 'r', encoding='utf-8') as f:
        full_log_content = f.read()
    
    # Split the entire log file into chunks, one per commit
    commit_blocks = full_log_content.split("\n======================================================================\n")
    
    for block in commit_blocks:
        if not block.strip():
            continue
            
        match = HEADER_PATTERN.search(block)
        if match:
            commit, author, date, message, diff_data = match.groups()
            
            # --- FIX 1: Create the "document" for the corpus ---
            # (This is the fix from our previous conversation)
            document_text = f"""
            Commit: {commit.strip()}
            Author: {author.strip()}
            Date: {date.strip()}
            Message: {message.strip()}
            
            Changes:
            {diff_data.strip()}
            """
            
            # --- FIX 2: Append to BOTH lists ---
            # Both appends MUST happen inside the "if match:"
            corpus.append(document_text)
            
            metadata.append({
                "commit": commit.strip(),
                "author": author.strip(),
                "date": date.strip(),
                "message": message.strip(),
            })
    # --- END OF CRITICAL SECTION ---

    if not corpus:
        print(f"Warning: Could not parse any commit blocks from '{LOG_FILE}'.")
        print("Please check your HEADER_PATTERN regex.")
        sys.exit(1)
        
    print(f"-> Successfully parsed {len(corpus)} commits.")

except FileNotFoundError:
    print(f"Error: Could not find '{LOG_FILE}'.")
    print("Please make sure it's in the same folder as this script.")
    sys.exit(1)
except Exception as e:
    print(f"Error parsing log file: {e}")
    sys.exit(1)


# --- 4. Indexing: Embed the Corpus ---
print("-> Loading embedding model (all-MiniLM-L6-v2). This may take a moment...")
try:
    embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    device = "cuda" if torch.cuda.is_available() else "cpu"
    embedding_model.to(device)
    print(f"-> Model loaded successfully on {device}.")
except Exception as e:
    print(f"Error loading SentenceTransformer model: {e}")
    sys.exit(1)

print(f"-> Creating embeddings for all {len(corpus)} commits...")
try:
    chunk_embeddings = embedding_model.encode(corpus, convert_to_tensor=True, show_progress_bar=True)
    print("-> Indexing complete.")
except Exception as e:
    print(f"Error during embedding: {e}")
    sys.exit(1)

# --- 5. Retrieval System (Semantic Search) ---
def retrieve_context(query, top_k=3):
    """
    Retrieves the top_k most relevant commits (and their data) from the corpus.
    """
    print(f"-> Embedding query: '{query}'")
    query_embedding = embedding_model.encode(query, convert_to_tensor=True)
    cos_scores = util.pytorch_cos_sim(query_embedding, chunk_embeddings)[0]
    top_results = torch.topk(cos_scores, k=min(top_k, len(corpus)))
    
    retrieved_contexts = []
    for score, idx in zip(top_results[0], top_results[1]):
        retrieved_contexts.append({
            "score": score.item(),
            "data": metadata[idx.item()] # Return the parsed header data
        })
    return retrieved_contexts

# --- 6. Generation System (Gemini API) ---
apiKey = os.getenv("GEMINI_API_KEY")
if not apiKey:
    print("\n" + "*"*50)
    print("WARNING: GEMINI_API_KEY not found in .env file.")
    print("Please create a .env file and add your API key like this:")
    print("\nGEMINI_API_KEY=\"YOUR_API_KEY_HERE\"\n")
    print("*"*50 + "\n")
    sys.exit(1)

apiUrl = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={apiKey}"

def generate_answer(query, context_list):
    """
    Generates an answer using the Gemini API, augmented with retrieved commit data.
    """
    context_str = "Here is the most relevant commit data I found:\n\n"
    for i, item in enumerate(context_list):
        context_str += f"Commit Context {i+1} (Relevance Score: {item['score']:.2f}):\n"
        # We provide the clean metadata to the LLM
        context_str += f"{json.dumps(item['data'], indent=2)}\n\n"

    prompt = f"""
    You are a helpful software engineering analyst.
    Based *only* on the following context (which contains git commit headers), 
    please answer the user's question.
    
    The user is searching a log of git diffs. The context provided is the *metadata* (commit hash, author, message) of the most relevant commits.

    Summarize the findings. If the context does not contain the answer, 
    say "I could not find a clear answer in the retrieved commits."

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
        
        if 'candidates' in result and len(result['candidates']) > 0:
            return result['candidates'][0]['content']['parts'][0]['text'].strip()
        else:
            return "Error: No answer candidate found."
            
    except requests.exceptions.RequestException as e:
        print(f"Error calling Gemini API: {e}")
        return "Error: Could not connect to the generation API."

# --- 7. Main RAG Loop ---
def main():
    print("\n--- RAG on Git Diff Log (v6) Implementation Demo ---")
    print("This script parses and performs RAG on your 'diffs.log' file.")
    print(f"Loaded {len(corpus)} commits from {LOG_FILE}.")
    print("Try: 'What changes did dependabot make?' or 'Find commits about 'SSLContext'.")
    print("Type 'quit' or 'exit' to stop.")
    
    while True:
        try:
            query = input("\nYour question: ")
            if query.lower() in ['quit', 'exit']:
                break
            if not query:
                continue

            # 1. Retrieve
            contexts = retrieve_context(query)
            if contexts:
                print(f"-> [Context Retrieved]: Found {len(contexts)} relevant commits.")
                for i, item in enumerate(contexts):
                    # Show the commit message we found
                    print(f"  {i+1}. [Commit {item['data']['commit'][:7]}] {item['data']['message'][:70]}...") 
            else:
                print("-> Could not retrieve context.")
                continue

            # 2. Generate
            print("-> Generating answer...")
            answer = generate_answer(query, contexts)
            
            print("\n" + "="*20 + " Answer " + "="*20)
            print(answer)
            print("="*48)

        except EOFError: break
        except KeyboardInterrupt: break

if __name__ == "__main__":
    main()