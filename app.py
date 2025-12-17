"""
JuriFy - AI-Powered Legal Intelligence Web Application
Flask backend with authentication, AI processing, history, and gamification
"""
import os
import sqlite3
import jwt
import json
import hashlib
import google.generativeai as genai
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════
# FLASK APP CONFIGURATION
# ═══════════════════════════════════════════════════════════════

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

# Configuration
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'jurifyx-secret-key-change-in-production')
app.config['DATABASE'] = 'jurifyx.db'
app.config['CACHE_EXPIRY_HOURS'] = int(os.environ.get('CACHE_EXPIRY_HOURS', 48))  # Cache expires after 48 hours by default

# Gemini API Configuration - Multiple API Keys with Fallback
# Add up to 3 API keys in .env: GEMINI_API_KEY, GEMINI_API_KEY_2, GEMINI_API_KEY_3
GEMINI_API_KEYS = []
for key_name in ['GEMINI_API_KEY', 'GEMINI_API_KEY_2', 'GEMINI_API_KEY_3']:
    key = os.environ.get(key_name, '').strip()
    if key:
        GEMINI_API_KEYS.append(key)
        print(f"[API KEY] Loaded {key_name}: {key[:8]}...{key[-4:]}")

# Track which API key to use (starts with first one)
current_api_key_index = 0

# ═══════════════════════════════════════════════════════════════
# DATABASE MODULE
# ═══════════════════════════════════════════════════════════════

def get_db():
    """Get database connection"""
    conn = sqlite3.connect(app.config['DATABASE'])
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database with required tables"""
    conn = get_db()
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL
    )''')
    
    # History table
    c.execute('''CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        issue TEXT NOT NULL,
        rights TEXT,
        steps TEXT,
        docs TEXT,
        notice TEXT,
        language TEXT DEFAULT 'en',
        xp_reward INTEGER DEFAULT 10,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    
    # XP Events table
    c.execute('''CREATE TABLE IF NOT EXISTS xp_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        xp_amount INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )''')
    
    # Cache table for API response caching
    c.execute('''CREATE TABLE IF NOT EXISTS cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_hash TEXT UNIQUE NOT NULL,
        issue TEXT NOT NULL,
        language TEXT NOT NULL,
        summarize INTEGER DEFAULT 0,
        response_json TEXT NOT NULL,
        hit_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    )''')
    
    # Free tier usage tracking table
    c.execute('''CREATE TABLE IF NOT EXISTS free_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        client_id TEXT UNIQUE NOT NULL,
        usage_count INTEGER DEFAULT 0,
        last_reset TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════
# FREE TIER RATE LIMITING
# ═══════════════════════════════════════════════════════════════

FREE_TIER_DAILY_LIMIT = 5

def get_free_usage(client_id):
    """Get free tier usage for a client, reset if 24 hours passed"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT usage_count, last_reset FROM free_usage WHERE client_id = ?', (client_id,))
    row = c.fetchone()
    
    now = datetime.now()
    
    if row:
        last_reset = datetime.fromisoformat(row['last_reset'])
        hours_since_reset = (now - last_reset).total_seconds() / 3600
        
        if hours_since_reset >= 24:
            # Reset usage after 24 hours
            c.execute('UPDATE free_usage SET usage_count = 0, last_reset = ? WHERE client_id = ?',
                      (now.isoformat(), client_id))
            conn.commit()
            conn.close()
            return {'usage_count': 0, 'remaining': FREE_TIER_DAILY_LIMIT, 'reset_in_hours': 24}
        else:
            conn.close()
            remaining = max(0, FREE_TIER_DAILY_LIMIT - row['usage_count'])
            reset_in = 24 - hours_since_reset
            return {'usage_count': row['usage_count'], 'remaining': remaining, 'reset_in_hours': round(reset_in, 1)}
    else:
        # New client
        c.execute('INSERT INTO free_usage (client_id, usage_count, last_reset, created_at) VALUES (?, 0, ?, ?)',
                  (client_id, now.isoformat(), now.isoformat()))
        conn.commit()
        conn.close()
        return {'usage_count': 0, 'remaining': FREE_TIER_DAILY_LIMIT, 'reset_in_hours': 24}

