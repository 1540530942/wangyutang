# Public workbench architecture

`common_api_manager` should be treated as the public workbench shell, not as a
single feature module.

```text
browser
  -> https://www.wangyutang.cn/common/<area>
  -> common_api_manager/app.py
  -> static page and/or modules/<area> router
  -> downstream service when needed
```

Current responsibilities:

| Layer | Responsibility | Example |
|---|---|---|
| Public page | Stable user-facing workbench URL | `/common/model-studio`, `/common/robot-skills` |
| Common API | Stable platform API wrapper | `/common/api/model-studio/selection` |
| Feature adapter | Hide provider/runtime differences | `modules/model_studio_catalog/router.py` |
| Runtime module | Execute the real domain action | `action_move`, camera service, LV/Spark/DashScope services |

The key boundary is:

```text
common_api_manager = public workspace, API adapter, UI entry
action_move        = robot atomic skill definition and execution
audio_recognition  = voice recognition and intent routing
camera service     = image capture and frame metadata
```

For Robot Skills, `common_api_manager` should not duplicate robot execution
logic. It exposes the workbench page and calls the existing action/camera public
APIs. The actual atomic skills remain defined and executed by `action_move`.

For Model Studio, `common_api_manager` owns both the page and the backend
selection/catalog APIs because model selection is a common platform concern used
by other modules such as `audio_recognition`.

Naming convention:

| URL segment | File/module segment | Python package style |
|---|---|---|
| `model-studio` | `model_studio` | `model_studio_catalog` |
| `robot-skills` | `robot_skills` | add a package only when backend state/API is needed |

Do not add compatibility routes for old names unless a migration window is
explicitly requested. The current decision is no old public routes.
