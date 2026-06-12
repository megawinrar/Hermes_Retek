# Push Verification Checklist

Full verification chain after making changes to a git remote:

## 1. Check remote URL

```bash
git remote -v
```

Expected: the correct owner/repo. If wrong:

```bash
git remote set-url origin git@github.com:correct-owner/repo.git
```

## 2. Check local state

```bash
git status
git log --oneline -3
```

## 3. Push

```bash
git add .
git commit -m "type: description"
git push -u origin main
```

## 4. Verify SHA matches remote

```bash
git rev-parse HEAD
git ls-remote --heads origin main
```

Both outputs must be the **same full 40-character SHA**.

## 5. External verification via GitHub raw content

```bash
curl -i https://raw.githubusercontent.com/owner/repo/main/path/to/file
```

Expected: `HTTP/2 200`. Also check with GitHub API:

```bash
curl -s -H "Authorization: token $TOKEN" \
  https://api.github.com/repos/owner/repo/contents?ref=main
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `Everything up-to-date` but files missing on GitHub | Wrong remote URL | `git remote set-url origin <correct>` then push |
| `HTTP 404` on raw.githubusercontent.com | Wrong branch name or repo | Check branch name and repo owner |
| SHA mismatch (`git rev-parse HEAD` ≠ `git ls-remote`) | Push failed or never happened | `git push -u origin main` |
| `Already up-to-date` after pull but files differ | Local commits not on remote | `git push -u origin main` |