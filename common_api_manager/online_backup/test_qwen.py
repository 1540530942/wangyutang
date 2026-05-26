
api_key = ""





# test_qwen.py
# -*- coding: utf-8 -*-

"""
阿里云百炼 / DashScope 单文件测试脚本

功能：
1. 直接运行：
   python test_qwen.py

   自动执行：
   - 文本问答测试
   - 自动生成测试图片
   - 图像理解测试

2. 文本问答：
   python test_qwen.py text "你好，介绍一下你自己"

3. 图像理解：
   python test_qwen.py image "D:/test.png" "这张图里有什么？"

4. 麦克风实时语音识别：
   python test_qwen.py asr 8

5. 语音问答：
   python test_qwen.py voice-chat 8

6. 语音 + 图像问答：
   python test_qwen.py voice-image "D:/test.png" 8

7. 列出麦克风设备：
   python test_qwen.py devices
"""

import os
import sys
import time
import base64
import queue
import zlib
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

import dashscope
from dashscope import Generation
from dashscope import MultiModalConversation


# ============================================================
# 0. API Key 与模型配置
# ============================================================

if load_dotenv is not None:
    load_dotenv()

# 方式1：推荐用环境变量或 .env
# .env 内容：
# DASHSCOPE_API_KEY=你的新sk

# 方式2：你本机自己硬编码
# 注意：不要把真实 Key 上传 GitHub 或发给别人


API_KEY = (
    api_key.strip()
    or os.getenv("DASHSCOPE_API_KEY", "").strip()
)

if not API_KEY:
    raise RuntimeError(
        "\n没有找到 DashScope API Key。\n\n"
        "你可以任选一种方式：\n\n"
        "方式1：在当前目录新建 .env 文件，内容为：\n"
        "DASHSCOPE_API_KEY=你的新sk\n\n"
        "方式2：打开 test_qwen.py，把 api_key = \"\" 改成：\n"
        "api_key = \"你的新sk\"\n"
    )

dashscope.api_key = API_KEY

# 中国内地北京地域
dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
REALTIME_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

TEXT_MODEL = os.getenv("TEXT_MODEL", "qwen-turbo")

# 更稳妥默认用 qwen-vl-plus；如果你账号支持 qwen3-vl-plus / qwen3.6-plus，可改环境变量
VISION_MODEL = os.getenv("VISION_MODEL", "qwen-vl-plus")

ASR_MODEL = os.getenv("ASR_MODEL", "qwen3-asr-flash-realtime")

DEFAULT_SAMPLE_RATE = 16000
DEFAULT_BLOCK_SIZE = 3200


# ============================================================
# 1. 文本问答
# ============================================================

def extract_text_response(response: Any) -> str:
    """
    兼容不同 DashScope SDK 版本的文本返回结构。
    """
    try:
        status_code = getattr(response, "status_code", None)
        if status_code is not None and status_code != 200:
            return f"调用失败：{response}"
    except Exception:
        pass

    try:
        return response.output.choices[0].message.content
    except Exception:
        pass

    try:
        return response["output"]["choices"][0]["message"]["content"]
    except Exception:
        pass

    try:
        return response.output.text
    except Exception:
        pass

    return str(response)


