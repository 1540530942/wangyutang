# 2026-08-18 · Qwen3.8-27B-FP8 切换 spark 主 LLM 服务

## 背景

替换 spark 上 `vllm-qwen36-nvfp4`（`qwen3.6-35b-a3b`）为 `Qwen3.8-27B-FP8`（`qwen3.8-27b-fp8`）。
影响面排查见 [2026-08-18-spark-hermes-dependency-discovery.md](2026-08-18-spark-hermes-dependency-discovery.md)。

## 部署前置检查（均通过）

- vLLM 0.21.0（容器内已装）的 `ModelRegistry.get_supported_archs()` 包含
  `Qwen3_5ForConditionalGeneration`（新模型 `config.json` 里的 `architectures`），**不需要升级镜像**。
- 权重下载：ModelScope 源，82 个文件，29GB，逐文件字节校验，0 失败。

## 遇到的问题 1：`gpu-memory-utilization=0.85` 导致启动失败

**现象**：
```
ValueError: Free memory on device cuda:0 (93.91/121.69 GiB) on startup is less than
desired GPU memory utilization (0.85, 103.44 GiB).
```
`cutover_qwen38.sh` 里设的 180 秒就绪超时触发，自动回滚到旧模型（回滚脚本自身设计的兜底逻辑，
21 秒内旧模型容器重新拉起，未造成额外风险）。

**根因**：把 `gpu-memory-utilization` 从旧模型的 `0.72` 提到 `0.85`，是按"停掉旧容器后 121GB 整机独占"
计算的，没算上：
- TTS（`qwen3-tts-server`）、ASR（`vllm-qwen3-asr`）两个常驻容器的显存占用
- `docker stop` 后 CUDA/驱动层不会瞬间归还全部显存，有结算延迟
- 系统本身的页缓存/常驻开销

实际可用只有 93.91GB，比 121GB 整机数字少了约 28GB。

**排除的假设**：一开始担心的是新模型的混合线性注意力（Gated DeltaNet）内核在这套镜像上跑不起来——
结果日志显示引擎已经完整走到"申请显存"这一步之前的所有阶段（架构识别、编译配置构建、NCCL 初始化
全部成功），只在最后的显存分配检查失败。**说明这是纯配置问题，不是架构兼容性问题**，找根因时不要
先入为主地怀疑最担心的那个点，日志里的失败位置才是准的。

**修复**：`gpu-memory-utilization` 回调到 `0.70`（略低于旧模型的 0.72，留更多安全边际），
同一份 `~/models/Scripts/start-qwen38-27b-fp8.sh` 直接改这一行常量，重新走 `cutover_qwen38.sh`。

## 遇到的问题 2：旧模型回滚后启动比预期慢

**现象**：回滚触发后，轮询 `/v1/models` 超过 120 秒仍无响应，一度怀疑回滚本身失败了。

**根因**：不是失败，是慢——`docker logs` 显示权重加载花了 107 秒（22GB checkpoint 在 ext4 上禁用了
prefetch，`Auto-prefetch is disabled because the filesystem (EXT4) is not a recognized network FS`），
后面 `torch.compile` 图编译又花了近 1 分钟（无热缓存的首次编译）。整个冷启动到真正 ready 大约 3-4 分钟，
比这次编排脚本里设定的 180 秒超时预期更长。

**教训**：vLLM 冷启动（尤其带 `torch.compile`、无编译缓存命中时）不是秒级的，之前的 180 秀超时是
照抄经验值，没有实测过这台机器的真实冷启动时长。后续同类脚本的就绪超时应该按"权重大小 + 编译"预留
更宽裕的窗口（建议 ≥300s），不要用一个想当然的数字。

## 遇到的问题 3：回滚脚本引入了自己的 bug——服务名对不上

**现象**：问题1触发自动回滚后，`cutover_qwen38.sh` 内部调用
`bash ~/models/Scripts/start-qwen36-nvfp4.sh` 时没有显式传 `SERVED_MODEL_NAME`，
该脚本自己的默认值是 `qwen3.6-35b-a3b-fp8`（**带 `-fp8` 后缀**），
不是生产环境实际用的 `qwen3.6-35b-a3b`（**不带后缀**，vLLM metrics 里确认过）。

