# GitHub Push Troubleshooting

This note records the Git HTTPS failure pattern seen on 2026-05-06 and the
recovery steps to use before changing repository state. It is historical: the
current Linux workspace has a GitHub-authorized SSH key and the normal push
path is documented in [git-push-remote-branch.md](git-push-remote-branch.md).

## Symptom

`git push` to the GitHub HTTPS remote may fail even when the repository, branch, and credentials were working earlier.

Observed errors:

```text
fatal: unable to access 'https://github.com/1540530942/MyTest.git/':
Failed to connect to github.com port 443 after 210xx ms: Could not connect to server

fatal: unable to access 'https://github.com/1540530942/MyTest.git/':
Recv failure: Connection was reset

error: RPC failed; curl 52 Recv failure: Connection was reset
fatal: expected flush after ref listing
```

At the same time, browser-style HTTPS can still work:

```powershell
Invoke-WebRequest -Uri 'https://github.com' -UseBasicParsing -TimeoutSec 20
```

At the time, SSH also reached GitHub port 22 but that Windows machine did not
have a GitHub-authorized SSH key:

```text
git@github.com: Permission denied (publickey).
```

## What This Means

This is not necessarily a bad commit, bad branch, or bad remote. In the 2026-05-06 incident:

- `origin` was still `https://github.com/1540530942/MyTest.git`.
- The branch was still `feature/llm-manager`.
- Earlier pushes on the same branch worked.
- The failure happened at Git HTTPS / curl network transport level.
- GitHub CLI `gh` was not installed, so it could not be used as a fallback.
- The GitHub connector was unavailable due MCP startup timeout.

Treat this as an intermittent local network or Git HTTPS transport problem unless `git status`, `git remote -v`, or auth checks prove otherwise.

## Safe Recovery Checklist

1. Confirm local state:

```powershell
git status -sb
git log --oneline -5
git remote -v
```

2. Confirm whether the local branch is ahead:

```powershell
git status -sb
```

Look for:

```text
[ahead 1]
```

3. Check GitHub TCP reachability:

```powershell
Test-NetConnection github.com -Port 443
```

4. Check browser-style HTTPS separately:

```powershell
Invoke-WebRequest -Uri 'https://github.com' -UseBasicParsing -TimeoutSec 20
```

5. Try the push with the buffer setting that has worked before:

```powershell
git -c http.postBuffer=524288000 push -u origin feature/llm-manager
```

6. If that fails, wait and retry. Do not amend, reset, rebase, or restage unrelated files just because the network failed.

7. Optional fallback if Git HTTPS remains unreliable: add a GitHub-authorized SSH key, then switch the remote only after confirming access:

```powershell
ssh -T git@github.com
git remote set-url origin git@github.com:1540530942/MyTest.git
git push -u origin feature/llm-manager
```

Do not switch to SSH while `ssh -T git@github.com` returns `Permission denied (publickey)`.

## Current Transport Status

On the current Linux workspace, `origin` uses
`git@github.com:1540530942/wangyutang.git` and the configured
`~/.ssh/github_id_ed25519` key successfully pushed `53a1e46` on 2026-07-27.
Use SSH by default there. The HTTPS guidance in this document applies only when
working from a machine where SSH has not been authorized or is temporarily
unavailable.

## Avoiding Future Stalls

- Keep this runbook linked from incident logs when a push fails.
- Install and authenticate GitHub CLI only if PR or auth diagnostics are needed:

```powershell
gh auth status
```

- Consider adding a dedicated GitHub SSH key to avoid HTTPS transport instability.
- Keep unrelated runtime logs unstaged, especially `camera_snapshot/gpio_ssh_bridge.out.log`.
