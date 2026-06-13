import os
import sqlite3
import hashlib
import uuid
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, jsonify, session, send_from_directory, g
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired, FileAllowed
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
from wtforms import StringField, FloatField, DateField, SelectField, TextAreaField, SubmitField, PasswordField, HiddenField
from wtforms.validators import DataRequired, NumberRange, Length, Optional
from werkzeug.utils import secure_filename
import pandas as pd
from receipt_ocr_genai import ReceiptOCRGenAI
import io
import json
import logging
import traceback
import difflib
import re

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

# Create directories if they don't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
BULK_REVIEW_FOLDER = os.path.join(app.root_path, 'bulk_reviews')
os.makedirs(BULK_REVIEW_FOLDER, exist_ok=True)

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
    location = StringField('Location (City, State)', validators=[Optional()])
    address = StringField('Full Address', validators=[Optional()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    date = DateField('Date', validators=[DataRequired()], default=datetime.now().date())
    category = SelectField('Category', choices=EXPENSE_CATEGORIES, validators=[DataRequired()])
    subtotal = FloatField('Subtotal', validators=[Optional(), NumberRange(min=0)])
    tax_amount = FloatField('Tax Amount', validators=[Optional(), NumberRange(min=0)])
    discount_amount = FloatField('Discount', validators=[Optional(), NumberRange(min=0)])
    tax_rate = FloatField('Tax Rate (%)', validators=[Optional(), NumberRange(min=0)])
    raw_json = HiddenField('Raw JSON')
    description = TextAreaField('Description')
    submit = SubmitField('Save Expense')

class BulkReceiptForm(FlaskForm):
    files = FileField('Receipt Images', validators=[
        FileRequired(),
        FileAllowed(['jpg', 'jpeg', 'png', 'pdf'], 'Only JPG, PNG, and PDF files are allowed!')
    ], render_kw={'multiple': True})
    submit = SubmitField('Upload and Process All')


class BulkReviewDecisionForm(FlaskForm):
    review_id = StringField('Review ID', validators=[DataRequired()], render_kw={'type': 'hidden'})
    submit = SubmitField('Save Selected')

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=20)])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')

class LocationForm(FlaskForm):
    name = StringField('Location Name', validators=[DataRequired(), Length(max=100)])
    address = StringField('Full Address', validators=[DataRequired()])
    category = SelectField('Default Category', choices=[('', '---')] + EXPENSE_CATEGORIES, validators=[Optional()])
    submit = SubmitField('Save Location')

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
            location TEXT,
            address TEXT,
            amount REAL NOT NULL,
            date DATE NOT NULL,
            category TEXT NOT NULL,
            description TEXT,
            receipt_filename TEXT,
            file_hash TEXT,
            subtotal REAL,
            tax_amount REAL,
            discount_amount REAL,
            tax_rate REAL,
            raw_json TEXT,
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
            quantity INTEGER DEFAULT 1,
            raw_line TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (expense_id) REFERENCES expenses (id) ON DELETE CASCADE
        )
    ''')

    # Locations table for address matching
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            city TEXT,
            state TEXT,
            zip_code TEXT,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    ''')
    
    # Add user_id column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE locations ADD COLUMN user_id INTEGER')
        conn.commit()
    except sqlite3.OperationalError:
        pass
    
    # Add additional columns if they don't exist (handle each separately so one failure doesn't stop the rest)
    for col_sql in [
        'ALTER TABLE expenses ADD COLUMN location TEXT',
        'ALTER TABLE expenses ADD COLUMN address TEXT',
        'ALTER TABLE expenses ADD COLUMN subtotal REAL',
        'ALTER TABLE expenses ADD COLUMN tax_amount REAL',
        'ALTER TABLE expenses ADD COLUMN discount_amount REAL',
        'ALTER TABLE expenses ADD COLUMN tax_rate REAL',
        'ALTER TABLE expenses ADD COLUMN raw_json TEXT'
    ]:
        try:
            cursor.execute(col_sql)
            conn.commit()
        except sqlite3.OperationalError:
            # Column already exists, ignore
            pass
    
    # Add quantity column to receipt_items if it doesn't exist
    try:
        cursor.execute('ALTER TABLE receipt_items ADD COLUMN quantity INTEGER DEFAULT 1')
        conn.commit()
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Create indexes for better query performance
    index_statements = [
        'CREATE INDEX IF NOT EXISTS idx_expenses_user_id ON expenses(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(date)',
        'CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category)',
        'CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses(user_id, date)',
        'CREATE INDEX IF NOT EXISTS idx_receipt_items_expense_id ON receipt_items(expense_id)',
    ]
    for idx_sql in index_statements:
        try:
            cursor.execute(idx_sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()

    # Update subtotals for all expenses based on actual item quantities
    try:
        cursor.execute('''
            UPDATE expenses 
            SET subtotal = (
                SELECT COALESCE(SUM(ri.price * ri.quantity), 0)
                FROM receipt_items ri 
                WHERE ri.expense_id = expenses.id
            )
            WHERE id IN (
                SELECT DISTINCT expense_id 
                FROM receipt_items
            )
        ''')
        conn.commit()
        print("Updated subtotals for all expenses based on item quantities")
    except sqlite3.Error as e:
        print(f"Error updating subtotals: {e}")
        pass
    
    # Migrate PDF filenames to image filenames if images exist
    try:
        cursor.execute('''
            SELECT id, receipt_filename FROM expenses 
            WHERE receipt_filename LIKE '%.pdf'
        ''')
        pdf_expenses = cursor.fetchall()
        
        migrated_count = 0
        for expense_id, pdf_filename in pdf_expenses:
            # Check if corresponding image file exists
            base_name = os.path.splitext(pdf_filename)[0]
            image_filename = f"{base_name}.jpg"
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], image_filename)
            
            if os.path.exists(image_path):
                # Update the database to use the image filename
                cursor.execute('''
                    UPDATE expenses 
                    SET receipt_filename = ? 
                    WHERE id = ?
                ''', (image_filename, expense_id))
                migrated_count += 1
                print(f"Migrated expense {expense_id}: {pdf_filename} -> {image_filename}")
        
        if migrated_count > 0:
            conn.commit()
            print(f"Successfully migrated {migrated_count} PDF filenames to image filenames")
    except sqlite3.Error as e:
        print(f"Error during PDF filename migration: {e}")
        pass
    
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


def infer_category_from_merchant(merchant_name, forced_category=None):
    """Map a merchant name to a best-guess category when possible."""
    if forced_category:
        return forced_category

    if not merchant_name:
        return 'other'

    merchant = merchant_name.lower()
    if any(word in merchant for word in ['pawn', 'estate', 'garage', 'yard', 'auction', 'thrift', 'flea']):
        return 'inventory'
    if any(word in merchant for word in ['restaurant', 'cafe', 'food', 'deli', 'bbq']):
        return 'meals'
    if any(word in merchant for word in ['gas', 'fuel', 'shell', 'exxon', 'uber', 'lyft', 'travel', 'hotel', 'motel']):
        return 'travel'
    if any(word in merchant for word in ['office', 'staples', 'depot', 'amazon', 'best buy', 'microcenter']):
        return 'office_supplies'
    if any(word in merchant for word in ['ads', 'marketing', 'facebook', 'google ads']):
        return 'advertising'
    return 'other'


