import os
import sqlite3
import requests
import threading
from flask import Flask, request, jsonify, render_template, redirect
from groq import Groq

# --- Configuration ---
client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "my_secure_verify_token")

# --- Database Setup ---
DB_NAME = "bot_memory.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            page_id TEXT PRIMARY KEY,
            page_access_token TEXT,
            client_name TEXT,
            custom_prompt TEXT,
            bot_status BOOLEAN DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            page_id TEXT,
            sender_id TEXT,
            role TEXT,
            content TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Database Helper Functions ---
def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def get_client_details(page_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT page_access_token, custom_prompt, bot_status, client_name FROM clients WHERE page_id = ?", (page_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row["page_access_token"], row["custom_prompt"], bool(row["bot_status"]), row["client_name"]
    return None, None, False, None

def get_user_history(page_id, sender_id, custom_prompt):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT role, content FROM (
            SELECT role, content, ROWID FROM chat_history 
            WHERE page_id = ? AND sender_id = ? 
            ORDER BY ROWID DESC LIMIT 10
        ) ORDER BY ROWID ASC
    """, (page_id, sender_id))
    
    rows = cursor.fetchall()
    conn.close()
    
    system_instruction = f"{custom_prompt}\n\n[STRICT RULE: Always reply clearly in Bengali (বাংলা) or Banglish as requested. Never use Chinese, Hindi, or unwanted foreign characters. Keep responses complete and meaningful.]"
    
    history = [{"role": "system", "content": system_instruction}]
    for row in rows:
        history.append({"role": row["role"], "content": row["content"]})
    return history

def save_message_to_db(page_id, sender_id, role, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (page_id, sender_id, role, content) VALUES (?, ?, ?, ?)", (page_id, sender_id, role, content))
    conn.commit()
    conn.close()

# --- Flask App Setup ---
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "autocraft_saas_secret_key")

# --- HTML View Routes ---
@app.route("/")
def admin_dashboard():
    return render_template("index.html")

@app.route("/dashboard/<page_id>")
def client_dashboard(page_id):
    return render_template("client.html", page_id=page_id)

# --- REST API Endpoints (Frontend JS Integration) ---

@app.route("/api/clients", methods=["GET"])
def api_get_clients():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT page_id, client_name, custom_prompt, bot_status FROM clients")
    rows = cursor.fetchall()
    conn.close()
    
    clients = []
    for r in rows:
        clients.append({
            "page_id": r["page_id"],
            "page_name": r["client_name"] or "Facebook Page",
            "system_prompt": r["custom_prompt"],
            "is_active": bool(r["bot_status"])
        })
    return jsonify({"clients": clients})

@app.route("/api/clients/<page_id>", methods=["GET"])
def api_get_single_client(page_id):
    token, prompt, is_active, name = get_client_details(page_id)
    if token:
        return jsonify({
            "success": True,
            "client": {
                "page_id": page_id,
                "page_name": name or "Facebook Page",
                "system_prompt": prompt,
                "is_active": is_active
            }
        })
    return jsonify({"success": False, "error": "Client not found"}), 404

@app.route("/api/clients/save", methods=["POST"])
def api_save_client():
    data = request.json or {}
    access_token = data.get("access_token", "").strip()
    system_prompt = data.get("system_prompt", "").strip()

    if not access_token or not system_prompt:
        return jsonify({"success": False, "error": "Access token and system prompt are required."}), 400

    # Fetch Page ID and Page Name from Facebook Graph API using the token
    fb_url = f"https://graph.facebook.com/v18.0/me?access_token={access_token}"
    res = requests.get(fb_url).json()

    if "error" in res:
        return jsonify({"success": False, "error": f"Invalid Access Token: {res['error'].get('message')}"}), 400

    page_id = res.get("id")
    page_name = res.get("name", "Facebook Page")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO clients (page_id, page_access_token, client_name, custom_prompt, bot_status)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(page_id) DO UPDATE SET
            page_access_token = excluded.page_access_token,
            client_name = excluded.client_name,
            custom_prompt = excluded.custom_prompt
    """, (page_id, access_token, page_name, system_prompt))
    conn.commit()
    conn.close()

    # Subscribe page to webhook events
    sub_url = f"https://graph.facebook.com/v18.0/{page_id}/subscribed_apps?subscribed_fields=messages&access_token={access_token}"
    requests.post(sub_url)

    return jsonify({"success": True, "page_id": page_id, "page_name": page_name})

