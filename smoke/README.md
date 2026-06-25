# smoke

必过功能集 smoke 测试，验证技能路由的核心路径在不需要 LLM 调用的情况下稳定通过。

## 测试分组

| 测试类 | 文件 | 说明 |
|---|---|---|
| `TestExactAliasRouting` | `test_routing.py` | exact-alias 快速路径绕过 LLM，验证常用语音指令直接命中正确技能 |
| `TestRegistryRouteSmoke` | `test_routing.py` | skill_registry 中每个技能的 route 字段正确 |
| `TestDispatchEnvelopeFormat` | `test_routing.py` | dispatch envelope 格式合法（用 mock LLM，不调真实接口） |
| `TestToolValidatorSmoke` | `test_routing.py` | validate_tool_call 对合法调用不拒绝 |
| `TestSchemaSmoke` | `test_schema.py` | skill_catalog.json 结构校验，每条技能必填字段完整 |

## 文件

| 文件 | 说明 |
|---|---|
| `cases.py` | `SkillCase` 数据类 + 所有技能的预期测试向量（transcript → skill_id, tool, route） |
| `conftest.py` | pytest fixtures（加载 skill_catalog、初始化 registry 等） |
| `test_routing.py` | 路由逻辑测试 |
| `test_schema.py` | schema 结构测试 |

## 运行

```bash
# 全部 smoke 测试
pytest smoke/ -v

# 只跑 exact-alias
pytest smoke/test_routing.py::TestExactAliasRouting -v

# 在 CI 中（无 LLM token）
pytest smoke/ -v -k "not llm"
```

## 原则

- 不依赖外部 LLM API，所有 LLM 路径用 mock 替代
- 运行时间 < 5 秒
- 每次 PR 必须通过，作为合并门禁
