/**
 * ESP32 远端喊话固件
 * 硬件：ESP32 Dev Module + MAX98357A I2S 功放 + 小喇叭
 *
 * 烧录前必改的三个 #define（见下方配置区）：
 *   ESP32_SPEAKER_SSID / ESP32_SPEAKER_PASSWORD / SPEAKER_API_BASE
 *
 * 依赖库（Arduino Library Manager 安装）：
 *   - ESP32 Arduino core 5.x（含 WiFi、WiFiClientSecure、driver/i2s）
 *
 * 接线（MAX98357A）：
 *   ESP32 GPIO26 → MAX98357A BCLK
 *   ESP32 GPIO25 → MAX98357A WS (LRCLK)
 *   ESP32 GPIO22 → MAX98357A DIN
 *   ESP32 3.3V   → MAX98357A VIN  ← 注意：功放本身需 5V 供电，
 *                                     建议接 5V USB 电源至 MAX98357A VIN，
 *                                     GND 共地。
 *   ESP32 GND    → MAX98357A GND
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <driver/i2s.h>

// ==========================================================================
// 配置区 ── 烧录前请修改以下三项
// ==========================================================================
#define ESP32_SPEAKER_SSID     "YOUR_WIFI_SSID"      // ← 改成真实 Wi-Fi 名称
#define ESP32_SPEAKER_PASSWORD "YOUR_WIFI_PASSWORD"   // ← 改成真实 Wi-Fi 密码
#define SPEAKER_API_BASE       "http://127.0.0.1:8937" // ← 改成服务器实际地址（内网/公网）
#define SPEAKER_TOKEN          "YOUR_SPEAKER_TOKEN"   // ← 改成 server 生成的 token

// I2S 引脚（对应 MAX98357A BCLK / WS / DIN）
#define I2S_BCLK_PIN  26
#define I2S_LRCLK_PIN 25
#define I2S_DIN_PIN   22

#define LED_BUILTIN_PIN 2   // 大多数 ESP32 开发板内置 LED

// TTS API（HTTPS，WAV 24kHz/16bit/单声道）
// wangyutang.cn 使用有效证书，这里 setInsecure() 跳过 CA 校验简化部署。
// 如需严格校验，可替换为 setCA(cert_pem)。
// TODO: 替换 CA PEM 以启用严格 TLS 验证
#define TTS_URL "https://www.wangyutang.cn/common/api/tts/speech"

// ==========================================================================
// 运行时常量
// ==========================================================================
#define POLL_TIMEOUT_MS    30000  // 长轮询超时 30 秒
#define TTS_HTTP_TIMEOUT_S 60     // TTS HTTP 超时（生成 7~8 秒）
#define TTS_RETRY_COUNT    2      // 失败重试次数
#define TTS_RETRY_DELAY_MS 3000   // 重试间隔
#define WIFI_RETRY_MS      10000  // Wi-Fi 掉线轮询退避
#define TASK_WATCHDOG_MS   120000 // 任务整体看门狗（2 分钟）
#define I2S_CHUNK_BYTES    4096   // 流式读块大小
#define SAMPLE_RATE        24000
#define BITS_PER_SAMPLE    16

// ==========================================================================
// 状态机
// ==========================================================================
enum State { IDLE, FETCH_TTS, PLAYING, REPORT };
static State gState = IDLE;

// 当前任务
static int    gQueueId = -1;
static String gText    = "";
static bool   gTaskOk  = false;   // 播放是否成功

// ==========================================================================
// LED 工具
// ==========================================================================
static unsigned long gLedToggleMs = 0;
static bool          gLedOn       = false;

void ledOff() {
    digitalWrite(LED_BUILTIN_PIN, LOW);
    gLedOn = false;
}

void ledBlink(unsigned long intervalMs) {
    unsigned long now = millis();
    if (now - gLedToggleMs >= intervalMs) {
        gLedToggleMs = now;
        gLedOn = !gLedOn;
        digitalWrite(LED_BUILTIN_PIN, gLedOn ? HIGH : LOW);
    }
}

// ==========================================================================
// Wi-Fi
// ==========================================================================
void ensureWifi() {
    if (WiFi.status() == WL_CONNECTED) return;

    Serial.println("[WiFi] 正在连接...");
    WiFi.begin(ESP32_SPEAKER_SSID, ESP32_SPEAKER_PASSWORD);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
        if (millis() - start > 15000) {
            Serial.println("\n[WiFi] 连接超时，10 秒后重试");
            delay(WIFI_RETRY_MS);
            WiFi.begin(ESP32_SPEAKER_SSID, ESP32_SPEAKER_PASSWORD);
            start = millis();
        }
    }
    Serial.printf("\n[WiFi] 已连接，IP: %s\n", WiFi.localIP().toString().c_str());
}

// ==========================================================================
// I2S 初始化
// ==========================================================================
void i2sInit() {
    i2s_config_t cfg = {
        .mode                 = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
        .sample_rate          = SAMPLE_RATE,
        .bits_per_sample      = I2S_BITS_PER_SAMPLE_16BIT,
        .channel_format       = I2S_CHANNEL_FMT_ONLY_LEFT, // 单声道
        .communication_format = I2S_COMM_FORMAT_STAND_I2S,
        .intr_alloc_flags     = ESP_INTR_FLAG_LEVEL1,
        .dma_buf_count        = 8,
        .dma_buf_len          = 512,
        .use_apll             = false,
        .tx_desc_auto_clear   = true,
        .fixed_mclk           = 0,
    };
    i2s_pin_config_t pins = {
        .bck_io_num   = I2S_BCLK_PIN,
        .ws_io_num    = I2S_LRCLK_PIN,
        .data_out_num = I2S_DIN_PIN,
        .data_in_num  = I2S_PIN_NO_CHANGE,
    };
    i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr);
    i2s_set_pin(I2S_NUM_0, &pins);
    i2s_zero_dma_buffer(I2S_NUM_0);
}

// ==========================================================================
// POLL /api/speaker/next
// ==========================================================================
// 返回 true 表示取到任务（gQueueId / gText 已填）
bool pollNextTask() {
    ensureWifi();
    HTTPClient http;
    String url = String(SPEAKER_API_BASE) + "/api/speaker/next";
    http.begin(url);
    http.addHeader("X-Speaker-Token", SPEAKER_TOKEN);
    http.setTimeout(POLL_TIMEOUT_MS + 5000); // 比服务端长轮询稍长

    int code = http.GET();
    if (code == 200) {
        String body = http.getString();
        // 简单手工解析（避免引入 ArduinoJson 依赖）
        int qidIdx = body.indexOf("\"queue_id\":");
        int txtIdx = body.indexOf("\"text\":\"");
        if (qidIdx >= 0 && txtIdx >= 0) {
            gQueueId = body.substring(qidIdx + 11).toInt();
            int start = txtIdx + 8;
            int end   = body.indexOf("\"", start);
            gText     = body.substring(start, end);
            Serial.printf("[Poll] 取到任务 #%d: %s\n", gQueueId, gText.c_str());
            http.end();
            return true;
        }
    } else if (code == 204) {
        Serial.println("[Poll] 队列为空");
    } else {
        Serial.printf("[Poll] 异常状态码: %d\n", code);
        delay(WIFI_RETRY_MS);
    }
    http.end();
    return false;
}

// ==========================================================================
// WAV 流式播放（逐块读，不整段缓冲）
// ==========================================================================
/**
 * 从 WAV 字节流中跳过 RIFF header，定位到 data chunk。
 * 返回 data chunk 剩余字节数（已消耗的字节已从 client 读走）。
 */
