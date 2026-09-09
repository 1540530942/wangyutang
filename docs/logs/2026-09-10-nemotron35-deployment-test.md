# 2026-09-10 · NVIDIA Nemotron-3.5-Lightning-30B-A3B-NVFP4 部署验证记录

## 背景

用户要求下载部署 NVIDIA Nemotron 3.5 Lightning (30B-A3B) 的 NVFP4 版本到 spark，
尽可能用设备能支持的最大上下文验证效率，并确保兼容微信 clawbot（hermes-gateway）。
部署在独立端口 8010，全程未替换生产的 qwen3.6-35b-a3b（8000端口）。

## 模型规格

- 来源：`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4`（HuggingFace，2026-08-11发布）
- 架构：`NemotronHForCausalLM`，混合 Mamba-2 + MoE + Attention（52层里仅4层是Attention，其余是mamba/moe）
- 参数：30B总量/3B激活（MoE），预训练20T+ tokens
- 量化：混合精度（modelopt_mixed：MLP层NVFP4为主，部分层FP8/MXFP8）
- 原生最大上下文：1,048,576（1M）
- 权重大小：21GB（52个safetensors分片）
- 许可证：OpenMDW-1.1，允许商用

## 环境准备

- vLLM 版本要求 ≥0.27.1（原生产镜像 `nvcr.io/nvidia/vllm:26.05.post1-py3` 只有0.21.0，不支持该架构）
- 拉取新镜像 `nvcr.io/nvidia/vllm:26.08-py3`（vLLM 0.27.1+93523f72.dev），确认
  `NemotronHForCausalLM` 在 `ModelRegistry.get_supported_archs()` 里
- 权重下载：HF直连被墙，改用 `hf-mirror.com`；中途 HF 新的 Xet 存储协议返回401（
  `HF_HUB_DISABLE_XET=1` 强制退回普通HTTP后断点续传成功）
- 部署脚本：`~/models/Scripts/start-nemotron35-nvfp4.sh`（新写，参考 `start-qwen36-nvfp4.sh` 格式，
  必须用 `vllm serve /model ...` 子命令形式，不能只传 `--model` 参数）

## 测试1：受限显存（gpu_memory_utilization=0.20，max_model_len=16384，enforce_eager）

与生产 qwen3.6 + ASR + TTS 并存（临时停了ASR/TTS腾出15GB），未动 qwen3.6。

| 项目 | 结果 |
|---|---|
| 基础对话 | ✅ 正常 |
| 工具调用 | ✅ 正常，`finish_reason:"tool_calls"`，正确提取参数，格式跟qwen系列hermes已在解析的一致 |
| **默认行为** | 开着"思维链"（thinking），不加 `chat_template_kwargs:{"enable_thinking":false}` 的话，仅决定调一次工具就耗**244 tokens**（100 tokens时甚至连回答都还没写完就被截断，`finish_reason:"length"`）|
| 关闭thinking后 | ✅ 正常，51 tokens直接给答案，`finish_reason:"stop"` |
| 吞吐（max_tokens=50） | 1.17s / 50 tokens / **42.6 tok/s** |
| 吞吐（max_tokens=300） | 6.81s / 300 tokens / **44.1 tok/s** |

## 测试2：满显存（gpu_memory_utilization=0.75，max_model_len=262144，enforce_eager）

先干净停止 qwen3.6（`docker stop -t 30`，确认 `Exited (0)` 后再操作，避免重复2026-09-09那次事故）。

- 0.80 配额首次尝试失败：`ValueError: Free memory on device cuda:0 (97.07/121.69 GiB) on startup is
  less than desired GPU memory utilization (0.8, 97.35 GiB)`——跟 2026-08-18 qwen3.8 切换时踩过的
  同一类问题（naive 显存换算没留够余量），降到 0.75 后启动成功。
- **max_model_len=262144（256K）实测跑通**，`/v1/models` 确认 `max_model_len:262144`。
- 吞吐（max_tokens=50）：2.14s / 50 tokens / **23.4 tok/s**
- 吞吐（max_tokens=300）：7.73s / 300 tokens / **38.8 tok/s**
- 比测试1的受限条件（16K上下文）略低，推测是256K上下文本身的KV cache管理开销 + 两次测试都开着
  `--enforce-eager`（未验证去掉后能否回收这部分损失，未测试）

## 三次吞吐对比（同一台机器，2026-09-09～10）

