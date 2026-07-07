# Verification records

Date: 2026-07-07

## Public URL verification

| Check | Result |
|---|---|
| `GET https://www.wangyutang.cn/common/model-studio` | HTTP 200 |
| `GET https://www.wangyutang.cn/common/robot-skills` | HTTP 200 |
| `GET https://www.wangyutang.cn/common/api/model-studio/catalog` | HTTP 200, `count=8` |
| `GET https://www.wangyutang.cn/common/api/model-studio/selection` | HTTP 200, `selection.source=model_studio` |
| `GET https://www.wangyutang.cn/common/api/lab/catalog` | HTTP 404 |
| `GET https://www.wangyutang.cn/common/api/lab/selection` | HTTP 404 |

## Model validation example

Command:

```bash
curl -X POST https://www.wangyutang.cn/common/api/model-studio/validate \
  -H "Content-Type: application/json" \
  -d '{"model_id":"dashscope-qwen3-32b"}'
```

Observed result:

```text
status=200
ok=True
verified=True
elapsed_ms=353
output=5
```

## audio_recognition integration

Health check confirmed that audio routing reads model selection from the new
Model Studio API:

```text
GET https://www.wangyutang.cn/audio/api/health
status=200
router.model_selection.source_url=https://www.wangyutang.cn/common/api/model-studio/selection
router.model_selection.selection.source=model_studio
selection_error=
```

Simulation command:

```bash
curl -X POST https://www.wangyutang.cn/audio/api/simulate/text \
  -H "Content-Type: application/json" \
  -d '{"device_id":"codex-verify","text":"\u524d\u8fdb","source":"codex-verification"}'
```

Observed result:

```text
status=200
ok=True
simulation.dispatch=disabled
skill.id=move_forward
plan.skill_id=move_forward
final_response=dry_run
```

## Robot Skills live verification examples

Front distance task:

```bash
curl -X POST https://www.wangyutang.cn/action/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"action":"front_distance","source":"robot_skills_verify","ttl_seconds":60}'
```

Observed result:

```text
task_id=1783361912165-7bd5f6
status=complete
device=turbopi-01
front_distance_estimate_cm=17.0
raw_mm_samples=173,173,173,170,173,173,173
confidence=1.0
transport=persistent_ros_controller
```

Camera capture task:

```bash
curl -X POST https://www.wangyutang.cn/camera/api/capture \
  -H "Content-Type: application/json" \
  -d '{"kind":"camera","mode":"single","query_gpio":26}'
```

Observed result:

```text
task_id=1783361914204-295024
frame=53
has_image=True
capture_source=web-video-server
content_length=52568
```

## Post-push deployment verification

Branch:

```text
feature/llm-manager
```

Public checks after pushing `feature/llm-manager`:

| Check | Result |
|---|---|
| `GET https://www.wangyutang.cn/common/model-studio` | HTTP 200, `len=9344` |
| `GET https://www.wangyutang.cn/common/robot-skills` | HTTP 200, `len=3816` |
| `GET https://www.wangyutang.cn/common/api/model-studio/catalog` | HTTP 200, `len=2262`, `count=8` |
| `GET https://www.wangyutang.cn/common/api/model-studio/selection` | HTTP 200, `selection.source=model_studio` |
| `GET https://www.wangyutang.cn/common/api/lab/selection` | HTTP 404 |
| `GET https://www.wangyutang.cn/audio/api/health` | HTTP 200, `status=ok` |
| `GET https://www.wangyutang.cn/action/api/health` | HTTP 200 |

Model Studio validation after deployment:

```text
POST /common/api/model-studio/validate {"model_id":"dashscope-qwen3-32b"}
ok=True
verified=True
output=5
elapsed_ms=468
```

Audio integration after deployment:

```text
audio.model_selection.source_url=https://www.wangyutang.cn/common/api/model-studio/selection
audio.model_selection.source=model_studio
audio.selection_error=
```

Robot Skills live verification after deployment:

```text
front_distance.task_id=1783362686259-902da6
front_distance.status=complete
front_distance.device_id=turbopi-01
front_distance_estimate_cm=17.0
raw_mm_samples=170,170,170,170,170,170,170
confidence=1.0
transport=persistent_ros_controller
```

Camera capture after deployment:

```text
camera.task_id=1783362688029-865306
camera.has_image=True
camera.frame=54
camera.capture_source=web-video-server
camera.content_length=52205
```

## Goal audit verification

Date: 2026-07-07, branch `feature/llm-manager`, commit `13d0760`.

Current public checks:

| Check | Result |
|---|---|
| `GET https://www.wangyutang.cn/common/model-studio` | HTTP 200, `len=9269` |
| `GET https://www.wangyutang.cn/common/robot-skills` | HTTP 200, `len=3816` |
| `GET https://www.wangyutang.cn/common/api/model-studio/catalog` | HTTP 200, `count=8` |
| `GET https://www.wangyutang.cn/common/api/model-studio/selection` | HTTP 200, `selection.source=model_studio` |
| `GET https://www.wangyutang.cn/common/api/lab/selection` | HTTP 404 |
| `GET https://www.wangyutang.cn/audio/api/health` | HTTP 200, `status=ok` |
| `GET https://www.wangyutang.cn/action/api/health` | HTTP 200 |
| `GET https://www.wangyutang.cn/camera/api/health` | HTTP 200 |

Model Studio validation:

```text
POST /common/api/model-studio/validate {"model_id":"dashscope-qwen3-32b"}
ok=True
verified=True
output=5
elapsed_ms=1018
```

Audio integration:

```text
audio.model_selection.source_url=https://www.wangyutang.cn/common/api/model-studio/selection
audio.model_selection.selection.source=model_studio
audio.selection_error=
```

Robot Skills sonar verification:

```text
front_distance.task_id=1783387775254-925491
front_distance.status=complete
front_distance.device_id=turbopi-01
front_distance_estimate_cm=23.5
raw_mm_samples=236,236,236,236,236,235,235
confidence=1.0
transport=persistent_ros_controller
```

Camera capture verification:

```text
camera.task_id=1783387776780-163df7
camera.has_image=True
camera.frame=15
camera.capture_source=web-video-server
camera.content_length=57598
```

Full module regression audit:

```text
action_move                PASS   7 passed, 1 skipped
audio_interact             PASS   5 passed
audio_recognition          PASS   65 passed, 1 warning, 33 subtests passed
camera_snapshot            PASS   15 passed
common_api_manager         PASS   17 passed
pi5_robot                  PASS   5 passed
slam_mapping               PASS   3 passed
slam_mapping/2d_action     PASS   6 passed
smoke                      PASS   56 passed, 2 warnings
pose_tracker               PASS   8 passed
simulation                 PASS   15 passed
loop_engineering           PASS   11 passed
pi5_monitor                PASS   12 passed
smile_face                 PASS   11 passed, 6 warnings
robot_gateway              PASS   5 passed
common_sense               PASS   4 passed
```

The raw pytest output for this audit is archived under `test_results/*/pytest_output.txt`.

## Local regression checks

Command:

```bash
python -m unittest common_api_manager.tests.test_model_studio_selection audio_recognition.tests.unit.test_case_store -v
```

Observed result:

```text
5 tests OK
```

JavaScript parse checks passed for:

```text
common_api_manager/static/robot_skills.js
common_api_manager/static/model_studio.js
common_api_manager/static/model_studio_catalog.js
```
