# 腾讯云 Common API 部署记录

## 当前状态

`common_api_manager` 已部署到腾讯云，作为公共接口 `common_api` 的一部分提供语音转文本能力。

在线页面（旧公网网关仍存在时可用；当前机器人平台仓库不再维护 Caddy 网关配置）：

```text
https://www.wangyutang.cn/common/
```

健康检查：

```text
GET https://www.wangyutang.cn/common/api/health
```

语音转文本接口：

```text
POST https://www.wangyutang.cn/common/api/asr/transcribe
```

请求格式：

```text
multipart/form-data
```

字段：

```text
file      WAV 音频文件，推荐 16-bit PCM WAV
language  可选，默认 zh
```

## 调用方式

打开在线接口网页：

```text
https://www.wangyutang.cn/common/
```

健康检查：

```bash
curl -fsS https://www.wangyutang.cn/common/api/health
```

公网测试命令：

```powershell
curl.exe -sS --max-time 120 `
  -F "file=@C:\Users\Administrator\Desktop\Workspace\Project_Codex\Project_ASR\online_api\samples\hello_zh.wav" `
  -F "language=zh" `
  https://www.wangyutang.cn/common/api/asr/transcribe
```

Linux / macOS curl：

```bash
curl -fsS --max-time 120 \
  -F "language=zh" \
  -F "file=@hello_zh.wav;type=audio/wav" \
  https://www.wangyutang.cn/common/api/asr/transcribe
```

Python：

```python
import requests

url = "https://www.wangyutang.cn/common/api/asr/transcribe"

with open("hello_zh.wav", "rb") as audio:
    response = requests.post(
        url,
        data={"language": "zh"},
        files={"file": ("hello_zh.wav", audio, "audio/wav")},
        timeout=120,
    )

response.raise_for_status()
print(response.json()["text"])
```

浏览器 JavaScript：

```javascript
const form = new FormData();
form.append("language", "zh");
form.append("file", fileInput.files[0]);

const response = await fetch("https://www.wangyutang.cn/common/api/asr/transcribe", {
  method: "POST",
  body: form,
});

const data = await response.json();
console.log(data.text);
```

兼容入口：

```text
POST https://www.wangyutang.cn/audio/api/asr/transcribe
```

该入口由 `robot_sandbox` 提供，只负责转发到 common API。新业务建议直接调用 `/common/api/asr/transcribe`。

已验证返回文本：

```text
你好，这是王语棠语音识别接口测试。
```

## 腾讯云部署位置

源码目录：

```text
/root/wangyutang_platform/common_api/app_src
```

环境文件：

```text
/root/wangyutang_platform/common_api/.env
```

环境变量：

```text
ASR_LANGUAGE=zh
LV_ASR_BASE_URL=http://39.156.151.204:8000
LV_ASR_MODEL=qwen3-asr-1.7b
LV_TTS_BASE_URL=http://39.156.151.204:8001
LV_TTS_MODEL=qwen3-tts-12hz-1.7b-customvoice
LV_TTS_VOICE=vivian
LV_CHAT_BASE_URL=http://127.0.0.1:18002
LV_CHAT_MODEL=qwen3.5-9b
MODEL_USAGE_COLLECTOR_URL=http://127.0.0.1:18080/usage
```

注意：`.env` 中包含真实 API Key，不要提交到代码仓库。

## 运行方式

当前没有使用 Docker 容器运行 `common-api`。

原因：腾讯云构建 Docker 镜像时拉取基础镜像和 pip 依赖容易超时。

最终使用：

```text
systemd + Python venv
```

systemd 服务：

```text
common-api.service
```

服务端口：

```text
0.0.0.0:8101
```

服务状态检查：

```bash
systemctl is-enabled common-api
systemctl is-active common-api
curl -fsS http://127.0.0.1:8101/api/health
```

当前确认状态：

```text
enabled
active
```

## 访问方式

当前仓库不再维护统一 Caddy 网关。`common-api` 如果继续运行，优先按独立 systemd 服务和直连端口验证：

```text
http://127.0.0.1:8101/api/health
```

如需重新暴露公网入口，应在新的网关或云厂商负载均衡中单独配置，不再依赖 `control_platform`。

## 部署链路

```text
本地 wangyutang_platform/common_api_manager
-> 打包上传到腾讯云
-> /root/wangyutang_platform/common_api/app_src
-> common-api.service
-> http://127.0.0.1:8101
```

## 数据链路

公网 API 调用：

```text
用户浏览器 / API 调用方
-> POST http://<host>:8101/api/asr/transcribe
-> FastAPI app.py
-> WAV 解析为 PCM
-> 重采样到 16k
-> LV qwen3-asr-1.7b HTTP 接口
-> 返回 JSON
```

返回 JSON 主要字段：

```json
{
  "text": "识别文本",
  "model": "qwen3-asr-1.7b",
  "provider": "lv_qwen_asr",
  "raw": {
    "text": "识别文本"
  }
}
```

## 本地测试

本地页面：

```text
http://127.0.0.1:8100/
```

本地接口：

```text
GET  http://127.0.0.1:8100/health
GET  http://127.0.0.1:8100/api/health
POST http://127.0.0.1:8100/api/transcribe
POST http://127.0.0.1:8100/api/asr/transcribe
```

本地启动：

```powershell
cd C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform\common_api_manager
.\start_api.ps1
```

如果本地页面显示旧内容或接口结果不对，通常是旧进程没有重启。处理方式：

```powershell
Get-NetTCPConnection -LocalPort 8100 -ErrorAction SilentlyContinue
```

停止旧进程后重新运行：

```powershell
.\start_api.ps1
```

## 代码检查

Python 语法检查：

```powershell
python -m py_compile C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform\common_api_manager\app.py
```

前端 JS 检查：

```powershell
wsl -d Ubuntu -- bash -lc 'node --check /mnt/c/Users/Administrator/Desktop/Workspace/Project_Codex/wangyutang_platform/common_api_manager/static/app.js'
```

## 准确率说明

本次公共接口部署已统一到 LV ASR/TTS/LLM 上游。

当前默认 ASR 模型是：

```text
qwen3-asr-1.7b
```

核心识别函数是：

```text
transcribe_with_lv_qwen()
```

如果感觉网页录音准确率下降，优先检查：

```text
浏览器麦克风权限
麦克风音量
录音距离
浏览器降噪/回声消除
环境噪声
录音时长
```

固定 WAV 上传测试更适合判断后端 ASR 是否正常。
