from openai import OpenAI
import os
from dotenv import load_dotenv

load_dotenv()

# Super simple test
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

print("Making the simplest possible OpenAI request...")

try:
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hi"}]
    )
    print("✅ SUCCESS!")
    print("Response:", response.choices[0].message.content)
except Exception as e:
    print("❌ FAILED!")
    print("Error:", str(e)) 