# asr_transcribe

对应接口：

```text
POST /api/transcribe
POST /api/asr/transcribe
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

PowerShell 示例：

```powershell
curl.exe -sS --max-time 120 `
  -F "file=@C:\Users\Administrator\Desktop\Workspace\Project_Codex\Project_ASR\online_api\samples\hello_zh.wav" `
  -F "language=zh" `
  https://www.wangyutang.cn/common/api/asr/transcribe
```

