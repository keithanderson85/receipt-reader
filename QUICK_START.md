# Quick Start Guide - Receipt Reader

This guide will get you up and running with the Receipt Reader in just a few minutes!

## ⚡ Option 1: Windows Quick Setup

### Step 1: Install Python
1. Go to https://www.python.org/downloads/
2. Download Python 3.11 or newer
3. **IMPORTANT**: Check "Add Python to PATH" during installation

### Step 2: Install Tesseract OCR
1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
2. Install the .exe file (use default settings)
3. The installer should add Tesseract to your PATH automatically

### Step 3: Set up the project
```bash
# Open Command Prompt or PowerShell
cd path\to\receipt_reader

# Create virtual environment
python -m venv venv

# Activate virtual environment
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Test your setup
python test_setup.py

# Run the app
python app.py
```

## 🐧 Option 2: Linux/Ubuntu Quick Setup

```bash
# Install Python and Tesseract
sudo apt update
sudo apt install python3 python3-pip python3-venv tesseract-ocr

# Set up the project
cd receipt_reader
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Test your setup
python test_setup.py

# Run the app
python app.py
```

## 🍎 Option 3: macOS Quick Setup

```bash
# Install Homebrew if you don't have it
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python and Tesseract
brew install python tesseract

# Set up the project
cd receipt_reader
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Test your setup
python test_setup.py

# Run the app
python app.py
```

## 🚀 Using the App

1. **Start the app**: Open your browser to `http://localhost:5000`

2. **Upload a receipt**: 
   - Click the upload area or drag & drop an image
   - Supported formats: JPG, PNG, PDF

3. **Review & save**: 
   - Check the extracted information
   - Select the right category
   - Add any notes
   - Click "Save Expense"

4. **Export for taxes**:
   - Go to "View Expenses"
   - Click "Export to Excel" or "Export to CSV"

## 🐛 Troubleshooting

### "Python was not found"
- Make sure you checked "Add Python to PATH" during installation
- Restart your terminal/command prompt
- Try `python3` instead of `python`

### "tesseract is not recognized"
- Restart your terminal after installing Tesseract
- On Windows, make sure Tesseract is in your PATH
- Test with: `tesseract --version`

### "Module not found" errors
- Make sure your virtual environment is activated
- Run `pip install -r requirements.txt` again

### OCR not working well
- Ensure receipt images are clear and well-lit
- Try scanning at higher resolution
- Make sure text is horizontal (not rotated)

### EasyOCR is slow on first use
- EasyOCR downloads AI models (~100MB) on first use
- This is normal and only happens once
- Subsequent uses will be much faster

## 💡 Pro Tips

1. **Better OCR results**:
   - Use good lighting when taking photos
   - Keep the receipt flat
   - Higher resolution is better (but not too large)

2. **Tax organization**:
   - Export data monthly for easier tracking
   - Use descriptive notes for business purpose
   - Keep receipt images as backup

3. **Categories**:
   - The app suggests categories based on merchant names
   - You can customize categories in `app.py`
   - Common categories are pre-configured for taxes

## 📱 Mobile Usage

The web app works great on mobile browsers:
- Take photos directly with your phone camera
- Upload immediately for processing
- Review and categorize on the go

## 🔄 Regular Backups

Your data is stored locally in:
- Database: `receipts.db`
- Images: `uploads/` folder

Make sure to backup these regularly!

## 🆘 Need Help?

1. Run the setup test: `python test_setup.py`
2. Check the main README.md for detailed troubleshooting
3. Ensure all prerequisites are installed correctly

---

**Ready to start tracking your business expenses? Run `python app.py` and visit http://localhost:5000!** 