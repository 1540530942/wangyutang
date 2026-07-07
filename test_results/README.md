# test_results

Archived, **real** test output for every engineering module. One subdirectory
per module; each holds `pytest_output.txt` — the verbatim `pytest -v` run
captured by [`scripts/run_all_module_tests.sh`](../scripts/run_all_module_tests.sh).

Regenerate everything with:

```bash
bash scripts/run_all_module_tests.sh
```

Tests run from **each module's own directory** because the suites import
sibling modules by bare name (e.g. `import action_move_executor`). The runner
handles the correct CWD per module.

## Latest run — 2026-07-07 (all green)

| Module | Result | Cases | What it verifies | Benefit |
|---|---|---|---|---|
| `action_move` | PASS | 7 (+1 skip) | Default unit distance = 10 cm, SLAM pose reporting | Motion API keeps calibrated step size + telemetry |
| `audio_interact` | PASS | 5 | Wake-state machine (sleep/wake/timeout) | Voice front-end only acts when actually woken |
| `robot_sandbox` | PASS | 65 (+33 sub) | ReAct pipeline, safety guard, legacy planners, case store, regression suite | robot_sandbox plans/executes/rejects correctly |
| `camera_snapshot` | PASS | 15 | Capture/latest/control smoke paths | Camera service contract stays stable |
| `common_api_manager` | PASS | 17 | Model Studio selection + smoke | Public workbench + ASR/TTS API stay wired |
| `pi5_robot` | PASS | 5 | Harness executor + robot safety | Patrol MVP won't dispatch unsafe actions |
| `slam_mapping` | PASS | 3 | SLAM core pose/grid math | Mapping feedback stays correct |
| `slam_mapping/2d_action` | PASS | 6 | Closed-loop 2D action control | Move→observe→correct loop holds |
| `smoke` | PASS | 56 | Skill routing + schema, no LLM | Core routing regressions caught cheaply/offline |
| `pose_tracker` | PASS | 8 | IMU quaternion→yaw, cmd_vel dead-reckoning, dt clamp, reset/trail | Pose estimate math is provably correct |
| `simulation` | PASS | 15 | RobotState physics: turn/move/clamp/gimbal/RGB/ray-cast | HW-free sim faithfully mirrors robot behaviour |
| `loop_engineering` | PASS | 11 | Evaluator scoring weights (plan/exec/obs) + sonar obs check | Eval harness scores runs honestly |
| `pi5_monitor` | PASS | 12 | Shutdown-snapshot + event analysis (sudo/OOM/undervoltage/network) | Disconnect forensics identify the real cause |
| `smile_face` | PASS | 11 | Render math + real Pillow draw; emotion/style fallback | Expression frames actually reflect emotion |
| `robot_gateway` | PASS | 5 | Caddyfile has every service route→upstream; static site present | A bad gateway edit fails CI, not prod |
| `common_sense` | PASS | 4 | Referenced docs exist; topology lists core services | Reference docs can't silently rot |

**Total: 245 passed, 1 skipped, 33 subtests across 16 suites.**

## Honesty notes

- Every case asserts against hand-verifiable behaviour or a real render/parse —
  no fabricated fixtures or hard-coded "pass" badges.
- `pose_tracker` cmd_vel tests inject `_last_vel_t` to fix `dt`; a few
  microseconds of real wall-clock ride on top, so those two assertions use
  `abs_tol=1e-3` (sub-millimetre) rather than bit-exactness. This was found by
  a genuine test failure, not assumed.
- `pi5_monitor` undervoltage fixture uses the real hyphenated kernel string
  `Under-voltage detected!` — also corrected after a real failure.
- `robot_gateway` / `common_sense` ship no service code; their suites are
  config/doc-integrity guards (the honest form of "test" for those modules).
  If the `caddy` binary is present, `caddy validate` is a stronger gateway check.

## Per-module raw output

See `test_results/<module>/pytest_output.txt` for the full verbatim run,
including the exact command, CWD, timestamp, and every test id.
