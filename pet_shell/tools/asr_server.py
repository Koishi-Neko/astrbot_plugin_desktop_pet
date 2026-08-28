"""桌宠语音输入 ASR 服务（whisper-large-v3-turbo-fp16 @ Intel NPU）。

OpenVINO GenAI WhisperPipeline 封装为本地 HTTP 服务：
  GET  /health      -> {"status": "ok", "device": "NPU", "model": "..."}
  POST /transcribe  -> body 为 16kHz 单声道 WAV 字节，返回 {"text": "..."}

模型加载：启动时预载（首次 NPU 编译约 4 分钟，之后走 ze_intel_npu_cache 秒载）；
NPU 加载失败自动回退 CPU 并在 /health 标注。
默认模型为 large-v3-turbo-fp16（2026-07-31 验证：NPU 中文/英文均正确，
中文 7.15s 音频 ~1.65s）；whisper-small-int8 在 NPU 上中文解码损坏，勿改回。
"""

import io
import os
import sys
import time

# pythonw（无控制台）下 sys.stdout/stderr 为 None，print 会直接崩——重定向到 null 保证安全
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import openvino_genai as ov_genai

MODEL_DIR = os.environ.get(
    "ASR_MODEL_DIR",
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "models",
        "whisper-large-v3-turbo-fp16-ov",
    ),
)
HOST = os.environ.get("ASR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASR_PORT", "15055"))
DEVICE = os.environ.get("ASR_DEVICE", "NPU")
# 显式锁定识别语言：openvino-genai WhisperPipeline 跨请求持有语言状态
# （EN 请求后中文会被"翻译"成英文，实测粘性 bug），默认固定 <|zh|>；
# 语言键必须是模型 generation_config.json 里 lang_to_id 的 token 形式
# （<|zh|>/<|en|>/<|ja|>…，用 "zh"/"chinese" 会报 "'language' not in lang_to_id"），
# 留空 = 自动检测（仅当换纯英文使用场景时改，且注意粘性 bug 仍会存在）。
LANGUAGE = os.environ.get("ASR_LANGUAGE", "<|zh|>")

app = FastAPI()
pipe = None
device_used = None
load_error = None


def _load(device: str):
    global pipe, device_used
    t0 = time.time()
    p = ov_genai.WhisperPipeline(MODEL_DIR, device)
    print(f"[asr] model loaded on {device} in {time.time() - t0:.1f}s", flush=True)
    pipe = p
    device_used = device


@app.on_event("startup")
def startup():
    global load_error
    try:
        _load(DEVICE)
    except Exception as e:
        print(f"[asr] load on {DEVICE} failed: {e}; falling back to CPU", flush=True)
        try:
            _load("CPU")
            load_error = f"NPU fallback: {e}"
        except Exception as e2:
            load_error = f"load failed: {e2}"
            print(f"[asr] load on CPU failed: {e2}", flush=True)


@app.get("/health")
def health():
    return {
        "status": "ok" if pipe is not None else "error",
        "device": device_used,
        "model": os.path.basename(MODEL_DIR),
        "load_error": load_error,
    }


@app.post("/transcribe")
async def transcribe(request: Request):
    if pipe is None:
        return JSONResponse({"error": f"model not loaded: {load_error}"}, status_code=503)
    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty body"}, status_code=400)
    try:
        data, sr = sf.read(io.BytesIO(body), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if sr != 16000:
            return JSONResponse({"error": f"expect 16kHz wav, got {sr}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"bad wav: {e}"}, status_code=400)
    if len(data) < 1600:  # <0.1s
        return {"text": ""}
    t0 = time.time()
    try:
        options = {}
        initial_prompt = request.query_params.get("initial_prompt")
        if initial_prompt:
            options["initial_prompt"] = initial_prompt
        if LANGUAGE:
            options["language"] = LANGUAGE
        result = None
        try:
            result = pipe.generate(data.tolist(), **options)
        except Exception:
            if "initial_prompt" not in options:
                raise
            # NPU 静态形状管道不支持 initial_prompt（OpenVINO make_tensor roi_end 校验
            # 失败），降级为不带热词重试——宁可识别不准也不能让语音输入整体 500
            print("[asr] initial_prompt 推理失败，降级重试（不带热词）", flush=True)
            options.pop("initial_prompt")
            result = pipe.generate(data.tolist(), **options)
        text = str(result).strip()
    except Exception as e:
        return JSONResponse({"error": f"inference failed: {e}"}, status_code=500)
    dt = time.time() - t0
    print(f"[asr] {len(data) / 16000:.1f}s audio -> {dt:.2f}s: {text[:60]}", flush=True)
    return {"text": text, "elapsed_s": round(dt, 3), "audio_s": round(len(data) / 16000, 2)}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