def chat_text(prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    文本模型调用。
    """
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": "你是一个严谨、清晰、实用的中文语音助手。"
        }
    ]

    if history:
        messages.extend(history)

    messages.append({
        "role": "user",
        "content": prompt
    })

    response = Generation.call(
        api_key=API_KEY,
        model=TEXT_MODEL,
        messages=messages,
        result_format="message",
    )

    return extract_text_response(response)


# ============================================================
# 2. 自动生成测试图片，不依赖 PIL
# ============================================================

def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    """
    写 PNG chunk。
    """
    return (
        struct.pack(">I", len(data))
        + chunk_type
        + data
        + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xFFFFFFFF)
    )


def create_demo_png(path: str = "demo_qwen_image.png") -> str:
    """
    生成一张简单测试图片：
    - 白底
    - 左侧红色矩形
    - 右侧蓝色圆形
    - 下方绿色横条

    不依赖 Pillow，纯标准库生成 PNG。
    """
    width, height = 480, 300

    white = (255, 255, 255)
    red = (220, 40, 40)
    blue = (40, 90, 220)
    green = (40, 170, 90)
    black = (20, 20, 20)

    rows = []

    for y in range(height):
        row = bytearray()
        for x in range(width):
            color = white

            # 黑色边框
            if x < 4 or x >= width - 4 or y < 4 or y >= height - 4:
                color = black

            # 红色矩形
            if 60 <= x <= 190 and 70 <= y <= 190:
                color = red

            # 蓝色圆形
            cx, cy, r = 340, 130, 65
            if (x - cx) ** 2 + (y - cy) ** 2 <= r ** 2:
                color = blue

            # 绿色横条
            if 80 <= x <= 400 and 230 <= y <= 260:
                color = green

            row.extend(color)

        # 每行前面加 filter type 0
        rows.append(b"\x00" + bytes(row))

    raw = b"".join(rows)

    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw, level=9))
    png += _png_chunk(b"IEND", b"")

    out_path = Path(path).resolve()
    with open(out_path, "wb") as f:
        f.write(png)

    return str(out_path)


# ============================================================
# 3. 图像理解
# ============================================================

def image_path_to_file_uri(image_path: str) -> str:
    """
    把本地图片路径转换成 DashScope 可识别的 file:// URI。

    Windows:
        D:/test.png
        -> file://D:/test.png

    Linux/macOS:
        /home/user/test.png
        -> file:///home/user/test.png
    """
    p = Path(image_path).expanduser().resolve()

    if not p.exists():
        raise FileNotFoundError(f"图片不存在：{p}")

    return "file://" + str(p).replace("\\", "/")


def extract_vision_response(response: Any) -> str:
    """
    解析 MultiModalConversation 返回。
    """
    try:
        status_code = getattr(response, "status_code", None)
        if status_code is not None and status_code != 200:
            return f"调用失败：{response}"
    except Exception:
        pass

    try:
        content = response.output.choices[0].message.content
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item:
                        texts.append(str(item["text"]))
                    else:
                        texts.append(str(item))
                else:
                    texts.append(str(item))
            return "\n".join(texts).strip()
        return str(content)
    except Exception:
        pass

    try:
        content = response["output"]["choices"][0]["message"]["content"]
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    texts.append(str(item.get("text", item)))
                else:
                    texts.append(str(item))
            return "\n".join(texts).strip()
        return str(content)
    except Exception:
        pass

    return str(response)


def chat_image(image_path: str, question: str) -> str:
    """
    图像理解调用。
    """
    image_uri = image_path_to_file_uri(image_path)

    messages = [
        {
            "role": "system",
            "content": [
                {
                    "text": "你是一个专业的图像理解助手。请准确识别图片内容、文字、图表和关键信息。"
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"image": image_uri},
                {"text": question},
            ],
        },
    ]

    response = MultiModalConversation.call(
        api_key=API_KEY,
        model=VISION_MODEL,
        messages=messages,
    )

    return extract_vision_response(response)


# ============================================================
# 4. 实时语音识别 ASR
# ============================================================

def _load_asr_sdk():
    """
    懒加载 ASR 相关模块。
    这样即使 ASR SDK 部分有问题，文本/图像测试也能先跑。
    """
    try:
        import sounddevice as sd
    except ImportError as e:
        raise ImportError(
            "未安装 sounddevice，请执行：python -m pip install sounddevice"
        ) from e

    try:
        from dashscope.audio.qwen_omni import (
            OmniRealtimeConversation,
            OmniRealtimeCallback,
            MultiModality,
        )
        from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams
    except ImportError as e:
        raise ImportError(
            "\nDashScope 实时语音模块导入失败。\n"
            "请执行：python -m pip install -U \"dashscope>=1.25.6\"\n\n"
            f"原始错误：{e}"
        ) from e

    return sd, OmniRealtimeConversation, OmniRealtimeCallback, MultiModality, TranscriptionParams


def list_microphones():
    """
    列出本机音频设备。
    """
    sd, *_ = _load_asr_sdk()
    print(sd.query_devices())


def record_and_recognize(
    seconds: int = 8,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    block_size: int = DEFAULT_BLOCK_SIZE,
) -> str:
    """
    从电脑麦克风采集音频，实时发送给 qwen3-asr-flash-realtime。
    """
    sd, OmniRealtimeConversation, OmniRealtimeCallback, MultiModality, TranscriptionParams = _load_asr_sdk()

    class RealtimeASRCallback(OmniRealtimeCallback):
        def __init__(self):
            super().__init__()
            self.final_texts: List[str] = []
            self.partial_text: str = ""

        def on_open(self):
            print("[ASR] WebSocket 已连接")

        def on_close(self, code, msg):
            print(f"\n[ASR] WebSocket 已关闭：code={code}, msg={msg}")

        def on_event(self, response: dict):
            event_type = response.get("type", "")

            if event_type == "session.created":
                session_id = response.get("session", {}).get("id", "")
                print(f"[ASR] 会话已创建：{session_id}")

            elif event_type == "session.updated":
                print("[ASR] 会话配置已更新")

            elif event_type == "input_audio_buffer.speech_started":
                print("\n[ASR] 检测到开始说话")

            elif event_type == "input_audio_buffer.speech_stopped":
                print("\n[ASR] 检测到说话结束")

            elif event_type == "conversation.item.input_audio_transcription.text":
                text = response.get("text", "")
                stash = response.get("stash", "")
                partial = f"{text}{stash}".strip()

                if partial:
                    self.partial_text = partial
                    print(f"\r[ASR] 中间识别：{partial}", end="", flush=True)

            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = response.get("transcript", "")
                if transcript:
                    self.final_texts.append(transcript)
                    print(f"\n[ASR] 最终识别：{transcript}")

            elif event_type == "session.finished":
                transcript = response.get("transcript", "")
                if transcript and transcript not in self.final_texts:
                    self.final_texts.append(transcript)
                    print(f"\n[ASR] 会话最终结果：{transcript}")

            elif event_type == "error":
                print(f"\n[ASR] 错误：{response}")

        def get_final_text(self) -> str:
            text = "".join(self.final_texts).strip()
            if text:
                return text
            return self.partial_text.strip()

    callback = RealtimeASRCallback()

    conversation = OmniRealtimeConversation(
        model=ASR_MODEL,
        url=REALTIME_URL,
        callback=callback,
    )

    audio_queue: queue.Queue[bytes] = queue.Queue()

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"[麦克风状态] {status}")
        audio_queue.put(bytes(indata))

    try:
        conversation.connect()

        transcription_params = TranscriptionParams(
            language="zh",
            sample_rate=sample_rate,
            input_audio_format="pcm",
        )

        conversation.update_session(
            output_modalities=[MultiModality.TEXT],
            enable_turn_detection=True,
            turn_detection_type="server_vad",
            turn_detection_threshold=0.0,
            turn_detection_silence_duration_ms=700,
            enable_input_audio_transcription=True,
            transcription_params=transcription_params,
        )

        print(f"\n请开始说话，录音 {seconds} 秒。")
        print("如果没有声音，请检查 Windows 麦克风权限和默认输入设备。\n")

        with sd.RawInputStream(
            samplerate=sample_rate,
            blocksize=block_size,
            dtype="int16",
            channels=1,
            callback=audio_callback,
        ):
            start = time.time()
            while time.time() - start < seconds:
                try:
                    audio_chunk = audio_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                audio_b64 = base64.b64encode(audio_chunk).decode("ascii")
                conversation.append_audio(audio_b64)

        print("\n[ASR] 录音结束，等待服务端完成最终识别...")

        try:
            conversation.end_session(timeout=20)
        except TypeError:
            conversation.end_session()
            time.sleep(2)

    finally:
        try:
            conversation.close()
        except Exception:
            pass

    return callback.get_final_text()


# ============================================================
# 5. 自动 Demo：构造输入和输出
# ============================================================

def run_auto_demo():
    """
    不传参数时自动运行：
    1. 文本测试
    2. 自动生成图片
    3. 图像理解测试
    """
    print("\n========== 当前配置 ==========")
    print(f"TEXT_MODEL   = {TEXT_MODEL}")
    print(f"VISION_MODEL = {VISION_MODEL}")
    print(f"ASR_MODEL    = {ASR_MODEL}")

    # 1. 文本测试
    text_input = "你好，请用三句话介绍一下你自己，并说明你能做什么。"

    print("\n========== 文本测试输入 ==========")
    print(text_input)

    try:
        text_output = chat_text(text_input)
    except Exception as e:
        text_output = f"文本测试失败：{e}"

    print("\n========== 文本测试输出 ==========")
    print(text_output)

    # 2. 自动生成图片
    try:
        demo_image = create_demo_png("demo_qwen_image.png")
        print("\n========== 已自动生成测试图片 ==========")
        print(demo_image)
    except Exception as e:
        print("\n生成测试图片失败：", e)
        demo_image = ""

    # 3. 图像理解测试
    if demo_image:
        image_question = (
            "请观察这张图片，回答："
            "1）图片中有哪些主要图形？"
            "2）分别是什么颜色？"
            "3）请用简洁中文总结。"
        )

        print("\n========== 图像测试输入 ==========")
        print("图片路径：", demo_image)
        print("问题：", image_question)

        try:
            image_output = chat_image(demo_image, image_question)
        except Exception as e:
            image_output = f"图像测试失败：{e}"

        print("\n========== 图像测试输出 ==========")
        print(image_output)

    print("\n========== 语音测试命令 ==========")
    print("实时语音识别：python test_qwen.py asr 8")
    print("语音问答：    python test_qwen.py voice-chat 8")
    print("语音+图像：   python test_qwen.py voice-image demo_qwen_image.png 8")


# ============================================================
# 6. 多轮文字聊天
# ============================================================

def interactive_text_chat():
    history: List[Dict[str, str]] = []

    print("进入多轮文字聊天。输入 exit 退出。\n")

    while True:
        user_input = input("你：").strip()

        if user_input.lower() in {"exit", "quit", "q"}:
            break

        if not user_input:
            continue

        answer = chat_text(user_input, history=history)
        print(f"AI：{answer}\n")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": answer})

        if len(history) > 20:
            history = history[-20:]


# ============================================================
# 7. 命令行入口
# ============================================================

def print_usage():
    print(
        f"""
当前配置：
  TEXT_MODEL   = {TEXT_MODEL}
  VISION_MODEL = {VISION_MODEL}
  ASR_MODEL    = {ASR_MODEL}

用法：

0）自动 Demo：
  python test_qwen.py

1）文本测试：
  python test_qwen.py text "你好，介绍一下你自己"

2）多轮文字聊天：
  python test_qwen.py chat

3）图像理解：
  python test_qwen.py image "D:/test.png" "这张图里有什么？请提取文字。"

4）实时语音识别：
  python test_qwen.py asr 8

5）语音问答：
  python test_qwen.py voice-chat 8

6）语音 + 图像问答：
  python test_qwen.py voice-image "D:/test.png" 8

7）列出麦克风设备：
  python test_qwen.py devices

说明：
  - asr 8 表示录音 8 秒。
  - Windows 图片路径建议写成 D:/test.png。
  - 如果要换模型，可设置环境变量 TEXT_MODEL / VISION_MODEL / ASR_MODEL。
"""
    )


def main():
    # 不传参数：自动跑测试
    if len(sys.argv) < 2:
        run_auto_demo()
        return

    mode = sys.argv[1].strip().lower()

    if mode == "help":
        print_usage()

    elif mode == "demo":
        run_auto_demo()

    elif mode == "text":
        prompt = sys.argv[2] if len(sys.argv) >= 3 else "你好，介绍一下你自己"
        print("\n========== 文本输入 ==========")
        print(prompt)
        answer = chat_text(prompt)
        print("\n========== 文本输出 ==========")
        print(answer)

    elif mode == "chat":
        interactive_text_chat()

    elif mode == "image":
        if len(sys.argv) < 3:
            print("请传入图片路径，例如：python test_qwen.py image D:/test.png")
            return

        image_path = sys.argv[2]
        question = (
            sys.argv[3]
            if len(sys.argv) >= 4
            else "请描述这张图片，并提取其中的文字和关键信息。"
        )

        print("\n========== 图像输入 ==========")
        print("图片路径：", image_path)
        print("问题：", question)

        answer = chat_image(image_path, question)
        print("\n========== 图像输出 ==========")
        print(answer)

    elif mode == "asr":
        seconds = int(sys.argv[2]) if len(sys.argv) >= 3 else 8
        text = record_and_recognize(seconds=seconds)

        print("\n========== ASR 输出 ==========")
        print(text if text else "[没有识别到文本]")

    elif mode == "voice-chat":
        seconds = int(sys.argv[2]) if len(sys.argv) >= 3 else 8
        text = record_and_recognize(seconds=seconds)

        print("\n========== ASR 输出 ==========")
        print(text if text else "[没有识别到文本]")

        if not text:
            return

        answer = chat_text(text)
        print("\n========== 大模型输出 ==========")
        print(answer)

    elif mode == "voice-image":
        if len(sys.argv) < 3:
            print("请传入图片路径，例如：python test_qwen.py voice-image D:/test.png 8")
            return

        image_path = sys.argv[2]
        seconds = int(sys.argv[3]) if len(sys.argv) >= 4 else 8

        question = record_and_recognize(seconds=seconds)

        print("\n========== ASR 识别到的问题 ==========")
        print(question if question else "[没有识别到文本]")

        if not question:
            return

        answer = chat_image(image_path, question)
        print("\n========== 语音 + 图像输出 ==========")
        print(answer)

    elif mode == "devices":
        list_microphones()

    else:
        print(f"未知模式：{mode}")
        print_usage()


if __name__ == "__main__":
    main()