**影响**：回滚"成功"了（容器起来了、`/v1/models` 有响应），但服务名是错的——
`~/.hermes/config.yaml` 里配的是不带后缀的名字，这个状态下 hermes 一旦真的发起请求会拿到
"model not found"。检查 `~/.hermes/logs/agent.log` 确认这个窗口内没有真实微信/钉钉消息，
算是运气好，没有真实用户受影响，但这本质是一次自己制造的、本可以避免的生产问题。

**教训**：编排脚本里调用另一个脚本做"回到已知状态"操作时，**必须显式传全部关键参数，
不能依赖被调脚本的默认值**——默认值是给"从零开始手动启动"场景设计的，不代表"当前生产实际配置"。
回滚逻辑本身也需要冒烟测试，不能假设"能跑起来"就等于"状态正确"。

**修复**：手动 `docker stop` 该容器，用 `SERVED_MODEL_NAME=qwen3.6-35b-a3b` 显式重启，
确认 `/v1/models` 返回值和真实推理都正确后才继续。

## 遇到的问题 4：旧模型冷启动比预期慢，一度误判为"卡住"

见上方"遇到的问题 2"，同样的规律在这次手动重启时又出现了一次——权重加载 107s +
torch.compile 图编译（`Compiling a graph for compile range (1, 8192) takes 49.73s` 等多段），
总冷启动时间 3-4 分钟。第二次遇到时已经知道这是正常现象，没有再误判，但值得强调：
**这台机器上 vLLM 冷启动没有"秒开"这回事，任何涉及重启的编排脚本都要按分钟级预留超时**。

## 最终结果：切换成功

第二次尝试（`GPU_MEMORY_UTILIZATION=0.70`，`READY_TIMEOUT=420`）：

```
[02:52:25] 停旧起新
[02:59:09] 新容器就绪（约 6:44 冷启动，权重 29GB + FP8 + torch.compile）
```

三项冒烟测试（spark 本机直连 `127.0.0.1:8000`）：

| 测试 | 结果 |
|---|---|
| 纯文本对话 | ✅ 连贯中文回复，`finish_reason: stop` |
| 工具调用 | ✅ 正确触发 `get_weather({"city":"北京"})`，`finish_reason: tool_calls` |
| 图片理解 | ⚠️→✅ 首次用 1x1 像素测试图返回"粉色"（图片本身退化导致，非模型问题），换 64x64 纯色图后正确返回"红色" |

下游消费方同步：

| 消费方 | 操作 | 验证方式 | 结果 |
|---|---|---|---|
| hermes-gateway（微信/钉钉） | `sed` 改 `~/.hermes/config.yaml` 两处 + `systemctl restart hermes-gateway` | `hermes chat -q "..."`（走网关同款 `conversation_loop` 代码路径，不打扰真实联系人） | ✅ 正确回复，vLLM 请求计数器同步 +2，0 错误 |
| `common_api_manager`（腾讯云） | 改 `.env` 的 `SPARK_QWEN_MODEL` + `systemctl restart common-api` | 公网 `https://www.wangyutang.cn/common/api/llm/spark-qwen/chat/completions` 和 `.../vision/spark/analyze-json` | ✅ chat 和 vision 均通过公网网关走通，`health` 聚合正确显示 `spark_llm: qwen3.8-27b-fp8` |

全程未观测到真实用户流量受影响（hermes 侧切换窗口内无真实消息；common_api_manager 侧对外一直是
`/v1/models` 或 `/health` 探测式流量，无正在进行的业务请求撞上切换窗口）。

旧模型（`vllm-qwen36-nvfp4`，`qwen3.6-35b-a3b`）容器已停止，权重和启动脚本保留，可随时回滚：
```bash
docker rm -f vllm-qwen38-27b-fp8
bash ~/models/Scripts/start-qwen36-nvfp4.sh
# 然后把 ~/.hermes/config.yaml 和腾讯云 .env 的模型名改回 qwen3.6-35b-a3b，各自重启
```

