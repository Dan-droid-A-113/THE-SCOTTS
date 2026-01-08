from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import sqlite3
import json
import os
import csv
import io

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

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Users table with Google OAuth support
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
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
class UserLogin(BaseModel):
    user_id: str
    role: str
    name: Optional[str] = None
    email: Optional[str] = None
    picture: Optional[str] = None
    google_id: Optional[str] = None

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
@app.post("/api/login")
def login(user: UserLogin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ? AND role = ?", (user.user_id, user.role))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return {"success": True, "user": dict(result)}
    
    # Auto-create user if not exists
    conn = get_db()
    cursor = conn.cursor()
    name = user.name or f"{user.role.title()} {user.user_id}"
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, name, email, picture, role, google_id) VALUES (?, ?, ?, ?, ?, ?)",
        (user.user_id, name, user.email, user.picture, user.role, user.google_id)
    )
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user.user_id,))
    result = cursor.fetchone()
    conn.close()
    
    return {"success": True, "user": dict(result)}

@app.post("/api/google-login")
def google_login(user: GoogleLogin):
    """Handle Google OAuth login - creates unique user based on Google ID"""
    conn = get_db()
    cursor = conn.cursor()
    
    # Create unique user_id from google_id and role
    user_id = f"google_{user.google_id}_{user.role}"
    
    # Check if user exists
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    if result:
        conn.close()
        return {"success": True, "user": dict(result)}
    
    # Create new user
    cursor.execute(
        "INSERT INTO users (user_id, name, email, picture, role, google_id) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, user.name, user.email, user.picture, user.role, user.google_id)
    )
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
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
    """Process voice query for stock managers"""
    today = date.today()
    
    # Greeting
    if text.strip() in ['hello', 'hi', 'hey', 'start', 'hi there', 'hello there']:
        return {
            "response": "Hello! I'm your stock management assistant. I can help you check your inventory, find expiring items, and get stock summaries. What would you like to know?",
            "action": "greeting",
            "context": {"stage": "initial"}
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
    product_keywords = ['apple', 'milk', 'bread', 'vegetable', 'fruit', 'dairy', 'meat', 'fish', 'rice', 'wheat', 'oil', 'sugar']
    mentioned = [kw for kw in product_keywords if kw in text]
    
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
            "response": "I can help you with: Getting inventory summary - say 'show summary'. Finding expiring items - say 'what's expiring soon'. Searching products - say 'find apples'. What would you like to do?",
            "action": "help",
            "context": {"stage": "initial"}
        }
    
    return {
        "response": "I can help you manage your stock. Try saying 'show summary', 'what's expiring soon', or 'find' followed by a product name.",
        "action": "default",
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
