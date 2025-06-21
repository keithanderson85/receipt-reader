from openai import OpenAI

# Use the API key directly (not from environment)
api_key = "your-openai-api-key-here"

print("Testing with API key directly (not from .env)...")
print(f"API key starts with: {api_key[:20]}...")

client = OpenAI(api_key=api_key)

try:
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Say hello"}],
        max_tokens=10
    )
    print("✅ SUCCESS!")
    print("Response:", response.choices[0].message.content)
except Exception as e:
    print("❌ FAILED!")
    print("Error type:", type(e).__name__)
    print("Error:", str(e)[:200] + "..." if len(str(e)) > 200 else str(e)) 