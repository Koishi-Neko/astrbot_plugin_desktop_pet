"""astrbot_plugin_desktop_pet

为自研桌面桌宠壳（pet_shell/）提供对话接口的 AstrBot 插件。

路由（挂在 dashboard 插件扩展路径下，需带 plugin scope 的 API Key 鉴权）：
- POST /api/v1/plugins/extensions/desktop_pet/pet/chat
    请求体: {"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}
    响应: text/event-stream，帧序列：
      data: {"type": "emotion", "label": "高兴"}   # 首帧，情绪标签
      data: {"type": "delta", "text": "……"}        # 正文分句，若干帧
      data: {"type": "done"}                       # 结束
      （异常时先补一帧 {"type": "error", "message": "..."}）
- GET  /api/v1/plugins/extensions/desktop_pet/pet/health
    探活，返回 JSON。
"""

import asyncio
import json
import re
from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.star import Context, Star
from astrbot.api.web import request
from starlette.responses import JSONResponse, StreamingResponse

# 情绪集合需与 pet_shell/assets/ 下的立绘文件名一一对应
EMOTIONS = ["平静", "高兴", "生气", "害羞", "惊讶", "难过", "疑惑", "调皮"]

DEFAULT_PERSONA = (
    "你是住在用户电脑桌面上的小桌宠，性格活泼粘人，把用户当作最重要的人。"
)

EMOTION_INSTRUCTION = (
    "\n\n【输出格式要求】每次回复必须以情绪标签开头，格式为「【情绪】正文」，"
    "情绪只能从以下列表中选择一个：{emotions}。"
    "标签之后紧接回复正文。正文要口语化、简短（1~3 句），"
    "就像桌宠气泡里说的话。不要使用 markdown、列表或代码块，"
    "除开头的情绪标签外不要输出任何其他方括号标记。"
)

_EMOTION_TAG = re.compile(r"^\s*【([^】]{1,8})】\s*")
_SENTENCES = re.compile(r"[^。！？!?；;\n]+[。！？!?；;\n]*")

ERROR_FALLBACK_TEXT = "呜……好像连不上大脑了，等下再找我聊吧。"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class DesktopPetBridge(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}

    async def initialize(self):
        self.context.register_web_api(
            "desktop_pet/pet/chat",
            self.chat,
            ["POST"],
            "桌宠对话接口（SSE 流式）",
        )
        self.context.register_web_api(
            "desktop_pet/pet/health",
            self.health,
            ["GET"],
            "桌宠接口探活",
        )
        logger.info("[desktop_pet] web api registered: desktop_pet/pet/chat, desktop_pet/pet/health")

    async def terminate(self):
        logger.info("[desktop_pet] plugin terminated")

    # ---------- 路由处理 ----------

    async def health(self):
        prov = self.context.get_using_provider()
        return {
            "status": "ok",
            "plugin": "astrbot_plugin_desktop_pet",
            "provider_id": (self.config.get("provider_id") or "").strip() or None,
            "default_provider_available": prov is not None,
            "emotions": EMOTIONS,
        }

    async def chat(self):
        raw = await request.body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        message = str(body.get("message") or "").strip()
        history = body.get("history") or []
        if not message:
            return JSONResponse({"error": "message is required"}, status_code=400)
        if not isinstance(history, list):
            history = []

        return StreamingResponse(
            self._stream_reply(message, history),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------- 内部逻辑 ----------

    def _build_system_prompt(self) -> str:
        persona = str(self.config.get("persona") or DEFAULT_PERSONA).strip()
        return persona + EMOTION_INSTRUCTION.format(emotions="、".join(EMOTIONS))

    def _split_emotion(self, text: str) -> tuple[str, str]:
        """从回复开头解析【情绪】标签，返回 (情绪, 正文)。"""
        default = str(self.config.get("default_emotion") or "平静").strip()
        if default not in EMOTIONS:
            default = "平静"
        m = _EMOTION_TAG.match(text)
        if not m:
            return default, text
        label = m.group(1).strip()
        if label not in EMOTIONS:
            label = default
        return label, text[m.end():].strip()

    async def _call_llm(self, message: str, contexts: list[dict]) -> str:
        system_prompt = self._build_system_prompt()
        provider_id = (self.config.get("provider_id") or "").strip()
        if provider_id:
            resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=message,
                system_prompt=system_prompt,
                contexts=contexts or None,
            )
        else:
            prov = self.context.get_using_provider()
            if prov is None:
                raise RuntimeError("AstrBot 未配置可用的默认对话模型（LLM Provider）")
            resp = await prov.text_chat(
                prompt=message,
                system_prompt=system_prompt,
                contexts=contexts or None,
            )
        return (resp.completion_text or "").strip()

    async def _stream_reply(
        self, message: str, history: list
    ) -> AsyncGenerator[str, None]:
        # 清洗并裁剪历史
        max_turns = self.config.get("history_turns", 10)
        try:
            max_turns = max(0, int(max_turns))
        except (TypeError, ValueError):
            max_turns = 10
        contexts = [
            {"role": h.get("role"), "content": str(h.get("content"))}
            for h in history
            if isinstance(h, dict)
            and h.get("role") in ("user", "assistant")
            and h.get("content")
        ]
        if max_turns:
            contexts = contexts[-max_turns * 2 :]
        logger.info(f"[desktop_pet] chat: message_len={len(message)} contexts={len(contexts)}")

        try:
            text = await self._call_llm(message, contexts)
            if not text:
                raise RuntimeError("LLM 返回了空内容")
            emotion, body = self._split_emotion(text)
            yield _sse({"type": "emotion", "label": emotion})
            for seg in _SENTENCES.findall(body):
                seg = seg.strip()
                if seg:
                    yield _sse({"type": "delta", "text": seg})
                    await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"[desktop_pet] chat failed: {e}")
            yield _sse({"type": "emotion", "label": "难过"})
            yield _sse({"type": "delta", "text": ERROR_FALLBACK_TEXT})
            yield _sse({"type": "error", "message": str(e)})
        yield _sse({"type": "done"})
