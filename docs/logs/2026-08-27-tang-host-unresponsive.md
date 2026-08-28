# 2026-08-27 tang 主机应用层全挂(资源耗尽,SSH/HTTP 均不应答)

## 现象

例行检查 clawbot→ywcoding.cn 部署时发现:
- `https://www.ywcoding.cn/` curl **超时**(exit 124 / SSL connection timeout),返回空。
- 同主机 `https://www.wangyutang.cn/`(含新部署的 `/devices/api/health`)同样全部超时。
- `ssh tang` 报 **"Connection timed out during banner exchange"**,多次重试(25s 超时 ×3)均失败。

## 诊断(逐层探测)

| 层 | 结果 | 含义 |
|----|------|------|
| ICMP ping | ✅ 0% 丢包,54ms | 网络通路 + 内核存活 |
| TCP 22/80/443 | ✅ 全 OPEN | 内核 TCP 栈在回 SYN/ACK |
| SSH banner (raw read :22) | ❌ 空,无 banner | sshd 抢不到 CPU 发 banner |
| HTTP :80 (Caddy) | ❌ timeout | Caddy 无法服务 |
| HTTPS 全域名 | ❌ timeout | 所有容器服务无法服务 |

**判定**:主机级**资源耗尽**(CPU 打满 / OOM / IO 饱和)。内核网络栈正常(能 ping、能完成 TCP 三次握手,
因为 SYN/ACK 在内核里),但所有 userspace 进程(sshd、Caddy、docker 服务)都抢不到资源去应答应用层请求。
**不是** deploy 管道问题,也**不是**单个服务问题 —— GitHub 侧 HEAD 仍 `50a3f47`(可达),没有"网页落后于仓库",
而是**整台 tang 在应用层等同离线**。

## 排除过的假设

- ❌ "部署没生效/网页落后":GitHub HEAD 未变(`50a3f47`,自 08-26 无新提交),不存在待部署的新内容。
  线上返回空是因为主机不应答,不是内容旧。
- ❓ "是不是我刚部署 device_hub 引入的":同日在 tang build 了 device-hub 镜像并起容器(之前 healthy)。
  device_hub 是纯请求驱动的 FastAPI,无后台循环/常驻计算,healthcheck 30s 一次(urllib GET /api/health,极轻),
  **单个小容器几乎不可能把整机拖垮**。更可能是独立的主机级负载(备份/cron/多容器内存压力/腾讯侧)。
  但**无法证伪**,因为进不去 SSH 看 `docker stats` / `dmesg`(OOM killer 记录)。**恢复后必须复核这一条**。

## 无法远程修复的原因

sshd 不回 banner → 拿不到 shell → 无法 `docker stats` / `free` / `dmesg` / 重启服务。

## 下一步(需要现场/控制台介入)

1. **腾讯云控制台**:VNC 登录 tang,或直接**强制重启**实例(端口都 OPEN 但应用不应答,通常重启最快恢复)。
2. 恢复后立刻查:`dmesg -T | grep -i oom`(有无 OOM 杀进程)、`docker stats --no-stream`(哪个容器吃资源)、
   `/var/log/clawbot-deploy.log` 尾部、`uptime` 历史 load。
3. **复核 device_hub 是否无辜**:看它的 `docker logs device-hub` 有无异常/重启循环,`docker inspect` 重启次数。
   若可疑,先 `docker compose stop device-hub` 观察。
4. 若确认是多容器内存压力常态化:考虑给吃内存大户加 `mem_limit`,或给实例扩内存。

## 状态

- 记录时间:2026-08-27。tang 应用层不可用,ywcoding.cn / wangyutang.cn 全站 502-等价(超时)。
- 本次部署校验**因主机不可达而无法完成**(不是失败/落后,是被阻塞)。
- 待主机恢复后重跑校验并回填本条结论(尤其 device_hub 是否相关)。

## 恢复 + 根因确认(2026-08-27 21:22,约 15 分钟后自愈,未重启)

主机**自行恢复**:uptime 133 天未变(没重启),恢复时 `load average: 0.00, 8.76, 64.51`——
15 分钟均值 64 的尖峰已过,1 分钟归 0。部署校验重跑 **sha256 MATCH ✅**,管道健康。

**根因 = `audio-interact` 容器 OOM(实锤)**:`dmesg` 有两次 oom-kill,cgroup `docker-2b2984b4...`
经 `docker inspect` 比对 = **audio-interact**。被杀进程均为 `uvicorn`,RSS 2.3–2.4GB / vm 5.7–7.2GB:
- `01:47:42` killed uvicorn pid 369119(rss 2.4GB)
- `21:06:53` killed uvicorn pid 1063873(rss 2.3GB)← 对应本次全站超时