def serialize_items(raw_items):
    """Normalize OCR item payloads into JSON-safe dictionaries."""
    serialized = []
    if not raw_items:
        return serialized

    for item in raw_items:
        try:
            price_val = item.get('price')
            price = float(price_val) if price_val not in (None, '') else None
        except (TypeError, ValueError):
            price = None

        try:
            qty_val = item.get('quantity')
            quantity = int(qty_val) if qty_val not in (None, '') else 1
        except (TypeError, ValueError):
            quantity = 1

        serialized.append({
            'sku': item.get('sku'),
            'description': item.get('description', '').strip() or 'Item',
            'price': price,
            'quantity': quantity,
            'raw_line': item.get('raw_line')
        })

    return serialized


def get_bulk_review_path(review_id):
    return os.path.join(BULK_REVIEW_FOLDER, f'{review_id}.json')


def save_bulk_review_payload(review_id, payload):
    with open(get_bulk_review_path(review_id), 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)


def load_bulk_review_payload(review_id):
    path = get_bulk_review_path(review_id)
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)


def cleanup_entry_files(entry):
    """Remove any temporary files associated with a bulk entry."""
    for key in ['uploaded_path', 'processed_path']:
        filepath = entry.get(key)
        if filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                continue

def check_for_duplicates(merchant_name, amount, date, file_hash=None, user_id=None):
    """Check for potential duplicate receipts for a specific user.

    Primary signal is an identical amount + date combination for the user.
    Merchant name is treated as advisory because vendors sometimes get renamed
    or reclassified. We still perform file-hash checks when available."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    duplicates = {
        'exact_file': [],
        'exact_match': [],
        'similar': []
    }
    
    normalized_merchant = merchant_name.strip() if merchant_name else ''
    normalized_date = str(date).strip() if date else ''
    try:
        normalized_amount = float(amount) if amount not in (None, '') else None
    except (TypeError, ValueError):
        normalized_amount = None

    # Attempt to normalize date string to YYYY-MM-DD
    parsed_date = None
    if normalized_date:
        try:
            parsed_date = datetime.strptime(normalized_date, '%Y-%m-%d').date()
        except ValueError:
            try:
                parsed_date = datetime.fromisoformat(normalized_date).date()
            except ValueError:
                parsed_date = None
    normalized_date = parsed_date.isoformat() if parsed_date else ''

    has_core_fields = bool(normalized_date and normalized_amount is not None)

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
    
    if has_core_fields:
        # Fetch all receipts with the same amount and date for this user
        cursor.execute('''
            SELECT id, merchant_name, amount, date, receipt_filename
            FROM expenses
            WHERE amount = ? AND date = ? AND user_id = ?
        ''', (normalized_amount, normalized_date, user_id))
        amount_date_matches = cursor.fetchall()

        for row in amount_date_matches:
            row_merchant = (row[1] or '').strip().lower()
            norm_merchant_lower = normalized_merchant.lower()
            if norm_merchant_lower and row_merchant:
                ratio = difflib.SequenceMatcher(None, norm_merchant_lower, row_merchant).ratio()
                if ratio >= 0.7:
                    duplicates['exact_match'].append(row)
                else:
                    duplicates['similar'].append(row)
            else:
                # If either merchant is blank, treat amount+date match as exact
                duplicates['exact_match'].append(row)

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

def clean_merchant_name(name):
    """Clean common AI artifacts from merchant names."""
    if not name:
        return name
    # Remove trailing symbols like #, *, -, etc.
    name = re.sub(r'[\s#\*\-]+$', '', name)
    # Correct common OCR misreadings
    name = name.replace('Walmart#', 'Walmart')
    return name.strip()

def correct_location_by_zip(address, current_location):
    """Fallback to correct Reno/Sparks based on Zip Code if AI misidentifies."""
    if not address:
        return current_location
    
    # Sparks Zips
    if any(zip_code in address for zip_code in ['89431', '89434', '89436', '89441']):
        return 'Sparks, NV'
    # Reno Zips
    if any(zip_code in address for zip_code in ['89501', '89502', '89503', '89506', '89509', '89511', '89512', '89519', '89521', '89523']):
        return 'Reno, NV'
        
    return current_location

def find_matching_location(address_str):
    """Try to match an extracted address against the locations database using fuzzy logic."""
    if not address_str:
        return None
        
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Try exact match first
    cursor.execute('SELECT id, name, category, address FROM locations WHERE address = ? AND user_id = ?', (address_str, current_user.id))
    match = cursor.fetchone()
    
    if not match:
        # Try fuzzy matching
        cursor.execute('SELECT id, name, category, address FROM locations WHERE user_id = ?', (current_user.id,))
        all_locations = cursor.fetchall()
        
        best_ratio = 0
        best_match = None
        
        # Normalize the input string
        input_norm = address_str.lower().strip()
        
        for loc in all_locations:
            loc_address = loc[3]
            if not loc_address:
                continue
                
            # Normalize target string
            target_norm = loc_address.lower().strip()
            
            # Use SequenceMatcher for fuzzy comparison
            ratio = difflib.SequenceMatcher(None, input_norm, target_norm).ratio()
            
            # If ratio is high enough (0.8 is usually a good threshold for addresses)
            if ratio > 0.8 and ratio > best_ratio:
                best_ratio = ratio
                best_match = loc
        
        if best_match:
            print(f"DEBUG: Fuzzy match found (Score: {best_ratio:.2f}): {best_match[1]}")
            match = best_match
            
    conn.close()
    return match

@app.route('/locations')
@login_required
def locations_page():
    """Manage known locations."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, address, category FROM locations WHERE user_id = ? ORDER BY name ASC', (current_user.id,))
    locations = cursor.fetchall()
    conn.close()
    
    form = LocationForm()
    return render_template('locations.html', locations=locations, form=form)

@app.route('/locations/add', methods=['POST'])
@login_required
def add_location():
    """Add a new location."""
    form = LocationForm()
    if form.validate_on_submit():
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO locations (user_id, name, address, category)
            VALUES (?, ?, ?, ?)
        ''', (current_user.id, form.name.data, form.address.data, form.category.data))
        conn.commit()
        conn.close()
        flash('Location added successfully!', 'success')
    else:
        flash('Failed to add location. Please check the form.', 'error')
    return redirect(url_for('locations_page'))

@app.route('/locations/delete/<int:location_id>', methods=['POST'])
@login_required
def delete_location(location_id):
    """Delete a location."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM locations WHERE id = ? AND user_id = ?', (location_id, current_user.id))
    conn.commit()
    conn.close()
    flash('Location deleted.', 'success')
    return redirect(url_for('locations_page'))

@app.route('/locations/quick_add', methods=['POST'])
@login_required
def quick_add_location():
    """Quickly add a location from the review screen."""
    name = request.form.get('merchant_name')
    address = request.form.get('address')
    category = request.form.get('category')
    
    if not (name and address):
        return jsonify({'success': False, 'error': 'Name and address are required'})
        
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO locations (user_id, name, address, category)
            VALUES (?, ?, ?, ?)
        ''', (current_user.id, name, address, category))
        conn.commit()
        cursor.execute('SELECT id FROM locations WHERE id = LAST_INSERT_ROWID()')
        new_id = cursor.fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'location_id': new_id})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/locations/reprocess_all', methods=['POST'])
