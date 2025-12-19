# Containerizing the RAG project

Quick instructions to build and run the repository in Docker.

Prereqs:
- Docker (Desktop) and Docker Compose installed.

## Database is NOT containerized

By default, the database files (`chroma_db/` and `runbook_db/`) are stored on your host machine and mounted into the container. This means your data is never lost if the container is removed, and you can inspect or back up the DB files directly from your host.

If you want to use a remote or managed database, update your environment variables and code to point to the remote DB location.

## Build and run (recommended via docker-compose)

```bash
# Build the image and start the service (host bind-mounts DB storage)
docker compose up --build -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

## Notes and tips
- The project uses persistent SQLite/Chroma files stored in `chroma_db/` and `runbook_db/`.
  Compose now maps those to your host, so you can back up or inspect them easily.
- Put your API keys in `.env` (GEMINI_API_KEY, GROQ_API_KEY). Do NOT commit `.env` to source control.
- The default script that runs inside the container is `ragrunbook.py`. To run a different script set the `SCRIPT` env var:

```bash
docker compose run --rm -e SCRIPT=rag.py rag
```

- The repo includes `torch` in `requirements.txt`. Installing `torch` inside the container may pull large wheels; expect a larger image. If you need GPU support, consider building from an NVIDIA base image.
