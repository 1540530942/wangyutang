# Push to Remote Branch

This runbook records the current default way to publish local changes to
`1540530942/wangyutang`, normally on branch `feature/llm-manager`.

## Current Default: SSH

The current Linux workspace is configured and verified for direct GitHub SSH
pushes:

```text
origin: git@github.com:1540530942/wangyutang.git
SSH identity: ~/.ssh/github_id_ed25519
```

Use normal Git commands. Do not request or paste a GitHub personal access token
for routine pushes, and do not require `gh` unless GitHub CLI or PR operations
are specifically needed.

```bash
git push -u origin "$(git branch --show-current)"
```

On 2026-07-27 this method successfully pushed commit `53a1e46` to
`feature/llm-manager`.

## Normal Flow

From the repository root:

```bash
git status -sb
git diff --check

# Stage only the files that belong to this change. Never use git add -A in a
# mixed worktree unless the entire worktree has explicitly been approved.
git add <changed-files>
git diff --cached --check
git commit -m "Concise change summary"

# Run the smallest validation that matches the changed area.
git push -u origin "$(git branch --show-current)"
git ls-remote --heads origin "$(git branch --show-current)"
```

The SHA shown by `git ls-remote` must match `git rev-parse HEAD`.

## Branch and Scope Rules

- Stay on the current non-default branch unless a separate branch is requested.
- Inspect `git status -sb` before every commit.
- In a mixed worktree, stage explicit file paths only; preserve unrelated user
  changes unstaged.
- Check the staged diff before committing: `git diff --cached`.
- Do not create a PR unless requested. A normal push does not require `gh`.

## Before Pushing

- Check `git status -sb` and `git diff --cached`.
- Avoid staging unrelated temporary files.
- Do not include `.github/workflows/deploy-modules.yml` unless it is intentionally part of the change.
- Run the smallest validation that matches the changed area.
- Confirm the SSH transport before a first push from a new machine:

```bash
ssh -T git@github.com
```

If SSH authentication fails, do not replace the remote or paste credentials into
chat. Fix the GitHub SSH key authorization first, or use the historical HTTPS
fallback below.

## Historical HTTPS Fallback

The older Windows workspace had an SSH key authorization failure and sometimes
needed an HTTPS fallback. That is not the current default, but remains useful if
SSH is unavailable on another machine:

```powershell
git -c http.version=HTTP/1.1 `
    -c http.sslBackend=schannel `
    -c http.postBuffer=524288000 `
    push -u https://github.com/1540530942/wangyutang.git feature/llm-manager:feature/llm-manager
```

Authenticate through Git Credential Manager or an interactive browser flow. Do
not paste a personal access token into chat, scripts, or command history.

## After Pushing

```bash
git rev-parse HEAD
git ls-remote --heads origin "$(git branch --show-current)"
```

The two SHAs should match.

For deployment changes, also verify the live service:

```bash
curl -fsS https://www.wangyutang.cn/api/health
```
