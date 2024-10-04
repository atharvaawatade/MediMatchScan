from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from openai import OpenAI
import os
import base64
import requests
from io import BytesIO
from PIL import Image
import time
import uuid
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Flask application initialization with template folder specified
app = Flask(__name__, template_folder="../templates", static_folder="../static")

# Configuration
MONGODB_URI = os.environ.get('MONGODB_URI')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PIXTRAL_API_KEY = os.environ.get('PIXTRAL_API_KEY')
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587

# MongoDB setup
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['bajaj']
collection = db['client']

# OpenAI API setup
client = OpenAI(api_key=OPENAI_API_KEY)

def encode_image(img):
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def extract_diagnosis_gpt(pixtral_response):
    try:
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical assistant. Extract the provisional diagnosis from the following text. Provide only the diagnosis without any additional text."},
                {"role": "user", "content": f"Extract the provisional diagnosis from this text: {pixtral_response}"}
            ]
        )
        diagnosis = completion.choices[0].message.content.strip()
        return diagnosis if diagnosis else "No provisional diagnosis found"
    except Exception as e:
        print(f"Error in GPT extraction: {str(e)}")
        return "Error in diagnosis extraction"

def send_email(to_email, ocr_result, diagnosis):
    msg = MIMEMultipart()
    msg['From'] = EMAIL_USER
    msg['To'] = to_email
    msg['Subject'] = "Your Prescription Analysis"

    body = f"""
    Dear User,

    Here is the analysis of your prescription:

    OCR Result:
    {ocr_result}

    Provisional Diagnosis:
    {diagnosis}

    Thank you for using our service.

    Best regards,
    Bima Sarthi Team
    """

    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        text = msg.as_string()
        server.sendmail(EMAIL_USER, to_email, text)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

def chat_with_pixtral(base64_img, mrn_number, user_question):
    api = "https://api.hyperbolic.xyz/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PIXTRAL_API_KEY}",
    }

    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_question},  
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"},
                    },
                ],
            }
        ],
        "model": "mistralai/Pixtral-12B-2409",
        "max_tokens": 2048,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    try:
        response = requests.post(api, headers=headers, json=payload, timeout=30)
        response.raise_for_status()  # Raises a HTTPError if the status is 4xx, 5xx
        response_data = response.json()
        if 'choices' in response_data:
            assistant_response = response_data['choices'][0]['message']['content']
            provisional_diagnosis = extract_diagnosis_gpt(assistant_response)
        else:
            assistant_response = "Response format is incorrect"
            provisional_diagnosis = "Response format is incorrect"
    except requests.Timeout:
        assistant_response = "Request timed out"
        provisional_diagnosis = "Request timed out"
    except requests.RequestException as e:
        assistant_response = f"API request failed: {str(e)}"
        provisional_diagnosis = "API request failed"
    except Exception as e:
        assistant_response = f"An error occurred: {str(e)}"
        provisional_diagnosis = "Error occurred during processing"

    unique_id = str(uuid.uuid4())

    document = {
        'mrn_number': mrn_number,
        'ocr_result': assistant_response,
        'provisional_diagnosis': provisional_diagnosis,
        'unique_id': unique_id,
        'got_mode': "plain texts OCR",
        'timestamp': time.time()
    }

    try:
        collection.insert_one(document)
    except Exception as e:
        print(f"Error inserting into MongoDB: {str(e)}")

    return assistant_response, provisional_diagnosis

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    try:
        image = request.files['image']
        mrn_number = request.form['mrn_number']
        user_question = request.form['user_question']

        img = Image.open(image)
        base64_img = encode_image(img)

        response, diagnosis = chat_with_pixtral(base64_img, mrn_number, user_question)
        return jsonify({'response': response, 'diagnosis': diagnosis})
    except Exception as e:
        print(f"Error in chat route: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/send_email', methods=['POST'])
def send_email_route():
    try:
        data = request.json
        to_email = data.get('email')
        ocr_result = data.get('ocr_result')
        diagnosis = data.get('diagnosis')

        if not all([to_email, ocr_result, diagnosis]):
            return jsonify({'success': False, 'message': 'Missing required data'}), 400

        email_sent = send_email(to_email, ocr_result, diagnosis)
        if email_sent:
            return jsonify({'success': True, 'message': 'Email sent successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to send email'}), 500
    except Exception as e:
        print(f"Error in send_email route: {str(e)}")
        return jsonify({'success': False, 'message': f'An error occurred: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True)
