# Authentication & Multi-User System

## Overview
The Receipt Reader application now includes a secure authentication system using Flask-Login and Flask-Bcrypt, with complete multi-user support where each user can only see and manage their own receipts.

## Features
- **User Registration**: New users can create accounts with username and password
- **Secure Login**: Passwords are hashed using bcrypt for security
- **Session Management**: Users stay logged in across browser sessions
- **Protected Routes**: All main functionality requires authentication
- **Multi-User Isolation**: Each user can only see their own receipts and expenses
- **Clean UI**: Modern login and registration forms

## Getting Started

### First Time Setup
1. Start the application: `python app.py`
2. Navigate to `http://localhost:5000`
3. You'll be redirected to the login page
4. Click "Register here" to create your first account
5. Choose a username (3-20 characters) and password (minimum 6 characters)
6. After registration, log in with your credentials

### Daily Usage
- The application will remember your login session
- Use the user dropdown in the top-right to logout
- All receipt processing, expense management, and export features require login

## Security Features
- Passwords are hashed with bcrypt (never stored in plain text)
- Session-based authentication with Flask-Login
- CSRF protection on all forms
- Login required for all sensitive operations

## Database Changes
A new `users` table has been added to store user accounts:
- `id`: Primary key
- `username`: Unique username
- `password_hash`: Bcrypt-hashed password
- `created_at`: Account creation timestamp

The `expenses` table has been updated to include user isolation:
- **NEW**: `user_id`: Foreign key linking each expense to a specific user
- All existing expenses are automatically migrated to the "keithanderson" user account

## Data Migration
When upgrading to the multi-user version:
1. Existing expenses are automatically assigned to user "keithanderson"
2. If "keithanderson" user doesn't exist, it's created with password "changeme123"
3. **Important**: Change the default password after first login for security!

## Files Cleaned Up
The following test files have been removed as they were no longer needed:
- `simple_test.py`
- `test_items_simple.py` 
- `debug_items.py`
- `debug_items_extraction.py`
- `test_receipt.py`
- `test_duplicates.py`
- `test_vision.py`
- `test_vision_receipt.py`
- `test_genai_receipt.py`
- `test_extraction.py`

## Duplicate Detection
The duplicate detection system is still fully functional and works by checking:

1. **Exact file hash match** - Same file uploaded before
2. **Exact receipt match** - Same merchant, amount, and date
3. **Similar receipts** - Same merchant, similar amount (±5%), within 3 days

This happens automatically during:
- Single receipt upload (`/upload`)
- Bulk receipt processing (`/bulk_upload`)
- Real-time checking via AJAX (`/check_duplicates`)

## New UI Features

### Collapsible Expense Rows
- **Click any expense row** to expand and see individual receipt items
- **Chevron icon** indicates expand/collapse state
- **AJAX loading** of items for better performance
- **Item summary** shows total items vs receipt total with difference calculation
- **Detailed item view** with SKU, description, price, and raw OCR line

### Enhanced Filtering & Sorting
- **Month filtering** in addition to year and category
- **Multiple sort options**: Date (newest/oldest), Amount (high/low), Merchant A-Z
- **Auto-submit forms** when filter/sort options change
- **Smart export filenames** that include filter criteria

### Improved User Experience
- **Item count badges** show number of items per receipt
- **Hover effects** and smooth transitions
- **Better mobile responsiveness**
- **Visual feedback** for loading states
- **Error handling** for failed item loads 