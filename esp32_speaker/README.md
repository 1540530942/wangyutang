# esp32_speaker — ESP32 远端喊话

手机发一句话，服务器 TTS，ESP32 出声。

## 架构

```
手机 curl / App
      │  POST /api/speaker/say
      ▼
  server.py（FastAPI + SQLite 队列）
      │  GET /api/speaker/next（长轮询）
      ▼
  ESP32（Wi-Fi 轮询）
      │  POST TTS API
      ▼
  wangyutang.cn /common/api/tts/speech
      │  WAV 流（24kHz/16bit/单声道）
      ▼
  I2S → MAX98357A 功放 → 喇叭
```

## 接线（ESP32 → MAX98357A）

| ESP32 引脚 | MAX98357A 引脚 | 说明 |
|-----------|--------------|------|
| GPIO 26   | BCLK         | 位时钟 |
| GPIO 25   | WS (LRCLK)   | 左右声道时钟 |
| GPIO 22   | DIN          | 数据输入 |
| **5V 外部** | VIN         | MAX98357A 需 5V 供电，不能接 ESP32 3.3V |
| GND       | GND          | 共地 |

> **注意**：MAX98357A 额定 5V，请从 USB 电源或外部 5V 供电至 VIN，GND 与 ESP32 共地。

## 服务器部署

```bash
# 1. 安装依赖
pip install -r esp32_speaker/requirements.txt

# 2. 生成 / 配置 token（二选一）
#   a. 环境变量方式（推荐）：
export SPEAKER_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo "Token: $SPEAKER_TOKEN"

#   b. 自动生成（首次启动时写入 ./speaker_token 文件）：
#      启动后读取文件：cat esp32_speaker/speaker_token

# 3. 启动服务（默认 127.0.0.1:8937）
cd esp32_speaker
python3 server.py

# 可用环境变量覆盖：
# SPEAKER_HOST=0.0.0.0 SPEAKER_PORT=8937 python3 server.py

# 4. 手机 curl 示例
curl -X POST http://<服务器IP>:8937/api/speaker/say \
  -H "X-Speaker-Token: $SPEAKER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text": "你好，我是机器人！"}'
```

## ESP32 烧录步骤

1. 安装 [Arduino IDE](https://www.arduino.cc/en/software) 并添加 ESP32 开发板包
   - 开发板管理器 URL：`https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`
2. 选择板型：**ESP32 Dev Module**（或对应开发板）
3. 打开 `firmware/esp32_speaker.ino`
4. 修改顶部三个 `#define`：
   - `ESP32_SPEAKER_SSID` → Wi-Fi 名称
   - `ESP32_SPEAKER_PASSWORD` → Wi-Fi 密码
   - `SPEAKER_API_BASE` → 服务器实际地址（如 `http://192.168.1.100:8937`）
   - `SPEAKER_TOKEN` → 与服务器一致的 token
5. 编译并上传

> PlatformIO 用户：创建 `platform.ini` 并选 `esp32dev`，源文件放 `src/` 即可。

## 时延说明（v1 预期）

| 阶段 | 时长 |
|------|------|
| TTS 生成（云端）| 7~8 秒 |
| 音频传输 + 播放 | 文本长度决定 |
| **合计** | **约 10~15 秒** |

这是 v1 的已知限制，适合非实时播报场景。

## 安全说明

- 所有接口均需 `X-Speaker-Token` Header，防止未授权调用。
- Token 建议通过环境变量注入，不要硬编码在固件或配置文件中。
- 建议将服务器部署在受信内网，或通过 HTTPS 反向代理暴露。
- 固件使用 `setInsecure()` 跳过 TLS CA 验证（简化部署），生产环境可替换 CA PEM 启用严格校验。

## 运行测试

```bash
pip install fastapi uvicorn pytest httpx
python3 -m pytest esp32_speaker/tests/ -v
```
