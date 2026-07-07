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

## Latest run — 2026-07-08 (all green)

Cases now include per-module JSON-driven data (`tests/data/*.json`, recording
input + expected output) added across every module.

| Module | Result | Cases | JSON data | What it verifies |
|---|---|---|---|---|
| `action_move` | PASS | 21 (+1 skip) | executor_cases.json (14) | clamp, unit_duration timing, velocity_scale, cmd_vel topics + defaults |
| `audio_interact` | PASS | 13 | wake_cases.json (8) | wake-state machine + wake-word homophones |
| `robot_sandbox` | PASS | 65 (+33 sub) | (inline) | ReAct pipeline, safety guard, planners, case store, regression |
| `camera_snapshot` | PASS | 43 | server_pure_cases.json (28) | kind/mode/gpio normalization, throttle bitmask, capture smoke |
| `common_api_manager` | PASS | 17 | (selection json) | Model Studio selection + smoke |
| `pi5_robot` | PASS | 11 | robot_safety_cases.json (6) | move/rotate limits, obstacle-blocks-forward |
| `slam_mapping` | PASS | 7 | slam_cases.json (4) | dead-reckoning pose + occupancy-grid scan |
| `slam_mapping/2d_action` | PASS | 6 | (inline) | closed-loop 2D action control |
| `smoke` | PASS | 76 | skill_routing_cases.json (19) | skill routing + schema, registry drift, no LLM |
| `pose_tracker` | PASS | 14 | pose_cases.json (6) | IMU yaw, cmd_vel dead-reckoning, dt clamp, reset |
| `simulation` | PASS | 26 | state_cases.json (11) | RobotState physics: turn/move/clamp/gimbal/RGB/ray-cast |
| `loop_engineering` | PASS | 19 | evaluator_cases.json (8) | evaluator scoring weights + sonar obs check |
| `pi5_monitor` | PASS | 21 | analyzer_cases.json (9) | shutdown-snapshot + event/summary analysis |
| `smile_face` | PASS | 26 | render_cases.json (15) | render math + real Pillow draw; emotion/style |
| `robot_gateway` | PASS | 15 | route_cases.json (10) | route→upstream contract, retired routes absent |
| `common_sense` | PASS | 10 | doc_cases.json (6) | referenced-doc integrity + README service tokens |

**Total: 390 passed, 1 skipped, 33 subtests across 16 suites (~148 of them
JSON data-driven cases).**

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
