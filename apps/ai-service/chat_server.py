from __future__ import annotations

import base64
import gc
import io
import os
import threading
import time
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from kernels import get_kernel
from pydantic import BaseModel, Field
from PIL import Image
from transformers import pipeline
from transformers.integrations.hub_kernels import _KERNEL_MODULE_MAPPING


app = FastAPI(title="PatternMate Qwen3-VL")
MODEL_PATH = os.getenv("QWEN_VL_MODEL", "/root/autodl-tmp/models/Qwen3-VL-4B-Instruct-FP8")
MODEL_NAME = os.getenv("QWEN_VL_SERVED_NAME", "qwen3-vl")
_pipe = None
_model_lock = threading.Lock()


def _load_fp8_kernel() -> None:
    if "finegrained-fp8" not in _KERNEL_MODULE_MAPPING:
        _KERNEL_MODULE_MAPPING["finegrained-fp8"] = get_kernel(
            "kernels-community/finegrained-fp8",
            version=3,
            trust_remote_code=True,
        )


def _weights_ready() -> bool:
    index = os.path.join(MODEL_PATH, "model.safetensors.index.json")
    if not os.path.isfile(index):
        return False
    return sum(
        os.path.getsize(os.path.join(MODEL_PATH, name))
        for name in os.listdir(MODEL_PATH)
        if name.endswith(".safetensors")
    ) >= 4_000_000_000


class ChatRequest(BaseModel):
    model: str = MODEL_NAME
    messages: list[dict[str, Any]] = Field(default_factory=list)
    temperature: float = 0.1
    max_tokens: int = 768


def _image(value: str) -> Image.Image:
    if not value.startswith("data:image/") or ";base64," not in value:
        raise ValueError("Only embedded PNG, JPEG and WebP images are accepted")
    return Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1]))).convert("RGB")


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "image_url":
                    parts.append({"type": "image", "image": _image(str((part.get("image_url") or {}).get("url", "")))})
                elif part.get("type") == "text":
                    parts.append({"type": "text", "text": str(part.get("text", ""))})
            content = parts
        prepared.append({"role": message.get("role", "user"), "content": content})
    return prepared


def _model():
    global _pipe
    if _pipe is None:
        if not _weights_ready():
            raise RuntimeError(f"Qwen3-VL weights are missing: {MODEL_PATH}")
        _load_fp8_kernel()
        _pipe = pipeline(
            "image-text-to-text",
            model=MODEL_PATH,
            device_map="cuda",
            dtype=torch.bfloat16,
            trust_remote_code=True,
        )
    return _pipe


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}] if _weights_ready() else []}


@app.post("/v1/chat/completions")
def chat(request: ChatRequest):
    try:
        generate_kwargs = {"do_sample": request.temperature > 0}
        if request.temperature > 0:
            generate_kwargs["temperature"] = request.temperature
        with _model_lock:
            generated = _model()(
                _messages(request.messages),
                max_new_tokens=request.max_tokens,
                generate_kwargs=generate_kwargs,
            )
        value = generated[0].get("generated_text", generated[0])
        if isinstance(value, list):
            value = value[-1].get("content", "")
        content = str(value)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"id": f"chatcmpl-{int(time.time() * 1000)}", "object": "chat.completion", "model": MODEL_NAME, "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


@app.post("/v1/unload")
def unload():
    global _pipe
    with _model_lock:
        _pipe = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
    return {"ok": True}
