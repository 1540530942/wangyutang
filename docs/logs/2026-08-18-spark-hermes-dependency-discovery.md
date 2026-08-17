# 2026-08-18 · Spark 模型切换影响面排查：hermes-gateway 隐藏依赖

## 背景

计划把 spark 上 `vllm-qwen36-nvfp4`（served-model-name `qwen3.6-35b-a3b`）替换成新下载的
`Qwen3.8-27B-FP8`。切换前按惯例评估影响面：谁在调用这个模型，切换时会不会打断谁。

## 第一次评估：结论错误

排查了两处日志：

- 腾讯云 `common-api` systemd journal（7 天窗口）：spark 相关路径命中 9 次，其中 8 次是健康检查轮询，
  仅 1 次真实推理调用，且来源是 `127.0.0.1`（判断为此前自己测试 audio_convert 模块时触发）。
- 腾讯云 `robot-gateway`（Caddy）访问日志（7 天窗口）：spark/qwen3.6-35b 相关路径命中 **0 次**。

据此得出"过去 7 天基本零外部流量，切换风险很低"的结论，并据此建议直接执行切换。

## 纠正：路径不对，不是流量为零

用户提到"微信里面的 clawbot 依赖它"，且称"刚刚调用了"。按此重新排查，在
`~/.hermes/logs/agent.log`（spark 本机文件，不在 systemd journal 里，也不在本仓库任何位置）中找到了
实时证据：

```
2026-08-18 01:06:41  inbound message: platform=weixin ... msg='你在哪里呢'
2026-08-18 01:06:55  API call #6: model=qwen3.6-35b-a3b provider=custom ... latency=14.1s
2026-08-18 01:06:55  response ready: platform=weixin ... response=228 chars
```

同一小时内还有另外两轮真实对话（00:16、00:26-00:27），共 6 次 API 调用，全部直连
`http://127.0.0.1:8000/v1`。vLLM 自带的 Prometheus 计数器（`/metrics`）也印证了这一点：

```
vllm:request_success_total = 8   (6 stop + 2 length)
vllm:prompt_tokens_total   = 157502
vllm:generation_tokens_total = 2968
```

**根因**：`hermes-gateway.service`（`~/.hermes/`，独立于本仓库的系统，`Hermes Agent Gateway -
Messaging Platform Integration`）以 OpenAI 兼容客户端身份，直接在 spark 本机调用
`http://127.0.0.1:8000/v1`，完全不经过腾讯云的 Caddy 网关或 common-api，所以两份公网侧日志天生看不到
这条流量。此前"7 天零流量"的判断只覆盖了到达腾讯云的调用，遗漏了 spark 本机的直连调用方。

## 完整影响面（修正后）

| 消费方 | 路径 | 真实活跃度 | 模型名写在哪 |
|---|---|---|---|
| **hermes-gateway（微信 clawbot）** | spark 本机直连 `127.0.0.1:8000` | **确认活跃** — 排查当时 1 小时内 3 轮真实对话 | `~/.hermes/config.yaml`：`model.default`（第 2 行）+ `custom_providers[].model`（第 558-561 行），均硬编码 `qwen3.6-35b-a3b` |
| hermes-gateway（钉钉） | 同上 | 配置存在（`pairing/dingtalk-approved.json`），排查当时未见实际调用记录 | 同上，与微信共用同一份 `model.default` |
| `common_api_manager` spark_qwen_chat/vision | 腾讯云网关 → 隧道 → spark:8000 | 低（7天内约1次，疑似自测） | `SPARK_QWEN_MODEL` 环境变量（腾讯云 `.env`） |
| `robot_sandbox` 视觉默认路径 | 同上 | 代码硬编码默认值，未观测实际调用 | `VISION_LLM_MODEL` 环境变量默认值 |
| Model Studio 页面 | 同上 | 可选项，非默认 | 前端 JS 里的模型下拉选项 |

## 结论与后续动作

1. 换 `served-model-name` 或迁移端口前，**必须同时确认 spark 本机是否有直连消费方**，不能只看
   经过腾讯云的日志。
2. `~/.hermes/config.yaml` 改完需要 `sudo systemctl restart hermes-gateway` 才生效。
3. 已把这条"同一模型两条独立调用路径"的拓扑关系写进
   [common_sense/README.md](../../common_sense/README.md) 和
   [common_sense/network_topology_remote_access.html](../../common_sense/network_topology_remote_access.html)，
   作为长期维护的参考文档，避免下次再犯同样的误判。
4. 模型切换本身尚未执行——按用户要求，暂定策略是先只切 `common_api_manager` 侧，
   `hermes-gateway` 保持指向旧模型，待新模型效果在别处验证满意后再单独切换，
   两个 vLLM 实例（旧 NVFP4 / 新 FP8）短期共存。
