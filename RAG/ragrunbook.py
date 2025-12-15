# runbook_rag.py

import chromadb
import json
import os
from dotenv import load_dotenv
from typing import List, Dict
from groq import Groq

# Import the new initializer logic
from db_initializer import initialize_knowledge_base, RUNBOOK_COLLECTION_NAME, DB_PATH

load_dotenv()

# --- Configuration ---
GROQ_MODEL = "llama-3.3-70b-versatile"
BACKUP_FILE_PATH = "datarunbook.py"

class RunbookRAG:
    def __init__(self):
        # 1. Initialize Database
        initialize_knowledge_base()
        
        # 2. Connect to the DB
        self.client = chromadb.PersistentClient(path=DB_PATH)
        self.collection = self.client.get_or_create_collection(name=RUNBOOK_COLLECTION_NAME)

        # 3. Initialize Groq
        try:
            self.groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        except Exception:
            print("⚠️ Error: GROQ_API_KEY not found.")

    def _backup_to_file(self, summary: str, solution: str):
        """SAFETY NET: Appends the new learned issue to datarunbook.py."""
        print("💾 Creating Safety Net Backup in datarunbook.py...")
        new_entry = {"issue": summary, "solution": solution}

        try:
            with open(BACKUP_FILE_PATH, "r") as f:
                content = f.read()

            last_bracket = content.rfind("]")
            
            if last_bracket != -1:
                entry_string = json.dumps(new_entry, indent=4)
                new_content = (
                    content[:last_bracket].rstrip() + 
                    ",\n    " + 
                    entry_string + 
                    "\n]" + 
                    content[last_bracket+1:]
                )
                with open(BACKUP_FILE_PATH, "w") as f:
                    f.write(new_content)
                print("✅ Backup Successful: 'datarunbook.py' updated.")
            else:
                print("❌ Backup Failed: Could not find closing ']' in datarunbook.py")

        except Exception as e:
            print(f"❌ Backup Failed: {e}")

    def _call_llm(self, prompt: str) -> str:
        print(f"\n[🚀 GROQ API Interaction] Generating with {GROQ_MODEL}...")
        try:
            chat_completion = self.groq_client.chat.completions.create(
                messages=[
                    {"role": "system", "content": "You are a helpful IT Support Bot. You output JSON only."},
                    {"role": "user", "content": prompt}
                ],
                model=GROQ_MODEL,
                response_format={"type": "json_object"}, 
                temperature=0.1,
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            print(f"❌ Groq API Error: {e}")
            return json.dumps({"status": "KNOWN", "troubleshooting_steps": [{"step": 1, "description": "API Error"}]})

    def get_solution(self, user_query: str) -> str:
        print(f"\n--- 🔍 RAG Processing for: '{user_query}' ---")
        
        # 1. Retrieval
        retrieved_data = self.collection.query(query_texts=[user_query], n_results=3)
        retrieved_documents = retrieved_data.get('documents', [[]])[0]
        context = retrieved_documents[0] if retrieved_documents else "No relevant context found."
        
        # 2. Prompt
        prompt =f"""
        You are a Technical Support Agent.
        CONTEXT FROM RUNBOOK: {context}
        USER QUERY: {user_query}
        
        INSTRUCTIONS:
        1. If CONTEXT has the answer -> Return JSON: {{"status": "KNOWN", "troubleshooting_steps": [{{"step": 1, "description": "..."}}]}}
        2. If CONTEXT is empty/irrelevant -> Return JSON: {{"status": "NEW_ISSUE", "is_new_issue": true, "issue_summary": "...", "proposed_solution": "..."}}
        """
        
        # 3. Generation
        llm_response = self._call_llm(prompt)
        
        # 4. Learning/Update Phase
        try:
            response_json: Dict = json.loads(llm_response)
            
            if response_json.get("status") == "NEW_ISSUE":
                print("\n🚨 NEW ISSUE DETECTED! Triggering Learn Loop.")
                summary = response_json.get("issue_summary", "Unknown summary")
                solution = response_json.get("proposed_solution", "No solution provided")
                
                self._update_knowledge_base(summary, solution)
                self._backup_to_file(summary, solution)
                
                return f"Solution proposed by Groq (and backed up):\n{solution}"
            else:
                print("✅ KNOWN ISSUE: Solution retrieved.")
                
                # --- 🛠️ FIX STARTS HERE: Handle both String and Dict formats ---
                steps = response_json.get("troubleshooting_steps", [])
                formatted_steps = []
                
                for s in steps:
                    if isinstance(s, dict):
                        # It is a dictionary: {"step": 1, "description": "..."}
                        formatted_steps.append(f"{s.get('step', '-')}. {s.get('description', '')}")
                    elif isinstance(s, str):
                        # It is just a string: "1. Check logs"
                        formatted_steps.append(s)
                
                return "Steps:\n" + "\n".join(formatted_steps)
                # --- 🛠️ FIX ENDS HERE ---

        except json.JSONDecodeError:
            print("❌ ERROR: LLM returned malformed JSON.")
            return "Error parsing LLM output."

    def _update_knowledge_base(self, summary: str, solution: str):
        new_document = f"{summary} :: {solution}"
        new_id = f"doc_{self.collection.count() + 1}"
        self.collection.add(
            documents=[new_document],
            metadatas=[{"source": "llm_learned_fix", "summary": summary}],
            ids=[new_id]
        )
        print(f"    -> Added new runbook entry with ID: {new_id} to Database.")


if __name__ == "__main__":
    rag_system = RunbookRAG()
    
    print("\n" + "="*50)
    print("        SCENARIO 1: KNOWN ISSUE (Original)")
    print("="*50)
    # Should retrieve Doc 0-3
    print(f"\nFINAL SOLUTION:\n{rag_system.get_solution('The login is showing 503 and Microservice A is definitely failing.')}")
    
    print("\n" + "="*50)
    print("        SCENARIO 2: KNOWN ISSUE (The Java NPE we just learned)")
    print("="*50)
    # Should retrieve Doc 4 (The one we added in the previous run)
    print(f"\nFINAL SOLUTION:\n{rag_system.get_solution('The daily synchronization job is failing with a Java NullPointer Exception (NPE) in the transform service.')}")

    print("\n" + "="*50)
    print("        SCENARIO 3: BRAND NEW 6TH ISSUE (Redis Failure)")
    print("="*50)
    # This is new! It should trigger "NEW ISSUE DETECTED" and create Doc 5.
    new_query = "The payment service is slow and logs show a RedisTimeoutException when connecting to the cache."
    print(f"\nFINAL SOLUTION:\n{rag_system.get_solution(new_query)}")

    print("\n--- Summary ---")
    print(f"Total documents in runbook: {rag_system.collection.count()}")