import cv2
import numpy as np
from PIL import Image
import re
import logging
import os
import json
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
import base64

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ReceiptOCR:
    def __init__(self):
        """Initialize the OCR system with OpenAI API key."""
        self.openai_api_key = os.getenv('OPENAI_API_KEY')
        if not self.openai_api_key:
            raise ValueError("OpenAI API key not found in environment variables")
        self.client = OpenAI(api_key=self.openai_api_key)
        
    def preprocess_image(self, image_path):
        """Preprocess the image for better OCR results."""
        try:
            # Read image
            img = cv2.imread(image_path)
            if img is None:
                raise ValueError(f"Could not read image at {image_path}")
                
            # Convert to grayscale
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            
            # Apply thresholding to get a binary image
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            
            # Denoise
            denoised = cv2.fastNlMeansDenoising(binary)
            
            return denoised
            
        except Exception as e:
            logger.error(f"Error in image preprocessing: {str(e)}")
            raise
            
    def extract_text_with_openai(self, image_path):
        """Extract text from image using OpenAI's Vision API."""
        try:
            # Read image file and encode as base64
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
                
                # Call OpenAI API
                response = self.client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Extract all text from this receipt. Include prices, dates, merchant name, and any other relevant information. Format the response as a JSON object with the following fields: merchant_name, date, total_amount, tax_amount, tax_rate, items (array of objects with description and price). For items, include both the description and price. Make sure to format the response as valid JSON."
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{base64_image}"
                                    }
                                }
                            ]
                        }
                    ],
                    max_tokens=1000
                )
                
                # Parse the response
                try:
                    # First try to parse as JSON
                    content = response.choices[0].message.content
                    # Clean up the response if it's not valid JSON
                    if not content.strip().startswith('{'):
                        # Try to find JSON in the response
                        json_match = re.search(r'\{.*\}', content, re.DOTALL)
                        if json_match:
                            content = json_match.group(0)
                    
                    result = json.loads(content)
                    
                    # Ensure all required fields are present
                    if 'items' not in result:
                        result['items'] = []
                    if 'tax_amount' not in result:
                        result['tax_amount'] = 0.0
                    if 'tax_rate' not in result:
                        result['tax_rate'] = None
                    if 'total_amount' not in result:
                        result['total_amount'] = None
                        
                    # Clean up items
                    cleaned_items = []
                    for item in result.get('items', []):
                        if isinstance(item, dict):
                            if 'description' in item and 'price' in item:
                                cleaned_items.append({
                                    'description': str(item['description']),
                                    'price': float(item['price'])
                                })
                    result['items'] = cleaned_items
                    
                    return result
                    
                except json.JSONDecodeError:
                    # If response is not valid JSON, try to extract information using regex
                    text = response.choices[0].message.content
                    return self._parse_text_response(text)
                    
        except Exception as e:
            logger.error(f"Error in OpenAI text extraction: {str(e)}")
            raise
            
    def _parse_text_response(self, text):
        """Parse text response into structured data."""
        try:
            # Initialize result dictionary
            result = {
                'merchant_name': None,
                'date': None,
                'total_amount': None,
                'tax_amount': 0.0,
                'tax_rate': None,
                'items': []
            }
            
            # Extract merchant name (usually at the top)
            merchant_match = re.search(r'^([^\n]+)', text)
            if merchant_match:
                result['merchant_name'] = merchant_match.group(1).strip()
                
            # Extract date
            date_match = re.search(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', text)
            if date_match:
                result['date'] = date_match.group(0)
                
            # Extract total amount
            total_match = re.search(r'total:?\s*\$?(\d+\.\d{2})', text.lower())
            if total_match:
                result['total_amount'] = float(total_match.group(1))
                
            # Extract tax information
            tax_match = re.search(r'tax:?\s*\$?(\d+\.\d{2})', text.lower())
            if tax_match:
                result['tax_amount'] = float(tax_match.group(1))
                
            tax_rate_match = re.search(r'(\d+(?:\.\d+)?)\s*%', text)
            if tax_rate_match:
                result['tax_rate'] = float(tax_rate_match.group(1))
                
            # Extract items
            lines = text.split('\n')
            for line in lines:
                # Look for lines with prices
                price_match = re.search(r'\$?(\d+\.\d{2})', line)
                if price_match:
                    # Remove the price from the line to get the description
                    description = re.sub(r'\$?\d+\.\d{2}', '', line).strip()
                    if description and not description.lower().startswith(('total', 'tax', 'subtotal')):
                        result['items'].append({
                            'description': description,
                            'price': float(price_match.group(1))
                        })
                        
            return result
            
        except Exception as e:
            logger.error(f"Error parsing text response: {str(e)}")
            raise
            
    def process_receipt(self, image_path):
        """Process a receipt image and extract relevant information."""
        try:
            logger.info(f"Processing receipt: {image_path}")
            
            # Extract text using OpenAI
            logger.info("Extracting text with OpenAI")
            result = self.extract_text_with_openai(image_path)
            
            if not result:
                logger.error("No text extracted from receipt")
                return {
                    'success': False,
                    'error': 'No text could be extracted from the receipt'
                }
                
            # Log the extracted information
            logger.info(f"Extracted merchant: {result.get('merchant_name')}")
            logger.info(f"Extracted date: {result.get('date')}")
            logger.info(f"Extracted total: {result.get('total_amount')}")
            logger.info(f"Extracted tax amount: {result.get('tax_amount')}")
            logger.info(f"Extracted tax rate: {result.get('tax_rate')}")
            logger.info(f"Extracted {len(result.get('items', []))} items")
            
            # Calculate subtotal from items
            subtotal = sum(item.get('price', 0) for item in result.get('items', []))
            result['subtotal'] = subtotal
            
            # For thrift stores, ensure tax is set to 0 if not found
            merchant_name = result.get('merchant_name', '').lower()
            if any(store in merchant_name for store in ['thrift', 'st vincent', 'vincent', 'goodwill', 'salvation army', 'savers']):
                if result.get('tax_amount') is None:
                    result['tax_amount'] = 0.0
                if result.get('tax_rate') is None:
                    result['tax_rate'] = 0.0
            else:
                # For non-thrift stores, calculate tax rate if not provided
                if result.get('tax_amount') and result.get('subtotal'):
                    tax_rate = (result['tax_amount'] / result['subtotal']) * 100
                    # Round to 1 decimal place
                    result['tax_rate'] = round(tax_rate, 1)
            
            return {
                'success': True,
                'merchant_name': result.get('merchant_name'),
                'date': result.get('date'),
                'total_amount': result.get('total_amount'),
                'tax_amount': result.get('tax_amount', 0.0),
                'tax_rate': result.get('tax_rate'),
                'subtotal': subtotal,
                'items': result.get('items', [])
            }
            
        except Exception as e:
            logger.error(f"Error processing receipt: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

# Test function
if __name__ == "__main__":
    # This is for testing the OCR functionality
    ocr = ReceiptOCR()
    
    # You can test with a sample image
    try:
        result = ocr.process_receipt("sample_receipt.jpg")
        print("OCR Result:", result)
    except Exception as e:
        print(f"Error: {e}") 