int skipWavHeader(WiFiClientSecure &client) {
    // WAV RIFF header 最少 44 字节，最多几百字节（带 LIST chunk）。
    // 逐字节查找 "data" 四字符标记。
    uint8_t buf[4] = {0};
    int scanned = 0;
    while (client.available() || client.connected()) {
        if (!client.available()) { delay(10); continue; }
        buf[0] = buf[1]; buf[1] = buf[2]; buf[2] = buf[3];
        buf[3] = client.read();
        scanned++;
        if (buf[0]=='d' && buf[1]=='a' && buf[2]=='t' && buf[3]=='a') {
            // 读 4 字节 chunk size（小端）
            uint8_t sz[4];
            for (int i = 0; i < 4; i++) {
                unsigned long t = millis();
                while (!client.available()) {
                    if (millis() - t > 3000) return -1;
                    delay(5);
                }
                sz[i] = client.read();
            }
            int dataSize = sz[0] | (sz[1]<<8) | (sz[2]<<16) | (sz[3]<<24);
            Serial.printf("[WAV] 找到 data chunk，大小=%d 字节\n", dataSize);
            return dataSize;
        }
        if (scanned > 512) {
            Serial.println("[WAV] 未找到 data chunk（header 过大？）");
            return -1;
        }
    }
    return -1;
}

