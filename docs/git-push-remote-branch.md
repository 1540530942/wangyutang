# Push to Remote Branch

This runbook records the safe way to publish local changes to the remote `feature/llm-manager` branch.

## Normal Flow

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_push_tmp

git status --short
git fetch origin feature/llm-manager
git log --oneline --left-right --cherry-pick origin/feature/llm-manager...HEAD

git add <changed-files>
git commit -m "Concise change summary"
git rebase origin/feature/llm-manager

python -m py_compile action_move/server.py action_move/action_move_executor.py action_move/edge_action_poller.py action_move/edge_ros_controller.py action_move/regression_guard.py
python -m json.tool action_move/skill_catalog.json > $null

git -c http.proxy= -c https.proxy= push origin HEAD:feature/llm-manager
```

## Before Pushing

- Check `git status --short` and avoid staging unrelated temporary files.
- Rebase onto `origin/feature/llm-manager` so the push is fast-forward.
- Run the smallest validation that matches the changed area.
- If GitHub HTTPS fails, check connectivity first:

```powershell
Test-NetConnection github.com -Port 443
```

If port 443 is down or reset, do not rewrite commits just to fix a network failure. Wait and retry the same push command.

## After Pushing

```powershell
git fetch origin feature/llm-manager
git rev-parse HEAD
git rev-parse origin/feature/llm-manager
```

The two SHAs should match. For deployment changes, check the matching GitHub Actions run before treating the work as deployed.
