import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    
    # Get API key
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        logger.error("No OpenAI API key found in environment variables")
        exit(1)
    
    client = OpenAI(api_key=api_key)
    
    logger.info("Testing basic text API connection...")
    
    # Test basic text completion first
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "Hello, just testing the API connection. Please respond with 'API working'."}],
            max_tokens=10
        )
        logger.info("✅ Basic text API working!")
        logger.info(f"Response: {response.choices[0].message.content}")
    except Exception as e:
        logger.error(f"❌ Basic text API failed: {e}")
        
    # Now test Vision API
    logger.info("Testing Vision API (gpt-4o-mini)...")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hello, just testing the Vision API connection. Please respond with 'Vision API working'."}],
            max_tokens=10
        )
        logger.info("✅ Vision API working!")
        logger.info(f"Response: {response.choices[0].message.content}")
    except Exception as e:
        logger.error(f"❌ Vision API failed: {e}")
        
except ImportError:
    logger.error("OpenAI library not installed. Run: pip install openai")
except Exception as e:
    logger.error(f"General error: {e}") 