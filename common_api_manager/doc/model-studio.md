# Model Studio

Public page:

```text
https://www.wangyutang.cn/common/model-studio
```

Backend APIs:

```text
GET  /common/api/model-studio/catalog
GET  /common/api/model-studio/selection
POST /common/api/model-studio/selection
POST /common/api/model-studio/validate
```

Local code:

```text
common_api_manager/
  app.py
  modules/model_studio_catalog/
    router.py
  static/
    model_studio.html
    model_studio.css
    model_studio.js
    model_studio_catalog.js
  tests/
    test_model_studio_selection.py
```

Selection file:

```text
common_api_manager/data/model_studio_selection.json
```

The previous data file name was `lab_model_selection.json`. New code reads and
writes the new file name, and returned selections use:

```json
{
  "source": "model_studio"
}
```

Example calls:

```bash
curl https://www.wangyutang.cn/common/api/model-studio/catalog
```

```bash
curl https://www.wangyutang.cn/common/api/model-studio/selection
```

```bash
curl -X POST https://www.wangyutang.cn/common/api/model-studio/validate \
  -H "Content-Type: application/json" \
  -d '{"model_id":"dashscope-qwen3-32b"}'
```

Downstream dependency:

`robot_sandbox` reads the selected model from:

```text
https://www.wangyutang.cn/common/api/model-studio/selection
```

Environment override:

```text
AUDIO_COMMON_MODEL_STUDIO_SELECTION_URL
```

Old names intentionally removed:

```text
/common/lab
/common/api/lab/*
COMMON_LAB_SELECTION_URL
AUDIO_COMMON_LAB_SELECTION_URL
```
