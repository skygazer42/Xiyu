"""LLM 客户端 - 支持 Ollama、OpenAI、vLLM 兼容接口
"""
import httpx
import hashlib
import time
from typing import AsyncGenerator, List, Dict, Optional, Any
from dataclasses import dataclass
from collections import OrderedDict
import json
import logging

from .cancel_token import CancelToken

logger = logging.getLogger(__name__)


@dataclass
class LLMMessage:
    """LLM消息"""
    role: str       # "system", "user", "assistant"
    content: str


class LRUCache:
    """LRU 缓存，支持 TTL"""

    def __init__(self, max_size: int = 1000, ttl: int = 3600):
        self.max_size = max_size
        self.ttl = ttl
        self.cache: OrderedDict[str, tuple] = OrderedDict()

    def _make_key(self, messages: List[LLMMessage]) -> str:
        """生成缓存键"""
        content = "|".join(f"{m.role}:{m.content}" for m in messages)
        return hashlib.md5(content.encode()).hexdigest()

    def get(self, messages: List[LLMMessage]) -> Optional[str]:
        """获取缓存"""
        key = self._make_key(messages)
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                # 移到末尾 (最近使用)
                self.cache.move_to_end(key)
                return value
            else:
                # 过期删除
                del self.cache[key]
        return None

    def set(self, messages: List[LLMMessage], value: str) -> None:
        """设置缓存"""
        key = self._make_key(messages)
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = (value, time.time())

        # 超出大小限制时删除最旧的
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    def clear(self) -> None:
        """清空缓存"""
        self.cache.clear()

    def __len__(self) -> int:
        return len(self.cache)