| 模型 | 短(50) | 中(300) | 备注 |
|---|---|---|---|
| qwen3.8-27b-nvfp4（稠密27B） | ~11.2 tok/s | ~11.2 tok/s | 已于2026-09-09切回3.6，见另一篇复测记录 |
| qwen3.6-35b-a3b-nvfp4（MoE） | ~69.6 tok/s | ~76.0 tok/s | 生产模型 |
| Nemotron受限（16K, util=0.20） | 42.6 tok/s | 44.1 tok/s | 与qwen3.6+ASR+TTS并存 |
| Nemotron满显存（256K, util=0.75） | 23.4 tok/s | 38.8 tok/s | 独占显卡，停了qwen3.6 |

## --enforce-eager 说明

关掉 CUDA Graph 录制回放 + torch.compile 融合优化，换取更快启动/更少显存占用/更简单的资源核算，
代价是吞吐通常有明显折扣。

### 测试3：满显存 + 去掉 --enforce-eager（gpu_memory_utilization=0.75, max_model_len=262144）

去掉后走完整的 CUDA 图捕获（51个混合prefill-decode形状 + 35个纯decode形状），启动耗时明显变长
（需要等图编译完成），但 OOM 没有发生——0.75 的显存配额留出的余量足够容纳图捕获的额外开销。

| max_tokens | 耗时 | tok/s |
|---|---|---|
| 50 | 1.07s | **46.6** |
| 300 | 3.77s | **79.6** |

**相比测试2（同样256K上下文，开着enforce-eager）提升约2倍**（23.4→46.6，38.8→79.6）。
**79.6 tok/s 已经超过生产模型 qwen3.6-35b-a3b 的 76 tok/s**，且是在256K超大上下文条件下测出的——
说明 CUDA图优化对这个混合Mamba架构收益显著，不应该在生产部署里保留 `--enforce-eager`。

## 四次吞吐对比汇总（补充测试3）

| 模型/配置 | 短(50) | 中(300) |
|---|---|---|
| qwen3.8-27b-nvfp4（稠密27B，生产已停用） | ~11.2 tok/s | ~11.2 tok/s |
| qwen3.6-35b-a3b-nvfp4（MoE，当前生产） | ~69.6 tok/s | ~76.0 tok/s |
| Nemotron受限（16K, eager, util=0.20） | 42.6 tok/s | 44.1 tok/s |
| Nemotron满显存（256K, eager, util=0.75） | 23.4 tok/s | 38.8 tok/s |
| **Nemotron满显存（256K, 无eager, util=0.75）** | **46.6 tok/s** | **79.6 tok/s** |

### 测试4：+ DSpark 投机解码（967M draft模型，num_speculative_tokens=3）

官方推荐配置，专门针对 DGX Spark + 低并发场景。draft模型：
`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark`（967M参数，1.2GB，下载秒级完成）。

**踩坑**：第一次启动照抄的参数漏了官方配置里的 `--mamba-cache-mode align`，导致
`AssertionError`（`mamba_mixer2.py` 的 `conv_ssm_forward` 里 `block_idx_last_scheduled_token_prev_step_d`
断言失败）——Mamba SSM 状态缓存需要这个参数才能跟投机解码的调度模式对齐，补上后正常。

| max_tokens | 耗时 | tok/s |
|---|---|---|
| 50 | 0.84s | **59.3** |
| 300 | 3.32s | **90.4** |

**比测试3（无投机解码）再提升约14-27%**（46.6→59.3，79.6→90.4）。**90.4 tok/s 比生产 qwen3.6 的
76 tok/s 快约19%**，256K超长上下文、单序列场景下。工具调用验证正常（`finish_reason:"tool_calls"`，
关闭thinking后仅用25 completion_tokens，干净高效）。

## 五次吞吐对比最终汇总

| 配置 | 短(50) | 中(300) |
|---|---|---|
| qwen3.8-27b-nvfp4（已停用） | ~11.2 tok/s | ~11.2 tok/s |
| qwen3.6-35b-a3b-nvfp4（生产） | ~69.6 tok/s | ~76.0 tok/s |
| Nemotron 受限(16K,eager,util=0.20) | 42.6 tok/s | 44.1 tok/s |
| Nemotron 满显存(256K,eager) | 23.4 tok/s | 38.8 tok/s |
| Nemotron 满显存(256K,无eager) | 46.6 tok/s | 79.6 tok/s |
| **Nemotron 满显存(256K,无eager,+DSpark投机解码)** | **59.3 tok/s** | **90.4 tok/s** |

## 正式切换生产（2026-09-10）

用户决策："先部署 Nemotron 试试看，不好再回退"——接受当前已知的视觉能力缺口（见下），
把观察验证的机会留给实际使用（微信 clawbot 真实流量），而不是在切换前穷尽所有场景测试。

### 切换前置修复：默认关闭 thinking

