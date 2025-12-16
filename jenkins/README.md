# Jenkins Git Diff to ChromaDB Pipeline

This Jenkins pipeline extracts git diffs from commits and stores them in ChromaDB for semantic search and RAG (Retrieval-Augmented Generation) use cases.

## Overview

The pipeline:
1. Checks out the repository
2. Extracts git diff between current and previous commits
3. Parses the diff into searchable chunks
4. Stores chunks in ChromaDB with rich metadata

## Prerequisites

### Jenkins Requirements
- Jenkins with Pipeline plugin
- Python 3.8+ available on Jenkins agent
- Git installed on Jenkins agent

### ChromaDB Setup

You can run ChromaDB locally or as a container:

```bash
# Option 1: Docker container
docker run -d -p 8000:8000 chromadb/chroma

# Option 2: Kubernetes deployment
kubectl apply -f jenkins/chromadb-deployment.yaml -n monitoring
```

## Jenkins Configuration

### 1. Create Credentials

In Jenkins, create a credential with ID `chromadb-host`:
- Kind: Secret text
- ID: `chromadb-host`
- Secret: Your ChromaDB URL (e.g., `http://chromadb:8000`)

### 2. Create Pipeline Job

1. Create a new Pipeline job in Jenkins
2. Configure SCM to point to your repository
3. Set "Script Path" to `Jenkinsfile`

### 3. Webhook Setup (Optional)

Configure a webhook in your Git provider to trigger the pipeline on push:
- GitHub: Settings → Webhooks → Add webhook
- URL: `http://<jenkins-url>/github-webhook/`

## Chunk Structure

Each diff is parsed into chunks with the following metadata:

| Field | Description |
|-------|-------------|
| `repo_name` | Repository name |
| `commit_sha` | Current commit SHA |
| `previous_sha` | Previous commit SHA |
| `author` | Commit author |
| `commit_message` | Commit message (truncated to 500 chars) |
| `timestamp` | Commit timestamp |
| `branch` | Branch name |
| `file_path` | File being modified |
| `file_extension` | File extension for filtering |
| `change_type` | added, deleted, or modified |
| `chunk_index` | Index of chunk within commit |
| `total_files_changed` | Total files changed in commit |
| `ingestion_time` | When the chunk was stored |

## Querying ChromaDB

Example Python code to query stored diffs:

```python
import chromadb

client = chromadb.HttpClient(host="localhost", port=8000)
collection = client.get_collection("git_diffs")

# Semantic search for changes related to "authentication"
results = collection.query(
    query_texts=["authentication login security"],
    n_results=10,
    where={"branch": "main"}  # Optional filter
)

for doc, metadata in zip(results['documents'][0], results['metadatas'][0]):
    print(f"Commit: {metadata['commit_sha'][:8]}")
    print(f"File: {metadata['file_path']}")
    print(f"Author: {metadata['author']}")
    print(f"---")
```

## Local Development

Test the script locally:

```bash
cd jenkins
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Generate a test diff
git diff HEAD~1 HEAD > ../diff_output.patch

# Run the script
python3 git_diff_to_chromadb.py \
    --chromadb-host "./chromadb_data" \
    --collection "git_diffs" \
    --repo-name "cloud_automation" \
    --commit-sha "$(git rev-parse HEAD)" \
    --previous-sha "$(git rev-parse HEAD~1)" \
    --author "$(git log -1 --format='%an')" \
    --message "$(git log -1 --format='%s')" \
    --timestamp "$(git log -1 --format='%ci')" \
    --branch "$(git rev-parse --abbrev-ref HEAD)" \
    --diff-file "../diff_output.patch" \
    --changed-files "$(git diff --name-only HEAD~1 HEAD)"
```

## Troubleshooting

### Connection Issues
- Verify ChromaDB is running: `curl http://localhost:8000/api/v1/heartbeat`
- Check network connectivity from Jenkins agent

### Empty Diffs
- Initial commits won't have a previous SHA - the script handles this
- Merge commits may have different diff behavior

### Large Diffs
- The script chunks large files to avoid embedding size limits
- Adjust `max_chunk_size` in `parse_diff_into_chunks()` if needed
