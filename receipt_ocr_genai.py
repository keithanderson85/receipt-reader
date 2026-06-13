import re
import logging
import json
import os
import base64
import time
import io
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

# Import PDF processing
try:
    from pdf2image import convert_from_path
    from PIL import Image
    PDF_PROCESSING_AVAILABLE = True
except ImportError:
    PDF_PROCESSING_AVAILABLE = False
    logger.warning("PDF processing not available. Install with: pip install pdf2image pillow")

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

    def convert_pdf_to_image(self, pdf_path: str) -> str:
        """Convert PDF to image and return the path to the permanent image file."""
        if not PDF_PROCESSING_AVAILABLE:
            raise ImportError("PDF processing not available. Install with: pip install pdf2image pillow")
            
        try:
            logger.info(f"Converting PDF to image: {pdf_path}")
            
            # On Windows, try to add poppler to PATH if it exists locally
            poppler_path = None
            if os.name == 'nt':  # Windows
                # Look for local poppler installation
                current_dir = os.path.dirname(os.path.abspath(__file__))
                local_poppler = os.path.join(current_dir, 'poppler', 'poppler-24.08.0', 'Library', 'bin')
                if os.path.exists(local_poppler):
                    poppler_path = local_poppler
                    logger.info(f"Found local poppler at: {poppler_path}")
            
            # Convert PDF to images (only first page for receipts)
            if poppler_path:
                images = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=300, poppler_path=poppler_path)
            else:
                images = convert_from_path(pdf_path, first_page=1, last_page=1, dpi=300)
            
            if not images:
                raise ValueError("No pages found in PDF")
                
            # Use the first page
            image = images[0]
            
            # Create permanent image file (replace .pdf with .jpg)
            temp_dir = os.path.dirname(pdf_path)
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            image_path = os.path.join(temp_dir, f"{base_name}.jpg")
            
            # Save as JPEG
            image.save(image_path, 'JPEG', quality=95)
            logger.info(f"PDF converted to permanent image: {image_path}")
            
            return image_path
            
        except Exception as e:
            logger.error(f"Failed to convert PDF to image: {e}")
            raise

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

    def call_openai_with_retry(self, messages: List[Dict], model: str = "gpt-4o-mini", max_retries: int = 3, max_tokens: int = 1500) -> Optional[str]:
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
                    max_tokens=max_tokens,
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

        # Check if file is a PDF and convert it to image
        converted_image_path = None
        processing_path = image_path
        is_pdf = False
        
        try:
            file_ext = os.path.splitext(image_path)[1].lower()
            if file_ext == '.pdf':
                logger.info("PDF file detected, converting to image...")
                is_pdf = True
                converted_image_path = self.convert_pdf_to_image(image_path)
                processing_path = converted_image_path
                logger.info(f"Using converted image: {processing_path}")

            # Encode image to base64
            base64_image = self.encode_image_to_base64(processing_path)
            
            # Get file extension to determine MIME type (use processing path for converted PDFs)
            file_ext = os.path.splitext(processing_path)[1].lower()
            if file_ext in ['.jpg', '.jpeg']:
                mime_type = "image/jpeg"
            elif file_ext == '.png':
                mime_type = "image/png"
            elif file_ext == '.gif':
                mime_type = "image/gif"
            elif file_ext == '.webp':
                mime_type = "image/webp"
            else:
                mime_type = "image/jpeg"  # Default
            
            logger.info(f"Using MIME type: {mime_type} for file extension: {file_ext}")

            # Prepare the message for GPT-4 Vision
            logger.info("Preparing OpenAI Vision API request...")
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": """Analyze this receipt image and extract the following information in JSON format:

{
    "merchant_name": "name of the business/store",
    "address": "full street address of the store (e.g., '123 Main St, Reno, NV 89501')",
    "location": "city and state of the store (e.g., 'Reno, NV' or 'Sparks, NV')",
    "amount": "total amount as a number (e.g., 15.99)",
    "date": "transaction date in YYYY-MM-DD format",
    "items": [
        {
            "description": "item description",
            "price": "item price as number",
            "quantity": "quantity as integer (default 1)",
            "sku": "item code/sku if visible",
            "discount": "discount applied to this item as a number (0 if none)"
        }
    ],
    "tax_amount": "tax amount as number (0 if no tax)",
    "tax_rate": "tax rate as percentage (null if unknown)",
    "subtotal": "subtotal before tax as number",
    "discount_amount": "total coupon or discount amount as number (0 if none)",
    "extra_discount": "any additional receipt-level discount or promo not already in items (0 if none)"
}

