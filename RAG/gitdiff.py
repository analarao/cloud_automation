import git
import sys
import os

# --- Configuration ---
REPO_URL = "https://github.com/psf/requests.git"  # A well-known repo
REPO_DIR = "cloned_repo/requests"
OUTPUT_FILE = "diffs.log"
COMMITS_TO_PROCESS = 100  # Process the last 100 commits (change as needed)

def extract_diffs():
    """
    Clones a repo and extracts the diffs for the latest commits,
    saving them to a single output file.
    """
    
    # --- 1. Clone or Open the Repo ---
    try:
        if os.path.exists(REPO_DIR):
            print(f"-> Repo already exists at {REPO_DIR}. Opening...")
            repo = git.Repo(REPO_DIR)
            print("-> Pulling latest changes...")
            repo.remotes.origin.pull()
        else:
            print(f"-> Cloning repo {REPO_URL} into {REPO_DIR}...")
            repo = git.Repo.clone_from(REPO_URL, REPO_DIR, progress=ProgressBar())
            print("\n-> Clone complete.")
    
    except git.exc.GitCommandError as e:
        print(f"ERROR: Could not clone or open repo: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

    # --- 2. Iterate Commits and Get Diffs ---
    print(f"-> Processing the last {COMMITS_TO_PROCESS} commits...")
    
    # Get a list of commits from the main branch
    try:
        commits = list(repo.iter_commits('master', max_count=COMMITS_TO_PROCESS))
    except git.exc.GitCommandError:
        # Fallback to 'main' if 'master' doesn't exist
        try:
            commits = list(repo.iter_commits('main', max_count=COMMITS_TO_PROCESS))
        except Exception as e:
            print(f"ERROR: Could not get commits. {e}")
            sys.exit(1)
            
    diff_count = 0
    
    # Open the output file to write all diffs
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        
        for commit in commits:
            # The initial commit has no parent, so no diff
            if not commit.parents:
                print(f"-> Skipping initial commit {commit.hexsha[:7]} (no parent)")
                continue

            # This is the start of the block
            try:
                # Get *only* the diff patch, not the full commit message
                # This diffs the commit against its first parent
                diff_data = repo.git.diff(commit.parents[0].hexsha, commit.hexsha)

                # Write the clean, single header
                f.write(f"\n{'='*70}\n")
                f.write(f"COMMIT: {commit.hexsha}\n")
                f.write(f"AUTHOR: {commit.author.name} <{commit.author.email}>\n")
    
                # Use authored_datetime to match the date you were searching for
                f.write(f"DATE:   {commit.authored_datetime}\n") 
                
                f.write(f"MESSAGE: {commit.message}\n")
                f.write(f"{'-'*70}\n\n")
                
                # Write *only* the patch
                f.write(diff_data)
                
                diff_count += 1
            
            # This 'except' block correctly matches the 'try'
            except Exception as e:
                print(f"ERROR: Could not get diff for commit {commit.hexsha}: {e}")
                
    print(f"\n-> Success! Processed {diff_count} commits.")
    print(f"-> All diff data saved to '{OUTPUT_FILE}'.")


# A simple progress bar for the clone operation
class ProgressBar(git.remote.RemoteProgress):
    def update(self, op_code, cur_count, max_count=None, message=''):
        print(f"  Cloning: {message or ''} {cur_count} / {max_count or '?'}", end='\r')


if __name__ == "__main__":
    
    # Check if GitPython is installed
    try:
        import git
    except ImportError:
        print("ERROR: 'GitPython' library not found.")
        print("Please install it by running: pip install GitPython")
        sys.exit(1)
        
    extract_diffs()