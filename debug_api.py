import logging
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging with more detail
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    import httpx
    
    # Get API key
    api_key = os.getenv('OPENAI_API_KEY')
    logger.info(f"API Key found: {api_key[:10]}...")
    
    # Create client with more debugging
    client = OpenAI(
        api_key=api_key,
        timeout=30.0,  # 30 second timeout
    )
    
    logger.info("OpenAI client created successfully")
    
    # Test with the most basic request possible
    try:
        logger.info("Making simple API call...")
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": "Say 'hello'"}
            ],
            max_tokens=5,
            temperature=0
        )
        
        logger.info("✅ API call successful!")
        logger.info(f"Response: {response.choices[0].message.content}")
        logger.info(f"Full response: {response}")
        
    except Exception as e:
        logger.error(f"❌ API call failed with error: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.error(f"Error details: {repr(e)}")
        
        # Check if it's an HTTP error
        if hasattr(e, 'response'):
            logger.error(f"HTTP Status: {e.response.status_code}")
            logger.error(f"HTTP Headers: {e.response.headers}")
            logger.error(f"HTTP Body: {e.response.text}")
        
        # Check if it's a connection error
        if hasattr(e, '__cause__'):
            logger.error(f"Underlying cause: {e.__cause__}")
            
except ImportError as e:
    logger.error(f"Import error: {e}")
except Exception as e:
    logger.error(f"General error: {e}")
    logger.error(f"Error type: {type(e).__name__}")
    logger.error(f"Error details: {repr(e)}") 