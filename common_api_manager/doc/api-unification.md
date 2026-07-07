# common_api 统一音频能力说明

更新时间：2026-05-17

## 结论

`common_api` 是平台级公共能力层，不只负责语音转文字，后续文本转语音也应该放在这里统一提供。

`robot_sandbox` 是业务模块，负责语音控制链路：

```text
录音 / 上传音频
-> 调用 common_api ASR
-> 得到文本
-> 匹配 voice_intents
-> 投递 action_move 任务
-> 树莓派 / TurboPi 执行动作
```

因此 `robot_sandbox` 不再维护 `dashscope_third_party`、`local_qwen3` 这类 ASR 方式切换。具体使用哪个模型、哪个第三方服务，由 `common_api` 内部管理。

## 当前接口

common API 本地源码位置：

```text
C:\Users\Administrator\Desktop\Workspace\Project_Codex\wangyutang_platform\common_api_manager
```

公共 ASR（规范入口）：

```text
POST https://www.wangyutang.cn/common/api/asr/transcribe
```

> 历史说明：早期曾有 `POST /audio/api/asr/transcribe` 兼容入口，转发到 `common_api`。
> 该兼容入口已随 `/audio/*` 网关路由于 2026-07-08 一并退役，请直接调用上面的
> `/common/api/asr/transcribe`。详见 [migration-robot_sandbox-routes.md](../../docs/migration-robot_sandbox-routes.md)。

## 如何调用

浏览器打开接口测试页：

```text
https://www.wangyutang.cn/common/
```

检查 common API 是否在线：

```bash
curl -fsS https://www.wangyutang.cn/common/api/health
```

上传 WAV 做语音转文字：

```bash
curl -fsS --max-time 120 \
  -F "language=zh" \
  -F "file=@hello_zh.wav;type=audio/wav" \
  https://www.wangyutang.cn/common/api/asr/transcribe
```

Windows PowerShell 示例：

```powershell
curl.exe -sS --max-time 120 `
  -F "language=zh" `
  -F "file=@C:\Users\Administrator\Desktop\Workspace\Project_Codex\Project_ASR\online_api\samples\hello_zh.wav;type=audio/wav" `
  https://www.wangyutang.cn/common/api/asr/transcribe
```

Python 示例：

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

浏览器 JavaScript 示例：

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

`robot_sandbox` 旧入口也可以继续调用：

```bash
curl -fsS --max-time 120 \
  -F "language=zh" \
  -F "file=@hello_zh.wav;type=audio/wav" \
  https://www.wangyutang.cn/common/api/asr/transcribe
```

这个入口会转发到 `common_api`，新业务优先直接调用 `/common/api/asr/transcribe`。

## 当前链路

浏览器手动上传 / 录音：

```text
https://www.wangyutang.cn/robot_sandbox/
-> POST /common/api/asr/transcribe
-> POST /common/api/asr/transcribe
-> 返回识别文本
-> POST /robot_sandbox/api/recognize-text
-> POST /action/api/tasks
```

树莓派 WonderEchoPro：

```text
WonderEchoPro / USB 麦克风
-> /home/pi/robot_sandbox/edge_audio_listener.py
-> POST https://www.wangyutang.cn/common/api/asr/transcribe
-> POST https://www.wangyutang.cn/robot_sandbox/api/results
-> POST https://www.wangyutang.cn/action/api/tasks
```

## 后续 TTS 建议

未来文本转语音建议新增到 `common_api`：

```text
POST https://www.wangyutang.cn/common/api/tts/synthesize
```

业务模块只调用公共 TTS，不直接绑定某个模型或供应商：

```text
action_move / robot_sandbox / 其他业务
-> common_api TTS
-> 返回音频文件或音频流
-> 树莓派播放
```

这样 ASR 和 TTS 都有统一入口，后续切换模型、增加本地模型、增加第三方服务时，不需要改每个业务模块。
