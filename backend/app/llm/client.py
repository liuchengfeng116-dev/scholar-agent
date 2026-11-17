"""大模型统一访问层（通义千问 · DashScope OpenAI 兼容模式）。

封装：普通对话、流式对话、JSON 结构化输出、Function Calling、
批量向量化、视觉理解（OCR / 公式识别）。上层模块只依赖本文件，
更换模型厂商只需修改 .env 中的 base_url 与模型名。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from app.core.config import settings

log = logging.getLogger(__name__)

_client: OpenAI | None = None
_lock = threading.Lock()

# 全局 Token 用量统计（用于技术文档中的成本分析）
usage_stats: dict[str, dict[str, int]] = {}


def client() -> OpenAI:
    global _client
    with _lock:
        if _client is None:
            if not settings.dashscope_api_key:
                raise RuntimeError("未配置 DASHSCOPE_API_KEY，请在 .env 中设置")
            _client = OpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.llm_base_url,
                timeout=120,
                max_retries=0,  # 重试由 _with_retry 统一控制
            )
        return _client


def _record_usage(model: str, usage: Any) -> None:
    if not usage:
        return
    s = usage_stats.setdefault(model, {"calls": 0, "prompt": 0, "completion": 0})
    s["calls"] += 1
    s["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
    s["completion"] += getattr(usage, "completion_tokens", 0) or 0


def _with_retry(fn, *, retries: int = 4):
    """指数退避重试：限流、网络错误、5xx。"""
    delay = 1.5
    for attempt in range(retries + 1):
        try:
            return fn()
        except (RateLimitError, APIConnectionError) as e:
            err = e
        except APIStatusError as e:
            if e.status_code < 500 and e.status_code != 429:
                raise
            err = e
        if attempt == retries:
            raise err
        log.warning("LLM 调用失败，%.1fs 后重试（%s）", delay, err)
        time.sleep(delay)
        delay *= 2


# ----------------------------------------------------------------------------
# 对话
# ----------------------------------------------------------------------------
def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    enable_thinking: bool | None = None,
) -> Any:
    """返回完整的 ChatCompletion message 对象。"""
    model = model or settings.llm_model
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if enable_thinking is not None:
        kwargs["extra_body"] = {"enable_thinking": enable_thinking}

    resp = _with_retry(lambda: client().chat.completions.create(**kwargs))
    _record_usage(model, resp.usage)
    return resp.choices[0].message


def complete(prompt: str, *, system: str | None = None, **kw) -> str:
    """单轮文本补全的便捷函数。"""
    msgs = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    return (chat(msgs, **kw).content or "").strip()


def chat_stream(
    messages: list[dict], *, model: str | None = None, temperature: float = 0.3
) -> Iterator[str]:
    """流式输出文本增量。"""
    model = model or settings.llm_model

    def _create():
        return client().chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )

    stream = _with_retry(_create)
    for chunk in stream:
        if chunk.usage:
            _record_usage(model, chunk.usage)
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


_JSON_BLOCK = re.compile(r"```(?:json)?\s*([\s\S]*?)```")


def parse_json(text: str) -> Any:
    """容错解析模型输出的 JSON（去除 markdown 代码块、截取首个对象/数组）。"""
    text = text.strip()
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        s, e = text.find(open_c), text.rfind(close_c)
        if s != -1 and e > s:
            try:
                return json.loads(text[s : e + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"无法解析 JSON：{text[:200]}")


def chat_json(prompt: str, *, system: str | None = None, retries: int = 2, **kw) -> Any:
    """要求模型输出 JSON，并做解析失败重试。"""
    sys_msg = (system or "你是严谨的学术助手。") + "\n只输出合法 JSON，不要输出任何解释。"
    last_err: Exception | None = None
    for _ in range(retries + 1):
        out = complete(prompt, system=sys_msg, json_mode=True, **kw)
        try:
            return parse_json(out)
        except ValueError as e:
            last_err = e
    raise last_err  # type: ignore[misc]


# ----------------------------------------------------------------------------
# 向量
# ----------------------------------------------------------------------------
EMBED_BATCH = 10  # DashScope text-embedding-v3 单次最多 10 条
EMBED_MAX_CHARS = 6000


def embed(texts: list[str], *, dim: int = 1024) -> list[list[float]]:
    """批量向量化，自动分批。"""
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [(t or " ")[:EMBED_MAX_CHARS] for t in texts[i : i + EMBED_BATCH]]

        def _create(b=batch):
            return client().embeddings.create(
                model=settings.embedding_model, input=b, dimensions=dim, encoding_format="float"
            )

        resp = _with_retry(_create)
        _record_usage(settings.embedding_model, resp.usage)
        out.extend(d.embedding for d in sorted(resp.data, key=lambda d: d.index))
    return out


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


# ----------------------------------------------------------------------------
# 视觉
# ----------------------------------------------------------------------------
def vision(
    image: bytes,
    prompt: str,
    *,
    model: str | None = None,
    mime: str = "image/png",
    max_tokens: int = 4096,
) -> str:
    """图像理解：扫描页 OCR、公式转 LaTeX、表格结构化、图表描述。"""
    model = model or settings.vlm_model
    b64 = base64.b64encode(image).decode()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    resp = _with_retry(
        lambda: client().chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=max_tokens
        )
    )
    _record_usage(model, resp.usage)
    return (resp.choices[0].message.content or "").strip()