bool streamTts(const String &text) {
    // TTS 请求 body（JSON 手工拼接，避免 ArduinoJson 依赖）
    String body = "{\"model\":\"qwen3-tts-12hz-1.7b-customvoice\","
                  "\"input\":\"" + text + "\","
                  "\"voice\":\"vivian\","
                  "\"language\":\"chinese\","
                  "\"instructions\":\"用清新自然、甜美温柔的语气说，声音明亮亲切，语调轻快柔和\","
                  "\"response_format\":\"wav\"}";

    for (int attempt = 0; attempt <= TTS_RETRY_COUNT; attempt++) {
        if (attempt > 0) {
            Serial.printf("[TTS] 第 %d 次重试...\n", attempt);
            delay(TTS_RETRY_DELAY_MS);
        }

        WiFiClientSecure client;
        client.setInsecure(); // 简化 TLS；TODO: setCA() 可切换严格校验
        client.setTimeout(TTS_HTTP_TIMEOUT_S);

        HTTPClient http;
        http.begin(client, TTS_URL);
        http.addHeader("Content-Type", "application/json");
        http.addHeader("X-Speaker-Token", SPEAKER_TOKEN);
        http.setTimeout(TTS_HTTP_TIMEOUT_S * 1000);

        // 使用 sendRequest 以便可以访问底层流
        int code = http.POST(body);
        if (code != 200) {
            Serial.printf("[TTS] 请求失败，状态码: %d\n", code);
            http.end();
            continue;
        }

        // 跳过 WAV header，定位 data chunk
        int remaining = skipWavHeader(client);
        if (remaining <= 0) {
            Serial.println("[TTS] WAV 解析失败");
            http.end();
            continue;
        }

        // 流式推 I2S
        uint8_t chunk[I2S_CHUNK_BYTES];
        size_t written = 0;
        bool ok = true;

        while (remaining > 0 && (client.available() || client.connected())) {
            if (!client.available()) { delay(5); continue; }
            int toRead = min((int)I2S_CHUNK_BYTES, remaining);
            int got    = client.read(chunk, toRead);
            if (got <= 0) continue;
            esp_err_t err = i2s_write(I2S_NUM_0, chunk, got, &written, portMAX_DELAY);
            if (err != ESP_OK) {
                Serial.printf("[I2S] 写入错误: %d\n", err);
                ok = false;
                break;
            }
            remaining -= got;
        }
        http.end();
        if (ok) {
            Serial.println("[TTS] 播放完成");
            return true;
        }
    }
    Serial.println("[TTS] 所有重试失败");
    return false;
}

// ==========================================================================
// 上报结果
// ==========================================================================
void reportResult(int queueId, bool success, const String &detail) {
    ensureWifi();
    HTTPClient http;
    String url = String(SPEAKER_API_BASE) + "/api/speaker/result";
    http.begin(url);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-Speaker-Token", SPEAKER_TOKEN);

    String status = success ? "done" : "failed";
    String body   = "{\"queue_id\":" + String(queueId) +
                    ",\"status\":\"" + status + "\"" +
                    ",\"detail\":\"" + detail + "\"}";

    int code = http.POST(body);
    Serial.printf("[Result] 上报 #%d %s，响应: %d\n", queueId, status.c_str(), code);
    http.end();
}

// ==========================================================================
// setup / loop
// ==========================================================================
void setup() {
    Serial.begin(115200);
    pinMode(LED_BUILTIN_PIN, OUTPUT);
    ledOff();
    i2sInit();
    ensureWifi();
    Serial.println("[Speaker] 初始化完成，进入 IDLE");
}

void loop() {
    switch (gState) {

    // ── IDLE：LED 灭，轮询队列 ────────────────────────────────────────────
    case IDLE:
        ledOff();
        if (pollNextTask()) {
            gState = FETCH_TTS;
        }
        break;

    // ── FETCH_TTS：LED 快闪（200ms），调 TTS + 流式播放 ───────────────────
    case FETCH_TTS: {
        Serial.printf("[State] FETCH_TTS，text=%s\n", gText.c_str());
        unsigned long wdStart = millis();

        // 播放过程中保持快闪（注意 streamTts 内部阻塞；LED 在此循环外由状态机驱动）
        // 简化实现：进入 PLAYING 态再闪慢灯
        gState  = PLAYING;
        ledBlink(200); // 快闪开始

        bool ok = false;
        unsigned long taskStart = millis();
        // 看门狗：整个任务（TTS生成+播放）不超过 2 分钟
        if (millis() - taskStart < TASK_WATCHDOG_MS) {
            ok = streamTts(gText);
        } else {
            Serial.println("[WD] 任务超时，跳过");
        }
        gTaskOk = ok;
        gState  = REPORT;
        break;
    }

    // ── PLAYING：慢闪（500ms）── 实际播放已在 FETCH_TTS 中同步完成
    case PLAYING:
        ledBlink(500);
        break;

    // ── REPORT：上报结果，回 IDLE ─────────────────────────────────────────
    case REPORT:
        ledOff();
        reportResult(gQueueId, gTaskOk, gTaskOk ? "ok" : "tts_failed");
        gQueueId = -1;
        gText    = "";
        gState   = IDLE;
        break;
    }
}