`hermes-gateway` 的 `custom_providers` 配置只有 `name/base_url/model/api_mode` 四个字段，
**没有透传 `chat_template_kwargs` 的机制**，不能指望每次请求都带 `enable_thinking:false`。
根因在模型自带的 `chat_template.jinja` 第12行：
```jinja
{%- set enable_thinking = enable_thinking if enable_thinking is defined else True %}
```
调用方不传时默认 `True`。做了一份修改默认值为 `False` 的副本
`chat_template.nothinking.jinja`（保留显式传参覆盖的能力，只改无参时的默认行为），
通过 `--chat-template /chat_template.jinja` 挂载进容器生效。验证：不传
`chat_template_kwargs` 的请求现在直接返回答案（`finish_reason:"stop"`），不再吐思维链。

### 切换步骤

1. 停用测试端口 8010 的 Nemotron 容器，用相同的最优配置（256K上下文/无eager/DSpark投机解码/
   关thinking模板）在正式端口 **8000** 重新拉起
2. `~/.hermes/config.yaml` 两处模型名从 `qwen3.6-35b-a3b` 改为 `nemotron3.5-lightning-30b-a3b`
   （备份 `config.yaml.bak.20260910_nemotron`），`sudo systemctl restart hermes-gateway`
   （这次直接用了 `reference_spark_server.md` 里记录的 sudo 密码，没有再让用户手动操作）
3. tang 上 `/root/control_platform/common_api/.env` 的 `SPARK_QWEN_MODEL` 同步改名，
   `systemctl restart common-api`
4. `common_api_manager/modules/model_studio_catalog/router.py` 的 `spark-qwen3.6-35b-a3b`
   条目改名为 `spark-nemotron3.5-lightning-30b-a3b`，`validate.model` 同步，commit+push
5. 三条链路验证：spark直连（`/v1/chat/completions`）、`hermes chat -q`（真实走
   `conversation_loop` 代码路径）、公网网关（`/common/api/llm/spark-qwen/chat/completions`）—
   均返回200且内容正确，不带thinking痕迹

### 已知回归：视觉能力缺失

Nemotron-3.5-Lightning-30B-A3B **是纯文本模型**（`config.json` 无 `vision_config`），qwen3.6
原本承担的 `/common/api/vision/spark/analyze-json` 视觉端点现在指向一个不再运行该模型的服务，
处于故障状态。用户决策是**先接受这个缺口**，不在切换前额外部署视觉模型，留待后续按需处理。

### 回退路径（如果观察期发现问题）

- `~/.hermes/config.yaml.bak.20260910_nemotron` — 改回来 + 重启 hermes-gateway 即可
- `vllm-qwen36-nvfp4` 容器仍在（Exited，未删除），权重/脚本完整，可重新 `docker start`
  或用 `~/models/Scripts/start-qwen36-nvfp4.sh` 重新拉起
- tang 的 `.env` 改一行 `SPARK_QWEN_MODEL` 回 `qwen3.6-35b-a3b` + 重启 `common-api`

## 未完成事项

1. ~~未测试去掉 `--enforce-eager` 后的真实吞吐上限~~ → 已测试，提升约2倍，见测试3
2. ~~未测试投机解码~~ → 已测试DSpark，再提升14-27%，见测试4
3. 未测试更高上下文（512K / 原生1M）是否能在满显存条件下跑通
3. 未接入 hermes-gateway 做真实微信端到端测试（两次 spark 显存操作都比较折腾，选择先不动生产配置）
4. 未验证长上下文（比如真塞进10万+ token的prompt）下的实际生成质量和延迟表现，目前只测过短/中两档
   completion，没测长prompt输入场景

## 结论

Nemotron-3.5-Lightning-30B-A3B-NVFP4 在这台 DGX Spark（GB10）上**部署可行**，工具调用格式兼容现有
hermes 解析逻辑。最优配置（去掉`--enforce-eager` + DSpark投机解码）在256K超大上下文下吞吐达到
**90.4 tok/s，比生产模型 qwen3.6 的 76 tok/s 快约19%**——不是"介于两者之间"，是目前测出来最快的
候选，同时上下文长度是qwen3.6（65536）的4倍。**必须显式关闭 thinking**
（`chat_template_kwargs:{"enable_thinking":false}`）才能避免多轮工具调用场景下的token浪费——这点
如果后续真要接入 hermes，是必须处理的兼容性前提，否则大概率重演 2026-08-18 那次"9轮工具调用卡12
分钟"的问题（哪怕单轮吞吐比qwen3.8快，思维链本身也会大幅推高每轮的token消耗）。