@app.route("/api/clients/toggle", methods=["POST"])
def api_toggle_client():
    data = request.json or {}
    page_id = data.get("page_id")
    is_active = data.get("is_active")

    if page_id is None or is_active is None:
        return jsonify({"success": False, "error": "Missing parameters"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE clients SET bot_status = ? WHERE page_id = ?", (1 if is_active else 0, page_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/api/clients/update-prompt", methods=["POST"])
def api_update_prompt():
    data = request.json or {}
    page_id = data.get("page_id")
    system_prompt = data.get("system_prompt")

    if not page_id or system_prompt is None:
        return jsonify({"success": False, "error": "Missing parameters"}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE clients SET custom_prompt = ? WHERE page_id = ?", (system_prompt, page_id))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

@app.route("/api/clients/delete", methods=["POST"])
def api_delete_client():
    data = request.json or {}
    page_id = data.get("page_id")

    if not page_id:
        return jsonify({"success": False, "error": "Missing page_id"}), 400

    token, _, _, _ = get_client_details(page_id)
    if token:
        try:
            unsub_url = f"https://graph.facebook.com/v18.0/{page_id}/subscribed_apps?access_token={token}"
            requests.delete(unsub_url)
        except Exception as e:
            print(f"Unsubscribe Error: {e}")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM clients WHERE page_id = ?", (page_id,))
    cursor.execute("DELETE FROM chat_history WHERE page_id = ?", (page_id,))
    conn.commit()
    conn.close()

    return jsonify({"success": True})

# --- AI & Messaging Async Workers ---
def generate_ai_reply(messages):
    try:
        completion = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=messages,
            temperature=0.6,
            max_tokens=800,
        )
        return completion.choices[0].message.content
    except Exception as e:
        print(f"Groq API Error: {e}")
        return "দুঃখিত, এই মুহূর্তে উত্তর দিতে একটু সমস্যা হচ্ছে।"

def send_facebook_message(page_id, recipient_id, message_text, page_access_token):
    url = f"https://graph.facebook.com/v18.0/me/messages?access_token={page_access_token}"
    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    headers = {"Content-Type": "application/json"}
    requests.post(url, json=payload, headers=headers)

def process_message_async(page_id, sender_id, user_message_text, audio_url, page_access_token, custom_prompt):
    try:
        final_input_text = user_message_text

        # Audio Processing via Whisper
        if audio_url:
            try:
                audio_data = requests.get(audio_url).content
                audio_path = f"temp_{sender_id}.mp3"
                with open(audio_path, "wb") as f:
                    f.write(audio_data)
                
                with open(audio_path, "rb") as file:
                    transcription = client.audio.translations.create(
                        file=(audio_path, file.read()),
                        model="whisper-large-v3-turbo",
                        response_format="text"
                    )
                
                # Instruction passed to LLM so it doesn't get confused
                final_input_text = f"[System Note: User sent a Voice Note. Transcribed content: '{transcription}']. Respond to the message directly. NEVER mention that you cannot process voice notes."
                
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except Exception as audio_err:
                print(f"Audio Transcription Error: {audio_err}")
                final_input_text = "আমি আপনার ভয়েস মেসেজটি পেয়েছি কিন্তু শুনতে সমস্যা হচ্ছে, দয়া করে কথাটি লিখে বলবেন?"

        if final_input_text:
            chat_messages = get_user_history(page_id, sender_id, custom_prompt)
            
            save_message_to_db(page_id, sender_id, "user", final_input_text)
            chat_messages.append({"role": "user", "content": final_input_text})

            ai_reply = generate_ai_reply(chat_messages)
            save_message_to_db(page_id, sender_id, "assistant", ai_reply)
            
            send_facebook_message(page_id, recipient_id=sender_id, message_text=ai_reply, page_access_token=page_access_token)
    except Exception as e:
        print(f"Async Error: {e}")

# --- Facebook Webhook Route ---
@app.route("/webhook", methods=["GET", "POST"])
def facebook_webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        
        if mode and token:
            if mode == "subscribe" and token == VERIFY_TOKEN:
                return challenge, 200
            else:
                return "Verification failed", 403
        return "Webhook Endpoint Active", 200

    if request.method == "POST":
        data = request.json
        try:
            if data.get("object") == "page":
                for entry in data.get("entry", []):
                    page_id = entry.get("id")
                    
                    page_access_token, custom_prompt, bot_is_running, _ = get_client_details(page_id)
                    
                    if not page_access_token or not bot_is_running or not custom_prompt:
                        continue

                    for messaging_event in entry.get("messaging", []):
                        if messaging_event.get("message", {}).get("is_echo"):
                            continue

                        sender_id = messaging_event.get("sender", {}).get("id")
                        if sender_id == page_id:
                            continue

                        user_message_text = ""
                        audio_url = None

                        if "message" in messaging_event and "text" in messaging_event["message"]:
                            user_message_text = messaging_event["message"]["text"]

                        if "message" in messaging_event and "attachments" in messaging_event["message"]:
                            for att in messaging_event["message"]["attachments"]:
                                att_type = att.get("type")
                                if att_type == "image":
                                    user_message_text = "এই ছবিটি দেখে আপনার সার্ভিস অনুযায়ী রেসপন্স করুন।"
                                elif att_type in ["audio", "voice"]:
                                    audio_url = att.get("payload", {}).get("url")

                        if user_message_text or audio_url:
                            threading.Thread(
                                target=process_message_async, 
                                args=(page_id, sender_id, user_message_text, audio_url, page_access_token, custom_prompt)
                            ).start()

        except Exception as e:
            print(f"Error processing webhook: {e}")
            
        return jsonify({"status": "event received"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)