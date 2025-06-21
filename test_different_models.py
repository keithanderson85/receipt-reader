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
    client = OpenAI(api_key=api_key)
    
    # Test different models and configurations
    test_configs = [
        {
            "name": "GPT-4o-mini (Vision Model)",
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5
        },
        {
            "name": "GPT-3.5-turbo",
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5
        },
        {
            "name": "GPT-4o (Latest)",
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5
        },
        {
            "name": "GPT-4-turbo",
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5
        },
        {
            "name": "Simple request - no max_tokens",
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "Hi"}]
        },
        {
            "name": "Different message format",
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say hello"}
            ],
            "max_tokens": 10
        }
    ]
    
    successful_configs = []
    failed_configs = []
    
    for config in test_configs:
        logger.info(f"\n{'='*60}")
        logger.info(f"Testing: {config['name']}")
        logger.info(f"{'='*60}")
        
        try:
            # Build request parameters
            request_params = {
                "model": config["model"],
                "messages": config["messages"]
            }
            
            # Add optional parameters
            if "max_tokens" in config:
                request_params["max_tokens"] = config["max_tokens"]
            if "temperature" in config:
                request_params["temperature"] = config["temperature"]
                
            logger.info(f"Request params: {request_params}")
            
            response = client.chat.completions.create(**request_params)
            
            logger.info(f"✅ SUCCESS with {config['name']}!")
            logger.info(f"Response: {response.choices[0].message.content}")
            successful_configs.append(config["name"])
            
        except Exception as e:
            logger.error(f"❌ FAILED with {config['name']}: {str(e)[:100]}...")
            failed_configs.append(config["name"])
            continue
    
    # Summary
    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"✅ Successful: {len(successful_configs)}")
    for config in successful_configs:
        logger.info(f"  - {config}")
    
    logger.info(f"❌ Failed: {len(failed_configs)}")
    for config in failed_configs:
        logger.info(f"  - {config}")
    
    if successful_configs:
        logger.info("\n🎯 At least one configuration worked! This suggests the issue is model or parameter specific.")
    else:
        logger.info("\n💥 All configurations failed! This suggests a broader network/server issue.")

except ImportError as e:
    logger.error(f"Import error: {e}")
except Exception as e:
    logger.error(f"General error: {e}") 