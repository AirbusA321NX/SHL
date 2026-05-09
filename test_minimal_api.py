import asyncio
import os
from openai import AsyncOpenAI

async def test_api():
    print("Testing minimal NVIDIA DeepSeek API connection...")
    
    api_key = "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"
    
    client = AsyncOpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
        timeout=10.0
    )

    try:
        response = await client.chat.completions.create(
            model="deepseek-ai/deepseek-v4-flash",
            messages=[{"role": "user", "content": "Say hello."}],
            max_tokens=20
        )
        
        print("\nAPI Connection Successful! ✓")
        print(response.choices[0].message.content)
        
    except Exception as e:
        print(f"\nAPI Connection Failed! Error: {type(e).__name__} - {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_api())