def increment_free_usage(client_id):
    """Increment usage count for free tier client"""
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE free_usage SET usage_count = usage_count + 1 WHERE client_id = ?', (client_id,))
    conn.commit()
    conn.close()

# ═══════════════════════════════════════════════════════════════
# CACHING MODULE
# ═══════════════════════════════════════════════════════════════

def get_cache_key(issue, language, summarize):
    """Generate a unique hash key for caching based on query parameters"""
    cache_string = f"{issue.strip().lower()}|{language}|{1 if summarize else 0}"
    return hashlib.sha256(cache_string.encode()).hexdigest()

def get_cached_response(issue, language, summarize):
    """Check cache for existing response, returns None if not found or expired"""
    cache_key = get_cache_key(issue, language, summarize)
    expiry_hours = app.config['CACHE_EXPIRY_HOURS']
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT response_json, created_at, hit_count FROM cache WHERE query_hash = ?', (cache_key,))
    row = c.fetchone()
    
    if row:
        created_at = datetime.fromisoformat(row['created_at'])
        age_hours = (datetime.now() - created_at).total_seconds() / 3600
        
        if age_hours < expiry_hours:
            # Update hit count
            c.execute('UPDATE cache SET hit_count = ? WHERE query_hash = ?', 
                      (row['hit_count'] + 1, cache_key))
            conn.commit()
            conn.close()
            
            print(f"[CACHE HIT] Query found in cache (age: {age_hours:.1f}h, hits: {row['hit_count'] + 1})")
            return json.loads(row['response_json'])
        else:
            # Cache expired, delete it
            c.execute('DELETE FROM cache WHERE query_hash = ?', (cache_key,))
            conn.commit()
            print(f"[CACHE EXPIRED] Removed stale cache entry (age: {age_hours:.1f}h)")
    
    conn.close()
    return None

def save_to_cache(issue, language, summarize, response):
    """Save successful API response to cache"""
    cache_key = get_cache_key(issue, language, summarize)
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('''INSERT OR REPLACE INTO cache 
                     (query_hash, issue, language, summarize, response_json, hit_count, created_at)
                     VALUES (?, ?, ?, ?, ?, 0, ?)''',
                  (cache_key, issue.strip(), language, 1 if summarize else 0, 
                   json.dumps(response), datetime.now().isoformat()))
        conn.commit()
        print(f"[CACHE SAVE] Response cached successfully")
    except Exception as e:
        print(f"[CACHE ERROR] Failed to save: {e}")
    finally:
        conn.close()

def get_cache_stats():
    """Get cache statistics"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) as total, SUM(hit_count) as total_hits FROM cache')
    row = c.fetchone()
    
    c.execute('SELECT COUNT(*) as expired FROM cache WHERE datetime(created_at) < datetime("now", ?)',
              (f'-{app.config["CACHE_EXPIRY_HOURS"]} hours',))
    expired = c.fetchone()
    
    conn.close()
    
    return {
        'total_entries': row['total'] or 0,
        'total_hits': row['total_hits'] or 0,
        'expired_entries': expired['expired'] or 0,
        'expiry_hours': app.config['CACHE_EXPIRY_HOURS']
    }

# ═══════════════════════════════════════════════════════════════
# AUTHENTICATION MODULE
# ═══════════════════════════════════════════════════════════════

def token_required(f):
    """Decorator to protect routes requiring authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            if auth_header.startswith('Bearer '):
                token = auth_header.split(' ')[1]
        
        if not token:
            return jsonify({'error': 'Token required'}), 401
        
        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user_id = data['user_id']
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        
        return f(current_user_id, *args, **kwargs)
    return decorated

@app.route('/auth/register', methods=['POST'])
def register():
    """Register a new user"""
    data = request.get_json()
    
    if not data or not all(k in data for k in ['name', 'email', 'password']):
        return jsonify({'error': 'Missing required fields'}), 400
    
    name = data['name'].strip()
    email = data['email'].strip().lower()
    password = data['password']
    
    if not name or not email or not password:
        return jsonify({'error': 'All fields are required'}), 400
    
    hashed_password = generate_password_hash(password)
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('INSERT INTO users (name, email, password) VALUES (?, ?, ?)',
                  (name, email, hashed_password))
        conn.commit()
        user_id = c.lastrowid
        conn.close()
        return jsonify({'message': 'Registration successful', 'user_id': user_id}), 201
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Email already registered'}), 409

