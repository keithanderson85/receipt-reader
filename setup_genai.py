#!/usr/bin/env python3

import os
import subprocess
import sys

def check_openai_installed():
    """Check if OpenAI package is installed."""
    try:
        import openai
        return True
    except ImportError:
        return False

def install_openai():
    """Install OpenAI package."""
    print("?? Installing OpenAI package...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai"])
        print("? OpenAI package installed successfully!")
        return True
    except subprocess.CalledProcessError:
        print("? Failed to install OpenAI package")
        return False

def check_api_key():
    """Check if OpenAI API key is set."""
    return os.getenv('OPENAI_API_KEY') is not None

def setup_api_key():
    """Help user set up API key."""
    print("\n?? OpenAI API Key Setup")
    print("=" * 30)
    
    api_key = input("Enter your OpenAI API key (or press Enter to skip): ").strip()
    
    if not api_key:
        print("\n??  No API key provided. You can set it later.")
        print("\nTo set the API key manually:")
        print("Windows PowerShell: $env:OPENAI_API_KEY = 'your-key-here'")
        print("Windows Command:    set OPENAI_API_KEY=your-key-here")
        print("Linux/Mac:          export OPENAI_API_KEY=your-key-here")
        return False
    
    # Create a simple .env file
    try:
        with open('.env', 'w') as f:
            f.write(f"OPENAI_API_KEY={api_key}\n")
        print("? API key saved to .env file")
        
        # Also set for current session
        os.environ['OPENAI_API_KEY'] = api_key
        print("? API key set for current session")
        return True
    except Exception as e:
        print(f"? Failed to save API key: {e}")
        return False

def test_genai_setup():
    """Test the GenAI setup."""
    print("\n?? Testing GenAI setup...")
    
    try:
        from receipt_ocr_genai import ReceiptOCRGenAI
        
        ocr = ReceiptOCRGenAI()
        
        if ocr.openai_client:
            print("? GenAI setup successful!")
            print("? OpenAI client initialized")
            return True
        else:
            print("??  GenAI setup incomplete - no OpenAI client")
            print("   Will fall back to regex processing")
            return False
    except Exception as e:
        print(f"? GenAI setup failed: {e}")
        return False

def main():
    print("?? GenAI Receipt Processing Setup")
    print("=" * 40)
    
    # Check if OpenAI is installed
    if not check_openai_installed():
        print("? OpenAI package not found")
        if input("Install OpenAI package? (y/n): ").lower().startswith('y'):
            if not install_openai():
                print("Setup aborted.")
                return
        else:
            print("Setup aborted.")
            return
    else:
        print("? OpenAI package found")
    
    # Check API key
    if not check_api_key():
        print("? No OpenAI API key found")
        print("\nTo get an API key:")
        print("1. Go to https://platform.openai.com/api-keys")
        print("2. Create a new secret key")
        print("3. Copy the key")
        
        if input("\nSet up API key now? (y/n): ").lower().startswith('y'):
            setup_api_key()
    else:
        print("? OpenAI API key found")
    
    # Test setup
    test_genai_setup()
    
    print("\n" + "=" * 40)
    print("?? Setup Complete!")
    print("=" * 40)
    print("""
Next steps:
1. Run the web app: python app.py
2. Or test with: python test_genai_receipt.py

Benefits of GenAI processing:
? Handles any receipt format automatically
? Corrects OCR errors intelligently
? Extracts items more accurately
? Much more reliable than regex patterns

Cost: ~$0.001-0.005 per receipt (under 1 cent each!)
""")

if __name__ == "__main__":
    main() 