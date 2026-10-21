import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


async def main() -> int:
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    base = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
    if not key:
        print("FAIL: NVIDIA_API_KEY is missing. Add it to .env (never paste it into GitHub).")
        return 2
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: NVIDIA connection OK"}],
        "temperature": 0,
        "max_tokens": 30,
    }
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(f"{base}/chat/completions", headers=headers, json=body)
        if response.status_code >= 400:
            print(f"FAIL: NVIDIA returned HTTP {response.status_code}. Check model access and API key.")
            print(response.text[:500])
            return 1
        data = response.json()
        print("PASS: NVIDIA API responded.")
        print("Model:", data.get("model", model))
        print("Reply:", data.get("choices", [{}])[0].get("message", {}).get("content", "(empty)"))
        return 0
    except httpx.RequestError as exc:
        print(f"FAIL: Network/API request error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
