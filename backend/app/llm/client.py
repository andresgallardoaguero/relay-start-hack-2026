"""OpenAI-compatible transport with the API key read afresh for every call."""

import os

from openai import AsyncOpenAI


class OpenAICompatibleTransport:
    def __init__(self, base_url, model, provider = "openai-compatible", initial_api_key = ""):
        self.base_url = base_url
        self.model = model
        self.provider = provider
        self.initial_api_key = initial_api_key

    async def complete_json(self, system_prompt, user_prompt, max_tokens):
        api_key = os.environ.get("LLM_API_KEY", "") or self.initial_api_key
        if api_key == "":
            raise RuntimeError("LLM_API_KEY is not configured")
        client_options = {"api_key": api_key}
        if self.base_url != "":
            client_options["base_url"] = self.base_url
        client = AsyncOpenAI(**client_options)
        answer = await client.chat.completions.create(
            model = self.model,
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens = max_tokens,
            temperature = 0,
        )
        content = answer.choices[0].message.content
        if not isinstance(content, str) or content.strip() == "":
            raise ValueError("The model returned no text")
        return content
