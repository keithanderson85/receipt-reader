import os
import sqlite3
import hashlib
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session, send_from_directory, g
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from wtforms import StringField, FloatField, DateField, SelectField, TextAreaField, SubmitField, PasswordField
from wtforms.validators import DataRequired, NumberRange, Length
from werkzeug.utils import secure_filename
import pandas as pd
from receipt_ocr_genai import ReceiptOCRGenAI
import io
import json
import logging
import traceback

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'your-secret-key-change-this')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max total request size

# Production settings
if os.getenv('RAILWAY_ENVIRONMENT'):
    app.config['DEBUG'] = False
else:
    app.config['DEBUG'] = True

# Initialize extensions
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
bcrypt = Bcrypt(app)

# Create upload directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Error handlers
@app.errorhandler(413)
def request_entity_too_large(error):
    flash('Files too large! Total upload size must be under 100MB. Try uploading fewer files at once.', 'error')
    return redirect(url_for('bulk_upload_page')), 413

# Initialize the receipt OCR with OpenAI Vision
openai_api_key = os.getenv('OPENAI_API_KEY')
print(f"DEBUG: OpenAI API Key loaded: {'YES' if openai_api_key else 'NO'}")
if openai_api_key:
    print(f"DEBUG: API Key starts with: {openai_api_key[:10]}...")
if not openai_api_key:
    print("WARNING: OPENAI_API_KEY environment variable not set!")
receipt_ocr = ReceiptOCRGenAI(openai_api_key=openai_api_key)

# Expense categories for business tax purposes
EXPENSE_CATEGORIES = [
    ('inventory', 'Inventory (Items for Resale)'),
    ('office_supplies', 'Office Supplies'),
    ('travel', 'Travel & Transportation'),
    ('meals', 'Meals & Entertainment'),
    ('equipment', 'Equipment & Technology'),
    ('advertising', 'Advertising & Marketing'),
    ('professional_services', 'Professional Services'),
    ('utilities', 'Utilities'),
    ('rent', 'Rent & Facility Costs'),
    ('insurance', 'Insurance'),
    ('other', 'Other Business Expenses')
]

class ReceiptForm(FlaskForm):
    file = FileField('Receipt Image', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Only JPG, PNG, and PDF files are allowed!')
    ])
    submit = SubmitField('Upload and Process')

