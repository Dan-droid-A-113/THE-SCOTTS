from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date
import sqlite3
import json
import os

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
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('manager', 'middleman')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Stock table
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
    
    # Insert demo users if not exist
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO users (user_id, name, role) VALUES ('manager1', 'Stock Manager 1', 'manager')")
        cursor.execute("INSERT INTO users (user_id, name, role) VALUES ('manager2', 'Stock Manager 2', 'manager')")
        cursor.execute("INSERT INTO users (user_id, name, role) VALUES ('middleman1', 'Middleman 1', 'middleman')")
        cursor.execute("INSERT INTO users (user_id, name, role) VALUES ('middleman2', 'Middleman 2', 'middleman')")
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_db()

# Pydantic models
class UserLogin(BaseModel):
    user_id: str
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
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, name, role) VALUES (?, ?, ?)",
        (user.user_id, f"{user.role.title()} {user.user_id}", user.role)
    )
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user.user_id,))
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
    Uses context-aware logic to handle stock queries and order confirmations.
    """
    conn = get_db()
    cursor = conn.cursor()
    
    user_text = input.text.lower().strip()
    context = input.context or {}
    
    # Get available stock for context
    cursor.execute('''
        SELECT s.*, u.name as manager_name 
        FROM stock s 
        JOIN users u ON s.manager_id = u.user_id 
        WHERE s.status = 'available'
        ORDER BY s.expiry_date ASC
    ''')
    available_stock = [dict(row) for row in cursor.fetchall()]
    
    response = process_voice_query(user_text, available_stock, context, input.user_id, cursor, conn)
    
    conn.close()
    return response

def process_voice_query(text: str, stock: list, context: dict, user_id: str, cursor, conn) -> dict:
    """Process voice query and generate intelligent response"""
    
    today = date.today()
    
    # Check for specific product mentions first
    product_keywords = ['apple', 'milk', 'bread', 'vegetable', 'fruit', 'dairy', 'meat', 'fish', 'rice', 'wheat', 'oil', 'sugar']
    mentioned_products = [kw for kw in product_keywords if kw in text]
    
    # Intent detection - greeting only if no product mentioned and explicit greeting
    if not mentioned_products and text.strip() in ['hello', 'hi', 'hey', 'start', 'hi there', 'hello there']:
        return {
            "response": "Hello! I'm your Smart Clearance assistant. I can help you find available stock, check expiry dates, and place orders. What would you like to do today?",
            "action": "greeting",
            "context": {"stage": "initial"}
        }
    
    # Search for products - check if user is looking for something
    search_intent = any(word in text for word in ['find', 'search', 'looking for', 'want', 'need', 'show', 'available', 'what', 'get', 'buy'])
    if search_intent or mentioned_products:
        # Extract product keywords
        products_found = []
        
        # Check for expiry-related queries
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
            
            # Filter by product name if mentioned
            if mentioned_products:
                if not any(kw in item['product_name'].lower() for kw in mentioned_products):
                    continue
            
            # Filter by expiry
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
        
        # Sort by expiry (most urgent first)
        products_found.sort(key=lambda x: x['days_left'])
        
        # Build response
        if len(products_found) == 1:
            item = products_found[0]
            price_info = f" at ₹{item['price']}" if item['price'] else ""
            response_text = f"I found {item['product_name']} from {item['manager_name']}. {item['quantity']} units available{price_info}, expiring in {item['days_left']} days. Would you like to place an order?"
        else:
            response_text = f"I found {len(products_found)} items. "
            # Highlight top 3
            for i, item in enumerate(products_found[:3]):
                price_info = f" at ₹{item['price']}" if item['price'] else ""
                response_text += f"{item['product_name']}: {item['quantity']} units{price_info}, {item['days_left']} days left. "
            
            if len(products_found) > 3:
                response_text += f"And {len(products_found) - 3} more items. "
            
            response_text += "Which one interests you?"
        
        return {
            "response": response_text,
            "action": "search_results",
            "data": products_found[:5],
            "context": {"stage": "product_selection", "results": products_found}
        }
    
    # Order confirmation
    if any(word in text for word in ['yes', 'confirm', 'order', 'buy', 'proceed', 'take']):
        # Check if we have context from previous search
        if context.get('stage') == 'product_selection' and context.get('results'):
            results = context['results']
            if len(results) == 1:
                item = results[0]
                # Create order
                cursor.execute(
                    "INSERT INTO orders (stock_id, middleman_id, quantity, status) VALUES (?, ?, ?, 'confirmed')",
                    (item['stock_id'], user_id, item['quantity'])
                )
                cursor.execute(
                    "UPDATE stock SET status = 'ordered' WHERE stock_id = ?",
                    (item['stock_id'],)
                )
                conn.commit()
                
                return {
                    "response": f"Order confirmed! You've ordered {item['quantity']} units of {item['product_name']} from {item['manager_name']}. The stock manager has been notified. Thank you for using Smart Clearance!",
                    "action": "order_confirmed",
                    "order": {"stock_id": item['stock_id'], "quantity": item['quantity']},
                    "context": {"stage": "completed"}
                }
            else:
                return {
                    "response": "I found multiple items. Please specify which product you'd like to order, or say the supplier name.",
                    "action": "clarification_needed",
                    "context": context
                }
        
        # Check for specific product in text
        for item in stock:
            if item['product_name'].lower() in text:
                cursor.execute(
                    "INSERT INTO orders (stock_id, middleman_id, quantity, status) VALUES (?, ?, ?, 'confirmed')",
                    (item['stock_id'], user_id, item['quantity'])
                )
                cursor.execute(
                    "UPDATE stock SET status = 'ordered' WHERE stock_id = ?",
                    (item['stock_id'],)
                )
                conn.commit()
                
                return {
                    "response": f"Order confirmed for {item['product_name']}! {item['quantity']} units have been reserved. The stock manager will be notified.",
                    "action": "order_confirmed",
                    "order": {"stock_id": item['stock_id'], "quantity": item['quantity']},
                    "context": {"stage": "completed"}
                }
        
        return {
            "response": "I'd be happy to help you place an order. Which product would you like to order? You can say something like 'I want apples' or 'show me what's expiring soon'.",
            "action": "order_guidance",
            "context": {"stage": "awaiting_product"}
        }
    
    # Price inquiry
    if any(word in text for word in ['price', 'cost', 'cheap', 'cheapest', 'expensive', 'budget']):
        priced_stock = [s for s in stock if s['price']]
        if priced_stock:
            priced_stock.sort(key=lambda x: x['price'])
            cheapest = priced_stock[0]
            return {
                "response": f"The most affordable option is {cheapest['product_name']} at ₹{cheapest['price']} per unit from {cheapest['manager_name']}. Would you like to order it?",
                "action": "price_info",
                "data": priced_stock[:3],
                "context": {"stage": "product_selection", "results": [cheapest]}
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
            response_text = f"I found {len(urgent_stock)} urgent items expiring within 3 days: "
            for item in urgent_stock[:3]:
                response_text += f"{item['product_name']} ({item['days_left']} days left), "
            response_text = response_text.rstrip(", ") + ". Would you like to order any of these?"
            
            return {
                "response": response_text,
                "action": "urgent_stock",
                "data": urgent_stock,
                "context": {"stage": "product_selection", "results": urgent_stock}
            }
        return {
            "response": "Good news! There's no critically expiring stock at the moment. Would you like to see all available items?",
            "action": "no_urgent",
            "context": {"stage": "initial"}
        }
    
    # Cancel/No
    if any(word in text for word in ['no', 'cancel', 'nevermind', 'stop']):
        return {
            "response": "No problem! Let me know if you need anything else. You can ask about available stock, prices, or place an order anytime.",
            "action": "cancelled",
            "context": {"stage": "initial"}
        }
    
    # Help
    if any(word in text for word in ['help', 'how', 'what can']):
        return {
            "response": "I can help you with: Finding available stock - say 'show me available items'. Checking expiring items - say 'what's expiring soon'. Placing orders - say 'I want to order' followed by the product. Checking prices - say 'what's the cheapest option'. What would you like to do?",
            "action": "help",
            "context": {"stage": "initial"}
        }
    
    # Default response
    return {
        "response": "I'm here to help you find and order clearance stock. You can ask me things like 'What's available?', 'Show me items expiring this week', or 'I want to order apples'. How can I assist you?",
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
