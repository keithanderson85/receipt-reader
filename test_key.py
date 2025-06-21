import os
import openai

# Print environment variable
print("API Key from environment:", os.getenv('OPENAI_API_KEY')[:10] + "..." if os.getenv('OPENAI_API_KEY') else "Not found")

# Try to initialize OpenAI client
try:
    client = openai.OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    print("OpenAI client initialized successfully")
    
    # Try a simple API call
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "Hello"}],
        max_tokens=5
    )
    print("API call successful!")
except Exception as e:
    print("Error:", str(e)) 