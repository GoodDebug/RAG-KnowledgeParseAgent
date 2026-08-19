# llm_adapters.py
# -*- coding: utf-8 -*-
import logging
import asyncio
import re


from typing import Iterator, Optional
from langchain_core.messages import (
    AIMessageChunk, HumanMessage, AIMessage, SystemMessage
)
from langchain.chat_models import (
    init_chat_model,
)  # 1.0 统一入口：根据 model + model_provider 创建聊天模型
from openai import OpenAI


def check_base_url(url: str) -> str:
    """
    处理base_url的规则：
    1. 如果url以#结尾，则移除#并直接使用用户提供的url
    2. 否则检查是否需要添加/v1后缀
    """
    url = url.strip()
    if not url:
        return url
        
    if url.endswith('#'):
        return url.rstrip('#')
        
    if not re.search(r'/v\d+$', url):
        if '/v1' not in url:
            url = url.rstrip('/') + '/v1'
    return url


class BaseLLMAdapter:
    """
    统一的 LLM 接口基类，为不同后端（OpenAI、Ollama、ML Studio、Gemini等）提供一致的方法签名。
    """
    def invoke(
        self,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIMessage:
        raise NotImplementedError("Subclasses must implement .invoke(prompt) method.")
    
    def stream(
        self,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[AIMessageChunk]:
        raise NotImplementedError("Subclasses must implement .stream(prompt) method.")
    
    def batch(
        self,
        requests: list[dict] = None,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> list[AIMessage | Iterator[AIMessageChunk] | str]:
        raise NotImplementedError("Subclasses must implement .batch(prompt) method.")
    
    async def ainvoke(self, **kwargs):
        # 同步invoke套一层异步，兼容async循环
        return await asyncio.to_thread(self.invoke, ** kwargs)    
    
    async def astream(self, **kwargs):
        return await asyncio.to_thread(self.stream, **kwargs)
    
    async def abatch(self, **kwargs):
        return await asyncio.to_thread(self.batch, **kwargs)


class DeepSeekAdapter(BaseLLMAdapter):
    """
    适配官方/OpenAI兼容接口
    """
    def __init__(self,  model_name: str, model_provider: str , api_key: str, base_url: str, max_tokens: int = None, temperature: float = 0.7, timeout: Optional[int] = 600 ,max_retries: int = 3):
        self.model_name = model_name
        self.model_provider = model_provider 
        self.base_url = check_base_url(base_url)
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

        self._client = init_chat_model(
            model=self.model_name,
            model_provider=self.model_provider,
            api_key=self.api_key,
            base_url=self.base_url,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )
        
    def _has_advanced_capabilities(self, **kwargs) -> bool:
        """检测是否传入了高级能力参数"""
        return any([
            kwargs.get("messages") is not None,
            kwargs.get("tools") is not None,
            kwargs.get("reasoning_effort") is not None,
            kwargs.get("thinking_enabled") is not None,
        ])


    def request(
        self,
        out_type: str = None,
        requests: list[dict] = None,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIMessage | Iterator[AIMessageChunk] | str | list[AIMessage] :
        if out_type is None:
            # 返回错误信息
            return "错误：未指定out_type参数"
        
        # ========== batch 独立分支（新增） ==========
        if out_type == "batch":
            if not requests:
                return "错误：未提供batch请求列表"
            batch_inputs = []
            for req in requests:
                if isinstance(req, str):
                    req = {"prompt": req}
                # 每个 req 复用消息构建逻辑（与 invoke/stream 的消息构建一致）
                if "messages" in req and req["messages"]:
                    req_msgs = list(req["messages"])
                    if req.get("system_prompt"):
                        if not req_msgs or not isinstance(req_msgs[0], SystemMessage):
                            req_msgs.insert(0, SystemMessage(content=req["system_prompt"]))
                else:
                    req_msgs = []
                    if req.get("system_prompt"):
                        req_msgs.append(SystemMessage(content=req["system_prompt"]))
                    req_msgs.append(HumanMessage(content=req.get("prompt", "")))
                    
                batch_inputs.append(req_msgs)    
                            
            try:
                # 像 invoke/stream 一样调用 client 的内置方法
                results = self._client.batch(batch_inputs, config={"max_concurrency": 5})
                return [
                    AIMessage(content=r.content if hasattr(r, 'content') else str(r)) for r in results
                ]
            except Exception as e:
                logging.exception(f"LLM batch failed, model={self.model_name}, err={str(e)}")
                raise
            
        if not self._has_advanced_capabilities(messages=messages, tools=tools, reasoning_effort=reasoning_effort, thinking_enabled=thinking_enabled):
            # 纯文本模式（向后兼容）
            temp = temperature if temperature is not None else self.temperature
            client = self._client
            kwargs = {}
            if temp != self.temperature:
                kwargs["temperature"] = temp
            
            # 纯文本分支也构建消息列表
            msgs = []
            if system_prompt:
                msgs.append(SystemMessage(content=system_prompt))
            msgs.append(HumanMessage(content=prompt))
                        
            try:
                if out_type == "invoke":
                    response = client.invoke(msgs, **kwargs)
                    return response
                elif out_type == "stream":
                    response = client.stream(msgs, **kwargs)
                    return response
                
            except Exception as e:
                logging.exception(f"LLM request failed, model={self.model_name}, err={str(e)}")
                raise 
                
        
        # 高级模式：构建消息列表
        if messages is None:
            msgs = []
            if system_prompt:
                msgs.append(SystemMessage(content=system_prompt))
            msgs.append(HumanMessage(content=prompt))
        else:
            msgs = list(messages)
            if system_prompt:
                msgs.insert(0, SystemMessage(content=system_prompt))
            
        # 构建带工具的 client
        client = self._client
        kwargs = {}
        
        if tools:
            client = client.bind_tools(tools)
        
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
            
        # DeepSeek 思考模式
        extra_body = {}
        if thinking_enabled is not None:
            extra_body["thinking"] = {"type": "enabled" if thinking_enabled else "disabled"}
        if extra_body:
            kwargs["extra_body"] = extra_body
        
        # 覆盖 temperature / max_tokens
        temp = temperature if temperature is not None else self.temperature
        if temp != self.temperature:
            kwargs["temperature"] = temp
        tokens = max_tokens if max_tokens is not None else self.max_tokens
        if tokens != self.max_tokens:
            kwargs["max_tokens"] = tokens
        
            # 分流处理
        try:
            if out_type == "invoke":
                response = client.invoke(msgs, **kwargs)
                return response  # 返回原始响应，保留 tool_calls
            elif out_type == "stream":
                response = client.stream(msgs, **kwargs)
                return response
        except Exception as e:
            logging.exception(f"LLM request failed, model={self.model_name}, err={str(e)}")
            raise 
    
    
    def invoke(
        self,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AIMessage:
        return self.request(
            out_type="invoke",
            prompt=prompt,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            thinking_enabled=thinking_enabled,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        
    def stream(
        self,
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Iterator[AIMessageChunk]:
        return self.request(
            out_type="stream",
            prompt=prompt,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            thinking_enabled=thinking_enabled,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    def batch(
        self,
        requests: list[dict],
        prompt: str = "",
        messages: list | None = None,
        tools: list | None = None,
        reasoning_effort: str | None = None,
        thinking_enabled: bool | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> list[AIMessage]:
        return self.request(
            out_type="batch",
            requests=requests,
            prompt=prompt,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            thinking_enabled=thinking_enabled,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,        
        )
        
# grok實現
class GrokAdapter(BaseLLMAdapter):
    """
    适配 xAI Grok API
    """
    def __init__(self, model_name: str, model_provider: str, api_key: str, base_url: str, max_tokens: int, temperature: float = 0.7, timeout: Optional[int] = 600):
        self.model_name = model_name
        self.model_provider = model_provider 
        self.base_url = check_base_url(base_url)
        self.api_key = api_key
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout
        )

    def invoke(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": "You are Grok, created by xAI."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                timeout=self.timeout
            )
            if response and response.choices:
                return response.choices[0].message.content
            else:
                logging.warning("No response from GrokAdapter.")
                return ""
        except Exception as e:
            logging.error(f"Grok API 调用失败: {e}")
            return ""

def create_llm_adapter(
    interface_format: str,
    model_name: str,
    model_provider: str,
    base_url: str,
    api_key: str,
    temperature: float,
    max_tokens: int,
    timeout: int
) -> BaseLLMAdapter:
    """
    工厂函数：根据 interface_format 返回不同的适配器实例。
    """
    fmt = interface_format.strip().lower()
    if fmt == "deepseek":
        return DeepSeekAdapter(model_name, model_provider, api_key, base_url, max_tokens, temperature, timeout)
    elif fmt == "grok":
        return GrokAdapter(model_name, model_provider ,api_key, base_url, max_tokens, temperature, timeout)
    else:
        raise ValueError(f"Unknown interface_format: {interface_format}")
