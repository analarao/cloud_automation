#!/usr/bin/env python3
"""
Git Diff to ChromaDB Ingestion Script

This script extracts git diff information and stores it in ChromaDB
for semantic search and retrieval-augmented generation (RAG) use cases.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from typing import Optional

import chromadb
from chromadb.config import Settings


def parse_diff_into_chunks(diff_content: str, max_chunk_size: int = 1000) -> list[dict]:
    """
    Parse a git diff into meaningful chunks for embedding.
    
    Each chunk contains:
    - The file being modified
    - The type of change (add, delete, modify)
    - The actual diff content
    """
    chunks = []
    current_file = None
    current_chunk = []
    current_chunk_size = 0
    
    lines = diff_content.split('\n')
    
    for line in lines:
        # Detect new file in diff
        if line.startswith('diff --git'):
            # Save previous chunk if exists
            if current_chunk and current_file:
                chunks.append({
                    'file': current_file,
                    'content': '\n'.join(current_chunk),
                    'type': 'diff'
                })
            
            # Extract filename from diff header
            parts = line.split(' ')
            if len(parts) >= 4:
                current_file = parts[2].lstrip('a/')
            current_chunk = [line]
            current_chunk_size = len(line)
            
        elif line.startswith('+++') or line.startswith('---'):
            current_chunk.append(line)
            current_chunk_size += len(line)
            
        elif line.startswith('@@'):
            # Start of a new hunk - might want to split here for large files
            if current_chunk_size > max_chunk_size and current_file:
                chunks.append({
                    'file': current_file,
                    'content': '\n'.join(current_chunk),
                    'type': 'diff'
                })
                current_chunk = [f"diff --git a/{current_file} b/{current_file}", line]
                current_chunk_size = len(current_chunk[0]) + len(line)
            else:
                current_chunk.append(line)
                current_chunk_size += len(line)
                
        else:
            current_chunk.append(line)
            current_chunk_size += len(line)
    
    # Don't forget the last chunk
    if current_chunk and current_file:
        chunks.append({
            'file': current_file,
            'content': '\n'.join(current_chunk),
            'type': 'diff'
        })
    
    return chunks


def generate_chunk_id(commit_sha: str, file_path: str, chunk_index: int) -> str:
    """Generate a unique ID for each chunk."""
    content = f"{commit_sha}:{file_path}:{chunk_index}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def classify_change_type(diff_content: str) -> str:
    """Classify the type of change based on diff content."""
    has_additions = any(line.startswith('+') and not line.startswith('+++') 
                       for line in diff_content.split('\n'))
    has_deletions = any(line.startswith('-') and not line.startswith('---') 
                       for line in diff_content.split('\n'))
    
    if has_additions and has_deletions:
        return 'modified'
    elif has_additions:
        return 'added'
    elif has_deletions:
        return 'deleted'
    return 'unknown'


def get_file_extension(filepath: str) -> str:
    """Extract file extension for categorization."""
    if '.' in filepath:
        return filepath.rsplit('.', 1)[-1].lower()
    return 'unknown'


def send_to_chromadb(
    chromadb_host: str,
    collection_name: str,
    repo_name: str,
    commit_sha: str,
    previous_sha: Optional[str],
    author: str,
    message: str,
    timestamp: str,
    branch: str,
    diff_file: str,
    changed_files: str
) -> None:
    """
    Parse git diff and send chunks to ChromaDB.
    """
    # Read diff content
    try:
        with open(diff_file, 'r', encoding='utf-8', errors='replace') as f:
            diff_content = f.read()
    except FileNotFoundError:
        print(f"Error: Diff file not found: {diff_file}")
        sys.exit(1)
    
    if not diff_content.strip():
        print("No diff content found. Skipping ChromaDB ingestion.")
        return
    
    # Parse diff into chunks
    chunks = parse_diff_into_chunks(diff_content)
    
    if not chunks:
        print("No chunks extracted from diff. Skipping ChromaDB ingestion.")
        return
    
    print(f"Extracted {len(chunks)} chunks from diff")
    
    # Connect to ChromaDB
    try:
        # Try HTTP client first (for remote ChromaDB)
        if chromadb_host.startswith('http'):
            client = chromadb.HttpClient(
                host=chromadb_host.replace('http://', '').replace('https://', '').split(':')[0],
                port=int(chromadb_host.split(':')[-1]) if ':' in chromadb_host.split('//')[-1] else 8000
            )
        else:
            # Fall back to persistent client for local development
            client = chromadb.PersistentClient(path=chromadb_host)
    except Exception as e:
        print(f"Warning: Could not connect to ChromaDB at {chromadb_host}: {e}")
        print("Falling back to local persistent storage at ./chromadb_data")
        client = chromadb.PersistentClient(path="./chromadb_data")
    
    # Get or create collection
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"description": "Git diff storage for RAG"}
    )
    
    # Prepare data for insertion
    ids = []
    documents = []
    metadatas = []
    
    changed_files_list = [f.strip() for f in changed_files.split('\n') if f.strip()]
    
    for idx, chunk in enumerate(chunks):
        chunk_id = generate_chunk_id(commit_sha, chunk['file'], idx)
        
        # Create searchable document combining context and diff
        document = f"""Repository: {repo_name}
