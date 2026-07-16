# docs

Cross-cutting documentation that does not belong to a single service. Per-module
docs live under each module (e.g. `robot_sandbox/docs/`, `pi5_robot/docs/`,
`common_api_manager/doc/`).

## Design & plans

- [audio-interact-robot-sandbox-design.md](audio-interact-robot-sandbox-design.md) — split architecture of the voice pipeline
- [audio-interact-robot-sandbox-implementation-plan.md](audio-interact-robot-sandbox-implementation-plan.md) — P1–P5 plan with acceptance criteria
- [audio-interact-golden-sessions.md](audio-interact-golden-sessions.md) — session-shaped golden data, dashboard compatibility, and layered CI guards
- [test-cases-and-expected-results.md](test-cases-and-expected-results.md) — verified test cases with real results

## Migrations

- [migration-robot_sandbox-routes.md](migration-robot_sandbox-routes.md) — audio_recognition→robot_sandbox rename + `/audio`,`/interact` route retirement (2026-07)

## Audits & reviews

- [project-review-2026-07-16.md](project-review-2026-07-16.md) — full-project module review: mission, strengths, issues (P0–P2), and 3-phase evolution plan
- [module-compliance-audit.md](module-compliance-audit.md) — module merge-criteria compliance
- [module-functional-review-2026-05-26.md](module-functional-review-2026-05-26.md) — cross-module functional review
- [pitfalls-and-fixes.md](pitfalls-and-fixes.md) — recurring pitfalls and their fixes

## Runbooks

- [git-push-remote-branch.md](git-push-remote-branch.md) — safe push runbook for this repo
- [github-push-troubleshooting.md](github-push-troubleshooting.md) — git push failure recovery
- [www-wangyutang-cn-fix.md](www-wangyutang-cn-fix.md) — public gateway domain fix

## Module-specific notes

- `action_move/` — latency analysis and follow-ups
- `camera_snapshot/` — screenshot diagnosis and three-channel refactor notes

## Time-ordered records

- `logs/` — dated engineering logs
- `incidents/` — incident post-mortems
- `verification/` — verification captures

## Test results

Automated per-module test results are stored under `test_results/` at the repo
root, one subdirectory per module. See [../test_results/README.md](../test_results/README.md).