@login_required
def reprocess_all_locations():
    """Apply current location database to all past expenses."""
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get all expenses for this user that have an address
        cursor.execute('''
            SELECT id, address, merchant_name, category 
            FROM expenses 
            WHERE user_id = ? AND (address IS NOT NULL AND address != '')
        ''', (current_user.id,))
        expenses = cursor.fetchall()
        
        updated_count = 0
        for exp_id, address, current_name, current_cat in expenses:
            # Clean name first
            address = address or ""
            
            # Reuse find_matching_location
            match = find_matching_location(address)
            
            if match:
                match_name = clean_merchant_name(match[1])
                match_cat = match[2]
                
                # Update if name index or cleaning makes it better, or if cat updated
                should_update = match_name != current_name
                if match_cat and (current_cat == 'other' or not current_cat or current_cat == 'None'):
                    should_update = True
                
                if should_update:
                    cursor.execute('''
                        UPDATE expenses 
                        SET merchant_name = ?, category = COALESCE(?, category)
                        WHERE id = ?
                    ''', (match_name, match_cat, exp_id))
                    updated_count += 1
        
        conn.commit()
        conn.close()
        flash(f'Successfully checked {len(expenses)} receipts. Updated {updated_count} records to match your business list.', 'success')
    except Exception as e:
        flash(f'Error during reprocessing: {str(e)}', 'error')
        
    return redirect(url_for('locations_page'))

@app.route('/')
@login_required
def index():
    """Home page showing recent expenses and upload form."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get recent expenses for current user
    cursor.execute('''
        SELECT id, merchant_name, amount, date, category, description, location, address
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

    # Get location breakdown for current month
    current_month = datetime.now().strftime('%m')
    current_year = datetime.now().strftime('%Y')
    cursor.execute('''
        SELECT 
            CASE 
                WHEN location LIKE '%Reno%' THEN 'Reno, NV'
                WHEN location LIKE '%Sparks%' THEN 'Sparks, NV'
                ELSE COALESCE(location, 'Other')
            END as loc_group,
            SUM(amount), COUNT(*)
        FROM expenses 
        WHERE user_id = ? AND strftime('%m', date) = ? AND strftime('%Y', date) = ?
        GROUP BY loc_group
        ORDER BY SUM(amount) DESC
    ''', (current_user.id, current_month, current_year))
    location_breakdown_month = cursor.fetchall()
    
    # Get all-time location breakdown
    cursor.execute('''
        SELECT 
            CASE 
                WHEN location LIKE '%Reno%' THEN 'Reno, NV'
                WHEN location LIKE '%Sparks%' THEN 'Sparks, NV'
                ELSE COALESCE(location, 'Other')
            END as loc_group,
            SUM(amount), COUNT(*)
        FROM expenses 
        WHERE user_id = ?
        GROUP BY loc_group
        ORDER BY SUM(amount) DESC
    ''', (current_user.id,))
    location_breakdown_all = cursor.fetchall()
    
    conn.close()
    
    form = ReceiptForm()
    
    return render_template('index.html', 
                         form=form,
                         recent_expenses=recent_expenses,
                         total_count=total_count,
                         total_amount=total_amount,
                         category_breakdown=category_breakdown,
                         location_breakdown=location_breakdown_month,
                         location_breakdown_all=location_breakdown_all)

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
        
        # DEBUG: Print OCR result keys and processed_filename
        print(f"DEBUG: OCR result keys: {list(ocr_result.keys())}")
        print(f"DEBUG: processed_filename in result: {ocr_result.get('processed_filename')}")
        print(f"DEBUG: converted_filename in result: {ocr_result.get('converted_filename')}")

        # Handle PDF conversion (filename change)
        processed_filename = ocr_result.get('processed_filename')
        if not processed_filename:
            processed_filename = ocr_result.get('converted_filename')
            
        if processed_filename:
            print(f"DEBUG: Updating filename from {filename} to {processed_filename}")
            filename = processed_filename

        if not ocr_result.get('success', False):
            return jsonify({
                'success': False, 
                'error': ocr_result.get('error', 'OCR processing failed')
            })
        
        # Clean and correct data
        ocr_result['merchant_name'] = clean_merchant_name(ocr_result.get('merchant_name', ''))
        ocr_result['location'] = correct_location_by_zip(ocr_result.get('address', ''), ocr_result.get('location', ''))

        # Try to match address to a known location for auto-renaming
        location_match = find_matching_location(ocr_result.get('address'))
        if location_match:
            print(f"DEBUG: Found matching location: {location_match[1]}")
            ocr_result['merchant_name'] = location_match[1]
            if location_match[2]:
                ocr_result['category'] = location_match[2]
            ocr_result['location_match'] = {
                'id': location_match[0],
                'name': location_match[1],
                'category': location_match[2]
            }

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
    
    # Get user's saved locations for quick selection
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name, address, category FROM locations WHERE user_id = ? ORDER BY name ASC', (current_user.id,))
    locations = [{'name': r[0], 'address': r[1], 'category': r[2]} for r in cursor.fetchall()]
    conn.close()

    # Populate raw_json if available
    form = ExpenseForm()
    if ocr_result.get('raw_text'):
        form.raw_json.data = ocr_result['raw_text']
    
    return render_template('edit_expense.html', 
                         form=form,
                         ocr_result=ocr_result,
                         locations=locations,
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
        
        # Check if this was a PDF that was converted to an image
        processed_filename = request.form.get('processed_filename')
        if processed_filename:
            # Use the converted image filename instead of the original PDF filename
            actual_filename = processed_filename
            print(f"Using converted image filename: {actual_filename} (original: {filename})")
        else:
            actual_filename = filename
        
        subtotal_value = form.subtotal.data if form.subtotal.data is not None else None
        tax_amount_value = form.tax_amount.data if form.tax_amount.data is not None else None
        discount_amount = form.discount_amount.data if form.discount_amount.data is not None else 0
        tax_rate_value = form.tax_rate.data if form.tax_rate.data is not None else None
        
        # Insert the main expense including advanced totals
        cursor.execute('''
            INSERT INTO expenses (user_id, merchant_name, location, address, amount, date, category, description, receipt_filename, file_hash, subtotal, tax_amount, discount_amount, tax_rate, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            current_user.id,
            form.merchant_name.data,
            form.location.data,
            form.address.data,
            form.amount.data,
            form.date.data,
            form.category.data,
            form.description.data,
            actual_filename,
            file_hash,
            subtotal_value,
            tax_amount_value,
            discount_amount,
            tax_rate_value,
            form.raw_json.data
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
                    quantity = request.form.get(f'item_{i}_quantity') or 1
                    raw_line = request.form.get(f'item_{i}_raw_line')
                    
                    if description and price:  # Only save if we have essential data
                        try:
                            price_float = float(price)
                            quantity_int = int(quantity)
                            cursor.execute('''
                                INSERT INTO receipt_items (expense_id, sku, description, price, quantity, raw_line)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                expense_id,
                                sku if sku else None,
                                description,
                                price_float,
                                quantity_int,
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
    search_query = request.args.get('q', '')
    sort_by = request.args.get('sort', 'date_desc')  # Default sort
    
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Build query with filters (always filter by current user)
    query = '''
        SELECT e.id, e.merchant_name, e.amount, e.date, e.category, e.description, e.receipt_filename,
               e.tax_amount, e.discount_amount, e.tax_rate, e.subtotal, e.location, e.address,
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
    
    if search_query:
        conditions.append('e.merchant_name LIKE ?')
        params.append(f'%{search_query}%')
    
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
                         current_sort=sort_by,
                         current_search=search_query)

@app.route('/export/<format>')
@login_required
def export_expenses(format):
    """Export expenses to Excel or CSV for tax purposes."""
    year = request.args.get('year')
    category = request.args.get('category')
    month = request.args.get('month')
    search_query = request.args.get('q')
    
    conn = sqlite3.connect('receipts.db')
    
    # Build query (always filter by current user)
    query = 'SELECT merchant_name, amount, date, category, description FROM expenses WHERE user_id = ?'
    params = [current_user.id]
    conditions = []
    
    # Only add filters if they have a value (not None and not empty string)
    if year:
        conditions.append('strftime("%Y", date) = ?')
        params.append(str(year))
    
    if category:
        conditions.append('category = ?')
        params.append(category)
    
    if month:
        conditions.append('strftime("%m", date) = ?')
        params.append(f"{int(month):02d}")

    if search_query:
        conditions.append('merchant_name LIKE ?')
        params.append(f'%{search_query}%')
    
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
    next_page = request.args.get('next')
    issue_filter = request.args.get('issue')

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

    if next_page == 'issues':
        if issue_filter:
            return redirect(url_for('issue_dashboard', issue=issue_filter))
        return redirect(url_for('issue_dashboard'))
    if next_page == 'dashboard':
        return redirect(url_for('index'))
    return redirect(url_for('view_expenses'))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded receipt images."""
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
    except Exception as e:
        print(f"Error serving file {filename}: {e}")
        return "File not found", 404