@app.route('/auth/login', methods=['POST'])
def login():
    """Login and return JWT token"""
    data = request.get_json()
    
    if not data or not all(k in data for k in ['email', 'password']):
        return jsonify({'error': 'Invalid credentials'}), 401
    
    email = data['email'].strip().lower()
    password = data['password']
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, password FROM users WHERE email = ?', (email,))
    user = c.fetchone()
    
    if not user or not check_password_hash(user['password'], password):
        conn.close()
        return jsonify({'error': 'Invalid credentials'}), 401
    
    # Award login XP
    c.execute('INSERT INTO xp_events (user_id, event_type, xp_amount, created_at) VALUES (?, ?, ?, ?)',
              (user['id'], 'login', 1, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    # Generate JWT token
    token = jwt.encode({
        'user_id': user['id'],
        'name': user['name'],
        'exp': datetime.utcnow() + timedelta(days=7)
    }, app.config['SECRET_KEY'], algorithm='HS256')
    
    return jsonify({
        'token': token,
        'user': {'id': user['id'], 'name': user['name'], 'email': email}
    })

# ═══════════════════════════════════════════════════════════════
# AI PROCESSING ENGINE
# ═══════════════════════════════════════════════════════════════

LANGUAGE_NAMES = {
    'en': 'English',
    'hi': 'Hindi',
    'mr': 'Marathi',
    'ta': 'Tamil',
    'bn': 'Bengali'
}

def build_prompt(issue, language, summarize=False):
    """Build the AI prompt with language instruction"""
    lang_name = LANGUAGE_NAMES.get(language, 'English')
    summary_instruction = " Keep responses concise and summarized." if summarize else ""
    
    return f"""You are JuriFy, an advanced AI legal assistant. Respond ONLY in {lang_name}.{summary_instruction}

User's Legal Issue: {issue}

Output EXACTLY these 4 sections with these exact headers:
YOUR RIGHTS:
[List the user's legal rights related to this issue]

IMMEDIATE STEPS:
[List actionable steps the user should take]

REQUIRED DOCUMENTS:
[List documents needed for this case]

FORMAL NOTICE FORMAT:
[Provide a professional legal notice template]

Be precise. Be actionable. No disclaimers."""

def parse_response(text):
    """Parse AI response into 4 sections"""
    sections = {'rights': '', 'steps': '', 'docs': '', 'notice': ''}
    
    if 'YOUR RIGHTS:' in text:
        remaining = text.split('YOUR RIGHTS:')[1]
        sections['rights'] = remaining.split('IMMEDIATE STEPS:')[0].strip() if 'IMMEDIATE STEPS:' in remaining else remaining.strip()
    
    if 'IMMEDIATE STEPS:' in text:
        remaining = text.split('IMMEDIATE STEPS:')[1]
        sections['steps'] = remaining.split('REQUIRED DOCUMENTS:')[0].strip() if 'REQUIRED DOCUMENTS:' in remaining else remaining.strip()
    
    if 'REQUIRED DOCUMENTS:' in text:
        remaining = text.split('REQUIRED DOCUMENTS:')[1]
        sections['docs'] = remaining.split('FORMAL NOTICE FORMAT:')[0].strip() if 'FORMAL NOTICE FORMAT:' in remaining else remaining.strip()
    
    if 'FORMAL NOTICE FORMAT:' in text:
        sections['notice'] = text.split('FORMAL NOTICE FORMAT:')[1].strip()
    
    return sections

def get_available_models():
    """Get list of available Gemini models"""
    try:
        models = []
        for model in genai.list_models():
            if 'generateContent' in model.supported_generation_methods:
                models.append(model.name)
        return models
    except Exception as e:
        print(f"Error listing models: {e}")
        return []

def process_issue(issue, language='en', summarize=False, skip_cache=False):
    """Process legal issue through Gemini API with caching and multi-key fallback support"""
    global current_api_key_index
    
    # Check cache first (unless explicitly skipped)
    if not skip_cache:
        cached = get_cached_response(issue, language, summarize)
        if cached:
            cached['from_cache'] = True
            return cached
    
    if not GEMINI_API_KEYS:
        return {'error': 'No Gemini API keys configured. Please add GEMINI_API_KEY to your .env file.'}
    
    # List of models to try in order of preference
    model_names = [
        'gemini-2.5-flash',           # Best: highest RPD on free tier
        'gemini-2.5-flash-lite',      # Lite version, also high RPD
        'gemini-2.0-flash',           # Fallback fast model
        'gemini-1.5-flash',           # Stable fallback
    ]
    
    prompt = build_prompt(issue, language, summarize)
    last_error = None
    
    # Try each API key when quota is exhausted
    keys_tried = 0
    start_key_index = current_api_key_index
    
    while keys_tried < len(GEMINI_API_KEYS):
        current_key = GEMINI_API_KEYS[current_api_key_index]
        print(f"[API KEY] Using key #{current_api_key_index + 1}: {current_key[:8]}...{current_key[-4:]}")
        
        # Configure genai with current key
        genai.configure(api_key=current_key)
        
        # Try each model with current API key
        for model_name in model_names:
            try:
                print(f"[API CALL] Trying model: {model_name}")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                
                if response and response.text:
                    print(f"[API SUCCESS] Model: {model_name}, Key #{current_api_key_index + 1}")
                    result = parse_response(response.text)
                    result['from_cache'] = False
                    result['api_key_used'] = current_api_key_index + 1
                    
                    # Save successful response to cache
                    save_to_cache(issue, language, summarize, result)
                    
                    return result
                else:
                    print(f"[API EMPTY] Empty response from {model_name}")
                    continue
                    
            except Exception as e:
                last_error = str(e)
                print(f"[API ERROR] Model {model_name} failed: {last_error}")
                
                # If it's a model not found error, try next model
                if '404' in last_error or 'not found' in last_error.lower():
                    continue
                # If quota exceeded, try next API key
                elif 'QUOTA' in last_error.upper() or 'LIMIT' in last_error.upper() or 'RESOURCE_EXHAUSTED' in last_error.upper() or '429' in last_error:
                    print(f"[QUOTA EXHAUSTED] Key #{current_api_key_index + 1} quota exceeded, trying next key...")
                    break  # Break model loop to try next API key
                # If it's an invalid API key error, try next key
                elif 'API_KEY' in last_error.upper() or 'INVALID' in last_error.upper():
                    print(f"[INVALID KEY] Key #{current_api_key_index + 1} is invalid, trying next key...")
                    break  # Break model loop to try next API key
                # For other errors, try next model
                continue
        
        # Move to next API key
        current_api_key_index = (current_api_key_index + 1) % len(GEMINI_API_KEYS)
        keys_tried += 1
        
        # If we've cycled back to the starting key, all keys are exhausted
        if current_api_key_index == start_key_index and keys_tried > 0:
            break
    
    # All API keys and models failed
    return {'error': f'All API keys exhausted. Last error: {last_error}. Please try again later or add more API keys.'}

@app.route('/debug/models', methods=['GET'])
def list_models():
    """Debug endpoint to list available models and API key status"""
    if not GEMINI_API_KEYS:
        return jsonify({'error': 'No API keys configured'}), 400
    
    # Configure with first available key to list models
    genai.configure(api_key=GEMINI_API_KEYS[0])
    models = get_available_models()
    
    # Mask API keys for security (show first 8 and last 4 chars)
    masked_keys = [f"{k[:8]}...{k[-4:]}" for k in GEMINI_API_KEYS]
    
    return jsonify({
        'available_models': models,
        'total_api_keys': len(GEMINI_API_KEYS),
        'current_key_index': current_api_key_index + 1,
        'api_keys_masked': masked_keys
    })

# ═══════════════════════════════════════════════════════════════
# CACHE MANAGEMENT ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/cache/stats', methods=['GET'])
def cache_stats():
    """Get cache statistics"""
    stats = get_cache_stats()
    return jsonify(stats)

@app.route('/cache/clear', methods=['POST'])
@token_required
def clear_cache(current_user_id):
    """Clear all cached responses"""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM cache')
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    print(f"[CACHE CLEAR] Deleted {deleted} cached entries")
    return jsonify({'message': f'Cache cleared. Deleted {deleted} entries.', 'deleted': deleted})

@app.route('/cache/clear-expired', methods=['POST'])
@token_required  
def clear_expired_cache(current_user_id):
    """Clear only expired cache entries"""
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM cache WHERE datetime(created_at) < datetime("now", ?)',
              (f'-{app.config["CACHE_EXPIRY_HOURS"]} hours',))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    print(f"[CACHE CLEANUP] Deleted {deleted} expired entries")
    return jsonify({'message': f'Expired cache cleared. Deleted {deleted} entries.', 'deleted': deleted})

@app.route('/process', methods=['POST'])
@token_required
def process_endpoint(current_user_id):
    """Process a legal issue with caching support"""
    data = request.get_json()
    
    if not data or 'issue' not in data:
        return jsonify({'error': 'Issue text required'}), 400
    
    issue = data['issue'].strip()
    language = data.get('language', 'en')
    summarize = data.get('summarize', False)
    voice_used = data.get('voice_used', False)
    skip_cache = data.get('skip_cache', False)  # Force fresh API call
    
    if not issue:
        return jsonify({'error': 'Issue text cannot be empty'}), 400
    
    # Process through AI (with caching)
    result = process_issue(issue, language, summarize, skip_cache)
    
    if 'error' in result:
        return jsonify(result), 500
    
    from_cache = result.get('from_cache', False)
    
    # Calculate XP reward (reduced XP for cached responses to encourage fresh queries)
    if from_cache:
        xp_reward = 2  # Minimal XP for cached response
    else:
        xp_reward = 10  # Base XP for fresh API call
        if summarize:
            xp_reward += 5
        if voice_used:
            xp_reward += 5
    
    # Save to history
    conn = get_db()
    c = conn.cursor()
    created_at = datetime.now().isoformat()
    
    c.execute('''INSERT INTO history (user_id, issue, rights, steps, docs, notice, language, xp_reward, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
              (current_user_id, issue, result.get('rights', ''), result.get('steps', ''),
               result.get('docs', ''), result.get('notice', ''), language, xp_reward, created_at))
    history_id = c.lastrowid
    
    # Award XP
    c.execute('INSERT INTO xp_events (user_id, event_type, xp_amount, created_at) VALUES (?, ?, ?, ?)',
              (current_user_id, 'query', xp_reward, created_at))
    
    conn.commit()
    conn.close()
    
    result['history_id'] = history_id
    result['xp_reward'] = xp_reward
    
    return jsonify(result)

# ═══════════════════════════════════════════════════════════════
# FREE TIER ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@app.route('/free/status', methods=['POST'])
def free_status():
    """Check free tier usage status"""
    data = request.get_json()
    client_id = data.get('client_id', '') if data else ''
    
    if not client_id:
        return jsonify({'error': 'Client ID required'}), 400
    
    usage = get_free_usage(client_id)
    return jsonify({
        'daily_limit': FREE_TIER_DAILY_LIMIT,
        'used': usage['usage_count'],
        'remaining': usage['remaining'],
        'reset_in_hours': usage['reset_in_hours']
    })

@app.route('/free/process', methods=['POST'])
def free_process():
    """Process a legal issue for free tier users (limited to 5/day)"""
    data = request.get_json()
    
    if not data or 'issue' not in data:
        return jsonify({'error': 'Issue text required'}), 400
    
    client_id = data.get('client_id', '')
    if not client_id:
        return jsonify({'error': 'Client ID required for free tier'}), 400
    
    # Check rate limit
    usage = get_free_usage(client_id)
    if usage['remaining'] <= 0:
        return jsonify({
            'error': f'Daily limit reached ({FREE_TIER_DAILY_LIMIT} queries/day). Please login for unlimited access or wait {usage["reset_in_hours"]} hours.',
            'limit_reached': True,
            'reset_in_hours': usage['reset_in_hours']
        }), 429
    
    issue = data['issue'].strip()
    language = data.get('language', 'en')
    summarize = data.get('summarize', False)
    
    if not issue:
        return jsonify({'error': 'Issue text cannot be empty'}), 400
    
    if len(issue) < 10:
        return jsonify({'error': 'Please provide more details about your issue'}), 400
    
    # Process through AI (always use cache for free tier to save quota)
    result = process_issue(issue, language, summarize, skip_cache=False)
    
    if 'error' in result:
        return jsonify(result), 500
    
    # Increment usage only on successful response
    increment_free_usage(client_id)
    
    # Get updated usage
    updated_usage = get_free_usage(client_id)
    
    result['free_tier'] = True
    result['queries_remaining'] = updated_usage['remaining']
    result['daily_limit'] = FREE_TIER_DAILY_LIMIT
    
    return jsonify(result)

# ═══════════════════════════════════════════════════════════════
# HISTORY MODULE
# ═══════════════════════════════════════════════════════════════

@app.route('/history', methods=['GET'])
@token_required
def get_history(current_user_id):
    """Get user's query history"""
    search = request.args.get('search', '').strip().lower()
    
    conn = get_db()
    c = conn.cursor()
    
    if search:
        c.execute('''SELECT * FROM history WHERE user_id = ? AND LOWER(issue) LIKE ? 
                     ORDER BY created_at DESC''', (current_user_id, f'%{search}%'))
    else:
        c.execute('SELECT * FROM history WHERE user_id = ? ORDER BY created_at DESC', (current_user_id,))
    
    rows = c.fetchall()
    conn.close()
    
    history = [dict(row) for row in rows]
    return jsonify(history)

@app.route('/history/<int:item_id>', methods=['GET'])
@token_required
def get_history_item(current_user_id, item_id):
    """Get specific history item"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT * FROM history WHERE id = ? AND user_id = ?', (item_id, current_user_id))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({'error': 'History item not found'}), 404
    
    return jsonify(dict(row))

@app.route('/history/<int:item_id>', methods=['DELETE'])
@token_required
def delete_history_item(current_user_id, item_id):
    """Delete a history item"""
    conn = get_db()
    c = conn.cursor()
    
    # Verify ownership
    c.execute('SELECT id FROM history WHERE id = ? AND user_id = ?', (item_id, current_user_id))
    if not c.fetchone():
        conn.close()
        return jsonify({'error': 'History item not found'}), 404
    
    c.execute('DELETE FROM history WHERE id = ? AND user_id = ?', (item_id, current_user_id))
    conn.commit()
    conn.close()
    
    return jsonify({'message': 'History item deleted'})

# ═══════════════════════════════════════════════════════════════
# XP AND GAMIFICATION MODULE
# ═══════════════════════════════════════════════════════════════

def calculate_xp(user_id):
    """Calculate total XP, level, and badges for user"""
    conn = get_db()
    c = conn.cursor()
    
    # Get total XP
    c.execute('SELECT SUM(xp_amount) as total FROM xp_events WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    total_xp = result['total'] or 0
    
    # Get query count
    c.execute('SELECT COUNT(*) as count FROM history WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    query_count = result['count'] or 0
    
    conn.close()
    
    # Calculate level
    level = total_xp // 100
    
    # Calculate badges
    badges = {
        'bronze': query_count >= 3,
        'silver': query_count >= 10,
        'gold': query_count >= 25,
        'diamond': query_count >= 50
    }
    
    return {
        'total_xp': total_xp,
        'level': level,
        'xp_in_level': total_xp % 100,
        'query_count': query_count,
        'badges': badges
    }

def award_xp(user_id, event_type, amount):
    """Award XP to user"""
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO xp_events (user_id, event_type, xp_amount, created_at) VALUES (?, ?, ?, ?)',
              (user_id, event_type, amount, datetime.now().isoformat()))
    conn.commit()
    conn.close()

@app.route('/xp', methods=['GET'])
@token_required
def get_xp(current_user_id):
    """Get user's XP and gamification data"""
    return jsonify(calculate_xp(current_user_id))

# ═══════════════════════════════════════════════════════════════
# STATIC FILE SERVING
# ═══════════════════════════════════════════════════════════════

@app.route('/')
def serve_index():
    """Serve the main HTML file"""
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    """Serve static files from public folder"""
    return send_from_directory('public', path)

# ═══════════════════════════════════════════════════════════════
# APPLICATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
