import os
import subprocess
import sys

# Set the API key
api_key = "your-openai-api-key-here"

# Set environment variable
os.environ['OPENAI_API_KEY'] = api_key

# Verify it was set
print("API Key set:", os.getenv('OPENAI_API_KEY')[:10] + "..." if os.getenv('OPENAI_API_KEY') else "Not set")

# Run the test script
print("\nRunning test script...")
subprocess.run([sys.executable, "test_key.py"]) 