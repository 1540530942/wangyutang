# 2026-09-09 · Nemotron 满显存测试导致 spark 内存耗尽、机器近乎失去响应

## 背景

在完成 Nemotron-3.5-Lightning-30B-A3B-NVFP4 的受限端口（8010，`gpu-memory-utilization=0.20`）验证后，
用户要求"测真实显存上限"+"接入 hermes 做真实微信测试"。为了给 Nemotron 腾出全部 121GB 统一内存，
决定临时停止生产的 `vllm-qwen36-nvfp4` 容器。

## 事故经过

1. `docker stop vllm-qwen36-nvfp4` 发起后**静默卡住**："tried to kill container, but did not receive
   an exit event"——容器没有真的停下来，但命令本身也没有立刻报错退出，SSH 会话看起来像卡死。
2. 紧接着 `docker rm -f vllm-nemotron35-nvfp4`（清理测试用的旧容器）同样失败："container ... PID ...
   is zombie and can not be killed. Use the --init option"——**根因：起 Nemotron 时的启动脚本忘了加
   `--init`，容器内 PID 1 没有正确 reap 子进程，退出时变成了僵尸进程，无法被正常回收。**
3. 两个大模型容器（qwen3.6 ~87GB + Nemotron 之前测试占用的部分）都卡在半死不活的状态，谁的显存都没
   真正释放，`free -h` 显示 **121Gi/121Gi 全部用满，仅剩 572Mi**。
4. 内存耗尽后，机器整体响应急剧恶化：`docker ps`、`ps aux`、`free -h` 这类本该秒回的命令开始超时
   （30s-120s 不等），后来连**新建 SSH 连接本身**都开始超时（不是命令卡住，是 TCP/Tailscale 层建连
   失败）——判断是内核层面在做内存回收/换页的极端抖动，拖慢了包括网络栈在内的整个系统。
5. `docker kill -s SIGKILL vllm-qwen36-nvfp4` 第一次尝试仍然报同样的"did not receive an exit event"
   错误——**SIGKILL 都杀不掉，说明进程卡在内核不可中断睡眠（D 状态），大概率是阻塞在 GPU 驱动层面的
   某个同步调用上**，不是普通的用户态卡死，常规信号机制对此无效。
6. 同一批 SIGKILL 命令里的 `vllm-nemotron35-nvfp4` 部分成功了（返回容器名，说明真的杀掉了），但杀掉
   之后 `free -h` 依然显示 121Gi/121Gi 没有变化——**说明 GPU 侧的统一内存不会因为容器进程被杀就立刻
   归还，存在结算延迟**（这跟 2026-08-18 cutover 记录里"docker stop 后 CUDA/驱动层不会瞬间归还全部
   显存"的教训是同一类问题，但这次因为进程本身卡死+僵尸问题叠加，延迟严重到让整机响应恶化）。
7. 在准备建议用户物理重启的过程中，**机器自己恢复了**：约 20-25 分钟后（从最初 stop 卡住算起），
   `free -h` 显示内存已经从 121Gi/121Gi 释放到 24Gi used / 90Gi free；`docker ps -a` 显示
   `vllm-qwen36-nvfp4` 状态为 `Exited (137)`（137 = 128+9，SIGKILL 生效）、`vllm-nemotron35-nvfp4`
   为 `Exited (0)`——内核最终还是把卡住的 D 状态进程和 GPU 内存都回收干净了，只是比正常情况慢得多。
8. 重新拉起 `vllm-qwen36-nvfp4`（`SERVED_MODEL_NAME=qwen3.6-35b-a3b`），冷启动约 5.5 分钟后就绪，
   `hermes chat -q` 实测 7 秒正常响应，公网网关 `/common/api/llm/spark-qwen/chat/completions` 实测
   200 且内容正确。**hermes-gateway 和 common_api_manager 的配置全程没有被这次事故改动过**
   （原计划是先测完显存上限、稳定后再考虑要不要改 hermes 指向 Nemotron，事故发生在改配置之前）。

## 根因总结

| 环节 | 问题 |
|---|---|
| `docker stop` | 对已经在高负载下运行的大模型容器发起 stop，卡住不报错也不生效 |
| Nemotron 启动脚本 | **缺少 `--init` 参数**，容器内 PID 1 无法正确 reap 子进程，异常退出时变僵尸 |
| 应对方式 | 遇到第一次 `stop` 卡住时，选择了叠加更多操作（`rm -f` 新容器、启动 Nemotron 满显存版）而不是先停下来诊断，导致两个大模型同时处于"卡住但没释放资源"的状态，加剧了问题 |
| GPU 统一内存回收 | 进程被杀（甚至 SIGKILL）后，统一内存不会立刻归还，尤其在进程本身卡在 D 状态时，回收会被推迟到内核真正处理完阻塞的驱动调用为止 |

## 教训

1. **`docker stop`/`rm -f` 没有在预期时间内返回时，应该先诊断（`docker ps` 看容器实际状态、检查是否
   D 状态）再决定下一步，不要在不确定前一个操作是否生效的情况下叠加更多操作**——这次是本次事故被放大的
   直接原因。
2. **任何新写的 vLLM/大模型启动脚本，`docker run` 必须带 `--init`**（对照 `start-qwen36-nvfp4.sh`
   等现成脚本，它们都带了这个参数，这次是我自己新写的 `start-nemotron35-nvfp4.sh` 遗漏了）。
3. **在统一内存架构（DGX Spark/GB10）上做"停一个大模型腾地方给另一个"这类操作，要预留远超平时的
   缓冲时间**——不能假设 `docker stop` 返回或容器状态变化就代表资源真的释放了，且这类操作本身可能让
   整机响应长时间恶化，不是立即可逆的。
4. 机器出现"命令持续超时、SSH 建连都失败"这种信号时，24 分钟左右的耐心等待+被动观察，比继续尝试更多
   远程操作更有效——这次最终是自愈的，没有真的需要物理重启。

## 影响范围

- 生产 LLM 服务（qwen3.6-35b-a3b）中断时长：约 25-30 分钟（从最初 `docker stop` 发起到重新 ready）
- hermes-gateway（微信 clawbot）配置全程未被改动，服务中断是因为后端模型服务本身不可用，不是配置错误
- 无数据丢失，Nemotron 权重/镜像均保留在磁盘上，后续如需重新测试满显存场景，修复 `--init` 缺失后可以
  重试，但需要预留更长的操作窗口和更谨慎的分步验证
