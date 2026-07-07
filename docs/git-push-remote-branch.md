# Push to Remote Branch

This runbook records the safe way to publish local changes to `1540530942/wangyutang` branch `feature/llm-manager`.

## Known Working Push Command

Use the HTTPS URL directly instead of the configured SSH `origin`.

The local `origin` currently points to:

```text
git@github.com:1540530942/wangyutang.git
```

SSH push failed with:

```text
Permission denied (publickey)
```

From the repository root, use:

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform

git -c http.version=HTTP/1.1 `
    -c http.sslBackend=schannel `
    -c http.postBuffer=524288000 `
    push -u https://github.com/1540530942/wangyutang.git feature/llm-manager:feature/llm-manager
```

This command successfully pushed fast-forward updates after GitHub connectivity recovered, including the homepage update to `dbf9a9f`.

## Normal Flow

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform

git status -sb

git add <changed-files>
git commit -m "Concise change summary"

docker compose config --quiet
python -m compileall -q camera_snapshot action_move robot_sandbox pi5_robot common_api_manager

git -c http.version=HTTP/1.1 `
    -c http.sslBackend=schannel `
    -c http.postBuffer=524288000 `
    push -u https://github.com/1540530942/wangyutang.git feature/llm-manager:feature/llm-manager
```

## Before Pushing

- Check `git status -sb`.
- Avoid staging unrelated temporary files.
- Do not include `.github/workflows/deploy-modules.yml` unless it is intentionally part of the change.
- Run the smallest validation that matches the changed area.
- If GitHub HTTPS fails, check connectivity first:

```powershell
Test-NetConnection github.com -Port 443
```

If port 443 is down or reset, do not rewrite commits just to fix a network failure. Wait and retry the known working push command.

## Avoid Repeating Failed Paths

- Do not use `git push origin ...` while `origin` is SSH unless the GitHub SSH key has been fixed.
- Do not keep retrying `git@github.com:1540530942/wangyutang.git`; it failed with `Permission denied (publickey)`.
- `gh` is installed, but `gh auth status` currently reports not logged in. Do not rely on `gh` until `gh auth login` has been completed.
- GitHub connector tree updates are not a good fallback unless blobs are uploaded first; local blob SHAs are not valid on GitHub until GitHub has those objects.

## After Pushing

```powershell
git rev-parse HEAD
git ls-remote https://github.com/1540530942/wangyutang.git refs/heads/feature/llm-manager
```

The two SHAs should match.

For deployment changes, also verify the live service:

```powershell
Invoke-WebRequest -Uri 'https://www.wangyutang.cn/api/health' -UseBasicParsing
```
