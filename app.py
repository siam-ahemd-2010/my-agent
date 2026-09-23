import os
import sqlite3
import requests
import threading
from flask import Flask, request, jsonify, render_template_string, redirect, session
from groq import Groq

# --- Configuration ---
client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "")

FB_APP_ID = os.environ.get("FB_APP_ID", "")
FB_APP_SECRET = os.environ.get("FB_APP_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "https://my-agent-anvr.onrender.com")

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect("bot_memory.db")
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

def get_client_details(page_id):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT page_access_token, custom_prompt, bot_status FROM clients WHERE page_id = ?", (page_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row[0], row[1], row[2]
    return None, None, False

def get_user_history(page_id, sender_id, custom_prompt):
    conn = sqlite3.connect("bot_memory.db")
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
        history.append({"role": row[0], "content": row[1]})
    return history

def save_message_to_db(page_id, sender_id, role, content):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (page_id, sender_id, role, content) VALUES (?, ?, ?, ?)", (page_id, sender_id, role, content))
    conn.commit()
    conn.close()

# Flask App Setup
flask_app = Flask(__name__)
flask_app.secret_key = os.environ.get("FLASK_SECRET_KEY", "super_secret_key_autocraft")

ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AutoCraft SaaS Central Admin Panel</title>
    <style>
        body { background: #0f172a; color: #f8fafc; font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 40px 20px; }
        .card { background: #1e293b; max-width: 800px; margin: 0 auto; padding: 30px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); border: 1px solid #334155; }
        textarea, button { width: 100%; padding: 12px; margin: 10px 0; border-radius: 8px; border: 1px solid #475569; font-size: 15px; box-sizing: border-box; }
        textarea { background: #0f172a; color: white; resize: vertical; height: 100px; }
        .page-item { background: #0f172a; padding: 18px; border-radius: 10px; margin-bottom: 15px; border: 1px solid #334155; display: flex; align-items: center; justify-content: space-between; }
        .page-info { display: flex; flex-direction: column; gap: 4px; }
        .actions { display: flex; align-items: center; gap: 12px; }
        .delete-btn { background: #ef4444; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-weight: bold; width: auto; margin: 0; font-size: 13px; }
        .delete-btn:hover { background: #dc2626; }
        
        .switch { position: relative; display: inline-block; width: 60px; height: 32px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #334155; transition: .4s; border-radius: 34px; border: 1px solid #475569; }
        .slider:before { position: absolute; content: ""; height: 24px; width: 24px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background: #22c55e; border-color: #4ade80; }
        input:checked + .slider:before { transform: translateX(28px); }
        h2, p { text-align: center; }
    </style>
</head>
<body>
    <div class="card">
        <h2>AutoCraft SaaS Central Admin Panel</h2>
        <p style="color: #38bdf8; font-size: 14px;">সকল পেজ এবং বটের কন্ট্রোল আপনার হাতে</p>
        
        {% if connected_pages %}
        <div style="margin-bottom: 25px;">
            <h3 style="color: #38bdf8; margin-bottom: 15px;">কানেক্টেড পেজসমূহ:</h3>
            {% for p in connected_pages %}
                <div class="page-item">
                    <div class="page-info">
                        <b style="font-size: 16px; color: #f8fafc;">{{ p[1] }}</b>
                        <small style="color: #94a3b8;">Page ID: {{ p[0] }}</small>
                        <small style="color: {{ '#4ade80' if p[2] == 1 else '#f87171' }}; font-weight: bold;">
                            স্ট্যাটাস: {{ 'ACTIVE (চালু)' if p[2] == 1 else 'INACTIVE (বন্ধ)' }}
                        </small>
                    </div>

                    <div class="actions">
                        <form action="/toggle-page" method="POST" style="margin:0;">
                            <input type="hidden" name="page_id" value="{{ p[0] }}">
                            <label class="switch" title="অন/অফ করুন">
                                <input type="checkbox" onchange="this.form.submit()" {{ 'checked' if p[2] == 1 else '' }}>
                                <span class="slider"></span>
                            </label>
                        </form>

                        <form action="/delete-page" method="POST" onsubmit="return confirm('আপনি কি নিশ্চিত যে এই পেজটি মুছে ফেলতে চান?');" style="margin:0;">
                            <input type="hidden" name="page_id" value="{{ p[0] }}">
                            <button type="submit" class="delete-btn">ডিলিট</button>
                        </form>
                    </div>
                </div>
            {% endfor %}
        </div>
        {% else %}
        <p style="color: #94a3b8; font-size: 14px;">এখনো কোনো পেজ যুক্ত করা হয়নি।</p>
        {% endif %}

        <hr style="border-color: #334155; margin: 25px 0;">

        <form action="/save-prompt" method="POST">
            <label style="font-weight: bold;">নতুন পেজের জন্য System Prompt (বটের নির্দেশিকা):</label>
            <textarea name="custom_prompt" placeholder="যেমন: আপনি ফ্যাশন হাউসের সেলস প্রতিনিধি..." required></textarea>
            <button type="submit" style="background: #2563eb; color: white; font-weight: bold; cursor: pointer;">নতুন ফেসবুক পেজ কানেক্ট করুন</button>
        </form>
    </div>
</body>
</html>
"""

@flask_app.route("/")
def dashboard():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT page_id, client_name, bot_status FROM clients")
    connected_pages = cursor.fetchall()
    conn.close()
    return render_template_string(ADMIN_TEMPLATE, connected_pages=connected_pages)

@flask_app.route("/toggle-page", methods=["POST"])
def toggle_page():
    page_id = request.form.get("page_id")
    if page_id:
        conn = sqlite3.connect("bot_memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT bot_status FROM clients WHERE page_id = ?", (page_id,))
        row = cursor.fetchone()
        if row:
            new_status = 0 if row[0] == 1 else 1
            cursor.execute("UPDATE clients SET bot_status = ? WHERE page_id = ?", (new_status, page_id))
            conn.commit()
        conn.close()
    return redirect("/")

@flask_app.route("/delete-page", methods=["POST"])
def delete_page():
    page_id = request.form.get("page_id")
    if page_id:
        conn = sqlite3.connect("bot_memory.db")
        cursor = conn.cursor()
        cursor.execute("SELECT page_access_token FROM clients WHERE page_id = ?", (page_id,))
        row = cursor.fetchone()
        
        if row and row[0]:
            try:
                unsub_url = f"https://graph.facebook.com/v18.0/{page_id}/subscribed_apps?access_token={row[0]}"
                requests.delete(unsub_url)
            except Exception as e:
                print(f"Unsubscribe error: {e}")

        cursor.execute("DELETE FROM clients WHERE page_id = ?", (page_id,))
        cursor.execute("DELETE FROM chat_history WHERE page_id = ?", (page_id,))
        conn.commit()
        conn.close()
        
    return redirect("/")

@flask_app.route("/save-prompt", methods=["POST"])
def save_prompt():
    session['custom_prompt'] = request.form.get("custom_prompt")
    fb_login_url = (
        f"https://www.facebook.com/v18.0/dialog/oauth?"
        f"client_id={FB_APP_ID}&"
        f"redirect_uri={BASE_URL}/auth/facebook/callback&"
        f"scope=pages_messaging,pages_show_list,pages_manage_metadata&"
        f"auth_type=rerequest"
    )
    return redirect(fb_login_url)

@flask_app.route("/auth/facebook/callback")
def facebook_callback():
    code = request.args.get("code")
    if not code:
        return "Facebook Auth Failed!", 400
        
    custom_prompt = session.get('custom_prompt', "আপনি এই পেজের প্রফেশনাল এআই অ্যাসিস্ট্যান্ট।")

    token_url = (
        f"https://graph.facebook.com/v18.0/oauth/access_token?"
        f"client_id={FB_APP_ID}&"
        f"redirect_uri={BASE_URL}/auth/facebook/callback&"
        f"client_secret={FB_APP_SECRET}&"
        f"code={code}"
    )
    res = requests.get(token_url).json()
    short_user_token = res.get("access_token")

    if not short_user_token:
        return f"Token Exchange Error: {res}", 400

    long_token_url = (
        f"https://graph.facebook.com/v18.0/oauth/access_token?"
        f"grant_type=fb_exchange_token&"
        f"client_id={FB_APP_ID}&"
        f"client_secret={FB_APP_SECRET}&"
        f"fb_exchange_token={short_user_token}"
    )
    long_res = requests.get(long_token_url).json()
    long_user_token = long_res.get("access_token", short_user_token)

    pages_url = f"https://graph.facebook.com/v18.0/me/accounts?access_token={long_user_token}&limit=250"
    pages_res = requests.get(pages_url).json()
    pages = pages_res.get("data", [])

    if not pages:
        return "কোনো ফেসবুক পেজ পাওয়া যায়নি!", 400

    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()

    for page in pages:
        page_id = page["id"]
        page_name = page["name"]
        page_access_token = page["access_token"]

        cursor.execute("SELECT custom_prompt FROM clients WHERE page_id = ?", (page_id,))
        existing = cursor.fetchone()

        if existing:
            cursor.execute("""
                UPDATE clients SET 
                    page_access_token = ?,
                    client_name = ?
                WHERE page_id = ?
            """, (page_access_token, page_name, page_id))
        else:
            cursor.execute("""
                INSERT INTO clients (page_id, page_access_token, client_name, custom_prompt, bot_status)
                VALUES (?, ?, ?, ?, 1)
            """, (page_id, page_access_token, page_name, custom_prompt))

        sub_url = f"https://graph.facebook.com/v18.0/{page_id}/subscribed_apps?subscribed_fields=messages&access_token={page_access_token}"
        requests.post(sub_url)

    conn.commit()
    conn.close()

    return redirect("/")

# --- Async Background Processing ---
def process_message_async(page_id, sender_id, user_message_text, audio_url, page_access_token, custom_prompt):
    try:
        final_input_text = user_message_text

        # Voice processing with whisper-large-v3-turbo
        if audio_url:
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
            final_input_text = f"[Voice Transcribed]: {transcription}"
            if os.path.exists(audio_path):
                os.remove(audio_path)

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
@flask_app.route("/webhook", methods=["GET", "POST"])
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
        return "Hello World", 200

    if request.method == "POST":
        data = request.json
        try:
            if data.get("object") == "page":
                for entry in data.get("entry", []):
                    page_id = entry.get("id")
                    
                    page_access_token, custom_prompt, bot_is_running = get_client_details(page_id)
                    
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
                                if att["type"] == "image":
                                    user_message_text = "এই ছবিটি দেখে আপনার সার্ভিস অনুযায়ী রেসপন্স করুন।"
                                elif att["type"] == "audio":
                                    audio_url = att["payload"]["url"]

                        if user_message_text or audio_url:
                            threading.Thread(
                                target=process_message_async, 
                                args=(page_id, sender_id, user_message_text, audio_url, page_access_token, custom_prompt)
                            ).start()

        except Exception as e:
            print(f"Error processing webhook: {e}")
            
        return jsonify({"status": "event received"}), 200

def generate_ai_reply(messages):
    try:
        # Text completion with openai/gpt-oss-120b
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)