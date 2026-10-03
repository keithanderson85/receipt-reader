import re
import logging
import json
import os
import base64
import time
import random
import io
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from receipt_items import merge_photo_items, subtotal_note

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Import OpenAI
try:
    from openai import (
        OpenAI,
        APIError,
        RateLimitError,
        APITimeoutError,
        APIConnectionError,
        InternalServerError
    )
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
        """Initialize the GenAI client with timeout and model selection."""
        self.default_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        
        if not openai_api_key:
            logger.warning("No OpenAI API key provided")
            self.openai_client = None
            return

        try:
            # Configure OpenAI client with a 45-second timeout to prevent hanging connections
            self.openai_client = OpenAI(
                api_key=openai_api_key,
                timeout=45.0,
                max_retries=0  # Handled in call_openai_with_retry with exponential backoff
            )
            logger.info(f"OpenAI client initialized successfully with model: {self.default_model}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI client: {e}")
            self.openai_client = None

    def _get_poppler_path(self) -> Optional[str]:
        """Resolve local poppler binary path on Windows if present."""
        if os.name == 'nt':
            current_dir = os.path.dirname(os.path.abspath(__file__))
            local_poppler = os.path.join(current_dir, 'poppler', 'poppler-24.08.0', 'Library', 'bin')
            if os.path.exists(local_poppler):
                return local_poppler
        return None

    def convert_pdf_to_images(self, pdf_path: str) -> Tuple[str, List[str], int]:
        """
        Convert all pages of a PDF to JPEG images.
        Stitches multi-page receipts vertically into a single permanent JPEG.
        Returns: (stitched_image_path, list_of_page_image_paths, page_count)
        """
        if not PDF_PROCESSING_AVAILABLE:
            raise ImportError("PDF processing not available. Install with: pip install pdf2image pillow")
            
        try:
            logger.info(f"Converting PDF to image(s): {pdf_path}")
            poppler_path = self._get_poppler_path()
            
            # Convert all pages from PDF (dpi 150 is optimal for fast OCR and low TPM footprint)
            if poppler_path:
                images = convert_from_path(pdf_path, dpi=150, poppler_path=poppler_path)
            else:
                images = convert_from_path(pdf_path, dpi=150)
            
            page_count = len(images)
            if page_count == 0:
                raise ValueError("No pages found in PDF")

            temp_dir = os.path.dirname(pdf_path)
            base_name = os.path.splitext(os.path.basename(pdf_path))[0]
            stitched_image_path = os.path.join(temp_dir, f"{base_name}.jpg")
            page_image_paths = []

            if page_count == 1:
                # Single page receipt
                images[0].save(stitched_image_path, 'JPEG', quality=92)
                page_image_paths.append(stitched_image_path)
                logger.info(f"PDF (1 page) converted to: {stitched_image_path}")
            else:
                # Multi-page receipt: stitch vertically and save individual page images
                logger.info(f"Multi-page PDF detected ({page_count} pages). Stitching vertically...")
                
                # Save each page individually for high-fidelity multi-image prompt
                for idx, img in enumerate(images):
                    page_path = os.path.join(temp_dir, f"{base_name}_page_{idx+1}.jpg")
                    img.save(page_path, 'JPEG', quality=90)
                    page_image_paths.append(page_path)

                # Create vertical composite
                total_height = sum(img.height for img in images)
                max_width = max(img.width for img in images)
                
                composite = Image.new('RGB', (max_width, total_height), color=(255, 255, 255))
                y_offset = 0
                for img in images:
                    x_offset = (max_width - img.width) // 2
                    composite.paste(img, (x_offset, y_offset))
                    y_offset += img.height

                composite.save(stitched_image_path, 'JPEG', quality=90)
                logger.info(f"Multi-page PDF ({page_count} pages) stitched to: {stitched_image_path}")

            return stitched_image_path, page_image_paths, page_count
            
        except Exception as e:
            logger.error(f"Failed to convert PDF to image: {e}")
            raise

    def stitch_photos(self, photo_paths: List[str], out_path: str, target_width: int = 1400) -> str:
        """Stack several photos of one long receipt into a single JPEG for archiving/display."""
        if not PDF_PROCESSING_AVAILABLE:
            raise ImportError("Image processing not available. Install with: pip install pillow")

        from PIL import ImageOps
        frames = []
        try:
            for path in photo_paths:
                with Image.open(path) as raw:
                    img = ImageOps.exif_transpose(raw).convert('RGB')
                if img.width != target_width:
                    img = img.resize((target_width, max(1, round(img.height * target_width / img.width))))
                frames.append(img)

            composite = Image.new('RGB', (target_width, sum(f.height for f in frames)), (255, 255, 255))
            y = 0
            for f in frames:
                composite.paste(f, (0, y))
                y += f.height
            composite.save(out_path, 'JPEG', quality=85)
            return out_path
        finally:
            for f in frames:
                f.close()

    def convert_pdf_to_image(self, pdf_path: str) -> str:
        """Backward-compatible helper returning single/stitched image path."""
        stitched_path, _, _ = self.convert_pdf_to_images(pdf_path)
        return stitched_path

    def encode_image_to_base64(self, image_path: str) -> str:
        """Encode image to base64 for OpenAI Vision."""
        try:
            if not os.path.exists(image_path):
                raise FileNotFoundError(f"Image file not found: {image_path}")
            
            file_size = os.path.getsize(image_path)
            
            # If image is over 4MB, downscale/compress with PIL
            if file_size > 4 * 1024 * 1024:
                logger.info(f"Image large ({file_size / 1024 / 1024:.1f}MB), compressing...")
                with Image.open(image_path) as img:
                    img.thumbnail((1600, 1600))
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=85)
                    return base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to encode image {image_path}: {e}")
            raise

    def call_openai_with_retry(
        self,
        messages: List[Dict],
        model: Optional[str] = None,
        max_retries: int = 3,
        max_tokens: int = 2500
    ) -> Optional[str]:
        """Call OpenAI API with typed exception handling, structured JSON mode, and exponential backoff."""
        if not self.openai_client:
            raise Exception("OpenAI client not initialized")

        use_model = model or self.default_model
        last_error = None

        for attempt in range(max_retries):
            try:
                logger.info(f"OpenAI API call (attempt {attempt + 1}/{max_retries}, model: {use_model})...")

                response = self.openai_client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.1
                )
                
                response_text = response.choices[0].message.content
                logger.info(f"✅ OpenAI API successful on attempt {attempt + 1}")
                return response_text
                
            except (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError) as retryable_err:
                last_error = retryable_err
                logger.warning(f"⚠️ Retryable OpenAI error (attempt {attempt + 1}/{max_retries}): {retryable_err}")
                
                if attempt < max_retries - 1:
                    # Exponential backoff with random jitter: (2^attempt * 2) + jitter
                    jitter = random.uniform(0.5, 2.0)
                    wait_time = (2 ** attempt) * 2 + jitter
                    if isinstance(retryable_err, RateLimitError):
                        wait_time = max(wait_time, 5.0 * (attempt + 1) + jitter)
                    
                    logger.info(f"⏳ Backing off for {wait_time:.1f}s before retry...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise retryable_err
                    
            except Exception as e:
                last_error = e
                logger.error(f"❌ Non-retryable error during OpenAI call: {e}")
                raise e
        
        logger.error(f"All {max_retries} attempts failed. Last error: {last_error}")
        raise last_error

    def create_fallback_result(self, image_path: str, error_message: str) -> Dict:
        """Create a fallback result when OpenAI is unavailable."""
        filename = os.path.basename(image_path)
        
        date_match = re.search(r'(\d{8})', filename)
        receipt_date = None
        if date_match:
            try:
                receipt_date = datetime.strptime(date_match.group(1), '%Y%m%d').date()
            except Exception:
                pass
        
        return {
            'raw_text': f'Receipt processing temporarily unavailable.\nError: {error_message}\nFilename: {filename}',
            'merchant_name': 'Processing Unavailable',
            'amount': None,
            'date': receipt_date or datetime.now().date(),
            'items': [{
                'description': 'Receipt processing temporarily unavailable due to server issues',
                'price': None,
                'quantity': 1
            }],
            'success': False,
            'error': f'OpenAI API unavailable: {error_message}',
            'method': 'fallback',
            'note': 'Please try again later'
        }

    def process_receipt(self, image_path: str, photo_paths: Optional[List[str]] = None) -> Dict:
        """Main method to process a receipt image/PDF using OpenAI Vision API.

        ``photo_paths`` (2+ images, in order) are treated as consecutive photos of ONE
        long receipt: they are read together, stitched into a single image, and repeated
        lines from overlapping/duplicated photos are merged.
        """
        logger.info(f"Processing receipt: {image_path}")

        if not self.openai_client:
            error_msg = "OpenAI client not initialized. Please provide a valid API key."
            logger.error(error_msg)
            return self.create_fallback_result(image_path, error_msg)

        converted_image_path = None
        page_image_paths = []
        is_pdf = False
        is_multi_photo = bool(photo_paths and len(photo_paths) > 1)
        page_count = 1

        try:
            file_ext = os.path.splitext(image_path)[1].lower()
            if is_multi_photo:
                page_image_paths = list(photo_paths)
                page_count = len(page_image_paths)
                stitched_path = os.path.splitext(page_image_paths[0])[0] + '_receipt.jpg'
                converted_image_path = self.stitch_photos(page_image_paths, stitched_path)
                logger.info(f"{page_count} photos stitched to: {converted_image_path}")
            elif file_ext == '.pdf':
                logger.info("PDF file detected, converting pages to image(s)...")
                is_pdf = True
                converted_image_path, page_image_paths, page_count = self.convert_pdf_to_images(image_path)
                logger.info(f"PDF converted: {page_count} page(s). Stitched: {converted_image_path}")
            else:
                converted_image_path = image_path
                page_image_paths = [image_path]

            # Build image_url content items for OpenAI message
            image_content_items = []
            
            # If multi-page PDF (<= 5 pages), send each page image individually for maximum OCR resolution
            if is_multi_photo:
                send_individually = page_count <= 8
            else:
                send_individually = is_pdf and 1 < page_count <= 5
            images_to_send = page_image_paths if send_individually else [converted_image_path]
            
            for img_p in images_to_send:
                base64_img = self.encode_image_to_base64(img_p)
                ext = os.path.splitext(img_p)[1].lower()
                mime = "image/png" if ext == '.png' else "image/jpeg"
                image_content_items.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{base64_img}",
                        "detail": "high"
                    }
                })

            prompt_instructions = """Analyze this receipt image (which may have one or multiple pages/parts) and extract all financial and item details in exact JSON format:

{
    "merchant_name": "name of the business/store",
    "address": "full street address of the store (e.g. '123 Main St, Reno, NV 89501')",
    "location": "city and state of the store (e.g. 'Reno, NV' or 'Sparks, NV')",
    "amount": 15.99,
    "date": "YYYY-MM-DD",
    "items": [
        {
            "description": "item description",
            "price": 5.00,
            "quantity": 1,
            "sku": "item code/sku if visible, or null",
            "discount": 0
        }
    ],
    "tax_amount": 1.25,
    "tax_rate": 8.25,
    "subtotal": 14.74,
    "discount_amount": 0,
    "extra_discount": 0
}

Important extraction rules:
1. Extract ALL line items across all visible pages/sections in sequential order.
2. If quantity is specified (e.g. "3 @ $2.50" or "2 x $5.00"), set quantity and per-unit price correctly.
3. If an item line begins with a count (e.g. "6 Men's Shoes"), set quantity=6.
4. Calculate subtotal as sum of (price × quantity) for all items.
5. tax_amount must be the actual tax charged (0 for tax-exempt or thrift purchases).
6. Ensure amount (total) equals subtotal + tax_amount - discounts.
7. Return valid JSON only."""

            if is_multi_photo and send_individually:
                prompt_instructions += f"""

MULTI-PHOTO RECEIPT: the {len(images_to_send)} images are consecutive photos, in order, of ONE long receipt. Neighbouring photos often overlap, and the same photo may even be included twice.
- For EVERY item, add a "photo" field with the 1-based number of the image you read it from.
- List every item you can read in each image, even when its line also appears in the previous image. Do NOT merge or skip repeated lines yourself - the application removes overlaps.
- Take merchant, address, date, subtotal, tax and total from the receipt's header/totals only; never add them up across photos."""

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_instructions},
                        *image_content_items
                    ]
                }
            ]

            logger.info(f"Sending vision request ({len(image_content_items)} image(s), {page_count} page(s))...")
            raw_response = self.call_openai_with_retry(messages, model=self.default_model, max_tokens=2500)

            response_text = (raw_response or '').strip()
            logger.info(f"Raw response preview: {response_text[:300]}...")

            try:
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                if start_idx != -1 and end_idx > start_idx:
                    parsed_data = json.loads(response_text[start_idx:end_idx])
                else:
                    parsed_data = json.loads(response_text)
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Failed to parse JSON: {e}")
                return self.create_fallback_result(image_path, f"JSON parsing failed: {str(e)}")

            # Process items list
            items_list = parsed_data.get('items', []) or []
            photo_notes = []
            if is_multi_photo:
                items_list, photo_notes = merge_photo_items(items_list, page_count)
                photo_notes += subtotal_note(items_list, parsed_data.get('subtotal'))
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

            item_level_discount = 0.0
            for it in items_list:
                try:
                    item_level_discount += float(it.get('discount', 0) or 0)
                except (ValueError, TypeError):
                    pass

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
                'page_count': page_count,
                'is_multipage': page_count > 1,
                'photo_notes': photo_notes,
                'success': True,
                'method': f"gpt_vision_{'pdf_' if is_pdf else 'photos_' if is_multi_photo else ''}{page_count}p",
                'original_filename': os.path.basename(image_path) if is_pdf else None,
                'converted_filename': os.path.basename(converted_image_path) if (is_pdf or is_multi_photo) else None
            }

            # Convert date string to date object
            if result['date']:
                try:
                    result['date'] = datetime.strptime(str(result['date']).split('T')[0], '%Y-%m-%d').date()
                except ValueError:
                    for fmt in ['%m/%d/%Y', '%d/%m/%Y', '%Y/%m/%d']:
                        try:
                            result['date'] = datetime.strptime(str(result['date']), fmt).date()
                            break
                        except ValueError:
                            continue
                    else:
                        result['date'] = datetime.now().date()

            # Clean up original PDF if converted
            if (is_pdf or is_multi_photo) and converted_image_path:
                try:
                    if os.path.exists(image_path) and image_path != converted_image_path:
                        os.remove(image_path)
                        logger.info(f"Cleaned up original PDF: {image_path}")
                except OSError as e:
                    logger.warning(f"Could not remove PDF file {image_path}: {e}")
                
                # Clean up individual page temp files if more than 1
                for p_img in page_image_paths:
                    if p_img != converted_image_path and os.path.exists(p_img):
                        try:
                            os.remove(p_img)
                        except OSError:
                            pass

                result['processed_filename'] = os.path.basename(converted_image_path)

            if (result['discount_amount'] in [None, 0]) and item_level_discount > 0:
                result['discount_amount'] = item_level_discount

            if result['discount_amount'] and result['discount_amount'] < 0:
                result['discount_amount'] = abs(result['discount_amount'])

            if not result['discount_amount']:
                coupon_match = re.search(r'(?i)(coupon|discount|savings)[^\d]*(?:-|\$)(\d+\.\d{2})', response_text)
                if coupon_match:
                    try:
                        result['discount_amount'] = float(coupon_match.group(2))
                    except ValueError:
                        pass

            if (result.get('subtotal') is not None) and (result.get('amount') is not None):
                try:
                    derived_discount = (result['subtotal'] + (result['tax_amount'] or 0)) - result['amount']
                    if derived_discount > (result['discount_amount'] or 0) + 0.009:
                        result['discount_amount'] = round(derived_discount, 2)
                except Exception:
                    pass

            logger.info(f"Processing complete for {image_path}. Pages: {page_count}. Success: {result['success']}")
            return result

        except Exception as e:
            logger.error(f"Error processing receipt: {e}")
            import traceback
            traceback.print_exc()
            
            if converted_image_path and converted_image_path != image_path and os.path.exists(converted_image_path):
                try:
                    os.remove(converted_image_path)
                except OSError:
                    pass
            
            return self.create_fallback_result(image_path, str(e)) 