from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime, date, timedelta
import sqlite3
import json
import os
import csv
import io
import hashlib
import secrets
import re

app = FastAPI(title="Smart Clearance System API")

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DB_PATH = os.path.join(os.path.dirname(__file__), "clearance.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str, salt: str = None) -> tuple:
    """Hash password with salt using SHA-256"""
    if salt is None:
        salt = secrets.token_hex(16)
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return hashed, salt

def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verify password against stored hash"""
    check_hash, _ = hash_password(password, salt)
    return check_hash == hashed

def validate_username(username: str) -> tuple:
    """
    Validate username follows standard rules:
    - 3-20 characters
    - Starts with a letter
    - Only alphanumeric and underscores
    - No consecutive underscores
    Returns (is_valid, error_message)
    """
    if not username:
        return False, "Username is required"
    if len(username) < 3:
        return False, "Username must be at least 3 characters"
    if len(username) > 20:
        return False, "Username must be at most 20 characters"
    if not username[0].isalpha():
        return False, "Username must start with a letter"
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', username):
        return False, "Username can only contain letters, numbers, and underscores"
    if '__' in username:
        return False, "Username cannot have consecutive underscores"
    return True, None

def validate_password(password: str) -> tuple:
    """
    Validate password strength:
    - At least 8 characters
    - Contains uppercase and lowercase
    - Contains at least one number
    - Contains at least one special character
    Returns (is_valid, error_message)
    """
    if not password:
        return False, "Password is required"
    if len(password) < 8:
        return False, "Password must be at least 8 characters"
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter"
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter"
    if not re.search(r'\d', password):
        return False, "Password must contain at least one number"
    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        return False, "Password must contain at least one special character (!@#$%^&*(),.?\":{}|<>)"
    return True, None

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table with authentication
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT,
            picture TEXT,
            role TEXT NOT NULL CHECK(role IN ('manager', 'middleman')),
            google_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Stock table - each manager has their own storage
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock (
            stock_id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id TEXT NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            expiry_date DATE NOT NULL,
            price REAL,
            status TEXT DEFAULT 'available' CHECK(status IN ('available', 'expired', 'ordered', 'reserved')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manager_id) REFERENCES users(user_id)
        )
    ''')
    
    # Orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            middleman_id TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'cancelled')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (stock_id) REFERENCES stock(stock_id),
            FOREIGN KEY (middleman_id) REFERENCES users(user_id)
        )
    ''')
    
    # Conversation context table for AI agent
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS conversations (
            conv_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            context TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Pydantic models
class UserRegister(BaseModel):
    username: str
    password: str
    name: str
    role: str
    email: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class GoogleLogin(BaseModel):
    google_id: str
    email: str
    name: str
    picture: Optional[str] = None
    role: str

class StockCreate(BaseModel):
    product_name: str
    quantity: int
    expiry_date: str  # YYYY-MM-DD format
    price: Optional[float] = None

class StockUpdate(BaseModel):
    quantity: Optional[int] = None
    price: Optional[float] = None
    status: Optional[str] = None

class VoiceInput(BaseModel):
    user_id: str
    text: str
    context: Optional[dict] = None
    role: Optional[str] = None  # 'manager' or 'middleman'

class OrderCreate(BaseModel):
    stock_id: int
    middleman_id: str
    quantity: int

# Health check
@app.get("/")
def health_check():
    return {"status": "Smart Clearance System running", "version": "1.0"}

# User endpoints
@app.post("/api/register")
def register(user: UserRegister):
    """Register a new user with username and password"""
    # Validate username
    is_valid, error = validate_username(user.username)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # Validate password
    is_valid, error = validate_password(user.password)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)
    
    # Validate role
    if user.role not in ['manager', 'middleman']:
        raise HTTPException(status_code=400, detail="Role must be 'manager' or 'middleman'")
    
    # Validate name
    if not user.name or len(user.name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters")
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if username already exists
    cursor.execute("SELECT username FROM users WHERE username = ?", (user.username.lower(),))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Username already taken")
    
    # Hash password
    password_hash, password_salt = hash_password(user.password)
    
    # Create unique user_id
    user_id = f"{user.username.lower()}_{user.role}"
    
    # Insert user
    cursor.execute(
        """INSERT INTO users (user_id, username, password_hash, password_salt, name, email, role) 
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, user.username.lower(), password_hash, password_salt, user.name.strip(), user.email, user.role)
    )
    conn.commit()
    
    cursor.execute("SELECT user_id, username, name, email, role, created_at FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    return {"success": True, "message": "Registration successful", "user": dict(result)}

@app.post("/api/login")
def login(user: UserLogin):
    """Login with username and password"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Find user by username
    cursor.execute(
        "SELECT user_id, username, password_hash, password_salt, name, email, role, created_at FROM users WHERE username = ?", 
        (user.username.lower(),)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Verify password
    if not verify_password(user.password, result['password_hash'], result['password_salt']):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    # Return user data (excluding password fields)
    user_data = {
        'user_id': result['user_id'],
        'username': result['username'],
        'name': result['name'],
        'email': result['email'],
        'role': result['role'],
        'created_at': result['created_at']
    }
    
    return {"success": True, "user": user_data}

@app.get("/api/check-username/{username}")
def check_username(username: str):
    """Check if username is available and valid"""
    # Validate format
    is_valid, error = validate_username(username)
    if not is_valid:
        return {"available": False, "valid": False, "error": error}
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username.lower(),))
    exists = cursor.fetchone() is not None
    conn.close()
    
    if exists:
        return {"available": False, "valid": True, "error": "Username already taken"}
    
    return {"available": True, "valid": True}

@app.post("/api/google-login")
def google_login(user: GoogleLogin):
    """Handle Google OAuth login - creates unique user based on Google ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Create unique user_id from google_id and role
    user_id = f"google_{user.google_id}_{user.role}"
    username = f"google_{user.google_id[:8]}"
    
    # Check if user exists
    cursor.execute("SELECT user_id, username, name, email, role, created_at FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    if result:
        conn.close()
        return {"success": True, "user": dict(result)}
    
    # Create new user with a placeholder password (Google users don't need password)
    password_hash, password_salt = hash_password(secrets.token_hex(32))
    
    cursor.execute(
        """INSERT INTO users (user_id, username, password_hash, password_salt, name, email, picture, role, google_id) 
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, username, password_hash, password_salt, user.name, user.email, user.picture, user.role, user.google_id)
    )
    conn.commit()
    cursor.execute("SELECT user_id, username, name, email, role, created_at FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    return {"success": True, "user": dict(result)}

@app.get("/api/users")
def get_users(role: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    if role:
        cursor.execute("SELECT * FROM users WHERE role = ?", (role,))
    else:
        cursor.execute("SELECT * FROM users")
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {"users": results}

# CSV Upload endpoint for bulk stock import
@app.post("/api/stock/upload-csv")
async def upload_csv(file: UploadFile = File(...), manager_id: str = None):
    """
    Upload CSV file to bulk import stock data.
    CSV format: product_name,quantity,expiry_date,price
    """
    if not manager_id:
        raise HTTPException(status_code=400, detail="manager_id is required")
    
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    content = await file.read()
    decoded = content.decode('utf-8')
    
    conn = get_db()
    cursor = conn.cursor()
    
    reader = csv.DictReader(io.StringIO(decoded))
    imported = 0
    errors = []
    
    for row_num, row in enumerate(reader, start=2):
        try:
            product_name = row.get('product_name', '').strip()
            quantity = int(row.get('quantity', 0))
            expiry_date = row.get('expiry_date', '').strip()
            price = float(row.get('price', 0)) if row.get('price') else None
            
            if not product_name or not expiry_date or quantity <= 0:
                errors.append(f"Row {row_num}: Missing required fields")
                continue
            
            # Validate date
            try:
                exp_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
                status = "expired" if exp_date < date.today() else "available"
            except ValueError:
                errors.append(f"Row {row_num}: Invalid date format (use YYYY-MM-DD)")
                continue
            
            cursor.execute(
                "INSERT INTO stock (manager_id, product_name, quantity, expiry_date, price, status) VALUES (?, ?, ?, ?, ?, ?)",
                (manager_id, product_name, quantity, expiry_date, price, status)
            )
            imported += 1
            
        except Exception as e:
            errors.append(f"Row {row_num}: {str(e)}")
    
    conn.commit()
    conn.close()
    
    return {
        "success": True,
        "imported": imported,
        "errors": errors,
        "message": f"Successfully imported {imported} items" + (f" with {len(errors)} errors" if errors else "")
    }

# Stock endpoints
@app.post("/api/stock")
def create_stock(stock: StockCreate, manager_id: str):
    conn = get_db()
    cursor = conn.cursor()
    
    # Check if expiry date is valid
    try:
        exp_date = datetime.strptime(stock.expiry_date, "%Y-%m-%d").date()
        status = "expired" if exp_date < date.today() else "available"
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    
    cursor.execute(
        "INSERT INTO stock (manager_id, product_name, quantity, expiry_date, price, status) VALUES (?, ?, ?, ?, ?, ?)",
        (manager_id, stock.product_name, stock.quantity, stock.expiry_date, stock.price, status)
    )
    stock_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"success": True, "stock_id": stock_id, "message": "Stock added successfully"}

@app.get("/api/stock")
def get_stock(
    manager_id: Optional[str] = None,
    status: Optional[str] = None,
    product: Optional[str] = None,
    expiry_before: Optional[str] = None
):
    conn = get_db()
    cursor = conn.cursor()
    
    # Update expired stock status
    cursor.execute(
        "UPDATE stock SET status = 'expired' WHERE expiry_date < date('now') AND status = 'available'"
    )
    conn.commit()
    
    query = "SELECT s.*, u.name as manager_name FROM stock s JOIN users u ON s.manager_id = u.user_id WHERE 1=1"
    params = []
    
    if manager_id:
        query += " AND s.manager_id = ?"
        params.append(manager_id)
    if status:
        query += " AND s.status = ?"
        params.append(status)
    if product:
        query += " AND s.product_name LIKE ?"
        params.append(f"%{product}%")
    if expiry_before:
        query += " AND s.expiry_date <= ?"
        params.append(expiry_before)
    
    query += " ORDER BY s.expiry_date ASC"
    
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Add days until expiry
    today = date.today()
    for item in results:
        exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
        item['days_until_expiry'] = (exp_date - today).days
    
    return {"stock": results}

@app.get("/api/stock/{stock_id}")
def get_stock_by_id(stock_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT s.*, u.name as manager_name FROM stock s JOIN users u ON s.manager_id = u.user_id WHERE s.stock_id = ?",
        (stock_id,)
    )
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        raise HTTPException(status_code=404, detail="Stock not found")
    
    return {"stock": dict(result)}

@app.put("/api/stock/{stock_id}")
def update_stock(stock_id: int, stock: StockUpdate):
    conn = get_db()
    cursor = conn.cursor()
    
    updates = []
    params = []
    
    if stock.quantity is not None:
        updates.append("quantity = ?")
        params.append(stock.quantity)
    if stock.price is not None:
        updates.append("price = ?")
        params.append(stock.price)
    if stock.status is not None:
        updates.append("status = ?")
        params.append(stock.status)
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    params.append(stock_id)
    cursor.execute(f"UPDATE stock SET {', '.join(updates)} WHERE stock_id = ?", params)
    conn.commit()
    conn.close()
    
    return {"success": True, "message": "Stock updated"}

@app.delete("/api/stock/{stock_id}")
def delete_stock(stock_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock WHERE stock_id = ?", (stock_id,))
    conn.commit()
    conn.close()
    return {"success": True, "message": "Stock deleted"}

# Order endpoints
@app.post("/api/orders")
def create_order(order: OrderCreate):
    conn = get_db()
    cursor = conn.cursor()
    
    # Check stock availability
    cursor.execute("SELECT * FROM stock WHERE stock_id = ? AND status = 'available'", (order.stock_id,))
    stock = cursor.fetchone()
    
    if not stock:
        conn.close()
        raise HTTPException(status_code=400, detail="Stock not available")
    
    if stock['quantity'] < order.quantity:
        conn.close()
        raise HTTPException(status_code=400, detail="Insufficient quantity")
    
    # Create order
    cursor.execute(
        "INSERT INTO orders (stock_id, middleman_id, quantity, status) VALUES (?, ?, ?, 'confirmed')",
        (order.stock_id, order.middleman_id, order.quantity)
    )
    order_id = cursor.lastrowid
    
    # Update stock
    new_quantity = stock['quantity'] - order.quantity
    if new_quantity == 0:
        cursor.execute("UPDATE stock SET quantity = 0, status = 'ordered' WHERE stock_id = ?", (order.stock_id,))
    else:
        cursor.execute("UPDATE stock SET quantity = ? WHERE stock_id = ?", (new_quantity, order.stock_id))
    
    conn.commit()
    conn.close()
    
    return {"success": True, "order_id": order_id, "message": "Order confirmed"}

@app.get("/api/orders")
def get_orders(middleman_id: Optional[str] = None, manager_id: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    
    query = '''
        SELECT o.*, s.product_name, s.expiry_date, s.price, s.manager_id, 
               u1.name as middleman_name, u2.name as manager_name
        FROM orders o
        JOIN stock s ON o.stock_id = s.stock_id
        JOIN users u1 ON o.middleman_id = u1.user_id
        JOIN users u2 ON s.manager_id = u2.user_id
        WHERE 1=1
    '''
    params = []
    
    if middleman_id:
        query += " AND o.middleman_id = ?"
        params.append(middleman_id)
    if manager_id:
        query += " AND s.manager_id = ?"
        params.append(manager_id)
    
    query += " ORDER BY o.created_at DESC"
    
    cursor.execute(query, params)
    results = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return {"orders": results}

# Voice AI Agent endpoint
@app.post("/api/voice-agent")
def voice_agent(input: VoiceInput):
    """
    Smart AI agent that processes voice input and returns intelligent responses.
    Supports both manager and middleman roles.
    """
    conn = get_db()
    cursor = conn.cursor()
    
    user_text = input.text.lower().strip()
    context = input.context or {}
    role = input.role or 'middleman'
    
    if role == 'manager':
        # Get manager's own stock
        cursor.execute('''
            SELECT s.*, u.name as manager_name 
            FROM stock s 
            JOIN users u ON s.manager_id = u.user_id 
            WHERE s.manager_id = ?
            ORDER BY s.expiry_date ASC
        ''', (input.user_id,))
        stock_data = [dict(row) for row in cursor.fetchall()]
        response = process_manager_voice_query(user_text, stock_data, context, input.user_id, cursor, conn)
    else:
        # Get all available stock for middleman
        cursor.execute('''
            SELECT s.*, u.name as manager_name 
            FROM stock s 
            JOIN users u ON s.manager_id = u.user_id 
            WHERE s.status = 'available'
            ORDER BY s.expiry_date ASC
        ''')
        available_stock = [dict(row) for row in cursor.fetchall()]
        response = process_middleman_voice_query(user_text, available_stock, context, input.user_id, cursor, conn)
    
    conn.close()
    return response

def process_manager_voice_query(text: str, stock: list, context: dict, user_id: str, cursor, conn) -> dict:
    """Process voice query for stock managers - includes adding stock via voice"""
    today = date.today()
    
    # Handle add stock flow
    if context.get('stage') == 'adding_stock':
        return handle_add_stock_flow(text, context, user_id, cursor, conn)
    
    # Greeting
    if text.strip() in ['hello', 'hi', 'hey', 'start', 'hi there', 'hello there']:
        return {
            "response": "Hello! I'm your stock management assistant. I can help you add stock, check inventory, find expiring items, and get summaries. Say 'add stock' to add new items or ask about your inventory.",
            "action": "greeting",
            "context": {"stage": "initial"}
        }
    
    # Add stock intent
    if any(phrase in text for phrase in ['add stock', 'add item', 'add product', 'new stock', 'new item', 'create stock', 'add new']):
        return {
            "response": "Sure! Let's add new stock. What's the product name?",
            "action": "add_stock_start",
            "context": {"stage": "adding_stock", "step": "product_name"}
        }
    
    # Quick add with product name mentioned
    product_keywords = ['apple', 'milk', 'bread', 'vegetable', 'fruit', 'dairy', 'meat', 'fish', 'rice', 'wheat', 'oil', 'sugar', 'orange', 'banana', 'tomato', 'potato', 'onion', 'chicken', 'egg', 'cheese', 'butter', 'yogurt']
    mentioned = [kw for kw in product_keywords if kw in text]
    
    if mentioned and any(word in text for word in ['add', 'create', 'new']):
        product_name = mentioned[0].title()
        # Try to extract quantity
        quantity = None
        words = text.split()
        for i, word in enumerate(words):
            if word.isdigit():
                quantity = int(word)
                break
        
        if quantity:
            return {
                "response": f"Adding {quantity} units of {product_name}. What's the expiry date? Say it like 'January 15' or 'in 7 days'.",
                "action": "add_stock_quantity",
                "context": {"stage": "adding_stock", "step": "expiry_date", "product_name": product_name, "quantity": quantity}
            }
        else:
            return {
                "response": f"Adding {product_name}. How many units?",
                "action": "add_stock_product",
                "context": {"stage": "adding_stock", "step": "quantity", "product_name": product_name}
            }
    
    # Stock summary
    if any(word in text for word in ['summary', 'overview', 'status', 'how much', 'total']):
        available = [s for s in stock if s['status'] == 'available']
        expired = [s for s in stock if s['status'] == 'expired']
        ordered = [s for s in stock if s['status'] == 'ordered']
        
        urgent = []
        for item in available:
            exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
            if (exp_date - today).days <= 3:
                urgent.append(item)
        
        response_text = f"Here's your inventory summary: {len(available)} items available, {len(ordered)} ordered, {len(expired)} expired. "
        if urgent:
            response_text += f"Warning: {len(urgent)} items expiring within 3 days!"
        else:
            response_text += "No urgent items expiring soon."
        
        return {
            "response": response_text,
            "action": "summary",
            "data": {"available": len(available), "ordered": len(ordered), "expired": len(expired), "urgent": len(urgent)},
            "context": {"stage": "initial"}
        }
    
    # Expiring items
    if any(word in text for word in ['expir', 'urgent', 'soon', 'critical']):
        urgent_items = []
        for item in stock:
            if item['status'] != 'available':
                continue
            exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
            days_left = (exp_date - today).days
            if days_left <= 7:
                item['days_left'] = days_left
                urgent_items.append(item)
        
        urgent_items.sort(key=lambda x: x['days_left'])
        
        if not urgent_items:
            return {
                "response": "Great news! You have no items expiring within the next week.",
                "action": "no_urgent",
                "context": {"stage": "initial"}
            }
        
        response_text = f"You have {len(urgent_items)} items expiring soon: "
        for item in urgent_items[:5]:
            response_text += f"{item['product_name']} ({item['days_left']} days, {item['quantity']} units), "
        response_text = response_text.rstrip(", ") + "."
        
        return {
            "response": response_text,
            "action": "urgent_items",
            "data": urgent_items[:5],
            "context": {"stage": "initial"}
        }
    
    # Search products
    if mentioned or any(word in text for word in ['find', 'search', 'show', 'check']):
        found = []
        for item in stock:
            if mentioned:
                if any(kw in item['product_name'].lower() for kw in mentioned):
                    exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
                    item['days_left'] = (exp_date - today).days
                    found.append(item)
            else:
                exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
                item['days_left'] = (exp_date - today).days
                found.append(item)
        
        if not found:
            return {
                "response": "I couldn't find any matching items in your inventory.",
                "action": "no_results",
                "context": {"stage": "initial"}
            }
        
        found.sort(key=lambda x: x['days_left'])
        response_text = f"Found {len(found)} items: "
        for item in found[:5]:
            response_text += f"{item['product_name']} ({item['quantity']} units, {item['days_left']} days left), "
        response_text = response_text.rstrip(", ") + "."
        
        return {
            "response": response_text,
            "action": "search_results",
            "data": found[:5],
            "context": {"stage": "initial"}
        }
    
    # Help
    if any(word in text for word in ['help', 'what can', 'how']):
        return {
            "response": "I can help you with: Adding stock - say 'add stock' or 'add 50 apples'. Getting summary - say 'show summary'. Finding expiring items - say 'what's expiring soon'. Searching - say 'find apples'. What would you like to do?",
            "action": "help",
            "context": {"stage": "initial"}
        }
    
    # Cancel
    if any(word in text for word in ['cancel', 'stop', 'nevermind', 'no']):
        return {
            "response": "Okay, cancelled. What else can I help you with?",
            "action": "cancelled",
            "context": {"stage": "initial"}
        }
    
    return {
        "response": "I can help you manage your stock. Say 'add stock' to add items, 'show summary' for overview, or 'what's expiring soon' to check urgent items.",
        "action": "default",
        "context": {"stage": "initial"}
    }

def handle_add_stock_flow(text: str, context: dict, user_id: str, cursor, conn) -> dict:
    """Handle the multi-step flow for adding stock via voice"""
    today = date.today()
    step = context.get('step', 'product_name')
    
    # Cancel at any point
    if any(word in text for word in ['cancel', 'stop', 'nevermind']):
        return {
            "response": "Stock addition cancelled. What else can I help you with?",
            "action": "cancelled",
            "context": {"stage": "initial"}
        }
    
    if step == 'product_name':
        # Extract product name from text
        product_name = text.strip().title()
        if len(product_name) < 2:
            return {
                "response": "I didn't catch that. What's the product name?",
                "action": "retry_product",
                "context": context
            }
        return {
            "response": f"Got it, {product_name}. How many units?",
            "action": "add_stock_product",
            "context": {"stage": "adding_stock", "step": "quantity", "product_name": product_name}
        }
    
    elif step == 'quantity':
        # Extract quantity
        quantity = None
        words = text.split()
        for word in words:
            if word.isdigit():
                quantity = int(word)
                break
        
        if not quantity:
            # Try to parse number words
            number_words = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'ten': 10, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'hundred': 100}
            for word, num in number_words.items():
                if word in text.lower():
                    quantity = num
                    break
        
        if not quantity or quantity <= 0:
            return {
                "response": "I didn't catch the quantity. How many units?",
                "action": "retry_quantity",
                "context": context
            }
        
        context['quantity'] = quantity
        context['step'] = 'expiry_date'
        return {
            "response": f"{quantity} units. What's the expiry date? Say something like 'January 15', 'next week', or 'in 10 days'.",
            "action": "add_stock_quantity",
            "context": context
        }
    
    elif step == 'expiry_date':
        # Parse expiry date
        expiry_date = None
        text_lower = text.lower()
        
        # Check for relative dates
        if 'today' in text_lower:
            expiry_date = today
        elif 'tomorrow' in text_lower:
            expiry_date = today + timedelta(days=1)
        elif 'next week' in text_lower or 'in a week' in text_lower:
            expiry_date = today + timedelta(days=7)
        elif 'in' in text_lower and 'day' in text_lower:
            # Extract number of days
            words = text.split()
            for i, word in enumerate(words):
                if word.isdigit():
                    expiry_date = today + timedelta(days=int(word))
                    break
        else:
            # Try to parse month and day
            months = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6, 
                     'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12,
                     'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
            
            month = None
            day = None
            words = text_lower.split()
            
            for word in words:
                if word in months:
                    month = months[word]
                elif word.isdigit() and 1 <= int(word) <= 31:
                    day = int(word)
            
            if month and day:
                year = today.year
                if month < today.month or (month == today.month and day < today.day):
                    year += 1
                try:
                    expiry_date = date(year, month, day)
                except ValueError:
                    pass
        
        if not expiry_date:
            return {
                "response": "I couldn't understand the date. Please say it like 'January 15', 'next week', or 'in 10 days'.",
                "action": "retry_expiry",
                "context": context
            }
        
        context['expiry_date'] = expiry_date.strftime("%Y-%m-%d")
        context['step'] = 'price'
        return {
            "response": f"Expiry date set to {expiry_date.strftime('%B %d, %Y')}. What's the price per unit? Say 'skip' if you don't want to set a price.",
            "action": "add_stock_expiry",
            "context": context
        }
    
    elif step == 'price':
        price = None
        
        if 'skip' in text.lower() or 'no price' in text.lower() or 'no' == text.lower().strip():
            price = None
        else:
            # Extract price
            words = text.replace('₹', '').replace('$', '').replace('rupees', '').replace('rupee', '').split()
            for word in words:
                try:
                    price = float(word)
                    break
                except ValueError:
                    continue
        
        context['price'] = price
        context['step'] = 'confirm'
        
        product_name = context.get('product_name')
        quantity = context.get('quantity')
        expiry_date = context.get('expiry_date')
        price_text = f"₹{price}" if price else "no price set"
        
        return {
            "response": f"Ready to add: {product_name}, {quantity} units, expires {expiry_date}, {price_text}. Say 'confirm' to add or 'cancel' to abort.",
            "action": "add_stock_confirm",
            "context": context
        }
    
    elif step == 'confirm':
        if any(word in text.lower() for word in ['yes', 'confirm', 'add', 'ok', 'okay', 'correct']):
            # Add the stock
            product_name = context.get('product_name')
            quantity = context.get('quantity')
            expiry_date = context.get('expiry_date')
            price = context.get('price')
            
            exp_date = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            status = "expired" if exp_date < today else "available"
            
            cursor.execute(
                "INSERT INTO stock (manager_id, product_name, quantity, expiry_date, price, status) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, product_name, quantity, expiry_date, price, status)
            )
            conn.commit()
            
            return {
                "response": f"Done! Added {quantity} units of {product_name} to your inventory. Would you like to add more stock?",
                "action": "stock_added",
                "data": {"product_name": product_name, "quantity": quantity, "expiry_date": expiry_date, "price": price},
                "context": {"stage": "initial"}
            }
        else:
            return {
                "response": "Say 'confirm' to add the stock or 'cancel' to abort.",
                "action": "awaiting_confirm",
                "context": context
            }
    
    return {
        "response": "Something went wrong. Let's start over. Say 'add stock' to begin.",
        "action": "error",
        "context": {"stage": "initial"}
    }

def process_middleman_voice_query(text: str, stock: list, context: dict, user_id: str, cursor, conn) -> dict:
    """Process voice query for middlemen - with improved product selection"""
    today = date.today()
    
    # Check for number selection (1, 2, 3, first, second, third, etc.)
    number_words = {'1': 0, '2': 1, '3': 2, '4': 3, '5': 4, 'one': 0, 'first': 0, 'two': 1, 'second': 1, 'three': 2, 'third': 2, 'four': 3, 'fourth': 3, 'five': 4, 'fifth': 4}
    
    # Handle product selection from multiple options
    if context.get('stage') == 'awaiting_selection' and context.get('results'):
        results = context['results']
        selected_index = None
        
        # Check for number selection
        for word, idx in number_words.items():
            if word in text.split():
                selected_index = idx
                break
        
        # Check for product name match
        if selected_index is None:
            for idx, item in enumerate(results):
                if item['product_name'].lower() in text or any(word in text for word in item['product_name'].lower().split()):
                    selected_index = idx
                    break
        
        if selected_index is not None and selected_index < len(results):
            item = results[selected_index]
            return {
                "response": f"You selected {item['product_name']} from {item['manager_name']}. {item['quantity']} units available at ₹{item['price'] or 'negotiable'} per unit, expiring in {item['days_left']} days. Say 'confirm' to place the order, or tell me how many units you want (in multiples of 10).",
                "action": "product_selected",
                "data": item,
                "context": {"stage": "confirm_order", "selected_item": item}
            }
        
        # If no valid selection, prompt again
        return {
            "response": "Please select a product by saying its number (1, 2, 3...) or name. " + " ".join([f"{i+1}. {r['product_name']}" for i, r in enumerate(results[:5])]),
            "action": "selection_prompt",
            "data": results[:5],
            "context": context
        }
    
    # Handle order confirmation with quantity
    if context.get('stage') == 'confirm_order' and context.get('selected_item'):
        item = context['selected_item']
        
        # Check for quantity specification
        quantity = None
        words = text.split()
        for i, word in enumerate(words):
            if word.isdigit():
                quantity = int(word)
                break
        
        # Check for confirmation
        if any(word in text for word in ['yes', 'confirm', 'order', 'proceed', 'ok', 'okay']):
            order_qty = quantity if quantity else item['quantity']
            # Round to nearest 10
            order_qty = max(10, (order_qty // 10) * 10)
            
            if order_qty > item['quantity']:
                order_qty = (item['quantity'] // 10) * 10
                if order_qty == 0:
                    order_qty = item['quantity']
            
            # Create order
            cursor.execute(
                "INSERT INTO orders (stock_id, middleman_id, quantity, status) VALUES (?, ?, ?, 'confirmed')",
                (item['stock_id'], user_id, order_qty)
            )
            
            # Update stock
            new_qty = item['quantity'] - order_qty
            if new_qty <= 0:
                cursor.execute("UPDATE stock SET quantity = 0, status = 'ordered' WHERE stock_id = ?", (item['stock_id'],))
            else:
                cursor.execute("UPDATE stock SET quantity = ? WHERE stock_id = ?", (new_qty, item['stock_id']))
            
            conn.commit()
            
            return {
                "response": f"Order confirmed! You've ordered {order_qty} units of {item['product_name']} from {item['manager_name']}. The supplier has been notified. Thank you!",
                "action": "order_confirmed",
                "order": {"stock_id": item['stock_id'], "quantity": order_qty},
                "context": {"stage": "completed"}
            }
        
        if any(word in text for word in ['no', 'cancel', 'nevermind']):
            return {
                "response": "Order cancelled. What else can I help you find?",
                "action": "cancelled",
                "context": {"stage": "initial"}
            }
        
        return {
            "response": f"Would you like to order {item['product_name']}? Say 'confirm' to proceed or specify a quantity in multiples of 10.",
            "action": "awaiting_confirmation",
            "context": context
        }
    
    # Greeting
    if text.strip() in ['hello', 'hi', 'hey', 'start', 'hi there', 'hello there']:
        return {
            "response": "Hello! I'm your Smart Clearance assistant. I can help you find available stock, check expiry dates, and place orders. What would you like to do today?",
            "action": "greeting",
            "context": {"stage": "initial"}
        }
    
    # Search for products
    product_keywords = ['apple', 'milk', 'bread', 'vegetable', 'fruit', 'dairy', 'meat', 'fish', 'rice', 'wheat', 'oil', 'sugar', 'orange', 'banana', 'tomato', 'potato', 'onion', 'chicken', 'egg', 'cheese', 'butter', 'yogurt']
    mentioned_products = [kw for kw in product_keywords if kw in text]
    
    search_intent = any(word in text for word in ['find', 'search', 'looking for', 'want', 'need', 'show', 'available', 'what', 'get', 'buy'])
    
    if search_intent or mentioned_products:
        products_found = []
        
        expiry_filter = None
        if 'this week' in text or 'week' in text:
            expiry_filter = 7
        elif 'today' in text:
            expiry_filter = 1
        elif 'tomorrow' in text:
            expiry_filter = 2
        elif 'soon' in text or 'expiring' in text:
            expiry_filter = 5
        
        for item in stock:
            exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
            days_left = (exp_date - today).days
            
            if mentioned_products:
                if not any(kw in item['product_name'].lower() for kw in mentioned_products):
                    continue
            
            if expiry_filter and days_left > expiry_filter:
                continue
            
            item['days_left'] = days_left
            products_found.append(item)
        
        if not products_found:
            return {
                "response": "I couldn't find any matching stock. Would you like me to show all available items instead?",
                "action": "no_results",
                "context": {"stage": "search_failed"}
            }
        
        products_found.sort(key=lambda x: x['days_left'])
        
        if len(products_found) == 1:
            item = products_found[0]
            price_info = f" at ₹{item['price']}" if item['price'] else ""
            return {
                "response": f"I found {item['product_name']} from {item['manager_name']}. {item['quantity']} units available{price_info}, expiring in {item['days_left']} days. Say 'confirm' to order or specify a quantity in multiples of 10.",
                "action": "single_result",
                "data": item,
                "context": {"stage": "confirm_order", "selected_item": item}
            }
        else:
            # Multiple results - ask user to select
            response_text = f"I found {len(products_found)} options. Please select one: "
            for i, item in enumerate(products_found[:5]):
                price_info = f"₹{item['price']}" if item['price'] else "negotiable"
                response_text += f"{i+1}. {item['product_name']} ({item['quantity']} units, {price_info}, {item['days_left']} days left). "
            
            response_text += "Say the number or name to select."
            
            return {
                "response": response_text,
                "action": "multiple_results",
                "data": products_found[:5],
                "context": {"stage": "awaiting_selection", "results": products_found[:5]}
            }
    
    # Price inquiry
    if any(word in text for word in ['price', 'cost', 'cheap', 'cheapest', 'expensive', 'budget']):
        priced_stock = [s for s in stock if s['price']]
        if priced_stock:
            for item in priced_stock:
                exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
                item['days_left'] = (exp_date - today).days
            priced_stock.sort(key=lambda x: x['price'])
            cheapest = priced_stock[0]
            return {
                "response": f"The most affordable option is {cheapest['product_name']} at ₹{cheapest['price']} per unit from {cheapest['manager_name']}. Say 'confirm' to order.",
                "action": "price_info",
                "data": cheapest,
                "context": {"stage": "confirm_order", "selected_item": cheapest}
            }
        return {
            "response": "I don't have pricing information for the current stock. Would you like to see what's available?",
            "action": "no_price_info",
            "context": {"stage": "initial"}
        }
    
    # Expiry queries
    if any(word in text for word in ['expir', 'urgent', 'critical', 'soon']):
        urgent_stock = []
        for item in stock:
            exp_date = datetime.strptime(item['expiry_date'], "%Y-%m-%d").date()
            days_left = (exp_date - today).days
            if days_left <= 3:
                item['days_left'] = days_left
                urgent_stock.append(item)
        
        if urgent_stock:
            urgent_stock.sort(key=lambda x: x['days_left'])
            
            if len(urgent_stock) == 1:
                item = urgent_stock[0]
                return {
                    "response": f"Found 1 urgent item: {item['product_name']} expiring in {item['days_left']} days. {item['quantity']} units available. Say 'confirm' to order.",
                    "action": "single_urgent",
                    "data": item,
                    "context": {"stage": "confirm_order", "selected_item": item}
                }
            
            response_text = f"Found {len(urgent_stock)} urgent items: "
            for i, item in enumerate(urgent_stock[:5]):
                response_text += f"{i+1}. {item['product_name']} ({item['days_left']} days left). "
            response_text += "Say the number to select one."
            
            return {
                "response": response_text,
                "action": "urgent_stock",
                "data": urgent_stock[:5],
                "context": {"stage": "awaiting_selection", "results": urgent_stock[:5]}
            }
        return {
            "response": "Good news! There's no critically expiring stock at the moment. Would you like to see all available items?",
            "action": "no_urgent",
            "context": {"stage": "initial"}
        }
    
    # Cancel/No
    if any(word in text for word in ['no', 'cancel', 'nevermind', 'stop']):
        return {
            "response": "No problem! Let me know if you need anything else.",
            "action": "cancelled",
            "context": {"stage": "initial"}
        }
    
    # Help
    if any(word in text for word in ['help', 'how', 'what can']):
        return {
            "response": "I can help you with: Finding stock - say 'show me apples' or 'what's available'. Checking urgent items - say 'what's expiring soon'. Placing orders - after finding items, say 'confirm' or specify quantity. What would you like to do?",
            "action": "help",
            "context": {"stage": "initial"}
        }
    
    return {
        "response": "I'm here to help you find and order clearance stock. Try saying 'show me apples', 'what's expiring soon', or 'find milk'. How can I assist you?",
        "action": "default",
        "context": {"stage": "initial"}
    }

# Statistics endpoint
@app.get("/api/stats")
def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) as total FROM stock WHERE status = 'available'")
    available = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM stock WHERE status = 'expired'")
    expired = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM stock WHERE status = 'ordered'")
    ordered = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM orders WHERE status = 'confirmed'")
    total_orders = cursor.fetchone()['total']
    
    cursor.execute("SELECT COUNT(*) as total FROM stock WHERE expiry_date <= date('now', '+3 days') AND status = 'available'")
    urgent = cursor.fetchone()['total']
    
    conn.close()
    
    return {
        "available_stock": available,
        "expired_stock": expired,
        "ordered_stock": ordered,
        "total_orders": total_orders,
        "urgent_items": urgent
    }
