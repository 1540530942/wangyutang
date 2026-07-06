# scripts

Deployment and local-check helpers. Production deploys are normally driven by
GitHub Actions (`.github/workflows/deploy-modules.yml`); these scripts cover
local checks and manual/legacy deploy paths.

| Script | Purpose |
| --- | --- |
| `check-local.ps1` | Probe local service `/api/health` endpoints after `docker compose up`. |
| `tencent_apply_release.sh` | Apply an uploaded release archive on the Tencent host (used by the Actions workflow). |
| `github_deploy_action_move.sh` | CI helper: unpack + redeploy the `action_move` service from a release archive. |
| `github_deploy_camera_snapshot.sh` | CI helper: unpack + redeploy the `camera_snapshot` service from a release archive. |
| `deploy_tencent.ps1` | Manual PowerShell deploy of the full platform to the Tencent host. |
| `deploy_camera_snapshot.ps1` | Manual PowerShell deploy of only the `camera_snapshot` service. |

The primary deploy path is the Actions workflow; prefer it over the manual
PowerShell scripts unless CI is unavailable. See [../DEPLOY.md](../DEPLOY.md).