class ExpenseForm(FlaskForm):
    merchant_name = StringField('Merchant Name', validators=[DataRequired()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    date = DateField('Date', validators=[DataRequired()], default=datetime.now().date())
    category = SelectField('Category', choices=EXPENSE_CATEGORIES, validators=[DataRequired()])
    description = TextAreaField('Description')
    submit = SubmitField('Save Expense')

class BulkReceiptForm(FlaskForm):
    files = FileField('Receipt Images', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Only JPG, PNG, and PDF files are allowed!')
    ], render_kw={'multiple': True})
    submit = SubmitField('Upload and Process All')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    submit = SubmitField('Register')

class User(UserMixin):
    def __init__(self, id, username, password_hash):
        self.id = id
        self.username = username
        self.password_hash = password_hash

@login_manager.user_loader
def load_user(user_id):
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, username, password_hash FROM users WHERE id = ?', (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    if user_data:
        return User(user_data[0], user_data[1], user_data[2])
    return None

def init_db():
    """Initialize the database with required tables."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Users table for authentication
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            merchant_name TEXT NOT NULL,
            amount REAL NOT NULL,
            date DATE NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            receipt_filename TEXT,
            file_hash TEXT,
            subtotal REAL,
            tax_amount REAL,
            tax_rate REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    # Add table for individual items
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS receipt_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expense_id INTEGER NOT NULL,
            sku TEXT,
            description TEXT NOT NULL,
            price REAL NOT NULL,
            raw_line TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (expense_id) REFERENCES expenses (id) ON DELETE CASCADE
        )
    ''')
    
    # Add tax-related columns if they don't exist
    try:
        cursor.execute('ALTER TABLE expenses ADD COLUMN subtotal REAL')
        cursor.execute('ALTER TABLE expenses ADD COLUMN tax_amount REAL')
        cursor.execute('ALTER TABLE expenses ADD COLUMN tax_rate REAL')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Columns already exist
    
    # Add file_hash column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE expenses ADD COLUMN file_hash TEXT')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    # Add user_id column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE expenses ADD COLUMN user_id INTEGER')
        conn.commit()
        
        # If we just added the user_id column, we need to migrate existing data
        cursor.execute('SELECT COUNT(*) FROM expenses WHERE user_id IS NULL')
        orphaned_expenses = cursor.fetchone()[0]
        
        if orphaned_expenses > 0:
            # Find or create the "keithanderson" user for existing data
            cursor.execute('SELECT id FROM users WHERE username = ?', ('keithanderson',))
            keith_user = cursor.fetchone()
            
            if keith_user:
                keith_user_id = keith_user[0]
                print(f"Migrating {orphaned_expenses} existing expenses to user 'keithanderson' (ID: {keith_user_id})")
            else:
                # Create the keithanderson user if it doesn't exist
                from flask_bcrypt import Bcrypt
                temp_bcrypt = Bcrypt()
                temp_password = temp_bcrypt.generate_password_hash('changeme123').decode('utf-8')
                cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', 
                              ('keithanderson', temp_password))
                keith_user_id = cursor.lastrowid
                print(f"Created user 'keithanderson' (ID: {keith_user_id}) and migrating {orphaned_expenses} existing expenses")
                print("IMPORTANT: Default password is 'changeme123' - please change it after login!")
            
            # Migrate all orphaned expenses to keithanderson
            cursor.execute('UPDATE expenses SET user_id = ? WHERE user_id IS NULL', (keith_user_id,))
            conn.commit()
            print(f"Successfully migrated {orphaned_expenses} expenses to user 'keithanderson'")
            
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    conn.commit()
    conn.close()

def calculate_file_hash(filepath):
    """Calculate SHA256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()

def check_for_duplicates(merchant_name, amount, date, file_hash=None, user_id=None):
    """Check for potential duplicate receipts for a specific user."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    duplicates = {
        'exact_file': [],
        'exact_match': [],
        'similar': []
    }
    
    # If no user_id provided, use current user
    if user_id is None and current_user.is_authenticated:
        user_id = current_user.id
    
    if user_id is None:
        # Can't check duplicates without a user context
        conn.close()
        return duplicates
    
    # Check for exact file hash match
    if file_hash:
        cursor.execute('''
            SELECT id, merchant_name, amount, date, receipt_filename
            FROM expenses 
            WHERE file_hash = ? AND user_id = ?
        ''', (file_hash, user_id))
        duplicates['exact_file'] = cursor.fetchall()
    
    # Check for exact receipt match (same merchant, amount, date)
    cursor.execute('''
        SELECT id, merchant_name, amount, date, receipt_filename
        FROM expenses 
        WHERE merchant_name = ? AND amount = ? AND date = ? AND user_id = ?
    ''', (merchant_name, amount, date, user_id))
    duplicates['exact_match'] = cursor.fetchall()
    
    # Check for similar receipts (same merchant, similar amount, close date)
    date_obj = datetime.strptime(str(date), '%Y-%m-%d').date() if isinstance(date, str) else date
    date_range_start = date_obj - timedelta(days=3)
    date_range_end = date_obj + timedelta(days=3)
    amount_min = float(amount) * 0.95  # 5% tolerance
    amount_max = float(amount) * 1.05
    
    cursor.execute('''
        SELECT id, merchant_name, amount, date, receipt_filename
        FROM expenses 
        WHERE LOWER(merchant_name) = LOWER(?) 
        AND amount BETWEEN ? AND ?
        AND date BETWEEN ? AND ?
        AND NOT (merchant_name = ? AND amount = ? AND date = ?)
        AND user_id = ?
    ''', (merchant_name, amount_min, amount_max, date_range_start, date_range_end,
          merchant_name, amount, date, user_id))
    duplicates['similar'] = cursor.fetchall()
    
    conn.close()
    return duplicates

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = LoginForm()
    if form.validate_on_submit():
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute('SELECT id, username, password_hash FROM users WHERE username = ?', (form.username.data,))
        user_data = cursor.fetchone()
        conn.close()
        
        if user_data and bcrypt.check_password_hash(user_data[2], form.password.data):
            user = User(user_data[0], user_data[1], user_data[2])
            login_user(user)
            flash('Logged in successfully!', 'success')
            next_page = request.args.get('next')
            return redirect(next_page) if next_page else redirect(url_for('index'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html', form=form)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    form = RegisterForm()
    if form.validate_on_submit():
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Check if username already exists
        cursor.execute('SELECT id FROM users WHERE username = ?', (form.username.data,))
        if cursor.fetchone():
            flash('Username already exists. Please choose a different one.', 'error')
            conn.close()
            return render_template('register.html', form=form)
        
        # Create new user
        password_hash = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', 
                      (form.username.data, password_hash))
        conn.commit()
        conn.close()
        
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    
    return render_template('register.html', form=form)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    """Home page showing recent expenses and upload form."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get recent expenses for current user
    cursor.execute('''
        SELECT id, merchant_name, amount, date, category, description
        FROM expenses 
        WHERE user_id = ?
        ORDER BY date DESC, created_at DESC 
        LIMIT 10
    ''', (current_user.id,))
    recent_expenses = cursor.fetchall()
    
    # Get summary statistics for current user
    cursor.execute('SELECT COUNT(*), SUM(amount) FROM expenses WHERE user_id = ?', (current_user.id,))
    total_count, total_amount = cursor.fetchone()
    total_amount = total_amount or 0
    
    # Get category breakdown for current user
    cursor.execute('''
        SELECT category, SUM(amount), COUNT(*)
        FROM expenses 
        WHERE user_id = ?
        GROUP BY category 
        ORDER BY SUM(amount) DESC
    ''', (current_user.id,))
    category_breakdown = cursor.fetchall()
    
    conn.close()
    
    form = ReceiptForm()
    
    return render_template('index.html', 
                         form=form,
                         recent_expenses=recent_expenses,
                         total_count=total_count,
                         total_amount=total_amount,
                         category_breakdown=category_breakdown)

@app.route('/upload', methods=['POST'])
@login_required
def upload_receipt():
    """Handle receipt upload and show processing screen."""
    form = ReceiptForm()
    
    if form.validate_on_submit():
        file = form.file.data
        filename = secure_filename(file.filename)
        
        # Add timestamp to filename to avoid conflicts
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
        filename = timestamp + filename
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        # Store filename in session for the AJAX processing
        session['processing_filename'] = filename
        
        # Create a simple form for CSRF token
        csrf_form = FlaskForm()
        
        # Show processing screen
        return render_template('processing.html', filename=filename, form=csrf_form)
    
    flash('Invalid file upload', 'error')
    return redirect(url_for('index'))

@app.route('/process_receipt_ajax', methods=['POST'])
@login_required
def process_receipt_ajax():
    """Process receipt via AJAX and return JSON response."""
    try:
        filename = request.form.get('filename')
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided'})
            
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            return jsonify({'success': False, 'error': 'File not found'})
        
        # Calculate file hash for duplicate detection
        file_hash = calculate_file_hash(filepath)
        
        # Process the receipt with OCR
        print(f"Processing receipt with OpenAI: {filename}")
        ocr_result = receipt_ocr.process_receipt(filepath)
        print(f"OCR processing complete. Success: {ocr_result.get('success', False)}")
        print(f"OCR result keys: {list(ocr_result.keys()) if ocr_result else 'None'}")
        
        if not ocr_result.get('success', False):
            return jsonify({
                'success': False, 
                'error': ocr_result.get('error', 'OCR processing failed')
            })
        
        # Check for duplicates if we have good data
        duplicates = None
        if ocr_result.get('merchant_name') and ocr_result.get('amount'):
            duplicates = check_for_duplicates(
                ocr_result['merchant_name'], 
                ocr_result['amount'], 
                ocr_result['date'],
                file_hash,
                current_user.id
            )
        
        # Add file hash and duplicates to OCR result
        ocr_result['file_hash'] = file_hash
        ocr_result['duplicates'] = duplicates
        
        # Store results in session for the edit page (handle date serialization)
        if 'date' in ocr_result and ocr_result['date']:
            # Convert date to string if it's a date object
            if hasattr(ocr_result['date'], 'strftime'):
                ocr_result['date'] = ocr_result['date'].strftime('%Y-%m-%d')
        
        session[f'ocr_result_{filename}'] = ocr_result
        
        # Store duplicate info in session for persistence
        if duplicates and (duplicates['exact_file'] or duplicates['exact_match'] or duplicates['similar']):
            session[f'duplicate_warning_{filename}'] = {
                'duplicates': duplicates,
                'merchant': ocr_result['merchant_name'],
                'amount': ocr_result['amount'],
                'date': ocr_result['date']
            }
        
        return jsonify({
            'success': True,
            'redirect_url': url_for('review_receipt', filename=filename)
        })
        
    except Exception as e:
        print(f"Error in AJAX processing: {str(e)}")
        import traceback
        traceback.print_exc()
        print(f"Error type: {type(e).__name__}")
        print(f"Error args: {e.args}")
        
        # Clean up the file on error
        filename = request.form.get('filename')
        if filename:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(filepath):
                os.remove(filepath)
        
        return jsonify({'success': False, 'error': str(e)})

@app.route('/review/<filename>')
@login_required 
def review_receipt(filename):
    """Review page for processed receipt."""
    # Get OCR results from session
    ocr_result = session.get(f'ocr_result_{filename}')
    if not ocr_result:
        flash('Receipt processing data not found. Please upload again.', 'error')
        return redirect(url_for('index'))
    
    # Clean up session data
    session.pop(f'ocr_result_{filename}', None)
    
    return render_template('edit_expense.html', 
                         form=ExpenseForm(),
                         ocr_result=ocr_result,
                         filename=filename)

@app.route('/save_expense', methods=['POST'])
@login_required
def save_expense():
    """Save expense to database."""
    form = ExpenseForm()
    
    if form.validate_on_submit():
        filename = request.form.get('filename')
        
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get file hash from form data
        file_hash = request.form.get('file_hash')
        
        # Insert the main expense
        cursor.execute('''
            INSERT INTO expenses (user_id, merchant_name, amount, date, category, description, receipt_filename, file_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            current_user.id,
            form.merchant_name.data,
            form.amount.data,
            form.date.data,
            form.category.data,
            form.description.data,
            filename,
            file_hash
        ))
        
        expense_id = cursor.lastrowid
        
        # Save individual items if they exist
        items_count = request.form.get('items_count')
        if items_count:
            try:
                items_count = int(items_count)
                print(f"DEBUG: Processing {items_count} items")  # Debug print
                
                for i in range(items_count):
                    sku = request.form.get(f'item_{i}_sku') or None
                    description = request.form.get(f'item_{i}_description')
                    price = request.form.get(f'item_{i}_price')
                    raw_line = request.form.get(f'item_{i}_raw_line')
                    
                    if description and price:  # Only save if we have essential data
                        try:
                            price_float = float(price)
                            cursor.execute('''
                                INSERT INTO receipt_items (expense_id, sku, description, price, raw_line)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (
                                expense_id,
                                sku if sku else None,
                                description,
                                price_float,
                                raw_line
                            ))
                            print(f"DEBUG: Saved item {i+1}: {description} - ${price_float}")
                        except ValueError:
                            print(f"WARNING: Invalid price for item {i+1}: {price}")
                            continue
                            
            except (ValueError, TypeError) as e:
                print(f"ERROR: Failed to process items: {e}")
                flash(f'Warning: Individual items could not be saved. Error: {str(e)}', 'warning')
        
        conn.commit()
        conn.close()
        
        flash('Expense saved successfully!', 'success')
        return redirect(url_for('index'))
    
    flash('Error saving expense. Please check the form.', 'error')
    return redirect(url_for('index'))

@app.route('/expenses')
@login_required
def view_expenses():
    """View all expenses with filtering options."""
    # Get filter parameters
    category_filter = request.args.get('category', '')
    year_filter = request.args.get('year', '')
    month_filter = request.args.get('month', '')
    sort_by = request.args.get('sort', 'date_desc')  # Default sort
    
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Build query with filters (always filter by current user)
    query = '''
        SELECT e.id, e.merchant_name, e.amount, e.date, e.category, e.description, e.receipt_filename,
               e.tax_amount, e.tax_rate, e.subtotal,
               COUNT(ri.id) as item_count
        FROM expenses e
        LEFT JOIN receipt_items ri ON e.id = ri.expense_id
        WHERE e.user_id = ?
    '''
    params = [current_user.id]
    conditions = []
    
    if category_filter:
        conditions.append('e.category = ?')
        params.append(category_filter)
    
    if year_filter:
        conditions.append('strftime("%Y", e.date) = ?')
        params.append(year_filter)
    
    if month_filter:
        conditions.append('strftime("%m", e.date) = ?')
        params.append(f"{int(month_filter):02d}")
    
    if conditions:
        query += ' AND ' + ' AND '.join(conditions)
    
    query += ' GROUP BY e.id'
    
    # Add sorting
    if sort_by == 'date_asc':
        query += ' ORDER BY e.date ASC, e.created_at ASC'
    elif sort_by == 'amount_desc':
        query += ' ORDER BY e.amount DESC'
    elif sort_by == 'amount_asc':
        query += ' ORDER BY e.amount ASC'
    elif sort_by == 'merchant':
        query += ' ORDER BY e.merchant_name ASC'
    else:  # date_desc (default)
        query += ' ORDER BY e.date DESC, e.created_at DESC'
    
    cursor.execute(query, params)
    expenses = cursor.fetchall()
    
    # Get available years and months for filters (current user only)
    cursor.execute('SELECT DISTINCT strftime("%Y", date) FROM expenses WHERE user_id = ? ORDER BY date DESC', (current_user.id,))
    available_years = [row[0] for row in cursor.fetchall()]
    
    cursor.execute('''
        SELECT DISTINCT strftime("%m", date) as month, strftime("%Y", date) as year 
        FROM expenses WHERE user_id = ? 
        ORDER BY year DESC, month DESC
    ''', (current_user.id,))
    available_months = []
    for row in cursor.fetchall():
        month_num = int(row[0])
        month_name = datetime(2000, month_num, 1).strftime('%B')
        available_months.append((row[0], f"{month_name} {row[1]}"))
    
    conn.close()
    
    return render_template('expenses.html', 
                         expenses=expenses,
                         categories=EXPENSE_CATEGORIES,
                         available_years=available_years,
                         available_months=available_months,
                         current_category=category_filter,
                         current_year=year_filter,
                         current_month=month_filter,
                         current_sort=sort_by)

@app.route('/export/<format>')
@login_required
def export_expenses(format):
    """Export expenses to Excel or CSV for tax purposes."""
    year = request.args.get('year', datetime.now().year)
    category = request.args.get('category', '')
    month = request.args.get('month', '')
    
    conn = sqlite3.connect('receipts.db')
    
    # Build query (always filter by current user)
    query = 'SELECT merchant_name, amount, date, category, description FROM expenses WHERE user_id = ?'
    params = [current_user.id]
    conditions = []
    
    if year:
        conditions.append('strftime("%Y", date) = ?')
        params.append(str(year))
    
    if category:
        conditions.append('category = ?')
        params.append(category)
    
    if month:
        conditions.append('strftime("%m", date) = ?')
        params.append(f"{int(month):02d}")
    
    if conditions:
        query += ' AND ' + ' AND '.join(conditions)
    
    query += ' ORDER BY date ASC'
    
    # Load data into pandas DataFrame
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    
    if df.empty:
        flash('No expenses found for the selected criteria.', 'warning')
        return redirect(url_for('view_expenses'))
    
    # Create summary
    total_amount = df['amount'].sum()
    category_summary = df.groupby('category')['amount'].sum().to_dict()
    
    # Add summary rows
    summary_data = []
    summary_data.append(['', '', '', '', ''])
    summary_data.append(['SUMMARY', '', '', '', ''])
    summary_data.append(['Total Amount:', f'${total_amount:.2f}', '', '', ''])
    summary_data.append(['', '', '', '', ''])
    summary_data.append(['Category Breakdown:', '', '', '', ''])
    
    for cat_code, cat_name in EXPENSE_CATEGORIES:
        if cat_code in category_summary:
            summary_data.append([cat_name, f'${category_summary[cat_code]:.2f}', '', '', ''])
    
    # Add summary to DataFrame
    summary_df = pd.DataFrame(summary_data, columns=df.columns)
    df_with_summary = pd.concat([df, summary_df], ignore_index=True)
    
    # Create output
    output = io.BytesIO()
    
    if format == 'excel':
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df_with_summary.to_excel(writer, sheet_name='Expenses', index=False)
        
        output.seek(0)
        
        # Build filename with filters
        filename_parts = ['business_expenses']
        if year:
            filename_parts.append(str(year))
        if month:
            month_name = datetime(2000, int(month), 1).strftime('%m_%B')
            filename_parts.append(month_name)
        if category:
            filename_parts.append(category)
        
        filename = '_'.join(filename_parts) + '.xlsx'
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    
    elif format == 'csv':
        df_with_summary.to_csv(output, index=False)
        output.seek(0)
        
        # Build filename with filters
        filename_parts = ['business_expenses']
        if year:
            filename_parts.append(str(year))
        if month:
            month_name = datetime(2000, int(month), 1).strftime('%m_%B')
            filename_parts.append(month_name)
        if category:
            filename_parts.append(category)
        
        filename = '_'.join(filename_parts) + '.csv'
        mimetype = 'text/csv'
    
    else:
        flash('Invalid export format', 'error')
        return redirect(url_for('view_expenses'))
    
    return send_file(
        output,
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename
    )

@app.route('/delete_expense/<int:expense_id>')
@login_required
def delete_expense(expense_id):
    """Delete an expense and its associated receipt file."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get the receipt filename before deletion (ensure it belongs to current user)
    cursor.execute('SELECT receipt_filename FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
    result = cursor.fetchone()
    
    if result:
        filename = result[0]
        
        # Delete from database
        cursor.execute('DELETE FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
        conn.commit()
        
        # Delete file if it exists
        if filename:
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            if os.path.exists(filepath):
                os.remove(filepath)
        
        flash('Expense deleted successfully!', 'success')
    else:
        flash('Expense not found!', 'error')
    
    conn.close()
    return redirect(url_for('view_expenses'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded receipt images."""
    return send_file(os.path.join(app.config['UPLOAD_FOLDER'], filename))

@app.route('/expense/<int:expense_id>/items')
@login_required
def view_expense_items(expense_id):
    """View individual items for a specific expense."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get expense details (ensure it belongs to current user)
    cursor.execute('''
        SELECT id, merchant_name, amount, date, category, description, receipt_filename
        FROM expenses 
        WHERE id = ? AND user_id = ?
    ''', (expense_id, current_user.id))
    expense = cursor.fetchone()
    
    if not expense:
        flash('Expense not found!', 'error')
        return redirect(url_for('view_expenses'))
    
    # Get individual items
    cursor.execute('''
        SELECT id, sku, description, price, raw_line
        FROM receipt_items 
        WHERE expense_id = ?
        ORDER BY id
    ''', (expense_id,))
    items = cursor.fetchall()
    
    conn.close()
    
    return render_template('expense_items.html', 
                         expense=expense,
                         items=items,
                         categories=EXPENSE_CATEGORIES)

@app.route('/view_raw_text/<filename>')
def view_raw_text(filename):
    """View the raw OCR text extracted from a receipt image for debugging."""
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if not os.path.exists(filepath):
            flash('Receipt file not found!', 'error')
            return redirect(url_for('index'))
        
        # Extract raw text using OCR
        raw_text = receipt_ocr.extract_text_from_image(filepath)
        
        # Also get the cleaned text
        cleaned_text = receipt_ocr.preprocess_text_for_genai(raw_text)
        
        return render_template('raw_text.html', 
                             filename=filename,
                             raw_text=raw_text,
                             cleaned_text=cleaned_text)
    
    except Exception as e:
        flash(f'Error extracting text: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/edit_expense/<int:expense_id>')
@login_required
def edit_expense(expense_id):
    """Edit an existing expense."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get expense details (ensure it belongs to current user)
    cursor.execute('''
        SELECT id, merchant_name, amount, date, category, description, receipt_filename
        FROM expenses 
        WHERE id = ? AND user_id = ?
    ''', (expense_id, current_user.id))
    expense = cursor.fetchone()
    
    if not expense:
        flash('Expense not found!', 'error')
        return redirect(url_for('view_expenses'))
    
    # Get individual items
    cursor.execute('''
        SELECT id, sku, description, price, raw_line
        FROM receipt_items 
        WHERE expense_id = ?
        ORDER BY id
    ''', (expense_id,))
    items = cursor.fetchall()
    
    conn.close()
    
    # Create form and populate with existing data
    form = ExpenseForm()
    form.merchant_name.data = expense[1]
    form.amount.data = expense[2]
    form.date.data = datetime.strptime(expense[3], '%Y-%m-%d').date()
    form.category.data = expense[4]
    form.description.data = expense[5]
    
    # Create OCR result-like structure for template compatibility
    ocr_result = {
        'merchant_name': expense[1],
        'amount': expense[2],
        'date': expense[3],
        'items': [
            {
                'sku': item[1],
                'description': item[2],
                'price': item[3],
                'raw_line': item[4]
            }
            for item in items
        ],
        'method': 'edit_existing',
        'raw_text': f'Editing existing expense #{expense_id}'
    }
    
    return render_template('edit_expense.html', 
                         form=form,
                         ocr_result=ocr_result,
                         filename=expense[6],
                         expense_id=expense_id,
                         editing=True)

@app.route('/update_expense/<int:expense_id>', methods=['POST'])
@login_required
def update_expense(expense_id):
    """Update an existing expense."""
    form = ExpenseForm()
    
    if form.validate_on_submit():
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Update the main expense (ensure it belongs to current user)
        cursor.execute('''
            UPDATE expenses 
            SET merchant_name = ?, amount = ?, date = ?, category = ?, description = ?
            WHERE id = ? AND user_id = ?
        ''', (
            form.merchant_name.data,
            form.amount.data,
            form.date.data,
            form.category.data,
            form.description.data,
            expense_id,
            current_user.id
        ))
        
        # Delete existing items and re-add (simpler than updating)
        cursor.execute('DELETE FROM receipt_items WHERE expense_id = ?', (expense_id,))
        
        # Save individual items if they exist
        items_count = request.form.get('items_count')
        if items_count:
            try:
                items_count = int(items_count)
                
                for i in range(items_count):
                    sku = request.form.get(f'item_{i}_sku') or None
                    description = request.form.get(f'item_{i}_description')
                    price = request.form.get(f'item_{i}_price')
                    raw_line = request.form.get(f'item_{i}_raw_line')
                    
                    if description and price:  # Only save if we have essential data
                        try:
                            price_float = float(price)
                            cursor.execute('''
                                INSERT INTO receipt_items (expense_id, sku, description, price, raw_line)
                                VALUES (?, ?, ?, ?, ?)
                            ''', (
                                expense_id,
                                sku if sku else None,
                                description,
                                price_float,
                                raw_line
                            ))
                        except ValueError:
                            continue
                            
            except (ValueError, TypeError) as e:
                flash(f'Warning: Individual items could not be updated. Error: {str(e)}', 'warning')
        
        conn.commit()
        conn.close()
        
        flash('Expense updated successfully!', 'success')
        return redirect(url_for('view_expenses'))
    
    flash('Error updating expense. Please check the form.', 'error')
    return redirect(url_for('view_expenses'))

@app.route('/bulk_upload')
@login_required
def bulk_upload_page():
    """Show the bulk upload page."""
    form = BulkReceiptForm()
    return render_template('bulk_upload.html', form=form)

@app.route('/bulk_upload', methods=['POST'])
@login_required
def bulk_upload_receipts():
    """Handle bulk receipt upload and processing."""
    form = BulkReceiptForm()
    
    if form.validate_on_submit():
        files = request.files.getlist('files')
        
        if not files or files[0].filename == '':
            flash('No files selected', 'error')
            return redirect(url_for('bulk_upload_page'))
        
        # Check total file size before processing
        total_size = sum(len(file.read()) for file in files)
        # Reset file pointers
        for file in files:
            file.seek(0)
        
        if total_size > app.config['MAX_CONTENT_LENGTH']:
            flash(f'Total file size ({total_size / 1024 / 1024:.1f}MB) exceeds limit (100MB). Please upload fewer files.', 'error')
            return redirect(url_for('bulk_upload_page'))
        
        results = []
        processed_count = 0
        error_count = 0
        
        for file in files:
            if file and file.filename != '':
                try:
                    filename = secure_filename(file.filename)
                    
                    # Add timestamp to filename to avoid conflicts
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_")
                    filename = timestamp + filename
                    
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    
                    # Calculate file hash for duplicate detection
                    file_hash = calculate_file_hash(filepath)
                    
                    # Process the receipt with OCR
                    ocr_result = receipt_ocr.process_receipt(filepath)
                    
                    # Check for duplicates
                    duplicates = None
                    if ocr_result.get('merchant_name') and ocr_result.get('amount'):
                        duplicates = check_for_duplicates(
                            ocr_result['merchant_name'], 
                            ocr_result['amount'], 
                            ocr_result['date'],
                            file_hash
                        )
                    
                    # Auto-save if we have good data and no exact duplicates
                    if (ocr_result.get('merchant_name') and ocr_result.get('amount') and 
                        (not duplicates or (not duplicates['exact_file'] and not duplicates['exact_match']))):
                        conn = sqlite3.connect('receipts.db')
                        cursor = conn.cursor()
                        
                        # Determine category based on merchant name
                        category = 'other'  # default
                        merchant = ocr_result['merchant_name'].lower()
                        if any(word in merchant for word in ['pawn', 'estate', 'garage', 'yard', 'auction', 'thrift', 'flea']):
                            category = 'inventory'
                        elif any(word in merchant for word in ['restaurant', 'cafe', 'food']):
                            category = 'meals'
                        elif any(word in merchant for word in ['gas', 'fuel', 'shell', 'exxon', 'uber', 'lyft']):
                            category = 'travel'
                        elif any(word in merchant for word in ['office', 'staples', 'depot', 'amazon']):
                            category = 'office_supplies'
                        
                        # Insert the main expense
                        cursor.execute('''
                            INSERT INTO expenses (user_id, merchant_name, amount, date, category, description, receipt_filename, file_hash)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            current_user.id,
                            ocr_result['merchant_name'],
                            ocr_result['amount'],
                            ocr_result['date'],
                            category,
                            f"Auto-processed from bulk upload (Method: {ocr_result.get('method', 'unknown')})",
                            filename,
                            file_hash
                        ))
                        
                        expense_id = cursor.lastrowid
                        
                        # Save individual items if they exist
                        items_saved = 0
                        if ocr_result.get('items'):
                            for item in ocr_result['items']:
                                try:
                                    cursor.execute('''
                                        INSERT INTO receipt_items (expense_id, sku, description, price, raw_line)
                                        VALUES (?, ?, ?, ?, ?)
                                    ''', (
                                        expense_id,
                                        item.get('sku'),
                                        item.get('description', ''),
                                        float(item.get('price', 0)),
                                        item.get('raw_line', '')
                                    ))
                                    items_saved += 1
                                except (ValueError, TypeError):
                                    continue
                        
                        conn.commit()
                        conn.close()
                        
                        results.append({
                            'filename': file.filename,
                            'status': 'success',
                            'merchant': ocr_result['merchant_name'],
                            'amount': ocr_result['amount'],
                            'date': ocr_result['date'],
                            'category': category,
                            'items_count': items_saved,
                            'method': ocr_result.get('method', 'unknown')
                        })
                        processed_count += 1
                    elif duplicates and (duplicates['exact_file'] or duplicates['exact_match']):
                        # Duplicate detected - skip processing
                        duplicate_type = 'exact file' if duplicates['exact_file'] else 'exact receipt'
                        results.append({
                            'filename': file.filename,
                            'status': 'duplicate',
                            'merchant': ocr_result.get('merchant_name', 'Unknown'),
                            'amount': ocr_result.get('amount', 0),
                            'date': ocr_result.get('date', 'Unknown'),
                            'error': f'Duplicate {duplicate_type} - skipped',
                            'method': ocr_result.get('method', 'unknown'),
                            'duplicates': duplicates
                        })
                        error_count += 1
                        # Clean up the file since we're not saving it
                        os.remove(filepath)
                    else:
                        # Partial data - needs manual review
                        results.append({
                            'filename': file.filename,
                            'status': 'partial',
                            'merchant': ocr_result.get('merchant_name', 'Unknown'),
                            'amount': ocr_result.get('amount', 0),
                            'date': ocr_result.get('date', 'Unknown'),
                            'error': 'Incomplete data - requires manual review',
                            'method': ocr_result.get('method', 'unknown')
                        })
                        error_count += 1
                        
                except Exception as e:
                    results.append({
                        'filename': file.filename,
                        'status': 'error',
                        'error': str(e)
                    })
                    error_count += 1
                    # Clean up the file if it was saved
                    if 'filepath' in locals() and os.path.exists(filepath):
                        os.remove(filepath)
        
        flash(f'Bulk upload complete! {processed_count} receipts processed successfully, {error_count} need attention.', 'success' if error_count == 0 else 'warning')
        return render_template('bulk_upload_results.html', results=results, processed_count=processed_count, error_count=error_count)
    
    flash('Invalid file upload', 'error')
    return redirect(url_for('bulk_upload_page'))

@app.route('/check_duplicates', methods=['POST'])
@login_required
def check_duplicates_ajax():
    """AJAX endpoint to check for duplicates while user is filling form."""
    try:
        data = request.get_json()
        merchant_name = data.get('merchant_name', '').strip()
        amount = data.get('amount')
        date = data.get('date')
        
        if not merchant_name or not amount or not date:
            return jsonify({'duplicates': None})
        
        duplicates = check_for_duplicates(merchant_name, float(amount), date)
        
        # Format the response for frontend
        response = {
            'has_duplicates': bool(duplicates['exact_file'] or duplicates['exact_match'] or duplicates['similar']),
            'duplicates': {
                'exact_file': len(duplicates['exact_file']),
                'exact_match': len(duplicates['exact_match']),
                'similar': len(duplicates['similar'])
            }
        }
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/get_expense_items/<int:expense_id>')
@login_required
def get_expense_items(expense_id):
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get expense details with tax information
        cursor.execute('''
            SELECT id, amount, tax_amount, tax_rate, subtotal,
                   receipt_filename, merchant_name, date, user_id
            FROM expenses
            WHERE id = ? AND user_id = ?
        ''', (expense_id, current_user.id))
        
        expense = cursor.fetchone()
        if not expense:
            return jsonify({'success': False, 'error': 'Expense not found'})
        
        # Get items
        cursor.execute('''
            SELECT id, sku, description, price
            FROM receipt_items
            WHERE expense_id = ?
            ORDER BY id
        ''', (expense_id,))
        
        items = [{
            'id': row[0],
            'sku': row[1],
            'description': row[2],
            'price': row[3]
        } for row in cursor.fetchall()]
        
        # Calculate items total directly from the items list
        items_total = sum(item['price'] for item in items)
        item_count = len(items)
        
        # Calculate tax amount if not set
        tax_amount = expense[2]  # tax_amount from expense
        if tax_amount is None:
            # Calculate tax as the difference between total and items total
            tax_amount = expense[1] - items_total  # amount - items_total
        
        # Convert expense tuple to dictionary with proper field names
        expense_dict = {
            'id': expense[0],
            'amount': expense[1],
            'tax_amount': tax_amount,
            'tax_rate': expense[3],
            'subtotal': items_total,  # Use the calculated items total
            'receipt_file': expense[5],
            'merchant': expense[6],
            'date': expense[7],
            'user_id': expense[8],
            'item_count': item_count,
            'items_total': items_total
        }
        
        return jsonify({
            'success': True,
            'expense': expense_dict,
            'items': items
        })
        
    except Exception as e:
        app.logger.error(f"Error getting expense items: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})
    finally:
        conn.close()

@app.route('/delete_uploaded_file', methods=['POST'])
@login_required
def delete_uploaded_file():
    """Delete an uploaded file that user wants to cancel."""
    try:
        data = request.get_json()
        filename = data.get('filename')
        
        if not filename:
            return jsonify({'success': False, 'error': 'No filename provided'}), 400
        
        # Security check - ensure filename is in uploads folder and secure
        secure_fname = secure_filename(filename)
        if secure_fname != filename:
            return jsonify({'success': False, 'error': 'Invalid filename'}), 400
        
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        
        # Check if file exists and delete it
        if os.path.exists(filepath):
            os.remove(filepath)
            return jsonify({'success': True, 'message': 'File deleted successfully'})
        else:
            return jsonify({'success': True, 'message': 'File already deleted or not found'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/reanalyze_receipt/<int:expense_id>', methods=['POST'])
@login_required
def reanalyze_receipt(expense_id):
    print(f"\n=== Starting re-analyze for expense {expense_id} ===")
    try:
        # Fetch expense details
        print(f"Fetching expense details for ID {expense_id}")
        conn = sqlite3.connect('receipts.db')
        conn.row_factory = sqlite3.Row  # This enables dictionary-like access
        cursor = conn.cursor()
        expense = conn.execute("""
            SELECT * FROM expenses 
            WHERE id = ? AND user_id = ?
        """, (expense_id, current_user.id)).fetchone()
        
        if not expense:
            return jsonify({'success': False, 'error': 'Expense not found'}), 404
            
        if not expense['receipt_filename']:
            return jsonify({'success': False, 'error': 'No receipt found for this expense'}), 400
            
        # Get receipt path
        receipt_path = os.path.join(app.config['UPLOAD_FOLDER'], expense['receipt_filename'])
        print(f"Looking for receipt at: {receipt_path}")
        
        if not os.path.exists(receipt_path):
            return jsonify({'success': False, 'error': 'Receipt file not found'}), 404
            
        # Initialize OCR
        print("Initializing OCR")
        ocr = ReceiptOCRGenAI(openai_api_key=openai_api_key)
        
        # Process receipt
        print("Processing receipt with OCR")
        result = ocr.process_receipt(receipt_path)
        
        if not result.get('success'):
            error_msg = result.get('error', 'Failed to process receipt')
            print(f"Error in re-analyze: {error_msg}")
            return jsonify({'success': False, 'error': error_msg}), 500
            
        # Get tax information
        tax_amount = result.get('tax_amount', 0.0)  # Default to 0.0 if not found
        tax_rate = result.get('tax_rate')
        subtotal = result.get('subtotal', 0.0)
        
        # For thrift stores, explicitly set tax to 0 if not found
        merchant_name = result.get('merchant_name', '').lower()
        if any(store in merchant_name for store in ['thrift', 'st vincent', 'vincent', 'goodwill', 'salvation army']):
            if tax_amount == 0.0 and tax_rate is None:
                print("Thrift store detected - setting tax to 0")
                tax_amount = 0.0
                tax_rate = 0.0
        
        print(f"Tax amount: {tax_amount}, Tax rate: {tax_rate}, Subtotal: {subtotal}")
            
        # Update expense with tax information
        conn.execute("""
            UPDATE expenses 
            SET subtotal = ?, tax_amount = ?, tax_rate = ?
            WHERE id = ?
        """, (
            subtotal,
            tax_amount,
            tax_rate,
            expense_id
        ))
            
        # Delete existing items
        conn.execute("DELETE FROM receipt_items WHERE expense_id = ?", (expense_id,))
        
        # Insert new items
        items = result.get('items', [])
        print(f"Found {len(items)} items in receipt")
        
        for item in items:
            conn.execute("""
                INSERT INTO receipt_items (expense_id, description, price, sku, raw_line)
                VALUES (?, ?, ?, ?, ?)
            """, (
                expense_id,
                item.get('description', ''),
                item.get('price', 0.0),
                item.get('sku', ''),
                json.dumps(item)  # Store the full item data
            ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Successfully re-analyzed receipt with {len(items)} items'
        })
        
    except Exception as e:
        print(f"Error in re-analyze: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/update_item/<int:item_id>', methods=['POST'])
@login_required
def update_item(item_id):
    """Update an item's description or price."""
    try:
        data = request.get_json()
        field = data.get('field')
        value = data.get('value')
        
        if field not in ['description', 'price']:
            return jsonify({'success': False, 'error': 'Invalid field'}), 400
            
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get expense_id for this item to verify ownership
        cursor.execute('''
            SELECT e.id, e.user_id 
            FROM receipt_items ri
            JOIN expenses e ON ri.expense_id = e.id
            WHERE ri.id = ?
        ''', (item_id,))
        result = cursor.fetchone()
        
        if not result:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
            
        expense_id, user_id = result
        
        if user_id != current_user.id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
            
        # Update the item
        cursor.execute(f'''
            UPDATE receipt_items 
            SET {field} = ?
            WHERE id = ?
        ''', (value, item_id))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'expense_id': expense_id
        })
        
    except Exception as e:
        print(f"Error updating item: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/delete_item/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    """Delete an item."""
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get expense_id for this item to verify ownership
        cursor.execute('''
            SELECT e.id, e.user_id 
            FROM receipt_items ri
            JOIN expenses e ON ri.expense_id = e.id
            WHERE ri.id = ?
        ''', (item_id,))
        result = cursor.fetchone()
        
        if not result:
            return jsonify({'success': False, 'error': 'Item not found'}), 404
            
        expense_id, user_id = result
        
        if user_id != current_user.id:
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
            
        # Delete the item
        cursor.execute('DELETE FROM receipt_items WHERE id = ?', (item_id,))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'expense_id': expense_id
        })
        
    except Exception as e:
        print(f"Error deleting item: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/save_item_changes/<int:expense_id>', methods=['POST'])
@login_required
def save_item_changes(expense_id):
    try:
        data = request.get_json()
        changes = data.get('changes', [])
        
        if not changes:
            return jsonify({'success': False, 'error': 'No changes provided'})
        
        # Get database connection
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        try:
            # Verify expense ownership
            cursor.execute('''
                SELECT user_id FROM expenses 
                WHERE id = ? AND user_id = ?
            ''', (expense_id, current_user.id))
            
            if not cursor.fetchone():
                return jsonify({'success': False, 'error': 'Unauthorized'})
            
            # Process each change
            for change in changes:
                item_id = change.get('item_id')
                
                # Verify item ownership through expense
                cursor.execute('''
                    SELECT ri.id FROM receipt_items ri
                    JOIN expenses e ON ri.expense_id = e.id
                    WHERE ri.id = ? AND e.id = ? AND e.user_id = ?
                ''', (item_id, expense_id, current_user.id))
                
                if not cursor.fetchone():
                    continue  # Skip unauthorized items
                
                if change.get('deleted'):
                    # Delete item
                    cursor.execute('DELETE FROM receipt_items WHERE id = ?', (item_id,))
                else:
                    # Update item
                    updates = []
                    params = []
                    
                    if 'description' in change:
                        updates.append('description = ?')
                        params.append(change['description'])
                    
                    if 'price' in change:
                        updates.append('price = ?')
                        params.append(change['price'])
                    
                    if updates:
                        params.append(item_id)
                        cursor.execute(f'''
                            UPDATE receipt_items 
                            SET {', '.join(updates)}
                            WHERE id = ?
                        ''', params)
            
            # Recalculate expense amount
            cursor.execute('''
                UPDATE expenses
                SET amount = (
                    SELECT COALESCE(SUM(price), 0)
                    FROM receipt_items
                    WHERE expense_id = ?
                )
                WHERE id = ?
            ''', (expense_id, expense_id))
            
            conn.commit()
            return jsonify({'success': True})
            
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
            
    except Exception as e:
        app.logger.error(f"Error saving item changes: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    init_db()
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=app.config['DEBUG']) 