class LLMClient:
    """
    LLM 客户端

    支持:
    - Ollama (本地)
    - OpenAI 兼容接口
    - vLLM (高吞吐推理)
    - 流式输出
    - 响应缓存
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen2.5:7b",
        api_key: str = "",
        backend: str = "auto",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cache_enable: bool = True,
        cache_size: int = 1000,
        cache_ttl: int = 3600,
    ):
        """
        初始化 LLM 客户端

        Args:
            base_url: API 基础 URL
            model: 模型名称
            api_key: API Key (OpenAI 兼容接口需要，Ollama 留空)
            backend: 后端类型 (auto, ollama, openai, vllm)
            temperature: 温度参数 (0-2)
            max_tokens: 最大生成 token 数
            timeout: 请求超时时间(秒)
            cache_enable: 是否启用缓存
            cache_size: 缓存大小
            cache_ttl: 缓存 TTL (秒)
        """
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.cache_enable = cache_enable

        # 初始化缓存
        self._cache = LRUCache(max_size=cache_size, ttl=cache_ttl) if cache_enable else None

        # 确定后端类型
        if backend == "auto":
            self.backend = self._detect_backend()
        else:
            self.backend = backend

        logger.info(f"LLM client initialized: backend={self.backend}, model={self.model}")

    def _detect_backend(self) -> str:
        """自动检测后端类型"""
        url_lower = self.base_url.lower()
        if "11434" in url_lower or "ollama" in url_lower:
            return "ollama"
        elif "vllm" in url_lower or "8000" in url_lower:
            return "vllm"
        else:
            return "openai"

    async def chat(
        self,
        messages: List[LLMMessage],
        stream: bool = False,
        use_cache: bool = True,
        cancel_token: Optional[CancelToken] = None,
    ) -> AsyncGenerator[str, None]:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            stream: 是否流式输出
            use_cache: 是否使用缓存
            cancel_token: 取消令牌，用于中断生成

        Yields:
            str: 生成的文本片段（如果stream=True）或完整响应（stream=False）
        """
        # 检查取消
        if cancel_token and cancel_token.is_cancelled:
            return

        # 检查缓存
        if use_cache and self._cache and not stream:
            cached = self._cache.get(messages)
            if cached is not None:
                logger.debug("LLM cache hit")
                yield cached
                return

        # 根据后端调用
        result_parts = []
        cancelled = False
        if self.backend == "ollama":
            async for chunk in self._chat_ollama(messages, stream):
                if cancel_token and cancel_token.is_cancelled:
                    logger.debug("LLM generation cancelled")
                    cancelled = True
                    break
                result_parts.append(chunk)
                yield chunk
        elif self.backend == "vllm":
            async for chunk in self._chat_vllm(messages, stream):
                if cancel_token and cancel_token.is_cancelled:
                    logger.debug("LLM generation cancelled")
                    cancelled = True
                    break
                result_parts.append(chunk)
                yield chunk
        else:
            async for chunk in self._chat_openai(messages, stream):
                if cancel_token and cancel_token.is_cancelled:
                    logger.debug("LLM generation cancelled")
                    cancelled = True
                    break
                result_parts.append(chunk)
                yield chunk

        # 保存到缓存 (取消的结果不缓存)
        if use_cache and self._cache and not stream and result_parts and not cancelled:
            self._cache.set(messages, "".join(result_parts))

    async def _chat_ollama(
        self,
        messages: List[LLMMessage],
        stream: bool
    ) -> AsyncGenerator[str, None]:
        """Ollama API 调用"""
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            }
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()

                if stream:
                    async for line in response.aiter_lines():
                        if line.strip():
                            try:
                                data = json.loads(line)
                                if "message" in data and "content" in data["message"]:
                                    yield data["message"]["content"]
                                if data.get("done"):
                                    break
                            except json.JSONDecodeError:
                                continue
                else:
                    content = await response.aread()
                    data = json.loads(content)
                    if "message" in data and "content" in data["message"]:
                        yield data["message"]["content"]

    async def _chat_openai(
        self,
        messages: List[LLMMessage],
        stream: bool
    ) -> AsyncGenerator[str, None]:
        """OpenAI 兼容 API 调用"""
        # Be tolerant with base_url formats:
        # - Some users configure `https://api.xxx.com` (no /v1)
        # - Some configure `https://api.xxx.com/v1` (already includes /v1)
        if self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"

        # 构建请求头
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()

                if stream:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            line_data = line[6:]
                            if line_data.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(line_data)
                                if "choices" in data and len(data["choices"]) > 0:
                                    delta = data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except json.JSONDecodeError:
                                continue
                else:
                    content = await response.aread()
                    data = json.loads(content)
                    if "choices" in data and len(data["choices"]) > 0:
                        yield data["choices"][0]["message"]["content"]

    async def _chat_vllm(
        self,
        messages: List[LLMMessage],
        stream: bool
    ) -> AsyncGenerator[str, None]:
        """vLLM API 调用 (OpenAI 兼容，但针对 vLLM 优化)

        vLLM 特点:
        - 支持 continuous batching
        - 支持 PagedAttention
        - 高吞吐量场景优化
        """
        if self.base_url.endswith("/v1"):
            url = f"{self.base_url}/chat/completions"
        else:
            url = f"{self.base_url}/v1/chat/completions"

        # 构建请求头
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": stream,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            # vLLM 特有参数
            "best_of": 1,
            "presence_penalty": 0.0,
            "frequency_penalty": 0.0,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()

                if stream:
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            line_data = line[6:]
                            if line_data.strip() == "[DONE]":
                                break
                            try:
                                data = json.loads(line_data)
                                if "choices" in data and len(data["choices"]) > 0:
                                    delta = data["choices"][0].get("delta", {})
                                    if "content" in delta:
                                        yield delta["content"]
                            except json.JSONDecodeError:
                                continue
                else:
                    content = await response.aread()
                    data = json.loads(content)
                    if "choices" in data and len(data["choices"]) > 0:
                        yield data["choices"][0]["message"]["content"]

    async def batch_chat(
        self,
        message_batches: List[List[LLMMessage]],
        max_concurrent: int = 5,
    ) -> List[str]:
        """批量聊天请求

        并发发送多个请求，提高吞吐量。

        Args:
            message_batches: 消息批次列表
            max_concurrent: 最大并发数

        Returns:
            响应列表
        """
        import asyncio

        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_single(messages: List[LLMMessage]) -> str:
            async with semaphore:
                result_parts = []
                async for chunk in self.chat(messages, stream=False):
                    result_parts.append(chunk)
                return "".join(result_parts)

        tasks = [process_single(batch) for batch in message_batches]
        return await asyncio.gather(*tasks)

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        if self._cache:
            return {
                "enabled": True,
                "size": len(self._cache),
                "max_size": self._cache.max_size,
                "ttl": self._cache.ttl,
            }
        return {"enabled": False}

    def clear_cache(self) -> None:
        """清空缓存"""
        if self._cache:
            self._cache.clear()


async def test_client():
    """测试客户端"""
    client = LLMClient()
    messages = [
        LLMMessage(role="system", content="你是一个智能助手"),
        LLMMessage(role="user", content="你好，请介绍一下自己")
    ]

    print("=== 非流式输出 ===")
    async for response in client.chat(messages, stream=False):
        print(response)

    print("\n=== 流式输出 ===")
    async for chunk in client.chat(messages, stream=True):
        print(chunk, end='', flush=True)
    print()

    print(f"\n=== 缓存统计 ===")
    print(client.get_cache_stats())


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_client())
