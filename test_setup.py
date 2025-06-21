#!/usr/bin/env python3
"""
Test script to verify Receipt Reader setup
"""

import sys
import subprocess
import importlib

def test_python_version():
    """Test if Python version is compatible"""
    print("Testing Python version...")
    version = sys.version_info
    if version.major >= 3 and version.minor >= 8:
        print(f"✅ Python {version.major}.{version.minor}.{version.micro} is compatible")
        return True
    else:
        print(f"❌ Python {version.major}.{version.minor}.{version.micro} is too old. Need Python 3.8+")
        return False

def test_tesseract():
    """Test if Tesseract is installed and accessible"""
    print("\nTesting Tesseract installation...")
    try:
        result = subprocess.run(['tesseract', '--version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            print(f"✅ {version_line}")
            return True
        else:
            print("❌ Tesseract command failed")
            return False
    except FileNotFoundError:
        print("❌ Tesseract not found. Please install Tesseract OCR.")
        print("   Windows: Download from https://github.com/UB-Mannheim/tesseract/wiki")
        print("   macOS: brew install tesseract")
        print("   Ubuntu: sudo apt install tesseract-ocr")
        return False
    except subprocess.TimeoutExpired:
        print("❌ Tesseract command timed out")
        return False

def test_imports():
    """Test if all required Python packages can be imported"""
    print("\nTesting Python package imports...")
    
    packages = {
        'flask': 'Flask',
        'cv2': 'OpenCV',
        'PIL': 'Pillow (PIL)',
        'pytesseract': 'PyTesseract',
        'easyocr': 'EasyOCR',
        'pandas': 'Pandas',
        'openpyxl': 'OpenPyXL',
        'wtforms': 'WTForms',
        'flask_wtf': 'Flask-WTF'
    }
    
    all_passed = True
    
    for package, display_name in packages.items():
        try:
            importlib.import_module(package)
            print(f"✅ {display_name}")
        except ImportError as e:
            print(f"❌ {display_name} - {str(e)}")
            all_passed = False
    
    return all_passed

def test_ocr_functionality():
    """Test basic OCR functionality"""
    print("\nTesting OCR functionality...")
    
    try:
        # Test our improved ReceiptOCR class
        from receipt_ocr import ReceiptOCR
        from PIL import Image, ImageDraw, ImageFont
        import tempfile
        import os
        
        # Create a simple test image with text
        img = Image.new('RGB', (300, 100), color='white')
        draw = ImageDraw.Draw(img)
        
        try:
            # Try to use a basic font
            font = ImageFont.load_default()
        except:
            font = None
        
        draw.text((10, 30), "Test Receipt", fill='black', font=font)
        draw.text((10, 50), "Total: $25.99", fill='black', font=font)
        
        # Save to temporary file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            img.save(tmp_file.name)
            temp_image_path = tmp_file.name
        
        try:
            # Test our OCR system
            ocr = ReceiptOCR()
            result = ocr.process_receipt(temp_image_path)
            
            if result and result.get('raw_text'):
                raw_text = result['raw_text'].lower()
                if "test" in raw_text or "25.99" in raw_text or "receipt" in raw_text:
                    print("✅ OCR functionality working with available engines")
                    return True
                else:
                    print(f"❌ OCR extracted text but didn't find expected content: '{result['raw_text']}'")
                    return False
            else:
                print("❌ OCR failed to extract any text")
                return False
                
        finally:
            # Clean up temporary file
            if os.path.exists(temp_image_path):
                os.unlink(temp_image_path)
            
    except Exception as e:
        print(f"❌ OCR test failed: {str(e)}")
        return False

def test_easyocr():
    """Test EasyOCR functionality"""
    print("\nTesting EasyOCR functionality...")
    
    try:
        import easyocr
        print("✅ EasyOCR imported successfully")
        print("   Note: EasyOCR will download models on first use (~100MB)")
        return True
    except Exception as e:
        print(f"❌ EasyOCR test failed: {str(e)}")
        return False

def test_database():
    """Test database functionality"""
    print("\nTesting database functionality...")
    
    try:
        import sqlite3
        
        # Test creating an in-memory database
        conn = sqlite3.connect(':memory:')
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE test_table (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        ''')
        
        cursor.execute("INSERT INTO test_table (name) VALUES (?)", ("test",))
        result = cursor.execute("SELECT * FROM test_table").fetchone()
        
        conn.close()
        
        if result:
            print("✅ SQLite database functionality working")
            return True
        else:
            print("❌ Database test failed")
            return False
            
    except Exception as e:
        print(f"❌ Database test failed: {str(e)}")
        return False

def main():
    """Run all tests"""
    print("Receipt Reader - Setup Verification Test")
    print("=" * 50)
    
    tests = [
        test_python_version,
        test_tesseract,
        test_imports,
        test_database,
        test_ocr_functionality,
        test_easyocr
    ]
    
    results = []
    for test in tests:
        results.append(test())
    
    print("\n" + "=" * 50)
    print("SUMMARY:")
    
    if all(results):
        print("🎉 All tests passed! Your setup is ready for Receipt Reader.")
        print("\nTo start the application, run:")
        print("   python app.py")
        return 0
    else:
        failed_count = results.count(False)
        print(f"❌ {failed_count} test(s) failed. Please fix the issues above.")
        print("\nInstallation help:")
        print("1. Install Tesseract OCR for your operating system")
        print("2. Run: pip install -r requirements.txt")
        print("3. Run this test again: python test_setup.py")
        return 1

if __name__ == "__main__":
    sys.exit(main()) 