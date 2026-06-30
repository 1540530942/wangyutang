# robot_gateway

基于 Caddy 的反向代理网关，运行在腾讯云，统一对外暴露所有子服务的 HTTPS 入口。

## 文件

| 文件 | 说明 |
|---|---|
| `Caddyfile` | Caddy 配置，定义路由规则和上游服务 |
| `site/` | 静态文件目录（如有首页/落地页） |

## 路由规则

| 路径前缀 | 上游服务 | 说明 |
|---|---|---|
| `/camera/*` | `camera-snapshot:8099` | 相机截图与视频流 |
| `/action/*` | `action-move:8094` | 机器人动作任务队列 |
| `/audio/*` | `audio-recognition:8095` | 语音识别路由 |
| `/common/*` | `host.docker.internal:8101` | 通用 API（ASR、TTS、视觉等） |
| `/robot/*` | `pi5-robot:8093` | Pi5 机器人直连服务 |
| `/slam/*` | `slam-mapping:8301` | 可选 SLAM/位姿/占用栅格服务 |
| `/face/*` | `smile-face:8096` | 表情控制 |
| `/api/health` | 内联响应 | 网关健康检查 |

## 特性

- 自动 HTTPS（Let's Encrypt ACME，邮箱通过 `$ACME_EMAIL` 环境变量注入）
- gzip/zstd 压缩
- 上游地址通过环境变量覆盖（`$CAMERA_UPSTREAM` 等），方便本地开发切换

## 启动

```bash
docker compose up robot-gateway

# 或本地调试（需安装 caddy）
ACME_EMAIL=you@example.com WWW_DOMAIN=example.com caddy run --config Caddyfile
```
