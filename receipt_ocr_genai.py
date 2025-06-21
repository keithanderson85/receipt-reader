import re
import logging
import json
import os
import base64
import time
from typing import Dict, List, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import OpenAI
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI not available. Install with: pip install openai")

class ReceiptOCRGenAI:
    def __init__(self, openai_api_key: Optional[str] = None):
        """Initialize the GenAI client."""
        if not openai_api_key:
            logger.warning("No OpenAI API key provided")
            self.openai_client = None
            return

        try:
            self.openai_client = OpenAI(api_key=openai_api_key)
            logger.info("OpenAI client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            self.openai_client = None

    def encode_image_to_base64(self, image_path: str) -> str:
        """Encode image to base64 for GPT-4 Vision."""
        try:
            # Check if file exists
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")
            
            # Get file size
            file_size = os.path.getsize(image_path)
            logger.info(f"Image file size: {file_size} bytes")
            
            # Check if file is too large (OpenAI has a 20MB limit)
            if file_size > 20 * 1024 * 1024:  # 20MB in bytes
                raise ValueError(f"Image file too large: {file_size} bytes (max 20MB)")
            
            with open(image_path, "rb") as image_file:
                image_data = image_file.read()
                encoded_string = base64.b64encode(image_data).decode('utf-8')
                
                # Log the first 100 characters of the base64 string for debugging
                logger.info(f"Base64 string starts with: {encoded_string[:100]}...")
                logger.info(f"Base64 string length: {len(encoded_string)}")
                
                return encoded_string
        except Exception as e:
            logger.error(f"Failed to encode image: {e}")
            raise

    def call_openai_with_retry(self, messages: List[Dict], model: str = "gpt-4o-mini", max_retries: int = 3) -> Optional[str]:
        """Call OpenAI API with exponential backoff retry logic."""
        if not self.openai_client:
            raise Exception("OpenAI client not initialized")
            
        last_error = None
        
        for attempt in range(max_retries):
            try:
                logger.info(f"OpenAI API attempt {attempt + 1}/{max_retries}")
                
                response = self.openai_client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=1500,
                    temperature=0.1
                )
                
                response_text = response.choices[0].message.content
                logger.info(f"✅ OpenAI API successful on attempt {attempt + 1}")
                return response_text
                
            except Exception as e:
                last_error = e
                error_str = str(e)
                logger.warning(f"❌ OpenAI API attempt {attempt + 1} failed: {error_str}")
                
                # Check if it's a server error (500) or rate limit
                if "500" in error_str or "Internal Server Error" in error_str:
                    if attempt < max_retries - 1:
                        # Exponential backoff: 2^attempt seconds
                        wait_time = 2 ** attempt
                        logger.info(f"⏳ Waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
                        continue
                elif "rate limit" in error_str.lower():
                    if attempt < max_retries - 1:
                        wait_time = 10 * (attempt + 1)  # 10, 20, 30 seconds
                        logger.info(f"⏳ Rate limited, waiting {wait_time} seconds...")
                        time.sleep(wait_time)
                        continue
                else:
                    # For other errors, don't retry
                    logger.error(f"Non-retryable error: {error_str}")
                    raise e
        
        # All retries failed
        logger.error(f"All {max_retries} attempts failed. Last error: {last_error}")
        raise last_error

    def create_fallback_result(self, image_path: str, error_message: str) -> Dict:
        """Create a fallback result when OpenAI is unavailable."""
        filename = os.path.basename(image_path)
        
        # Try to extract some basic info from filename if it follows a pattern
        date_match = re.search(r'(\d{8})', filename)
        receipt_date = None
        if date_match:
            try:
                date_str = date_match.group(1)
                receipt_date = datetime.strptime(date_str, '%Y%m%d').date()
            except:
                pass
        
        return {
            'raw_text': f'Receipt processing temporarily unavailable. OpenAI servers experiencing issues.\nFilename: {filename}',
            'merchant_name': 'Processing Unavailable',
            'amount': None,
            'date': receipt_date or datetime.now().date(),
            'items': [{
                'description': 'Receipt processing temporarily unavailable due to OpenAI server issues',
                'price': None,
                'quantity': 1
            }],
            'success': False,
            'error': f'OpenAI API temporarily unavailable: {error_message}',
            'method': 'fallback',
            'note': 'Please try again later when OpenAI servers are fully operational'
        }

    def process_receipt(self, image_path: str) -> Dict:
        """Main method to process a receipt image using OpenAI Vision API."""
        logger.info(f"Processing receipt: {image_path}")
        
        if not self.openai_client:
            error_msg = "OpenAI client not initialized. Please provide a valid API key."
            logger.error(error_msg)
            return self.create_fallback_result(image_path, error_msg)

        try:
            # Encode image to base64
            base64_image = self.encode_image_to_base64(image_path)
            
            # Get file extension to determine MIME type
            file_ext = os.path.splitext(image_path)[1].lower()
            if file_ext in ['.jpg', '.jpeg']:
                mime_type = "image/jpeg"
            elif file_ext == '.png':
                mime_type = "image/png"
            else:
                mime_type = "image/jpeg"  # Default
            
            logger.info(f"Using MIME type: {mime_type} for file extension: {file_ext}")
            
            # Use a clear prompt for JSON extraction
            prompt = (
                "Analyze this receipt image and extract the following information in JSON format: "
                '{\n'
                '  "merchant_name": "Name of the store/business",\n'
                '  "amount": total amount as a number (e.g., 42.99),\n'
                '  "date": date in YYYY-MM-DD format,\n'
                '  "subtotal": subtotal before tax as a number,\n'
                '  "tax_amount": tax amount as a number,\n'
                '  "tax_rate": tax rate as a percentage (e.g., 8.5),\n'
                '  "items": [\n'
                '    {\n'
                '      "description": "Item description",\n'
                '      "price": price as a number,\n'
                '      "quantity": quantity as a number (if available)\n'
                '    }\n'
                '  ]\n'
                '}\n'
                "Only include fields you can confidently identify. If a field is unclear, omit it. "
                "Ensure all monetary values are numbers, not strings. "
                "Look for tax information in the receipt, including subtotal, tax amount, and tax rate. "
                "If tax information is not clearly shown, omit those fields."
            )

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}}
                    ]
                }
            ]

            # Call OpenAI with retry logic
            response_text = self.call_openai_with_retry(messages)
            
            logger.info(f"OpenAI API raw response text: {response_text}")

            # Try to parse JSON from the response
            try:
                # Extract JSON from Markdown code block if present
                import re
                match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', response_text)
                if match:
                    json_str = match.group(1)
                else:
                    json_str = response_text.strip()
                result = json.loads(json_str)
                result['raw_text'] = response_text
                result['success'] = True
                result['method'] = 'gpt4_vision'
                logger.info("✅ Receipt processed successfully with OpenAI")
                return result
            except Exception as e:
                logger.error(f"Failed to parse JSON from OpenAI response: {e}")
                return {
                    'raw_text': response_text,
                    'merchant_name': None,
                    'amount': None,
                    'date': None,
                    'items': [],
                    'success': False,
                    'error': 'Could not parse JSON from OpenAI response',
                    'method': 'gpt4_vision'
                }
        except Exception as e:
            logger.error(f"GPT-4 Vision processing failed: {e}")
            
            # Check if it's a server error and provide appropriate fallback
            error_str = str(e)
            if "500" in error_str or "Internal Server Error" in error_str:
                logger.warning("🔄 OpenAI servers are experiencing issues, providing fallback result")
                return self.create_fallback_result(image_path, "OpenAI servers are temporarily experiencing issues")
            else:
                return {
                    'raw_text': '',
                    'merchant_name': 'Unknown Merchant',
                    'amount': None,
                    'date': datetime.now().date(),
                    'items': [],
                    'success': False,
                    'error': str(e),
                    'method': 'error'
                } 