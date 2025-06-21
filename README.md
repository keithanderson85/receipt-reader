# Receipt Reader - Business Expense Tracker

A powerful web application that uses OCR (Optical Character Recognition) to automatically extract data from receipt images and track business expenses for tax purposes.

## 🚀 Features

### OCR & Receipt Processing
- **Dual OCR Engines**: Uses both Tesseract and EasyOCR for maximum accuracy
- **Smart Data Extraction**: Automatically extracts merchant name, amount, and date
- **Image Preprocessing**: Enhances image quality for better OCR results
- **Multiple Formats**: Supports JPG, PNG, and PDF files

### Expense Management
- **Automatic Categorization**: Business expense categories aligned with tax requirements
- **Smart Category Suggestions**: AI-powered category recommendations based on merchant names
- **Manual Review**: Always allows manual verification and correction of extracted data
- **Receipt Storage**: Saves original receipt images linked to expenses

### Tax Compliance
- **IRS-Ready Categories**: Pre-configured business expense categories
- **Export Options**: Export to Excel or CSV for tax filing
- **3-Year Storage**: Built-in reminder about IRS record-keeping requirements
- **Summary Reports**: Category breakdowns and totals for easy accounting

### Modern Web Interface
- **Responsive Design**: Works on desktop, tablet, and mobile
- **Drag & Drop Upload**: Easy file upload with visual feedback
- **Real-time Processing**: Live OCR processing with progress indicators
- **Dashboard Analytics**: Visual expense summaries and insights

## 🏗️ Tech Stack

- **Backend**: Python Flask
- **Database**: SQLite (easily replaceable with PostgreSQL/MySQL)
- **OCR**: Tesseract + EasyOCR
- **Image Processing**: OpenCV + PIL
- **Frontend**: Bootstrap 5 + Vanilla JavaScript
- **Data Export**: Pandas + OpenPyXL

## 📋 Prerequisites

### System Requirements
- Python 3.8 or higher
- Tesseract OCR engine

### Installing Tesseract

**Windows:**
1. Download from: https://github.com/UB-Mannheim/tesseract/wiki
2. Install and add to PATH

**macOS:**
```bash
brew install tesseract
```

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install tesseract-ocr tesseract-ocr-eng
```

**CentOS/RHEL/Fedora:**
```bash
sudo yum install tesseract tesseract-langpack-eng
# or for newer versions:
sudo dnf install tesseract tesseract-langpack-eng
```

## 🛠️ Installation

1. **Clone the repository:**
```bash
git clone <repository-url>
cd receipt_reader
```

2. **Create a virtual environment:**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Set up the environment:**
```bash
# Create uploads directory
mkdir uploads

# Set Flask environment variables (optional)
export FLASK_ENV=development
export FLASK_DEBUG=1
```

5. **Initialize the database:**
The database will be automatically created when you first run the app.

## 🚀 Usage

### Starting the Application

```bash
python app.py
```

The application will be available at: `http://localhost:5000`

### Using the Receipt Reader

1. **Upload a Receipt**:
   - Navigate to the dashboard
   - Click the upload area or drag & drop a receipt image
   - Supported formats: JPG, PNG, PDF

2. **Review Extracted Data**:
   - The OCR engine will automatically extract:
     - Merchant name
     - Transaction amount
     - Transaction date
   - Review and correct any information as needed

3. **Categorize the Expense**:
   - Select the appropriate business expense category
   - Add any additional description or notes
   - Save the expense

4. **View & Manage Expenses**:
   - View all expenses in the "View Expenses" section
   - Filter by category or year
   - Edit or delete expenses as needed

5. **Export for Taxes**:
   - Export to Excel or CSV format
   - Data includes all required information for tax filing
   - Exports can be filtered by year or category

### Business Expense Categories

The app includes IRS-aligned categories:
- Office Supplies
- Travel & Transportation
- Meals & Entertainment
- Equipment & Technology
- Advertising & Marketing
- Professional Services
- Utilities
- Rent & Facility Costs
- Insurance
- Other Business Expenses

## 🔧 Configuration

### Environment Variables

Create a `.env` file in the root directory:

```bash
# Flask Configuration
SECRET_KEY=your-secret-key-here
FLASK_ENV=production  # or development

# File Upload
MAX_CONTENT_LENGTH=16777216  # 16MB in bytes
UPLOAD_FOLDER=uploads

# Database (optional - defaults to SQLite)
DATABASE_URL=sqlite:///receipts.db

# OCR Configuration (optional)
TESSERACT_CMD=/usr/bin/tesseract  # Path to tesseract executable
```

### Customizing Categories

Edit the `EXPENSE_CATEGORIES` list in `app.py` to add or modify categories:

```python
EXPENSE_CATEGORIES = [
    ('custom_category', 'Custom Category Name'),
    # Add more categories as needed
]
```

## 📊 Tax Season Features

### What the App Tracks for Taxes

- **Total business expenses** by category and year
- **Receipt images** as backup documentation
- **Transaction details** including date, merchant, and amount
- **Business purpose** through descriptions and categories

### What You Don't Need to Track Individually

Based on IRS requirements, you typically don't need to track:
- Individual items within a receipt (unless for inventory/COGS)
- Personal expenses (keep these separate)
- Expenses under $75 without receipts (though this app helps you track everything)

### Exporting for Your Accountant

1. Go to "View Expenses"
2. Filter by the tax year
3. Click "Export to Excel" or "Export to CSV"
4. The export includes:
   - Summary totals by category
   - Detailed transaction list
   - All required tax documentation

## 🔒 Security Considerations

- Change the default `SECRET_KEY` in production
- Consider using environment variables for sensitive configuration
- Regularly backup your receipt images and database
- Consider HTTPS for production deployments

## 🚀 Production Deployment

### Using Gunicorn (Recommended)

1. **Install Gunicorn:**
```bash
pip install gunicorn
```

2. **Run with Gunicorn:**
```bash
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```

### Using Docker

1. **Create Dockerfile:**
```dockerfile
FROM python:3.9-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 5000

CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:5000", "app:app"]
```

2. **Build and run:**
```bash
docker build -t receipt-reader .
docker run -p 5000:5000 -v $(pwd)/uploads:/app/uploads receipt-reader
```

## 🐛 Troubleshooting

### OCR Not Working

1. **Check Tesseract installation:**
```bash
tesseract --version
```

2. **Verify image quality:**
   - Ensure receipts are clear and well-lit
   - Try preprocessing the image (the app does this automatically)

3. **Check file permissions:**
   - Ensure the uploads directory is writable
   - Check file size limits

### Performance Issues

1. **Large file uploads:**
   - Reduce image size before uploading
   - Check `MAX_CONTENT_LENGTH` setting

2. **OCR processing slow:**
   - EasyOCR loads models on first use (takes a few seconds)
   - Consider using only Tesseract for faster processing

### Database Issues

1. **Database locked errors:**
   - Ensure only one instance is running
   - Check file permissions on the database file

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 📞 Support

For issues and questions:
1. Check the troubleshooting section above
2. Search existing GitHub issues
3. Create a new issue with detailed information

## 🙏 Acknowledgments

- **Tesseract OCR** - Google's powerful OCR engine
- **EasyOCR** - Modern deep learning-based OCR
- **Flask** - Lightweight web framework
- **Bootstrap** - UI framework for responsive design

---

**Note**: This app is designed to help you organize business expenses for tax purposes. Always consult with a qualified accountant or tax professional for specific tax advice. 