Branch: {branch}
Commit: {commit_sha}
Author: {author}
Message: {message}
File: {chunk['file']}
Change Type: {classify_change_type(chunk['content'])}

Diff Content:
{chunk['content']}"""
        
        metadata = {
            'repo_name': repo_name,
            'commit_sha': commit_sha,
            'previous_sha': previous_sha or '',
            'author': author,
            'commit_message': message[:500],  # Truncate long messages
            'timestamp': timestamp,
            'branch': branch,
            'file_path': chunk['file'],
            'file_extension': get_file_extension(chunk['file']),
            'change_type': classify_change_type(chunk['content']),
            'chunk_index': idx,
            'total_files_changed': len(changed_files_list),
            'ingestion_time': datetime.utcnow().isoformat()
        }
        
        ids.append(chunk_id)
        documents.append(document)
        metadatas.append(metadata)
    
    # Upsert to ChromaDB (handles duplicates gracefully)
    try:
        collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas
        )
        print(f"Successfully ingested {len(ids)} chunks to ChromaDB collection '{collection_name}'")
        
        # Print summary
        print(f"\nIngestion Summary:")
        print(f"  - Commit: {commit_sha[:8]}")
        print(f"  - Author: {author}")
        print(f"  - Message: {message[:50]}...")
        print(f"  - Files changed: {len(changed_files_list)}")
        print(f"  - Chunks created: {len(chunks)}")
        
    except Exception as e:
        print(f"Error upserting to ChromaDB: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Extract git diff and send to ChromaDB'
    )
    parser.add_argument('--chromadb-host', required=True,
                       help='ChromaDB host URL or local path')
    parser.add_argument('--collection', required=True,
                       help='ChromaDB collection name')
    parser.add_argument('--repo-name', required=True,
                       help='Repository name')
    parser.add_argument('--commit-sha', required=True,
                       help='Current commit SHA')
    parser.add_argument('--previous-sha', default='',
                       help='Previous commit SHA')
    parser.add_argument('--author', required=True,
                       help='Commit author')
    parser.add_argument('--message', required=True,
                       help='Commit message')
    parser.add_argument('--timestamp', required=True,
                       help='Commit timestamp')
    parser.add_argument('--branch', required=True,
                       help='Branch name')
    parser.add_argument('--diff-file', required=True,
                       help='Path to diff file')
    parser.add_argument('--changed-files', default='',
                       help='Newline-separated list of changed files')
    
    args = parser.parse_args()
    
    send_to_chromadb(
        chromadb_host=args.chromadb_host,
        collection_name=args.collection,
        repo_name=args.repo_name,
        commit_sha=args.commit_sha,
        previous_sha=args.previous_sha if args.previous_sha else None,
        author=args.author,
        message=args.message,
        timestamp=args.timestamp,
        branch=args.branch,
        diff_file=args.diff_file,
        changed_files=args.changed_files
    )


if __name__ == '__main__':
    main()
