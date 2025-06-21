# OpenAI API Setup for Better Receipt Processing

Your receipt reader is currently using fallback regex processing because the OpenAI API key isn't configured. For the best item extraction results, set up the OpenAI API:

## Option 1: Quick Setup (Recommended)

1. **Get an OpenAI API Key:**
   - Go to https://platform.openai.com/api-keys
   - Create an account or sign in
   - Click "Create new secret key"
   - Copy the key (starts with `sk-`)

2. **Set the Environment Variable:**
   
   **Windows (PowerShell):**
   ```powershell
   $env:OPENAI_API_KEY = "sk-your-openai-api-key-here"
   ```
   
   **Windows (Command Prompt):**
   ```cmd
   set OPENAI_API_KEY=sk-your-key-here
   ```
   
   **For permanent setup, add to your system environment variables:**
   - Open System Properties > Environment Variables
   - Add new variable: `OPENAI_API_KEY` = `sk-your-openai-api-key-here`

3. **Restart your Flask app:**
   ```powershell
   py app.py
   ```

## Option 2: Use .env File

1. Create a `.env` file in your project directory:
   ```
   OPENAI_API_KEY=sk-your-key-here
   ```

2. The app will automatically load it on startup.

## Cost Information

- **GPT-4o Vision**: ~$0.01-0.03 per receipt (best quality)
- **GPT-4o-mini**: ~$0.001-0.003 per receipt (good quality, 99% cheaper)
- **Regex fallback**: Free but less accurate for complex receipts

## What You Get With OpenAI

✅ **Much better item extraction** - especially for:
- Thrift store receipts (like St. Vincent's)
- European number formatting (commas as decimals)
- Poor OCR quality receipts
- Complex multi-item receipts

✅ **Automatic error correction** for OCR mistakes

✅ **Smart categorization** of merchants and items

## Current Status

Your system is working with **regex fallback only**. This is fine for simple receipts, but OpenAI processing will give much better results for complex receipts like your thrift store examples.

## Test It

After setting up the API key, try uploading the same St. Vincent's receipt. You should see much better item extraction! 