@app.route('/expense/<int:expense_id>/items')
@login_required
def view_expense_items(expense_id):
    """View individual items for a specific expense."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    
    # Get expense details (ensure it belongs to current user)
    cursor.execute('''
        SELECT id, amount, tax_amount, discount_amount, tax_rate, subtotal,
               receipt_filename, merchant_name, date, user_id
        FROM expenses
        WHERE id = ? AND user_id = ?
    ''', (expense_id, current_user.id))
    expense = cursor.fetchone()
    
    if not expense:
        flash('Expense not found!', 'error')
        return redirect(url_for('view_expenses'))
    
    # Get individual items
    cursor.execute('''
        SELECT id, sku, description, price, quantity
        FROM receipt_items
        WHERE expense_id = ?
        ORDER BY id
    ''', (expense_id,))
    items = [{
        'id': row[0],
        'sku': row[1],
        'description': row[2],
        'price': row[3],
        'quantity': row[4] if row[4] is not None else 1
    } for row in cursor.fetchall()]
    
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

def parse_date_string(date_str):
    """Robustly parse date strings from DB or AI."""
    if not date_str:
        return datetime.now().date()
    if isinstance(date_str, datetime):
        return date_str.date()
    if hasattr(date_str, 'date'): # already a date object
        return date_str
        
    # Try various formats
    for fmt in ('%Y-%m-%d', '%m/%d/%Y', '%Y/%m/%d', '%d-%m-%Y', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(str(date_str).split(' ')[0], fmt).date()
        except (ValueError, TypeError):
            continue
    return datetime.now().date()

@app.route('/edit_expense/<int:expense_id>')
@login_required
def edit_expense(expense_id):
    """Edit an existing expense."""
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get expense details (ensure it belongs to current user)
        cursor.execute('''
             SELECT id, merchant_name, amount, date, category, description, receipt_filename,
                 subtotal, tax_amount, discount_amount, tax_rate, location, address, raw_json
            FROM expenses 
            WHERE id = ? AND user_id = ?
        ''', (expense_id, current_user.id))
        expense = cursor.fetchone()
        
        if not expense:
            flash('Expense not found!', 'error')
            return redirect(url_for('view_expenses'))
        
        # Get individual items
        cursor.execute('''
            SELECT id, sku, description, price, quantity, raw_line
            FROM receipt_items 
            WHERE expense_id = ?
            ORDER BY id
        ''', (expense_id,))
        items = cursor.fetchall()

        # Get user's saved locations
        cursor.execute('SELECT name, address, category FROM locations WHERE user_id = ? ORDER BY name ASC', (current_user.id,))
        locations = [{'name': r[0], 'address': r[1], 'category': r[2]} for r in cursor.fetchall()]
        conn.close()
        
        # Check if we have reprocessed data in session
        reprocessed_data = session.pop(f'reprocessed_ocr_{expense_id}', None)
        
        # Create form and populate with existing data
        form = ExpenseForm()
        
        if reprocessed_data:
            # Use reprocessed data
            form.merchant_name.data = reprocessed_data.get('merchant_name')
            form.location.data = reprocessed_data.get('location')
            form.address.data = reprocessed_data.get('address')
            form.amount.data = reprocessed_data.get('amount')
            form.date.data = parse_date_string(reprocessed_data.get('date'))
                
            form.category.data = reprocessed_data.get('category', 'other')
            form.subtotal.data = reprocessed_data.get('subtotal')
            form.tax_amount.data = reprocessed_data.get('tax_amount')
            form.discount_amount.data = reprocessed_data.get('discount_amount')
            form.tax_rate.data = reprocessed_data.get('tax_rate')
            form.raw_json.data = reprocessed_data.get('raw_text')
            form.description.data = expense[5] # keep original description
            
            ocr_result = reprocessed_data
            ocr_result['method'] = 'reprocessed'
        else:
            # Use existing database data
            form.merchant_name.data = expense[1]
            form.location.data = expense[11]
            form.address.data = expense[12]
            form.amount.data = expense[2]
            form.date.data = parse_date_string(expense[3])
            form.category.data = expense[4]
            form.description.data = expense[5]
            form.subtotal.data = expense[7]
            form.tax_amount.data = expense[8]
            form.discount_amount.data = expense[9]
            form.tax_rate.data = expense[10]
            form.raw_json.data = expense[13]
            
            # Try to match address to a known location for existing record
            location_match_data = None
            match = find_matching_location(expense[12])
            if match:
                location_match_data = {
                    'id': match[0],
                    'name': match[1],
                    'category': match[2]
                }
            
            # Create OCR result-like structure for template compatibility
            ocr_result = {
                'merchant_name': expense[1],
                'location': expense[11],
                'address': expense[12],
                'amount': expense[2],
                'date': expense[3],
                'subtotal': expense[7],
                'tax_amount': expense[8],
                'discount_amount': expense[9],
                'tax_rate': expense[10],
                'raw_text': expense[13],
                'location_match': location_match_data,
                'items': [
                    {
                        'sku': item[1],
                        'description': item[2],
                        'price': item[3],
                        'quantity': item[4] if item[4] is not None else 1,
                        'raw_line': item[5]
                    }
                    for item in items
                ],
                'method': 'edit_existing',
            }
        
        return render_template('edit_expense.html', 
                             form=form,
                             ocr_result=ocr_result,
                             locations=locations,
                             filename=expense[6],
                             expense_id=expense_id,
                             editing=True)
    except Exception as e:
        print(f"CRITICAL ERROR in edit_expense: {str(e)}")
        traceback.print_exc()
        flash(f"Error loading expense: {str(e)}", 'error')
        return redirect(url_for('view_expenses'))

@app.route('/reprocess_expense/<int:expense_id>', methods=['POST'])
@login_required
def reprocess_expense(expense_id):
    """Re-run OCR for an existing expense."""
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT receipt_filename FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
    expense = cursor.fetchone()
    conn.close()

    if not expense:
        flash('Expense not found!', 'error')
        return redirect(url_for('view_expenses'))

    filename = expense[0]
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    if not os.path.exists(filepath):
        flash('Receipt image file not found on server.', 'error')
        return redirect(url_for('edit_expense', expense_id=expense_id))

    try:
        flash('Reprocessing receipt with OpenAI...', 'info')
        ocr_result = receipt_ocr.process_receipt(filepath)
        
        if ocr_result.get('success'):
            # Clean and correct data
            ocr_result['merchant_name'] = clean_merchant_name(ocr_result.get('merchant_name', ''))
            ocr_result['location'] = correct_location_by_zip(ocr_result.get('address', ''), ocr_result.get('location', ''))

            # Calculate file hash for consistency
            ocr_result['file_hash'] = calculate_file_hash(filepath)
            
            # Try to match address to a known location
            location_match = find_matching_location(ocr_result.get('address'))
            if location_match:
                print(f"DEBUG: Found matching location for reprocess: {location_match[1]}")
                ocr_result['merchant_name'] = location_match[1]
                if location_match[2]:
                    ocr_result['category'] = location_match[2]
                ocr_result['location_match'] = {
                    'id': location_match[0],
                    'name': location_match[1],
                    'category': location_match[2]
                }
            
            # Convert date object to string for session serialization
            if ocr_result.get('date') and hasattr(ocr_result['date'], 'isoformat'):
                ocr_result['date'] = ocr_result['date'].isoformat()
            
            # Store in session for edit_expense to pick up
            session[f'reprocessed_ocr_{expense_id}'] = ocr_result
            flash('Receipt reprocessed successfully! Review and save changes below.', 'success')
        else:
            flash(f"Reprocessing failed: {ocr_result.get('error', 'Unknown error')}", 'error')
            
    except Exception as e:
        flash(f"Error during reprocessing: {str(e)}", 'error')
        
    return redirect(url_for('edit_expense', expense_id=expense_id))

@app.route('/update_expense/<int:expense_id>', methods=['POST'])
@login_required
def update_expense(expense_id):
    """Update an existing expense."""
    form = ExpenseForm()
    
    if form.validate_on_submit():
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        subtotal_value = form.subtotal.data if form.subtotal.data is not None else None
        tax_amount_value = form.tax_amount.data if form.tax_amount.data is not None else None
        discount_amount = form.discount_amount.data if form.discount_amount.data is not None else 0
        tax_rate_value = form.tax_rate.data if form.tax_rate.data is not None else None

        # Update the main expense (ensure it belongs to current user)
        cursor.execute('''
            UPDATE expenses 
            SET merchant_name = ?, location = ?, address = ?, amount = ?, date = ?, category = ?, description = ?,
                subtotal = ?, tax_amount = ?, discount_amount = ?, tax_rate = ?
            WHERE id = ? AND user_id = ?
        ''', (
            form.merchant_name.data,
            form.location.data,
            form.address.data,
            form.amount.data,
            form.date.data,
            form.category.data,
            form.description.data,
            subtotal_value,
            tax_amount_value,
            discount_amount,
            tax_rate_value,
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
                    quantity = request.form.get(f'item_{i}_quantity') or 1
                    raw_line = request.form.get(f'item_{i}_raw_line')
                    
                    if description and price:  # Only save if we have essential data
                        try:
                            price_float = float(price)
                            quantity_int = int(quantity)
                            cursor.execute('''
                                INSERT INTO receipt_items (expense_id, sku, description, price, quantity, raw_line)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                expense_id,
                                sku if sku else None,
                                description,
                                price_float,
                                quantity_int,
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

@app.route('/update_expense_field/<int:expense_id>', methods=['POST'])
@login_required
def update_expense_field(expense_id):
    """Update a single field of an expense via AJAX."""
    try:
        data = request.get_json()
        field = data.get('field')
        value = data.get('value')
        
        if not field or value is None:
            return jsonify({'success': False, 'error': 'Missing field or value'})
        
        # Validate field name - allow editing of additional numeric fields
        allowed_fields = ['merchant_name', 'category', 'amount', 'tax_amount', 'discount_amount', 'subtotal', 'tax_rate']
        if field not in allowed_fields:
            return jsonify({'success': False, 'error': 'Invalid field name'})
        
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Check if expense belongs to current user
        cursor.execute('SELECT id FROM expenses WHERE id = ? AND user_id = ?', (expense_id, current_user.id))
        expense = cursor.fetchone()
        
        if not expense:
            return jsonify({'success': False, 'error': 'Expense not found'})
        
        # Update the field
        cursor.execute(f'''
            UPDATE expenses 
            SET {field} = ?
            WHERE id = ? AND user_id = ?
        ''', (value, expense_id, current_user.id))
        
        conn.commit()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/bulk_upload')
@login_required
def bulk_upload_page():
    """Show the bulk upload page, surfacing any pending partial review."""
    form = BulkReceiptForm()
    pending_review_id = session.get('pending_bulk_review_id')
    pending_review = None
    if pending_review_id:
        payload = load_bulk_review_payload(pending_review_id)
        if payload and payload.get('user_id') == current_user.id and payload.get('entries'):
            pending_review = {
                'review_id': pending_review_id,
                'count': len(payload['entries']),
                'created_at': payload.get('created_at', '')
            }
    return render_template('bulk_upload.html', form=form, pending_review=pending_review)


@app.route('/bulk_upload/resume/<review_id>')
@login_required
def bulk_upload_resume(review_id):
    """Resume a partial bulk review that was interrupted."""
    payload = load_bulk_review_payload(review_id)
    if not payload or payload.get('user_id') != current_user.id:
        flash('That review session was not found or has expired.', 'error')
        return redirect(url_for('bulk_upload_page'))

    entries = payload.get('entries', [])
    summary_counts = {'ready': 0, 'duplicate': 0, 'needs_data': 0, 'error': 0}
    for entry in entries:
        status = entry.get('status', 'error')
        if status in summary_counts:
            summary_counts[status] += 1
    summary_counts['total'] = len(entries)
    summary_counts['auto_select'] = sum(1 for e in entries if e.get('status') == 'ready')

    decision_form = BulkReviewDecisionForm()
    decision_form.review_id.data = review_id

    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, address, category FROM locations WHERE user_id = ? ORDER BY name ASC', (current_user.id,))
    locations = [{'id': r[0], 'name': r[1], 'address': r[2], 'category': r[3]} for r in cursor.fetchall()]
    conn.close()

    flash(f'Resumed interrupted upload — {len(entries)} receipt(s) recovered.', 'info')
    return render_template(
        'bulk_upload_review.html',
        review_id=review_id,
        entries=entries,
        summary=summary_counts,
        decision_form=decision_form,
        locations=locations,
        categories=EXPENSE_CATEGORIES
    )

@app.route('/bulk_upload', methods=['POST'])
@login_required
def bulk_upload_receipts():
    """Handle bulk receipt upload and processing (pre-review stage)."""
    form = BulkReceiptForm()

    if not form.validate_on_submit():
        flash('Invalid file upload', 'error')
        return redirect(url_for('bulk_upload_page'))

    files = request.files.getlist('files')
    if not files or files[0].filename == '':
        flash('No files selected', 'error')
        return redirect(url_for('bulk_upload_page'))

    # Check total file size before processing
    total_size = 0
    for upload in files:
        total_size += len(upload.read())
    for upload in files:
        upload.seek(0)

    if total_size > app.config['MAX_CONTENT_LENGTH']:
        flash(f'Total file size ({total_size / 1024 / 1024:.1f}MB) exceeds limit (100MB). Please upload fewer files.', 'error')
        return redirect(url_for('bulk_upload_page'))

    default_merchant = request.form.get('default_merchant', '').strip()
    default_category = request.form.get('default_category', '').strip() or None

    review_entries = []
    summary_counts = {'ready': 0, 'duplicate': 0, 'needs_data': 0, 'error': 0}

    # Generate review_id before the loop so partial progress is preserved on failure
    review_id = str(uuid.uuid4())
    session['pending_bulk_review_id'] = review_id

    for file_obj in files:
        if not file_obj or file_obj.filename == '':
            continue

        entry = {
            'id': str(uuid.uuid4()),
            'original_filename': file_obj.filename,
            'issues': [],
            'status': 'ready',
            'can_save': True,
            'duplicate_flag': False,
            'uploaded_path': None,
            'processed_path': None
        }

        try:
            safe_name = secure_filename(file_obj.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            stored_upload_name = timestamp + safe_name
            upload_path = os.path.join(app.config['UPLOAD_FOLDER'], stored_upload_name)
            file_obj.save(upload_path)
            entry['uploaded_filename'] = stored_upload_name
            entry['uploaded_path'] = upload_path

            file_hash = calculate_file_hash(upload_path)
            ocr_result = receipt_ocr.process_receipt(upload_path)

            # DEBUG: Print OCR result keys and processed_filename
            print(f"DEBUG: OCR result keys: {list(ocr_result.keys())}")
            print(f"DEBUG: processed_filename in result: {ocr_result.get('processed_filename')}")
            print(f"DEBUG: converted_filename in result: {ocr_result.get('converted_filename')}")

            # Try to get the processed filename (image) if available
            processed_filename = ocr_result.get('processed_filename')
            if not processed_filename:
                processed_filename = ocr_result.get('converted_filename')
            
            processed_path = None
            receipt_filename = stored_upload_name
            if processed_filename:
                processed_path = processed_filename if os.path.isabs(processed_filename) else os.path.join(app.config['UPLOAD_FOLDER'], processed_filename)
                receipt_filename = os.path.basename(processed_filename)
                entry['processed_filename'] = os.path.basename(processed_filename)
                entry['processed_path'] = processed_path

            amount_val = ocr_result.get('amount')
            try:
                amount = float(amount_val) if amount_val not in (None, '') else None
                amount = round(amount, 2) if amount is not None else None
            except (TypeError, ValueError):
                amount = None

            date_val = ocr_result.get('date')
            if isinstance(date_val, datetime):
                date_val = date_val.date().isoformat()
            elif hasattr(date_val, 'isoformat') and not isinstance(date_val, str):
                try:
                    date_val = date_val.isoformat()
                except Exception:
                    date_val = str(date_val)
            
            # Clean and correct data
            detected_merchant = clean_merchant_name((ocr_result.get('merchant_name') or '').strip())
            ocr_result['location'] = correct_location_by_zip(ocr_result.get('address', ''), ocr_result.get('location', ''))

            # Try to match address to a known location
            location_match = find_matching_location(ocr_result.get('address'))
            location_match_info = None
            category = None
            
            if location_match:
                print(f"DEBUG: Found matching location for bulk: {location_match[1]}")
                location_match_info = {
                    'id': location_match[0],
                    'name': location_match[1],
                    'category': location_match[2]
                }
                final_merchant = location_match[1]
                if location_match[2]:
                    category = location_match[2]
            else:
                final_merchant = detected_merchant or default_merchant

            if not category:
                category = infer_category_from_merchant(final_merchant, default_category)

            duplicates = {'exact_file': [], 'exact_match': [], 'similar': []}
            duplicate_flag = False
            if amount is not None and date_val:
                duplicates = check_for_duplicates(final_merchant, amount, date_val, file_hash)
                duplicate_flag = bool(duplicates['exact_file'] or duplicates['exact_match'])

            entry.update({
                'merchant_name': final_merchant,
                'detected_merchant': detected_merchant,
                'location': ocr_result.get('location', ''),
                'address': ocr_result.get('address', ''),
                'location_match': location_match_info,
                'amount': amount,
                'date': date_val,
                'category': category,
                'description': f"Auto-processed from bulk upload (Method: {ocr_result.get('method', 'unknown')})",
                'method': ocr_result.get('method', 'unknown'),
                'file_hash': file_hash,
                'receipt_filename': receipt_filename,
                'items': serialize_items(ocr_result.get('items')),
                'subtotal': ocr_result.get('subtotal'),
                'tax_amount': ocr_result.get('tax_amount'),
                'tax_rate': ocr_result.get('tax_rate'),
                'discount_amount': ocr_result.get('discount_amount'),
                'raw_json': ocr_result.get('raw_text'),
                'duplicate_flag': duplicate_flag,
                'duplicate_examples': [{
                    'id': dup[0],
                    'merchant_name': dup[1],
                    'amount': dup[2],
                    'date': dup[3]
                } for dup in duplicates.get('exact_match', [])[:5]],
                'duplicate_file_match': bool(duplicates.get('exact_file'))
            })

            entry['can_save'] = bool(final_merchant and amount is not None and date_val)

            if not entry['can_save']:
                entry['status'] = 'needs_data'
                if not final_merchant:
                    entry['issues'].append('Merchant missing')
                if amount is None:
                    entry['issues'].append('Amount missing or unreadable')
                if not date_val:
                    entry['issues'].append('Date missing')
            elif duplicate_flag:
                entry['status'] = 'duplicate'
                entry['issues'].append('Matches an existing receipt with the same amount and date.')
            else:
                entry['status'] = 'ready'

        except Exception as e:
            entry['status'] = 'error'
            entry['issues'].append(f'Processing failed: {str(e)}')
            entry['can_save'] = False
            cleanup_entry_files(entry)
            entry['uploaded_path'] = None
            entry['processed_path'] = None

        review_entries.append(entry)
        if entry['status'] in summary_counts:
            summary_counts[entry['status']] += 1

        # Save after each file so partial progress survives a timeout or crash
        save_bulk_review_payload(review_id, {
            'id': review_id,
            'user_id': current_user.id,
            'created_at': datetime.utcnow().isoformat(),
            'entries': review_entries
        })

    if not review_entries:
        flash('No valid files were provided.', 'error')
        return redirect(url_for('bulk_upload_page'))

    summary_counts['total'] = len(review_entries)
    summary_counts['auto_select'] = sum(1 for entry in review_entries if entry['status'] == 'ready')

    decision_form = BulkReviewDecisionForm()
    decision_form.review_id.data = review_id

    # Fetch locations for the quick store selector in bulk review
    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()
    cursor.execute('SELECT id, name, address, category FROM locations WHERE user_id = ? ORDER BY name ASC', (current_user.id,))
    locations = [{'id': r[0], 'name': r[1], 'address': r[2], 'category': r[3]} for r in cursor.fetchall()]
    conn.close()

    flash('Receipts analyzed. Review duplicates before saving.', 'info')
    return render_template(
        'bulk_upload_review.html',
        review_id=review_id,
        entries=review_entries,
        summary=summary_counts,
        decision_form=decision_form,
        locations=locations,
        categories=EXPENSE_CATEGORIES
    )

@app.route('/bulk_upload/confirm', methods=['POST'])
@login_required
def bulk_upload_confirm():
    """Persist the receipts the user approved from the review screen."""
    form = BulkReviewDecisionForm()
    if not form.validate_on_submit():
        flash('Unable to verify that review submission. Please try again.', 'error')
        return redirect(url_for('bulk_upload_page'))

    review_id = form.review_id.data
    payload = load_bulk_review_payload(review_id)
    if not payload or payload.get('user_id') != current_user.id:
        flash('That review session expired. Please upload the receipts again.', 'error')
        return redirect(url_for('bulk_upload_page'))

    selected_ids = set(request.form.getlist('selected_entries'))
    removed_ids = set(request.form.getlist('removed_entries'))
    cancel_batch = request.form.get('cancel_batch') == '1'

    results = []
    processed_count = 0
    error_count = 0
    skipped_count = 0

    for entry in payload.get('entries', []):
        entry_id = entry.get('id')
        should_save = entry_id in selected_ids and not cancel_batch
        
        # Check if basic info is presence in entry or form override
        form_merchant = request.form.get(f'merchant_{entry_id}')
        form_amount = request.form.get(f'amount_{entry_id}')
        form_date = request.form.get(f'date_{entry_id}')
        
        has_basic_data = (entry.get('merchant_name') and entry.get('amount') is not None and entry.get('date')) or \
                         (form_merchant and form_amount and form_date)

        if should_save and has_basic_data:
            try:
                # Use form overrides if present
                merchant_name = form_merchant or entry.get('merchant_name')
                location = request.form.get(f'location_{entry_id}') or entry.get('location')
                address = request.form.get(f'address_{entry_id}') or entry.get('address')
                category = request.form.get(f'category_{entry_id}') or entry.get('category') or 'other'
                
                try:
                    amount = float(form_amount) if form_amount else entry.get('amount')
                except (TypeError, ValueError):
                    amount = entry.get('amount')
                
                date = form_date or entry.get('date')

                conn = sqlite3.connect('receipts.db')
                try:
                    cursor = conn.cursor()

                    cursor.execute('''
                        INSERT INTO expenses (
                            user_id, merchant_name, location, address, amount, date, category,
                            description, receipt_filename, file_hash,
                            subtotal, tax_amount, discount_amount, tax_rate, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        current_user.id,
                        merchant_name,
                        location,
                        address,
                        amount,
                        date,
                        category,
                        entry.get('description'),
                        entry.get('receipt_filename'),
                        entry.get('file_hash'),
                        entry.get('subtotal'),
                        entry.get('tax_amount'),
                        entry.get('discount_amount'),
                        entry.get('tax_rate'),
                        entry.get('raw_json')
                    ))

                    expense_id = cursor.lastrowid
                    for item in entry.get('items', []):
                        if item.get('description') and item.get('price') is not None:
                            cursor.execute('''
                                INSERT INTO receipt_items (expense_id, sku, description, price, quantity, raw_line)
                                VALUES (?, ?, ?, ?, ?, ?)
                            ''', (
                                expense_id,
                                item.get('sku'),
                                item.get('description'),
                                item.get('price'),
                                item.get('quantity', 1),
                                item.get('raw_line')
                            ))

                    conn.commit()
                finally:
                    conn.close()

                results.append({
                    'filename': entry.get('original_filename'),
                    'status': 'success',
                    'merchant': merchant_name or 'Unknown',
                    'amount': amount,
                    'date': date,
                    'category': category,
                    'method': entry.get('method'),
                    'note': 'Saved (duplicate previously flagged)' if entry.get('duplicate_flag') else 'Saved'
                })
                processed_count += 1
            except Exception as exc:
                results.append({
                    'filename': entry.get('original_filename'),
                    'status': 'error',
                    'merchant': (form_merchant or entry.get('merchant_name')) or 'Unknown',
                    'amount': form_amount or entry.get('amount'),
                    'date': form_date or entry.get('date'),
                    'error': str(exc)
                })
                error_count += 1
                cleanup_entry_files(entry)
        else:
            reason = 'Batch cancelled' if cancel_batch else 'Removed by user' if entry_id in removed_ids else 'Not selected'
            results.append({
                'filename': entry.get('original_filename'),
                'status': 'skipped',
                'merchant': entry.get('merchant_name') or entry.get('detected_merchant') or 'Unknown',
                'amount': entry.get('amount'),
                'date': entry.get('date'),
                'error': reason
            })
            skipped_count += 1
            cleanup_entry_files(entry)

    # Delete the review cache file once processed and clear the session pointer
    review_path = get_bulk_review_path(review_id)
    if os.path.exists(review_path):
        try:
            os.remove(review_path)
        except OSError:
            pass
    session.pop('pending_bulk_review_id', None)

    flash(
        f"Saved {processed_count} receipt(s). {skipped_count} skipped and {error_count} errored.",
        'success' if error_count == 0 else 'warning'
    )

    return render_template(
        'bulk_upload_results.html',
        results=results,
        processed_count=processed_count,
        skipped_count=skipped_count,
        error_count=error_count
    )


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


@app.route('/issues')
@login_required
def issue_dashboard():
    """Central hub for locating and fixing problematic expenses."""
    issue_filter = request.args.get('issue', 'all')

    conn = sqlite3.connect('receipts.db')
    cursor = conn.cursor()

    # Duplicate groups (amount + date collisions)
    cursor.execute('''
        SELECT amount, date, COUNT(*)
        FROM expenses
        WHERE user_id = ?
        GROUP BY amount, date
        HAVING COUNT(*) > 1
        ORDER BY date DESC
    ''', (current_user.id,))
    duplicate_groups = []
    for amount, date_value, count in cursor.fetchall():
        cursor.execute('''
            SELECT id, merchant_name, amount, date, category
            FROM expenses
            WHERE user_id = ? AND amount = ? AND date = ?
            ORDER BY id DESC
        ''', (current_user.id, amount, date_value))
        entries = cursor.fetchall()
        duplicate_groups.append({
            'amount': amount,
            'date': date_value,
            'count': count,
            'expenses': [{
                'id': row[0],
                'merchant_name': row[1],
                'amount': row[2],
                'date': row[3],
                'category': row[4]
            } for row in entries]
        })

    # Invalid amounts (null or <= 0)
    cursor.execute('''
        SELECT id, merchant_name, amount, date, category
        FROM expenses
        WHERE user_id = ? AND (amount IS NULL OR amount <= 0)
        ORDER BY date DESC
    ''', (current_user.id,))
    invalid_amounts = [{
        'id': row[0],
        'merchant_name': row[1],
        'amount': row[2],
        'date': row[3],
        'category': row[4]
    } for row in cursor.fetchall()]

    # Missing category assignments
    cursor.execute('''
        SELECT id, merchant_name, amount, date
        FROM expenses
        WHERE user_id = ? AND (category IS NULL OR category = '')
        ORDER BY date DESC
    ''', (current_user.id,))
    missing_categories = [{
        'id': row[0],
        'merchant_name': row[1],
        'amount': row[2],
        'date': row[3]
    } for row in cursor.fetchall()]

    conn.close()

    issue_counts = {
        'duplicates': len(duplicate_groups),
        'invalid_amounts': len(invalid_amounts),
        'missing_categories': len(missing_categories)
    }
    issue_counts['total'] = sum(issue_counts.values())

    return render_template(
        'issues.html',
        active_issue=issue_filter,
        duplicates=duplicate_groups,
        invalid_amounts=invalid_amounts,
        missing_categories=missing_categories,
        issue_counts=issue_counts
    )

@app.route('/get_expense_items/<int:expense_id>')
@login_required
def get_expense_items(expense_id):
    try:
        conn = sqlite3.connect('receipts.db')
        cursor = conn.cursor()
        
        # Get expense details with tax information
        cursor.execute('''
            SELECT id, amount, tax_amount, discount_amount, tax_rate, subtotal,
                   receipt_filename, merchant_name, date, user_id
            FROM expenses
            WHERE id = ? AND user_id = ?
        ''', (expense_id, current_user.id))
        
        expense = cursor.fetchone()
        if not expense:
            return jsonify({'success': False, 'error': 'Expense not found'})
        
        # Get items
        cursor.execute('''
            SELECT id, sku, description, price, quantity
            FROM receipt_items
            WHERE expense_id = ?
            ORDER BY id
        ''', (expense_id,))
        
        items = [{
            'id': row[0],
            'sku': row[1],
            'description': row[2],
            'price': row[3],
            'quantity': row[4] if row[4] is not None else 1
        } for row in cursor.fetchall()]
        
        # Calculate items total directly from the items list (price * quantity)
        items_total = sum(item['price'] * item['quantity'] for item in items)
        item_count = len(items)
        
        print(f"Getting items for expense {expense_id}: {item_count} items, total=${items_total:.2f}")
        for item in items:
            print(f"  Item {item['id']}: {item['description']} - ${item['price']:.2f} x {item['quantity']} = ${item['price'] * item['quantity']:.2f}")
        
        # Calculate tax amount if not set
        tax_amount = expense[2]  # tax_amount from expense
        if tax_amount is None:
            # Calculate tax as the difference between total and items total
            tax_amount = expense[1] - items_total  # amount - items_total
        
        # Derive discount if not provided or mismatch
        if expense[3] is None or expense[3] == 0:
            discount_amount = round(expense[1] - items_total, 2)
            if discount_amount < 0:
                discount_amount = abs(discount_amount)
        else:
            discount_amount = expense[3]
        
        # Calculate tax rate if not set
        tax_rate = expense[4]
        if tax_rate is None:
            # Calculate tax rate as the difference between tax_amount and amount
            tax_rate = tax_amount / expense[1] if expense[1] != 0 else 0
        
        # Calculate subtotal
        calculated_subtotal = items_total + tax_amount - discount_amount
        
        # Convert expense tuple to dictionary with proper field names
        expense_dict = {
            'id': expense[0],
            'amount': expense[1],
            'tax_amount': tax_amount,
            'discount_amount': discount_amount,
            'tax_rate': tax_rate,
            'subtotal': calculated_subtotal,
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
            # If the file doesn't exist, it might be an old PDF that was converted to an image
            # Try looking for a PDF version of this filename
            base_name = os.path.splitext(expense['receipt_filename'])[0]
            pdf_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{base_name}.pdf")
            
            if os.path.exists(pdf_path):
                receipt_path = pdf_path
                print(f"Found PDF version at: {receipt_path}")
            else:
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
            
        # Get tax & discount information
        tax_amount = result.get('tax_amount', 0.0)  # Default to 0.0 if not found
        tax_rate = result.get('tax_rate')
        discount_amount = result.get('discount_amount', 0.0)
        
        # For thrift stores, explicitly set tax to 0 if not found
        merchant_name = result.get('merchant_name', '').lower()
        if any(store in merchant_name for store in ['thrift', 'st vincent', 'vincent', 'goodwill', 'salvation army']):
            if tax_amount == 0.0 and tax_rate is None:
                print("Thrift store detected - setting tax to 0")
                tax_amount = 0.0
                tax_rate = 0.0
        
        print(f"Tax amount: {tax_amount}, Tax rate: {tax_rate}")
            
        # Delete existing items first
        conn.execute("DELETE FROM receipt_items WHERE expense_id = ?", (expense_id,))
        
        # Insert new items
        items = result.get('items', [])
        print(f"Found {len(items)} items in receipt")
        
        calculated_subtotal = 0.0
        for item in items:
            price = float(item.get('price', 0.0))
            quantity = int(item.get('quantity', 1))
            item_total = price * quantity
            calculated_subtotal += item_total
            
            conn.execute("""
                INSERT INTO receipt_items (expense_id, description, price, quantity, sku, raw_line)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                expense_id,
                item.get('description', ''),
                price,
                quantity,
                item.get('sku', ''),
                json.dumps(item)  # Store the full item data
            ))
        
        print(f"Calculated subtotal from items: {calculated_subtotal}")
        
        # Derive discount if not provided or mismatch
        if discount_amount is None or discount_amount == 0:
            discount_amount = round(calculated_subtotal + tax_amount - result.get('amount', 0), 2)
            if discount_amount < 0:
                discount_amount = abs(discount_amount)
        
        # Update expense with tax, discount, and calculated subtotal
        conn.execute("""
            UPDATE expenses 
            SET subtotal = ?, tax_amount = ?, discount_amount = ?, tax_rate = ?
            WHERE id = ?
        """, (
            calculated_subtotal,
            tax_amount,
            discount_amount,
            tax_rate,
            expense_id
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
        
        if field not in ['description', 'price', 'quantity']:
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
                print(f"Processing change for item {item_id}: {change}")
                
                # Verify item ownership through expense
                cursor.execute('''
                    SELECT ri.id FROM receipt_items ri
                    JOIN expenses e ON ri.expense_id = e.id
                    WHERE ri.id = ? AND e.id = ? AND e.user_id = ?
                ''', (item_id, expense_id, current_user.id))
                
                if not cursor.fetchone():
                    print(f"Skipping unauthorized item {item_id}")
                    continue  # Skip unauthorized items
                
                if change.get('deleted'):
                    # Delete item
                    print(f"Deleting item {item_id}")
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
                    
                    if 'quantity' in change:
                        updates.append('quantity = ?')
                        params.append(change['quantity'])
                    
                    if updates:
                        params.append(item_id)
                        query = f'''
                            UPDATE receipt_items 
                            SET {', '.join(updates)}
                            WHERE id = ?
                        '''
                        print(f"Updating item {item_id} with query: {query}, params: {params}")
                        cursor.execute(query, params)
            
            # Recalculate subtotal based on item quantities
            cursor.execute('''
                UPDATE expenses
                SET subtotal = (
                    SELECT COALESCE(SUM(price * COALESCE(quantity, 1)), 0)
                    FROM receipt_items
                    WHERE expense_id = ?
                )
                WHERE id = ?
            ''', (expense_id, expense_id))
            
            # Log the update for debugging
            cursor.execute('''
                SELECT subtotal, 
                       (SELECT COUNT(*) FROM receipt_items WHERE expense_id = ?) as item_count,
                       (SELECT SUM(price * COALESCE(quantity, 1)) FROM receipt_items WHERE expense_id = ?) as calculated_total
                FROM expenses WHERE id = ?
            ''', (expense_id, expense_id, expense_id))
            debug_info = cursor.fetchone()
            print(f"Updated expense {expense_id}: subtotal={debug_info[0]}, items={debug_info[1]}, calculated={debug_info[2]}")
            
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