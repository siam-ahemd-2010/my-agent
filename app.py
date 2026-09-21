import os
import sqlite3
import requests
import httpx
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

app = Flask(__name__)

DB_FILE = "chat_history.db"

# ---------------- DATABASE SETUP ----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Client configurations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            page_id TEXT PRIMARY KEY,
            page_name TEXT,
            access_token TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Conversation messages table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id TEXT NOT NULL,
            sender_psid TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Database Helper Functions
def save_client_config(page_id, page_name, access_token, system_prompt):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO clients (page_id, page_name, access_token, system_prompt, is_active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(page_id) DO UPDATE SET
            page_name=excluded.page_name,
            access_token=excluded.access_token,
            system_prompt=excluded.system_prompt,
            is_active=1
    ''', (page_id, page_name, access_token, system_prompt))
    conn.commit()
    conn.close()

def get_client_by_page_id(page_id):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM clients WHERE page_id = ?', (page_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_clients():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute('SELECT page_id, page_name, is_active, system_prompt, access_token FROM clients ORDER BY created_at DESC')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def update_client_status(page_id, is_active):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE clients SET is_active = ? WHERE page_id = ?', (1 if is_active else 0, page_id))
    conn.commit()
    conn.close()

def save_message(page_id, sender_psid, role, content):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO messages (page_id, sender_psid, role, content)
            VALUES (?, ?, ?, ?)
        ''', (page_id, sender_psid, role, content))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Save Error: {e}")

def get_conversation_history(page_id, sender_psid, limit=10):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT role, content FROM messages
            WHERE page_id = ? AND sender_psid = ?
            ORDER BY id DESC LIMIT ?
        ''', (page_id, sender_psid, limit))
        rows = cursor.fetchall()
        conn.close()
        return [{"role": role, "content": content} for role, content in reversed(rows)]
    except Exception as e:
        print(f"DB Fetch Error: {e}")
        return []

# Helper: Detect Page Details using Access Token
def get_facebook_page_info(access_token):
    url = f"https://graph.facebook.com/v19.0/me?access_token={access_token}"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if "id" in data:
            return data["id"], data.get("name", "FB Page")
    except Exception as e:
        print(f"FB Page Fetch Error: {e}")
    return None, None

# ---------------- GROQ AI SETUP ----------------
def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key, http_client=httpx.Client())

def generate_ai_reply(page_id, sender_psid, user_message, system_prompt):
    try:
        groq_client = get_groq_client()
        if not groq_client:
            return "Service unavailable."

        past_history = get_conversation_history(page_id, sender_psid, limit=10)

        messages_payload = [{"role": "system", "content": system_prompt}]
        messages_payload.extend(past_history)
        messages_payload.append({"role": "user", "content": user_message})

        default_model = os.getenv("DEFAULT_AI_MODEL", "llama-3.1-8b-instant")

        chat_completion = groq_client.chat.completions.create(
            messages=messages_payload,
            model=default_model,
            max_tokens=300
        )

        reply_content = chat_completion.choices[0].message.content

        save_message(page_id, sender_psid, "user", user_message)
        save_message(page_id, sender_psid, "assistant", reply_content)

        return reply_content
    except Exception as e:
        print(f"Groq AI Error: {e}")
        return "Thank you for messaging us. We will get back to you shortly."

def send_messenger_message(sender_psid, text, access_token):
    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={access_token}"
    payload = {
        "recipient": {"id": sender_psid},
        "message": {"text": text}
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"FB Send Error: {e}")

# ---------------- API ROUTES ----------------

@app.route('/')
def home():
    return render_template('index.html')

# Get List of All Configured Clients
@app.route('/api/clients', methods=['GET'])
def list_clients():
    clients = get_all_clients()
    return jsonify({"success": True, "clients": clients})

# Save or Add New Client Settings
@app.route('/api/clients/save', methods=['POST'])
def save_client():
    data = request.get_json() or {}
    token = data.get('access_token', '').strip()
    prompt = data.get('system_prompt', '').strip()

    if not token or not prompt:
        return jsonify({"success": False, "error": "Access Token and System Prompt are required"}), 400

    page_id, page_name = get_facebook_page_info(token)
    if not page_id:
        return jsonify({"success": False, "error": "Invalid Facebook Access Token"}), 400

    save_client_config(page_id, page_name, token, prompt)
    return jsonify({
        "success": True,
        "client": {
            "page_id": page_id,
            "page_name": page_name,
            "is_active": 1,
            "system_prompt": prompt
        }
    })

# Toggle Client Status On/Off
@app.route('/api/clients/toggle', methods=['POST'])
def toggle_client():
    data = request.get_json() or {}
    page_id = data.get('page_id')
    is_active = data.get('is_active')

    if page_id is None or is_active is None:
        return jsonify({"success": False, "error": "Missing page_id or is_active"}), 400

    update_client_status(page_id, is_active)
    return jsonify({"success": True, "is_active": is_active})

# Webhook Verification (GET)
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')
    verify_token = os.getenv('FB_VERIFY_TOKEN')

    if mode == 'subscribe' and token == verify_token:
        return challenge, 200
    return "Forbidden", 403

# Webhook Event Processing (POST) - MULTI-TENANT ENGINE
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.get_json() or {}

    if data.get('object') == 'page':
        for entry in data.get('entry', []):
            page_id = entry.get('id')  # Detect which page received the message!
            
            # 1. Fetch Client Configuration from DB for this specific page_id
            client = get_client_by_page_id(page_id)
            if not client or not client['is_active']:
                print(f"Skipping: Client page {page_id} is not configured or inactive.")
                continue

            messaging_list = entry.get('messaging', [])
            for messaging_event in messaging_list:
                if 'message' in messaging_event and not messaging_event['message'].get('is_echo'):
                    sender_psid = messaging_event['sender']['id']
                    user_message = messaging_event['message'].get('text')

                    if user_message:
                        # 2. Generate Reply using this client's specific System Prompt
                        ai_reply = generate_ai_reply(
                            page_id=page_id,
                            sender_psid=sender_psid,
                            user_message=user_message,
                            system_prompt=client['system_prompt']
                        )
                        # 3. Send Message using this client's specific Access Token
                        send_messenger_message(
                            sender_psid=sender_psid,
                            text=ai_reply,
                            access_token=client['access_token']
                        )

        return "EVENT_RECEIVED", 200
    return "Not Found", 404

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    app.run(host='0.0.0.0', port=port)