# db_initializer.py

import chromadb
from typing import List, Dict
from datarunbook import INITIAL_RUNBOOK_ENTRIES

RUNBOOK_COLLECTION_NAME = "troubleshooting_runbooks"
DB_PATH = "./runbook_db"

def initialize_knowledge_base():
    """
    Checks if the ChromaDB collection is empty.
    If empty, loads data from datarunbook.py.
    """
    # Connect to the persistent database
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_or_create_collection(name=RUNBOOK_COLLECTION_NAME)

    if collection.count() == 0:
        print("💾 Database is empty. Loading from 'datarunbook.py'...")
        
        documents = []
        ids = []
        metadatas = []
        
        for index, entry in enumerate(INITIAL_RUNBOOK_ENTRIES):
            # Create a combined string for better semantic search
            doc_text = f"{entry['issue']} :: {entry['solution']}"
            documents.append(doc_text)
            
            # Metadata helps us track where this data came from
            metadatas.append({"source": "initial_runbook"})
            ids.append(f"doc_{index}")

        if documents:
            collection.add(documents=documents, metadatas=metadatas, ids=ids)
            print(f"✅ Successfully loaded {len(documents)} documents from file.")
    else:
        print(f"📚 Database loaded from disk ({collection.count()} documents). Skipping file load.")

if __name__ == "__main__":
    # This allows you to run this file directly to force an update/check
    initialize_knowledge_base()