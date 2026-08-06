# 可观测性栈（账本二）

按 `docs/full-link-trace-design.md` §4 落地，但对生产机资源做了如实取舍：

- Tencent 主机仅 3.6G 内存 / 12G 空闲磁盘，已跑 8 个业务容器。
- **本栈只跑 Prometheus + Grafana**（各限 256M，端口只绑 127.0.0.1），
  SLO 指标由 audio_interact 的 `/metrics` 直接暴露（`runtime/metrics.py`）。
- **Tempo/Collector 暂不部署**：OTel span 埋点代码已在
  `runtime/metrics.py:init_tracing`，设 `OTEL_EXPORTER_OTLP_ENDPOINT` 即启用，
  换更大主机或内存扩容后加一个 Tempo service 即可接上，业务代码零改动。

## 部署

```bash
scp -r observability tang:/root/wangyutang_platform/
ssh tang 'cd /root/wangyutang_platform/observability && docker compose -f docker-compose.observability.yml up -d'
```

## 访问（SSH 隧道）

```bash
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 tang
# Grafana http://localhost:3000  （匿名只读；admin 密码见 compose env）
# 看板：语音交互 / 语音交互 SLO
```

## 指标口径（与设计 §4.2 对应）

| 指标 | 含义 | 目标 |
|---|---|---|
| `audio_e2e_response_seconds` | speech_end → 首块 TTS 上线 | P95 ≤ 2.5s |
| `audio_bargein_cancel_delay_seconds` | 插话 → 扬声器静音（对时后/本地反应） | ≤ 0.3s |
| `audio_bargein_tail_seconds` | cancel 到达 → 停播（本地先杀=0） | ≤ 0.15s |
| `audio_asr/route/tts_first_chunk_*` | 分阶段时延 | 看板阈值 |
| `audio_bargein_total{stop_source}` | 打断次数（local_energy/server_cancel） | — |
| `audio_sessions_total{retention_tier}` | 会话落盘（T0 证据/T1 常规） | — |
