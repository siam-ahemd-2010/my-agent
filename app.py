import os
import requests
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
from groq import Groq

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Global Configuration State for Client
client_config = {
    "systemPrompt": "You are a helpful AI customer service assistant.",
    "isActive": True
}

# Safe Groq Client Initialization
def get_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("WARNING: GROQ_API_KEY environment variable is not set!")
        return None
    return Groq(api_key=api_key)

# Function: Generate AI Reply using Groq Python SDK
def generate_ai_reply(user_message):
    try:
        groq_client = get_groq_client()
        if not groq_client:
            return "Thank you for messaging us. Our service is currently under maintenance."

        chat_completion = groq_client.chat.completions.create(
            messages=[
                {"role": "system", "content": client_config["systemPrompt"]},
                {"role": "user", "content": user_message}
            ],
            model=os.getenv("DEFAULT_AI_MODEL", "llama-3.3-70b-versatile"),
            max_tokens=300
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        print(f"Groq AI Error: {e}")
        return "Thank you for messaging us. We will get back to you shortly."

# Function: Send Reply to Messenger via Requests
def send_messenger_message(sender_psid, text):
    page_token = os.getenv("FB_PAGE_ACCESS_TOKEN")
    if not page_token:
        print("ERROR: FB_PAGE_ACCESS_TOKEN is missing!")
        return

    url = f"https://graph.facebook.com/v19.0/me/messages?access_token={page_token}"
    payload = {
        "recipient": {"id": sender_psid},
        "message": {"text": text}
    }
    try:
        response = requests.post(url, json=payload)
        print(f"FB Send Status: {response.status_code}")
    except Exception as e:
        print(f"FB Send Error: {e}")

# ---------------- ROUTES ----------------

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/config', methods=['GET'])
def get_config():
    return jsonify(client_config)

@app.route('/api/config/prompt', methods=['POST'])
def update_prompt():
    data = request.get_json() or {}
    if 'systemPrompt' in data and data['systemPrompt'].strip():
        client_config['systemPrompt'] = data['systemPrompt'].strip()
        return jsonify({"success": True, "message": "Prompt updated"})
    return jsonify({"success": False, "error": "Prompt cannot be empty"}), 400

@app.route('/api/config/toggle', methods=['POST'])
def toggle_status():
    data = request.get_json() or {}
    if 'isActive' in data:
        client_config['isActive'] = bool(data['isActive'])
        return jsonify({"success": True, "isActive": client_config['isActive']})
    return jsonify({"success": False}), 400

# Webhook Verification (GET)
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    verify_token = os.getenv('FB_VERIFY_TOKEN')

    if mode == 'subscribe' and token == verify_token:
        print("Webhook verified successfully!")
        return challenge, 200
    return "Forbidden", 403

# Webhook Event Processing (POST)
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.get_json() or {}

    if data.get('object') == 'page':
        # Check if Automation is turned ON
        if not client_config['isActive']:
            print("Automation is turned OFF. Skipping webhook.")
            return "EVENT_RECEIVED", 200

        for entry in data.get('entry', []):
            messaging_list = entry.get('messaging', [])
            for messaging_event in messaging_list:
                # Ignore echo messages (bot's own replies)
                if 'message' in messaging_event and not messaging_event['message'].get('is_echo'):
                    sender_psid = messaging_event['sender']['id']
                    user_message = messaging_event['message'].get('text')

                    if user_message:
                        print(f"Received message: '{user_message}' from PSID: {sender_psid}")
                        
                        # 1. Generate AI Response
                        ai_reply = generate_ai_reply(user_message)
                        
                        # 2. Send back to FB Messenger
                        send_messenger_message(sender_psid, ai_reply)

        return "EVENT_RECEIVED", 200
    return "Not Found", 404

if __name__ == '__main__':
    port = int(os.getenv('PORT', 3000))
    app.run(host='0.0.0.0', port=port)