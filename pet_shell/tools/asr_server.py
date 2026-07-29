"""桌宠语音输入 ASR 服务（whisper-small int8 @ Intel NPU）。

OpenVINO GenAI WhisperPipeline 封装为本地 HTTP 服务：
  GET  /health      -> {"status": "ok", "device": "NPU", "model": "..."}
  POST /transcribe  -> body 为 16kHz 单声道 WAV 字节，返回 {"text": "..."}

模型加载：启动时预载（首次 NPU 编译需数分钟，之后走 ze_intel_npu_cache 秒载）；
NPU 加载失败自动回退 CPU 并在 /health 标注。
"""

import io
import os
import sys
import time

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import openvino_genai as ov_genai

MODEL_DIR = os.environ.get(
    "ASR_MODEL_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "whisper-small-int8-ov"),
)
HOST = os.environ.get("ASR_HOST", "127.0.0.1")
PORT = int(os.environ.get("ASR_PORT", "5055"))
DEVICE = os.environ.get("ASR_DEVICE", "NPU")

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
        result = pipe.generate(data.tolist())
        text = str(result).strip()
    except Exception as e:
        return JSONResponse({"error": f"inference failed: {e}"}, status_code=500)
    dt = time.time() - t0
    print(f"[asr] {len(data) / 16000:.1f}s audio -> {dt:.2f}s: {text[:60]}", flush=True)
    return {"text": text, "elapsed_s": round(dt, 3), "audio_s": round(len(data) / 16000, 2)}


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