Important guidelines:
1. Extract ALL items from the receipt with accurate prices
2. If quantity is mentioned, extract it correctly (e.g., "2 x $5.00" means quantity 2, price $5.00 each)
3. Calculate subtotal as sum of (price × quantity) for all items
4. Tax amount should be the actual tax charged, not calculated
5. For thrift stores or tax-exempt purchases, tax_amount should be 0
6. Ensure total = subtotal + tax_amount
7. Use exact text from receipt for descriptions
8. If no SKU/barcode visible, leave sku as null
9. When an item line begins with a number followed by the description (e.g., "6  Women's Clothing"), treat that number as the item quantity.
10. Return valid JSON only, no additional text"""
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ]

            logger.info("Sending request to OpenAI Vision API...")
            raw_response = self.call_openai_with_retry(messages, model="gpt-4o-mini", max_tokens=2000)

            logger.info("Received response from OpenAI Vision API")
            response_text = (raw_response or '').strip()
            logger.info(f"Raw response: {response_text[:500]}...")  # Log first 500 chars

            # Try to extract JSON from the response
            try:
                # Find JSON in the response
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                
                if start_idx != -1 and end_idx > start_idx:
                    json_str = response_text[start_idx:end_idx]
                    parsed_data = json.loads(json_str)
                    logger.info("Successfully parsed JSON response")
                else:
                    raise ValueError("No JSON found in response")
                
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse JSON: {e}")
                logger.error(f"Raw response: {response_text}")
                # Return a fallback result
                return self.create_fallback_result(image_path, f"JSON parsing failed: {str(e)}")

            # Validate and process the extracted data
            # Infer quantity if missing or 1 but subtotal suggests multiple units
            items_list = parsed_data.get('items', [])
            if (
                items_list
                and len(items_list) == 1
                and items_list[0].get('quantity', 1) == 1
                and parsed_data.get('subtotal')
            ):
                try:
                    price = float(items_list[0].get('price'))
                    subtotal_val = float(parsed_data.get('subtotal'))
                    qty_guess = round(subtotal_val / price)
                    if qty_guess > 1 and abs((price * qty_guess) - subtotal_val) < 0.02:
                        items_list[0]['quantity'] = qty_guess
                except Exception:
                    pass

            # Calculate discount from items if provided
            item_level_discount = 0.0
            for it in items_list:
                try:
                    item_level_discount += float(it.get('discount', 0) or 0)
                except (ValueError, TypeError):
                    pass

            # Add extra receipt discount
            extra_discount_val = float(parsed_data.get('extra_discount', 0) or 0)
            item_level_discount += extra_discount_val

            result = {
                'raw_text': response_text,
                'merchant_name': parsed_data.get('merchant_name', ''),
                'address': parsed_data.get('address', ''),
                'location': parsed_data.get('location', ''),
                'amount': parsed_data.get('amount'),
                'date': parsed_data.get('date'),
                'items': items_list,
                'tax_amount': parsed_data.get('tax_amount', 0),
                'tax_rate': parsed_data.get('tax_rate'),
                'discount_amount': parsed_data.get('discount_amount', 0) or item_level_discount,
                'subtotal': parsed_data.get('subtotal'),
                'success': True,
                'method': 'gpt4_vision_pdf' if is_pdf else 'gpt4_vision',
                'original_filename': os.path.basename(image_path) if is_pdf else None,
                'converted_filename': os.path.basename(processing_path) if is_pdf else None
            }

            # Convert date string to date object if possible
            if result['date']:
                try:
                    result['date'] = datetime.strptime(result['date'], '%Y-%m-%d').date()
                except ValueError:
                    # Try alternative formats
                    for fmt in ['%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
                        try:
                            result['date'] = datetime.strptime(result['date'], fmt).date()
                            break
                        except ValueError:
                            continue
                    else:
                        result['date'] = datetime.now().date()

            # If we converted a PDF, clean up the original PDF file and update the filename
            if is_pdf and converted_image_path:
                # Clean up the original PDF file
                try:
                    os.remove(image_path)
                    logger.info(f"Cleaned up original PDF file: {image_path}")
                except OSError as e:
                    logger.warning(f"Could not remove PDF file {image_path}: {e}")
                
                # Update the result to indicate we should save the image filename, not PDF
                result['processed_filename'] = os.path.basename(converted_image_path)

            # Heuristic: if discount_amount is 0 but raw response contains coupon/discount value, try to extract
            if (result['discount_amount'] in [None, 0]) and item_level_discount > 0:
                result['discount_amount'] = item_level_discount

            # ensure discount positive number
            if result['discount_amount'] and result['discount_amount'] < 0:
                result['discount_amount'] = abs(result['discount_amount'])

            # Heuristic regex fallback
            if not result['discount_amount']:
                # Look for patterns like "Coupon Savings: -$0.81" or "Savings: $1.23"
                coupon_match = re.search(r'(?i)(coupon|discount|savings)[^\d]*(?:-|\$)(\d+\.\d{2})', response_text)
                if coupon_match:
                    try:
                        result['discount_amount'] = float(coupon_match.group(2))
                    except ValueError:
                        pass

            # Derive discount from totals if still zero and all parts present
            if (result.get('subtotal') is not None) and (result.get('amount') is not None):
                try:
                    derived_discount = (result['subtotal'] + (result['tax_amount'] or 0)) - result['amount']
                    if derived_discount > (result['discount_amount'] or 0) + 0.009:
                        result['discount_amount'] = round(derived_discount, 2)
                except Exception:
                    pass

            # --- Begin unit-price adjustment heuristic ---
            try:
                # Approximate gross total (subtotal + discounts + tax) to gauge scale
                gross_total = 0.0
                try:
                    gross_total = float(result.get('amount') or 0) \
                                 + float(result.get('discount_amount') or 0) \
                                 + float(result.get('tax_amount') or 0)
                except Exception:
                    pass

                items_fixed = False
                for it in result.get('items', []):
                    try:
                        qty = int(it.get('quantity', 1) or 1)
                        price_val = float(it.get('price')) if it.get('price') is not None else None
                        if price_val is None or qty <= 1:
                            continue  # Nothing to fix

                        # Two complementary checks:
                        #   1. For small/medium receipts, if (price × qty) exceeds ~90% of the gross total, it is suspicious.
                        #   2. For bigger receipts, keep the original 1.3× guard.
                        suspicious_large_fraction = gross_total > 0 and (price_val * qty) > gross_total * 0.9
                        suspicious_large_multiple = gross_total > 0 and (price_val * qty) > gross_total * 1.3

                        if suspicious_large_fraction or suspicious_large_multiple:
                            new_unit_price = round(price_val / qty, 2)
                            logger.info(
                                f"Adjusting price for '{it.get('description', '')}' from {price_val} to {new_unit_price} "
                                f"based on quantity {qty} and gross_total {gross_total}")
                            it['price'] = new_unit_price
                            items_fixed = True
                    except Exception:
                        # Skip any item that fails numeric conversion
                        continue

                # Re-compute subtotal if any adjustments were made
                if items_fixed:
                    try:
                        result['subtotal'] = round(
                            sum(float(item.get('price', 0)) * int(item.get('quantity', 1)) for item in result['items']), 2
                        )
                        # Re-derive discount based on new subtotal
                        if result.get('amount') is not None:
                            new_derived_discount = (result['subtotal'] + (result.get('tax_amount') or 0)) - result['amount']
                            if new_derived_discount >= 0:
                                result['discount_amount'] = round(new_derived_discount, 2)
                    except Exception:
                        pass
            except Exception as _e:
                logger.warning(f"Unit-price adjustment heuristic failed: {_e}")
            # --- End unit-price adjustment heuristic ---

            # --- Final consistency check: align discount with subtotal, tax, and total amount ---
            try:
                if (result.get('subtotal') is not None) and (result.get('amount') is not None):
                    expected_discount = round(
                        (result['subtotal'] + (result.get('tax_amount') or 0)) - result['amount'], 2
                    )

                    # Discount should not be negative
                    if expected_discount < 0:
                        expected_discount = 0.0

                    current_discount = round(float(result.get('discount_amount') or 0), 2)

                    # If current discount differs significantly (>5¢) from expected, correct it.
                    if abs(expected_discount - current_discount) > 0.05:
                        logger.info(
                            f"Adjusting discount_amount for consistency from {current_discount} to {expected_discount}"
                        )
                        result['discount_amount'] = expected_discount
            except Exception as _e:
                logger.warning(f"Consistency check failed: {_e}")

            logger.info(f"Processing complete. Success: {result['success']}")
            return result

        except Exception as e:
            logger.error(f"Error processing receipt: {e}")
            import traceback
            traceback.print_exc()
            
            # Clean up converted image if there was an error
            if converted_image_path and os.path.exists(converted_image_path):
                try:
                    os.remove(converted_image_path)
                except OSError:
                    pass
            
            return self.create_fallback_result(image_path, str(e)) 