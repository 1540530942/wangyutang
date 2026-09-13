# 2026-09-09 · Spark 主 LLM 吞吐复测：qwen3.8-27b-nvfp4 vs qwen3.6-35b-a3b-nvfp4

## 背景

用户要求实测对比当前生产模型 `qwen3.8-27b-nvfp4`（served-name 仍是旧的 `qwen3.8-27b-fp8`）
和已停用但保留的 `qwen3.6-35b-a3b-nvfp4`，如果后者更合适就切回去。

## 实测方法

`ssh archer@spark-c9a7`，直接对本机 `localhost:8000/v1/chat/completions` 发请求，
`chat_template_kwargs.enable_thinking=false`，短/中/长三档 `max_tokens=50/300/800`，
用相同的长文本提示词强制模型生成到接近上限（不被自然停止提前截断），
用 `usage.completion_tokens` 除以墙钟耗时算 tok/s。

## qwen3.8-27b-nvfp4 实测结果（当前生产模型）

| max_tokens | completion_tokens | 耗时 | tok/s |
|---|---|---|---|
| 50 | 50 | 4.48s | 11.16 |
| 300 | 300 | 26.68s | 11.24 |
| 800 | 800 | 71.34s | 11.21 |

三档高度一致，**~11.2 tok/s**，跟 2026-09-05 那次记录（45s/500 tokens ≈ 11 tok/s）吻合，
确认不是单次测量误差，是稳定的真实吞吐。

## qwen3.6-35b-a3b-nvfp4（本次未实测，容器当前停着）

未重启该容器做本次实测，避免影响正在跑的生产服务。引用历史记录（2026-07-16 测的）：
**~65-75 tok/s**（文本），图像 ~75 tok/s。

## 对比结论

| | qwen3.6-35b-a3b-nvfp4 | qwen3.8-27b-nvfp4（当前） |
|---|---|---|
| 架构 | MoE，每token激活~3B参数 | 稠密27B，每token全部参数参与 |
| 吞吐 | ~65-75 tok/s | ~11.2 tok/s（本次三档实测一致） |
| 倍数 | 基准 | 约 **1/6～1/7** |

## 当初为什么切到 qwen3.8（追溯 2026-08-18 cutover 日志）

`docs/logs/2026-08-18-qwen38-fp8-cutover.md` 记录：切换后曾因 9 轮工具调用对话导致单条消息
卡 12 分钟以上，用户当时发现问题后选择"没问题，你就用qwen3.8吧，后面会有优化的模型"——
**这是接受速度代价换新模型的临时决定，文档里没有记录 qwen3.8 相对 qwen3.6 有任何速度以外的
能力优势（准确率/指令遵循/工具调用正确率等）作为切换的实质理由**。

## 决策

用户本次明确要求"要是 qwen3.6 更合适就用 qwen3.6"。鉴于：
1. 速度差距达 6-7 倍，且是稳定复现的（本次三档实测 + 2026-09-05 单次实测 + 2026-08-18 生产事故三次独立验证一致）
2. 当初切到 qwen3.8 没有速度以外的实质理由，只是"先接受，等后续优化"的过渡决定
3. 多轮工具调用场景（hermes-gateway）此前已造成过真实生产事故（12分钟无响应）

→ **切回 qwen3.6-35b-a3b-nvfp4**，过程见后续操作记录。

## 切换执行记录

1. spark：`docker stop vllm-qwen38-27b-nvfp4` → `SERVED_MODEL_NAME=qwen3.6-35b-a3b bash ~/models/Scripts/start-qwen36-nvfp4.sh`（显式传参，避开 2026-08-18 踩过的默认值带 `-fp8` 后缀的坑）
2. 冷启动约 4-5 分钟（跟 2026-08-18 记录的规律一致，没有误判成卡死）
3. `~/.hermes/config.yaml` 两处模型名改回 `qwen3.6-35b-a3b`（备份为 `config.yaml.bak.20260909_qwen36revert`）+ `sudo systemctl restart hermes-gateway`（这步需要密码，用户本人手动执行的）
4. `hermes status` 确认 `Model: qwen3.6-35b-a3b`，`hermes chat -q` 实测 3 秒内正常回复
5. tang 上 `/root/control_platform/common_api/.env` 的 `SPARK_QWEN_MODEL` 改回 `qwen3.6-35b-a3b` + `systemctl restart common-api`（tang 是 root 直连，这步不需要额外授权）
6. 公网网关 `https://www.wangyutang.cn/common/api/llm/spark-qwen/chat/completions` 实测 200，返回 `"model":"qwen3.6-35b-a3b"`
7. 本仓库 `common_api_manager/modules/model_studio_catalog/router.py` 两处硬编码模型名同步改掉，commit+push（注意：这个仓库到 tang 的 `/root/control_platform/common_api/` 不是 CI/CD 关系，是独立的 tar 包手动部署，这次 push 只是让代码库跟运行时状态一致，不会自动生效到 tang）

## 切换后复测（qwen3.6-35b-a3b，生产环境实测）

| max_tokens | completion_tokens | 耗时 | tok/s |
|---|---|---|---|
| 50 | 50 | 0.72s | 69.6 |
| 300 | 300 | 3.94s | 76.0 |
| 800 | 800 | 10.45s | 76.5 |

跟历史记录的 65-75 tok/s 吻合，中长两档甚至略高。**实测提速 6.8 倍**（11.2 tok/s → 76 tok/s）。

## 遗留

- `vllm-qwen38-27b-nvfp4` 容器已停止但保留（镜像/权重都还在，可随时回滚）
- `common_api_manager` 仓库里其余大量 README/文档字符串仍写着 `qwen3.8-27b-fp8`（`common_api_manager/README.md`、`static/model_studio.html` 等多处），本次只改了功能性代码（`router.py` 的 `validate.model`），文档批量更新未做，后续需要时再补