`~/.hermes/config.yaml.bak.20260818_qwen38cutover` 和
`/root/control_platform/common_api/.env.bak.20260818_qwen38cutover` 是切换前的配置快照。

## 后续：真实使用暴露的性能问题 + 回滚 + 重新上线（同日）

切换完成约 5 小时后，用户在微信里向 hermes-gateway（clawbot）问了一个开放性问题，触发连续 9 轮
工具调用（`session_search` / `skill_view` / `terminal`），**单条消息卡了 12 分钟以上没有任何回复**。
用户主动反馈"手机里试了下 clawbot，发现有问题"。

### 根因排查

- `docker logs vllm-qwen38-27b-fp8` 显示 vLLM 引擎日志：`Avg generation throughput: 7.1-7.3 tokens/s`。
- `nvidia-smi` 显示 GPU 利用率 96% 但功耗仅 33W——确认是显存带宽瓶颈，不是算力瓶颈，也不是请求卡死
  （`vllm:num_requests_running=1`，确实在持续生成，只是极慢）。
- 根因：`qwen3.6-35b-a3b` 名字里的 **A3B = Active 3B**，MoE 架构每 token 仅激活约 3B 参数；
  `qwen3.8-27b-fp8` 是稠密架构，每 token 全部 27B 参数参与计算。单序列（batch=1）解码场景下吞吐
  大致与激活参数量成反比，9 倍参数差距对应约 9 倍速度差距，与实测数字吻合。
- **这是本次评估的真实疏漏**：之前的三项冒烟测试都是短问答，几十个 completion_tokens，即使在
  7 tok/s 下也是秒级完成，感觉不出速度问题——验证只测了"结果对不对"，没测"生成速度"，直到真实的
  长对话+多轮工具调用才暴露出这个数量级的差距，多轮调用会把每一轮的速度劣势线性叠加。

### 第一次决策：回滚

发现问题后立即把 hermes-gateway、`common_api_manager`、Model Studio 校验配置三处全部回滚到
`qwen3.6-35b-a3b`，`docker stop vllm-qwen38-27b-fp8` 结束了那次卡住的生成。回滚过程中又踩到一个
新坑：`docker stop` 后我编辑了 `~/.hermes/config.yaml` 但忘了 `systemctl restart hermes-gateway`
让配置生效，导致用户手机上出现 "API failed after 3 retries - Connection error"（进程内存里还是
旧的 in-memory 配置，指向已经不存在的模型名）——用户追问后立刻定位并补上重启，之后 hermes 侧
`hermes chat -q` 实测 5 秒内完成，确认回滚生效。

### 第二次决策：用户明确选择继续使用 qwen3.8-27b-fp8

回滚文档还在写的过程中，用户回复："没问题，你就用qwen3.8吧，后面会有优化的模型"——明确知晓速度
代价后选择接受，把这次的慢速度当作过渡期成本，等待后续模型优化。

于是把 hermes-gateway、`common_api_manager`、Model Studio 校验配置三处**再次切回**
`qwen3.8-27b-fp8`，重新走 `docker stop`(旧)→`start-qwen38-27b-fp8.sh`→ready-poll→重启下游 的流程，
复验：`hermes status` 确认 `Model: qwen3.8-27b-fp8`，公网 `health` 聚合确认
`models.spark_llm: qwen3.8-27b-fp8`。

### 最终状态

`qwen3.8-27b-fp8` 是当前（本文档更新时点）的生产模型，**已知生成速度约 7 tok/s，明显慢于旧模型
的 65-75 tok/s，多轮工具调用场景会放大这个劣势**——这是用户知情后的明确选择，不是待修复的 bug。
如果之后想换回快模型或换新模型，两条路径（hermes-gateway 本机直连、common_api_manager 公网网关）
都需要同步修改，参照本文档"下游消费方同步"一节的操作方式。
