# 模块合入规范说明

本规范适用于 `wangyutang_platform` 下机器人平台相关模块的新增、修改、部署和回滚。仓库已移除统一门户、Web Manager、遥感、论文学习和 SLAM 实验模块；新的合入标准以模块目录、`docker-compose.yml`、健康检查和部署脚本为准。

## 基本原则

1. 每次模块变更都要留下可追踪记录。
2. 新增模块必须明确边界、端口、接口、健康检查、数据目录和部署方式。
3. 修改现有模块时，不要顺手改无关模块。
4. 不得破坏现有机器人平台服务的本地入口、健康检查和数据卷。
5. 涉及服务器部署时，必须记录服务器改动、验证结果和遗留风险。

## 必填记录

每次修改或新增模块后，按影响范围更新：

- 模块自己的 `README.md`
- 根目录 `README.md`
- `docker-compose.yml`
- 相关部署脚本，例如 `scripts/deploy_tencent.ps1` 和 `scripts/tencent_apply_release.sh`
- 相关运维文档或 `docs/logs/` 记录

记录内容至少包含：

- 修改日期
- 模块名称
- 修改目的
- 变更文件
- 新增或变更的接口
- 新增或变更的端口
- 数据目录或数据卷
- 本地验证结果
- 服务器验证结果，如果已经部署
- 已知问题和下一步计划

## 新增模块准入

新增模块必须满足：

1. 有独立目录，例如 `new_module/`。
2. 有模块级 `README.md`，说明用途、运行方式、接口、端口和数据目录。
3. 有健康检查接口，优先使用 `/api/health`。
4. 如需容器运行，必须补充 `Dockerfile` 和根 `docker-compose.yml` 服务配置。
5. 如有持久化数据，必须使用明确的数据卷或数据目录。
6. 本地语法检查通过。
7. 本地 Compose 配置检查通过。
8. 不影响已有模块启动和访问。

## 修改模块准入

修改现有模块必须满足：

1. 明确修改范围。
2. 更新模块 README 中的行为说明。
3. 接口、端口、数据结构或环境变量变化时，同步更新根 README、Compose 和部署脚本。
4. 涉及前端页面时，确认页面可以打开且主要交互不报错。
5. 涉及后端时，至少完成语法检查和健康检查。
6. 涉及容器时，确认镜像能构建、容器能启动、健康检查能通过。
7. 不得删除用户数据、数据卷或历史配置，除非已有明确备份和确认。

## 合入前检查

```text
[ ] 模块 README 已更新，或确认无需更新
[ ] 根目录 README 已更新，或确认无需更新
[ ] docker-compose.yml 已更新，或确认无需更新
[ ] 部署脚本已更新，或确认无需更新
[ ] 本地语法检查通过
[ ] docker compose config --quiet 通过
[ ] 本地健康检查通过，或说明为什么不能运行
[ ] 页面入口可打开，或确认该模块无页面
[ ] 新增/修改接口已验证
[ ] 未破坏已有模块入口
[ ] 如已部署服务器，容器状态正常
[ ] 如已部署服务器，服务健康检查通过
[ ] 已记录已知问题和下一步计划
```

## 推荐验证命令

本地配置检查：

```powershell
docker compose config --quiet
```

本地健康检查：

```powershell
.\scripts\check-local.ps1
```

Python 模块检查：

```powershell
python -m compileall -q .\camera_snapshot .\action_move .\robot_sandbox .\pi5_robot .\remote_control_cloud
```

服务器检查示例：

```bash
cd /root/wangyutang_platform
docker compose --env-file .env -f docker-compose.yml ps
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8099/api/health
curl -fsS http://127.0.0.1:8094/api/health
curl -fsS http://127.0.0.1:8095/api/health
curl -fsS http://127.0.0.1:8093/api/health
curl -fsS http://127.0.0.1:8096/api/health
```