tang **总内存仅 3.6GB(3655MB)、无 swap**。audio-interact(音频/ASR)内存尖峰到 2.3GB 就吃掉大半物理内存,
触发 **global OOM**(constraint=CONSTRAINT_NONE=全局,非容器 cgroup 限额),内核抖动 → sshd/Caddy 抢不到资源 → 全站超时。

**device_hub 洗清嫌疑** ✅:restarts=0、running、RSS 仅 **48MB(1.33%)**、不是被 OOM 的容器、日志全是 healthcheck 200。
当时那次 build/起容器与 01:47 的 OOM 时间接近纯属巧合,真凶始终是 audio-interact 的内存尖峰。

## 复发风险 + 建议修复(待用户确认)

**会复发**:一天内已 OOM 两次,主机 3.6GB 无 swap,audio-interact 每次重活都可能再把整机拖垮。
- **首选(限制爆炸半径)**:给 audio-interact 加 `mem_limit`(如 1.5–2GB)+ `mem_swappiness`,让它撑爆时**只有它自己**被容器级 OOM 重启,而不是全局 OOM 连累 sshd/Caddy/全站。把"整机宕"降级成"单容器重启"。
  - 取舍:若 audio-interact 合法需要 >2GB,会被更频繁重启 → 需先确认它 2.3GB 是正常工作集还是泄漏。
- **次选**:给实例加 swap(1–2GB),给内存尖峰兜底(但 swap 抖动会拖慢)。
- **治本**:查 audio-interact 为何吃 2.3GB(加载了什么模型/缓存/是否泄漏),从源头压低。
- 可选:关键基础设施(robot-gateway/Caddy、sshd)本就轻,给重内存容器都设 `mem_limit` 后可整体隔离。

## 深挖根因(2026-08-27,用户选"先查为何吃 2.3GB")—— 实锤:装了 CUDA 版 torch 但主机无 GPU

audio-interact `requirements.txt` 有 `torch` + `silero-vad`。容器内实测:
- **`torch 2.13.0+cu130`**(CUDA 13.0 构建),`/site-packages/nvidia` = **2.7GB** CUDA 库,`torch` 本体 1.1GB。
- 光 `import torch` 就吃 **475MB RSS**;跑 silero VAD 推理(4 线程 + CUDA 懒加载尝试 + 音频张量)冲到 2.3GB。
- **numpy 根本没装**(torch 报 "Failed to initialize NumPy",降级运行)。
- 主机确认**无 GPU**:`lspci` 仅 `Cirrus Logic GD 5446`(云虚拟 VGA),无 nvidia-smi、无 NVIDIA 硬件。
  → CUDA 版 torch **从未用过 CUDA**,2.7GB CUDA 库纯死重,却在会话时把 RSS 顶到 2.3GB → 3.6GB 机上全局 OOM。

**根因定性**:不是内存泄漏,是**错误的 torch 变体(cu130)在纯 CPU 小内存主机上的内存尖峰**。空闲 46MB 正常,
一旦音频会话触发 torch 就爆。

**治本修复(已应用 2026-08-28)** ✅:audio-interact 改装 **CPU-only torch**——`requirements.txt`:
```
--extra-index-url https://download.pytorch.org/whl/cpu
torch  (CPU wheel, 无 +cuXXX)
numpy  (补上,消除降级告警)
```
重建 audio-interact 镜像。预期:CUDA 2.7GB 库消失,import RSS 从 475MB 大降,推理峰值从 2.3GB → 几百 MB,
在 3.6GB 预算内留足余量,消除 OOM。功能零影响(主机本就无 GPU,silero VAD 一直跑在 CPU)。

---

## 2026-08-28 device_hub v2 部署踩坑（sed -i inode 断联）

### 问题
`sed -i` 替换文件时创建新 inode，Docker bind mount 仍指向旧 inode → 容器读到的 Caddyfile 没有更新。
`docker cp` 到 bind-mounted 路径报 "device or resource busy"，同样无法覆盖。

### 复现条件
任何用 `sed -i` / `echo > file` / `cp src dst` 修改 bind-mounted 文件的场景都会触发此问题。

### 修复
重启容器。Docker 在容器启动时重新执行 bind mount，拿到主机上的当前 inode，读到最新文件。
```bash
docker restart robot-gateway
```

### 今后规则
需要修改 bind-mounted 配置文件后，必须 **重启对应容器**，不能只做 `caddy reload`。
或用 `python3 -c "open('file','w').write(...)"` / `tee` 等就地写入（不替换 inode）。
