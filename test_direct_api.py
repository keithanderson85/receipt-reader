import logging
import os
import httpx
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
    logger.info(f"API Key found: {api_key[:10]}...")
    
    # Try different client configurations to bypass CloudFlare issues
    configs = [
        {
            "name": "Default Client",
            "client": OpenAI(api_key=api_key)
        },
        {
            "name": "Client with Custom Base URL",
            "client": OpenAI(
                api_key=api_key,
                base_url="https://api.openai.com/v1"
            )
        },
        {
            "name": "Client with Custom Headers",
            "client": OpenAI(
                api_key=api_key,
                default_headers={"User-Agent": "CustomPythonClient/1.0"}
            )
        },
        {
            "name": "Client with HTTP Client Config",
            "client": OpenAI(
                api_key=api_key,
                http_client=httpx.Client(
                    timeout=60.0,
                    headers={"Connection": "keep-alive"}
                )
            )
        }
    ]
    
    # Test each configuration
    for config in configs:
        logger.info(f"\n{'='*50}")
        logger.info(f"Testing: {config['name']}")
        logger.info(f"{'='*50}")
        
        try:
            client = config['client']
            
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "Hello"}],
                max_tokens=5,
                temperature=0
            )
            
            logger.info(f"✅ SUCCESS with {config['name']}!")
            logger.info(f"Response: {response.choices[0].message.content}")
            break  # Stop on first success
            
        except Exception as e:
            logger.error(f"❌ FAILED with {config['name']}: {str(e)[:100]}...")
            continue
    
    else:
        logger.error("All configurations failed!")
        
        # Let's try a direct HTTP request to see if it's a library issue
        logger.info("\nTrying direct HTTP request...")
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 5
            }
            
            with httpx.Client(timeout=30.0) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=headers,
                    json=data
                )
                
                logger.info(f"Direct HTTP Status: {response.status_code}")
                logger.info(f"Direct HTTP Response: {response.text[:200]}...")
                
        except Exception as e:
            logger.error(f"Direct HTTP also failed: {e}")

except ImportError as e:
    logger.error(f"Import error: {e}")
except Exception as e:
    logger.error(f"General error: {e}") 