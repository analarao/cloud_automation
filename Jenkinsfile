pipeline {
    agent any

    environment {
        CHROMADB_HOST = credentials('chromadb-host')
        CHROMADB_COLLECTION = 'git_diffs'
        REPO_NAME = 'cloud_automation'
    }

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Setup Python Environment') {
            steps {
                sh '''
                    python3 -m venv venv
                    . venv/bin/activate
                    pip install --upgrade pip
                    pip install -r jenkins/requirements.txt
                '''
            }
        }

        stage('Extract Git Diff') {
            steps {
                script {
                    // Get the current and previous commit SHAs
                    env.CURRENT_COMMIT = sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
                    env.PREVIOUS_COMMIT = sh(script: 'git rev-parse HEAD~1 2>/dev/null || echo ""', returnStdout: true).trim()
                    
                    // Get commit metadata
                    env.COMMIT_AUTHOR = sh(script: 'git log -1 --format="%an"', returnStdout: true).trim()
                    env.COMMIT_MESSAGE = sh(script: 'git log -1 --format="%s"', returnStdout: true).trim()
                    env.COMMIT_TIMESTAMP = sh(script: 'git log -1 --format="%ci"', returnStdout: true).trim()
                    
                    // Get actual branch name (handles detached HEAD from Jenkins checkout)
                    env.GIT_BRANCH_NAME = sh(script: '''
                        # Try to get branch from Jenkins env vars first
                        if [ -n "${GIT_BRANCH:-}" ]; then
                            echo "${GIT_BRANCH}" | sed 's|origin/||'
                        elif [ -n "${BRANCH_NAME:-}" ]; then
                            echo "${BRANCH_NAME}"
                        else
                            # Fallback: try git branch --show-current or parse from ref
                            git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD
                        fi
                    ''', returnStdout: true).trim()
                    
                    if (env.PREVIOUS_COMMIT) {
                        // Extract the diff between commits
                        sh "git diff ${env.PREVIOUS_COMMIT} ${env.CURRENT_COMMIT} > diff_output.patch"
                        
                        // Get list of changed files
                        env.CHANGED_FILES = sh(
                            script: "git diff --name-only ${env.PREVIOUS_COMMIT} ${env.CURRENT_COMMIT}",
                            returnStdout: true
                        ).trim()
                    } else {
                        echo "No previous commit found (initial commit)"
                        sh "git show --format='' ${env.CURRENT_COMMIT} > diff_output.patch"
                        env.CHANGED_FILES = sh(
                            script: "git diff-tree --no-commit-id --name-only -r ${env.CURRENT_COMMIT}",
                            returnStdout: true
                        ).trim()
                    }
                }
            }
        }

        stage('Send to ChromaDB') {
            steps {
                sh '''
                    . venv/bin/activate
                    python3 jenkins/git_diff_to_chromadb.py \
                        --chromadb-host "${CHROMADB_HOST}" \
                        --collection "${CHROMADB_COLLECTION}" \
                        --repo-name "${REPO_NAME}" \
                        --commit-sha "${CURRENT_COMMIT}" \
                        --previous-sha "${PREVIOUS_COMMIT}" \
                        --author "${COMMIT_AUTHOR}" \
                        --message "${COMMIT_MESSAGE}" \
                        --timestamp "${COMMIT_TIMESTAMP}" \
                        --branch "${GIT_BRANCH_NAME}" \
                        --diff-file "diff_output.patch" \
                        --changed-files "${CHANGED_FILES}"
                '''
            }
        }

        stage('Cleanup') {
            steps {
                sh 'rm -f diff_output.patch'
            }
        }
    }

    post {
        success {
            echo "Git diff successfully extracted and sent to ChromaDB"
        }
        failure {
            echo "Failed to process git diff"
        }
        always {
            cleanWs(cleanWhenNotBuilt: false)
        }
    }
}
