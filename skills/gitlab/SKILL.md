---
name: gitlab
version: 1.0.0
description: "GitLab: clone, push, MR, CI/CD — RETEK migration from GitHub"
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gitlab, git, repository, migration, ci-cd]
    related_skills: [github-code-review, test-driven-development, plan]
---

# GitLab Skill — RETEK

## Overview

Git operations for GitLab repositories. Migrated from GitHub to GitLab.

## Prerequisites

- GitLab Personal Access Token set in environment: `GITLAB_TOKEN`
- GitLab URL (default: https://gitlab.com)

### Environment Setup

```bash
# Check token
if [ -z "$GITLAB_TOKEN" ]; then
    echo "Error: GITLAB_TOKEN not set"
    exit 1
fi

# Set GitLab URL
GITLAB_URL="${GITLAB_URL:-https://gitlab.com}"
GITLAB_API="${GITLAB_URL}/api/v4"
```

## 1. Clone Repository

```bash
# Clone via HTTPS with token
git clone "https://oauth2:${GITLAB_TOKEN}@gitlab.com/OWNER/REPO.git"

# Or via SSH (if SSH key configured)
git clone "git@gitlab.com:OWNER/REPO.git"
```

## 2. Create Merge Request

```bash
# Push branch first
git push -u origin feature-branch

# Create MR via API
curl -X POST "${GITLAB_API}/projects/$(echo "OWNER/REPO" | sed 's/\//%2F/g')/merge_requests" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
        "source_branch": "feature-branch",
        "target_branch": "main",
        "title": "Feature: description",
        "description": "Changes overview",
        "remove_source_branch": true
    }'
```

## 3. List Repositories

```bash
# List all accessible projects
curl -s -X GET "${GITLAB_API}/projects?per_page=100&membership=true" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" | \
    python3 -c "import sys,json; [print(p['path_with_namespace']) for p in json.load(sys.stdin)]"
```

## 4. CI/CD Pipeline Status

```bash
# Get latest pipeline
curl -s -X GET "${GITLAB_API}/projects/$(echo "OWNER/REPO" | sed 's/\//%2F/g')/pipelines?per_page=1" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); print('Status:', d[0]['status'], 'Branch:', d[0]['ref']) if d else print('No pipelines')"
```

## 5. Code Review (MR Discussion)

```bash
# Get MR diff
curl -s -X GET "${GITLAB_API}/projects/$(echo "OWNER/REPO" | sed 's/\//%2F/g')/merge_requests/${MR_IID}/changes" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" | \
    python3 -c "import sys,json; [print(c['diff']) for c in json.load(sys.stdin).get('changes',[])]"

# Post comment
curl -X POST "${GITLAB_API}/projects/$(echo "OWNER/REPO" | sed 's/\//%2F/g')/merge_requests/${MR_IID}/discussions" \
    -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{"body": "Review comment here"}'
```

## Migration Checklist from GitHub

| GitHub | GitLab |
|--------|--------|
| `git clone github.com/...` | `git clone gitlab.com/...` |
| `gh pr create` | `glab mr create` or API |
| `gh pr list` | API call (see above) |
| `gh pr review` | MR Discussion API |
| GitHub Actions | GitLab CI/CD |
| Issues | Issues (API compatible) |
