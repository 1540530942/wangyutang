# audio_convert

把任意录音转成 ASR 可直接吃的音频：**16kHz 单声道**，可按静音切片，结果留在服务端供下载或接口取回。

## 用途

原始录音（手机 m4a、会议 mp3、立体声高采样 wav）里的高采样率和多声道对 ASR 完全无用——模型内部一律重采样到 16kHz 单声道。这个模块把这一步前移到服务端，好处有三个：

1. **体积** —— m4a → opus 通常降到 1/3~1/10，跨境/弱网回传不再是瓶颈。
2. **切片** —— 长录音一次性送进 ASR 有撞上模型上下文上限（`max_model_len=2048`）被静默截断的风险；按静音切片同时给出时间轴，方便定位是哪一段识别有误。
3. **可取回** —— 转换结果带稳定 URL，网页能下载，脚本和其他模块也能直接拉。

## 页面

`https://www.wangyutang.cn/common/audio-convert`

上传 → 选输出格式和切片长度 → 转换 → 逐片下载或打包 ZIP，下方是历史任务列表。

## 接口

基础 URL：`https://www.wangyutang.cn/common`（本机直连为 `http://127.0.0.1:8101`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/audio/convert/capabilities` | ffmpeg 是否可用、支持的输出格式、上传上限和保留策略 |
| POST | `/api/audio/convert` | 上传并转换，返回 manifest |
| GET | `/api/audio/convert/jobs?limit=50` | 历史任务列表，最新在前 |
| GET | `/api/audio/convert/{job_id}` | 单个任务的 manifest |
| GET | `/api/audio/convert/{job_id}/files/{filename}` | 下载单个分片 |
| GET | `/api/audio/convert/{job_id}/archive` | 打包下载全部分片 + manifest（ZIP） |
| DELETE | `/api/audio/convert/{job_id}` | 删除任务及其文件 |

### POST 参数

| 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `file` | multipart 文件 | 必填 | 任意 ffmpeg 可读的音频 |
| `format` | 字符串 | `wav` | `wav`（PCM，最大最稳）/ `opus`（24kbps，最小）/ `mp3`（32kbps，通用播放） |
| `split` | 数字 | `0` | 目标分片秒数；`0` 不切片，否则最小 5 秒 |

### 示例

```bash
curl -s https://www.wangyutang.cn/common/api/audio/convert \
  -F "file=@20260811_223051.m4a" \
  -F "format=opus" -F "split=120"
```

返回的 `chunks[].path` 是相对 `/common/` 的路径，拼上基础 URL 即可下载：

```bash
curl -O https://www.wangyutang.cn/common/api/audio/convert/<job_id>/files/<name>.opus
```

## 切片逻辑

1. `silencedetect`（阈值 `-35dB`、最短 `0.45s`）扫出全部静音段。
2. 以每个静音段中点为候选切点，在 `[0.5×target, 1.5×target]` 窗口内取离目标最近的一个。
3. 窗口内没有静音时才在目标位置硬切。

所以分片长度是浮动的，不会从句子中间切断。`manifest.json` 记录每片的 `start` / `end`，可还原到原始时间轴。

## 存储与清理

| 项 | 环境变量 | 默认 |
| --- | --- | --- |
| 存储目录 | `AUDIO_CONVERT_DIR` | `<common_api_manager>/data/audio_convert` |
| 单文件上传上限 | `AUDIO_CONVERT_MAX_UPLOAD_BYTES` | 209715200（200MB） |
| 保留任务数 | `AUDIO_CONVERT_MAX_JOBS` | 40 |
| 保留总体积 | `AUDIO_CONVERT_MAX_STORE_BYTES` | 2147483648（2GB） |
| 保留时长 | `AUDIO_CONVERT_MAX_AGE_HOURS` | 168（7 天） |
| 并发转换数 | `AUDIO_CONVERT_CONCURRENCY` | 2 |
| 单次 ffmpeg 超时 | `AUDIO_CONVERT_TIMEOUT_SECONDS` | 600 |

每次转换结束会按上述预算清理旧任务，超并发返回 `429`。上传的原始文件在转换完成后立即删除，只保留转换结果。

## 依赖

宿主机需要 `ffmpeg` 和 `ffprobe`。缺失时 `capabilities` 返回 `available: false`，转换接口返回 `503`，页面按钮置灰——不会让整个 common-api 起不来。

```bash
# OpenCloudOS 9 / 腾讯云生产机
dnf install -y ffmpeg
# Debian/Ubuntu
apt-get install -y ffmpeg
```

## 健康检查

沿用 common_api_manager 的 `/api/health`；本模块可用性单独看 `/api/audio/convert/capabilities`。
