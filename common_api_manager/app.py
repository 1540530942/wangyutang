from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from common.settings import settings
from modules.asr_transcribe import router as asr_transcribe_router
from modules.audio_convert import router as audio_convert_router
from modules.dashscope_qwen_chat import router as dashscope_qwen_chat_router
from modules.dashscope_qwen_vision import router as dashscope_qwen_vision_router
from modules.health_check import router as health_check_router
from modules.model_studio_catalog import router as model_studio_catalog_router
from modules.lv_qwen_asr import router as lv_qwen_asr_router
from modules.lv_qwen_chat import router as lv_qwen_chat_router
from modules.lv_qwen_tts import router as lv_qwen_tts_router
from modules.lv_qwen_vision import router as lv_qwen_vision_router
from modules.spark_qwen_chat import router as spark_qwen_chat_router
from modules.spark_qwen_vision import router as spark_qwen_vision_router

app = FastAPI(title="Wangyutang Common API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
app.include_router(health_check_router)
app.include_router(model_studio_catalog_router)
app.include_router(asr_transcribe_router)
app.include_router(audio_convert_router)
app.include_router(lv_qwen_asr_router)
app.include_router(lv_qwen_chat_router)
app.include_router(dashscope_qwen_chat_router)
app.include_router(dashscope_qwen_vision_router)
app.include_router(lv_qwen_tts_router)
app.include_router(lv_qwen_vision_router)
app.include_router(spark_qwen_chat_router)
app.include_router(spark_qwen_vision_router)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(f"{settings.static_dir}/index.html")


@app.get("/model-studio")
def model_studio() -> FileResponse:
    return FileResponse(f"{settings.static_dir}/model_studio.html")


@app.get("/robot-skills")
def robot_skills() -> FileResponse:
    return FileResponse(f"{settings.static_dir}/robot_skills.html")


@app.get("/audio-convert")
def audio_convert() -> FileResponse:
    return FileResponse(f"{settings.static_dir}/audio_convert.html")
