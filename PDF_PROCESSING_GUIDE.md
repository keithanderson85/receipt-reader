# PDF Processing Guide

## Overview

Your receipt reader now supports PDF files! You can upload PDF receipts and they will be automatically converted to images and processed using OpenAI's GPT-4 Vision API.

## What's New

✅ **PDF Upload Support**: Upload PDF receipts alongside JPG, PNG, and other image formats
✅ **Automatic Conversion**: PDFs are automatically converted to images for processing
✅ **Same OCR Quality**: PDF receipts get the same high-quality text extraction as images
✅ **Bulk Processing**: PDF files work with both single and bulk upload features

## Setup Instructions

### Windows Users

1. **Install PDF Processing Dependencies**:
   ```bash
   pip install pdf2image
   python setup_poppler.py
   ```

2. **For New Sessions**: Run `set_poppler_path.bat` in new command prompts to enable PDF processing

### Mac/Linux Users

1. **Install PDF Processing Dependencies**:
   ```bash
   pip install pdf2image
   ```

2. **Install Poppler**:
   - **Ubuntu/Debian**: `sudo apt-get install poppler-utils`
   - **macOS**: `brew install poppler` 
   - **Arch Linux**: `sudo pacman -S poppler`

## How It Works

1. **Upload**: Select PDF files through the web interface
2. **Conversion**: PDFs are automatically converted to high-quality images (300 DPI)
3. **Processing**: The converted images are sent to OpenAI's GPT-4 Vision API
4. **Extraction**: Receipt data is extracted just like with regular images
5. **Cleanup**: Temporary image files are automatically cleaned up

## Supported File Types

- **Images**: JPG, JPEG, PNG, GIF, WEBP
- **Documents**: PDF (new!)

## Features

- ✅ Multi-page PDF support (processes first page)
- ✅ High-quality conversion (300 DPI)
- ✅ Automatic temporary file cleanup
- ✅ Same processing workflow as images
- ✅ Works with both single and bulk uploads

## Processing Method

PDF files are processed using the `gpt4_vision_pdf` method, which:
1. Converts PDF to JPEG image using pdf2image
2. Processes the image with OpenAI Vision API
3. Extracts merchant, amount, date, items, and tax information
4. Cleans up temporary files

## Troubleshooting

### "Unable to get page count" Error

This means poppler is not installed or not in PATH:

**Windows**: Run `python setup_poppler.py` and then `set_poppler_path.bat`
**Mac/Linux**: Install poppler using your package manager

### Large PDF Files

- PDFs are converted to images, which may be large
- OpenAI has a 20MB limit per request
- Very large PDFs may fail - try using smaller/compressed PDFs

### Multi-page PDFs

Currently only the first page is processed. For multi-page receipts, consider:
- Splitting into separate single-page PDFs
- Using image formats if possible

## Benefits

- **Convenience**: Upload receipts in any format
- **Quality**: Same high-quality OCR as images
- **Efficiency**: Automatic conversion and processing
- **Compatibility**: Works with existing receipt processing workflow

## Technical Details

The PDF processing is handled by:
- `pdf2image`: Converts PDFs to PIL images
- `poppler`: Underlying PDF rendering engine
- OpenAI GPT-4 Vision API: Text extraction and parsing

Processing flow:
```
PDF → Convert to Image → Base64 Encode → OpenAI API → JSON Response → Database
```

## Getting Started

1. Make sure poppler is installed (run setup_poppler.py on Windows)
2. Upload a PDF receipt through the web interface
3. The system will automatically detect it's a PDF and convert it
4. Review and save the extracted data as usual

That's it! Your receipt reader now supports PDF files seamlessly. 