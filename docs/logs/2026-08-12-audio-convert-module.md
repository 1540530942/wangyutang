# 2026-08-12 · common_api_manager 新增 audio_convert 模块

## 修改目的

平台缺少「把原始录音变成 ASR 可直接吃的音频」这一步。原始录音（手机 m4a、会议 mp3、
立体声高采样 wav）的高采样率和多声道对 ASR 完全无用——模型内部一律重采样到 16kHz
单声道。把这一步前移到服务端，解决三个实际问题：

1. **回传体积**：m4a → opus 通常降到 1/3~1/10，弱网/跨境回传不再是瓶颈。
2. **静默截断**：长录音一次性送进 ASR 有撞上 `max_model_len=2048` 被截断的风险，
   按静音切片并给出时间轴，可定位到具体哪一段识别有误。
3. **可取回**：转换结果有稳定 URL，网页能下载，脚本/其他模块也能直接拉。

## 模块名称

`common_api_manager/modules/audio_convert`（不是独立服务，挂在既有 common-api 宿主进程内）

## 变更文件

```text
新增 common_api_manager/modules/audio_convert/__init__.py
新增 common_api_manager/modules/audio_convert/router.py
新增 common_api_manager/modules/audio_convert/service.py
新增 common_api_manager/modules/audio_convert/README.md
新增 common_api_manager/static/audio_convert.html
新增 common_api_manager/static/audio_convert.css
新增 common_api_manager/static/audio_convert.js
改  common_api_manager/app.py                （注册 router + /audio-convert 页面路由）
改  common_api_manager/common/settings.py    （8 个 audio_convert_* 配置项）
改  common_api_manager/static/index.html     （/common/ 入口新增卡片）
改  common_api_manager/README.md             （能力总览表）
改  README.md                                （宿主服务说明 + 网页列表）
```

## 新增接口

公网前缀 `https://www.wangyutang.cn/common`：

| 方法 | 路径 |
| --- | --- |
| GET | `/api/audio/convert/capabilities` |
| POST | `/api/audio/convert` |
| GET | `/api/audio/convert/jobs` |
| GET | `/api/audio/convert/{job_id}` |
| GET | `/api/audio/convert/{job_id}/files/{filename}` |
| GET | `/api/audio/convert/{job_id}/archive` |
| DELETE | `/api/audio/convert/{job_id}` |

页面：`/common/audio-convert`

## 端口

无新增端口，复用 common-api 宿主进程 `host:8101`，经 robot-gateway `handle_path /common/*` 暴露。

## 数据目录

生产：`/root/control_platform/common_api/data/audio_convert`

**刻意放在 `app_src` 之外**：CI 每次部署会 `cp -a app_src app_src.backup.<TAG>`，
若音频落在 `app_src` 内，每次部署都会整份复制（当前已有 20 个 backup 目录）。
通过服务器 `.env` 的 `AUDIO_CONVERT_DIR` 指定，本地开发默认仍是
`<common_api_manager>/data/audio_convert`（已被 `.gitignore` 的 `data/` 覆盖）。

保留策略：数量 40 个 / 总体积 1GB（生产覆盖值，默认 2GB）/ 时长 7 天，三者任一超限即清理最旧任务。
上传的原始文件在转换完成后立即删除，只保留转换结果。

## 服务器改动

| 项 | 内容 |
| --- | --- |
| 主机 | `110.40.154.41`（root，OpenCloudOS 9.4） |
| 安装依赖 | `dnf install -y ffmpeg` → ffmpeg/ffprobe 7.0.2（EPOL 源），磁盘占用约 200MB（6.5G → 6.3G 可用） |
| 配置 | `/root/control_platform/common_api/.env` 追加 `AUDIO_CONVERT_DIR`、`AUDIO_CONVERT_MAX_STORE_BYTES`；原文件已备份为 `.env.bak.20260812` |
| 新建目录 | `/root/control_platform/common_api/data/audio_convert` |
| 部署方式 | 推送到 `feature/llm-manager` → `.github/workflows/deploy-common-api.yml` 自动 rsync + `systemctl restart common-api`，未手动改代码 |

## 本地验证结果

在 Korea 机（`VM-0-5-ubuntu`）用独立 venv 起 `uvicorn app:app`，`AUDIO_CONVERT_DIR` 指向临时目录：

```text
python -m compileall -q common_api_manager      OK
app 导入                                         OK（全部既有模块一并导入成功）
GET  /api/audio/convert/capabilities            200，available=true，ffmpeg 6.1.1
POST /api/audio/convert (m4a, opus, split=60)   200，189.4s → 3 片，988KB → 368KB（37%），耗时 4.9s
     切点 60.568s / 122.120s                     均落在静音中点，未从句中切断
GET  .../files/<name>.opus                      200，content-type: audio/ogg
GET  .../archive                                200，ZIP 含 3 个分片 + manifest.json，临时文件已清理
GET  /audio-convert, /static/audio_convert.{js,css}, /, /model-studio   均 200
错误路径：format=flac → 400 / split=2 → 400 / 非音频 → 400 / 非法 job id → 400 /
         路径穿越 → 404 / 不存在的文件 → 404
端到端：下载的 opus 分片送 spark qwen3-asr-1.7b，正确返回中文转写
```

## 服务器验证结果

```text
GET  /common/api/audio/convert/capabilities     200，available=true，ffmpeg 7.0.2
GET  /common/audio-convert                      200
GET  /common/static/audio_convert.{js,css}      200
GET  /common/ , /common/api/health              200（其余模块未受影响）
POST /common/api/audio/convert                  200，988KB m4a → 3 片 opus 368KB
GET  .../files/<name>.opus                      200，119323 bytes
GET  .../archive                                200，334370 bytes
DELETE /{job_id}                                200，jobs 列表回到空
落盘位置                                         /root/control_platform/common_api/data/audio_convert（app_src 内未产生音频）
/tmp 残留 zip                                    0
端到端：从生产下载的 opus 分片送 ASR，正确返回中文转写
```

测试任务已删除，生产数据目录清空。

## 开发过程中修掉的问题

- `shutil.make_archive` 原本把 zip 写进被打包目录本身，导致压缩包递归包含自己、
  `/archive` 接口挂死。改为用 `zipfile` 写到 `tempfile` 临时文件，响应发完由
  `BackgroundTask` 删除。
- 中文文件名经 ASCII 清洗后会退化成同名 `audio_000.opus`，多任务下载互相覆盖。
  改为清洗结果为空时回落到 `job_id` 前缀。
- ffprobe 失败时把服务端上传路径原样回给调用方，已改为脱敏成 `<input>`。

## 已知问题和下一步

- ffmpeg 是宿主机依赖，不在 CI 里管理。换机或重装系统需要重新 `dnf install -y ffmpeg`，
  否则模块降级为 `capabilities.available=false` + `503`（不影响 common-api 其他接口）。
- 转换是同步阻塞接口，并发上限 2（`AUDIO_CONVERT_CONCURRENCY`），超出返回 429。
  生产机 4 核 3GB 内存，长录音批量转换时需要注意；若后续要处理小时级录音，
  应改成异步任务 + 轮询。
- 生产磁盘已用 85%（6.3G 可用），其中 `app_src.backup.*` 已累积 20 份。
  本模块数据已隔离在 `app_src` 外并设 1GB 上限，但 backup 目录本身建议单独清理。
- 接口无鉴权，与 common-api 其余接口一致。上传的录音在服务端保留最多 7 天，
  敏感录音建议用完后立即调 `DELETE /{job_id}`。
