import os
import sys

def set_api_key():
    print("Current API Key:", os.getenv('OPENAI_API_KEY', 'Not set'))
    
    # Get the API key from user
    api_key = input("Please enter your OpenAI API key: ").strip()
    
    if not api_key:
        print("No API key provided. Exiting...")
        return False
    
    # Set the environment variable
    os.environ['OPENAI_API_KEY'] = api_key
    
    # Verify it was set
    if os.getenv('OPENAI_API_KEY'):
        print("API key set successfully!")
        print("First 10 characters:", api_key[:10] + "...")
        return True
    else:
        print("Failed to set API key")
        return False

if __name__ == "__main__":
    set_api_key() 