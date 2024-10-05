from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from openai import OpenAI
import os
import base64
import requests
import json
from io import BytesIO
from PIL import Image
import time
import uuid
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import asyncio
from celery import Celery
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Flask application initialization
app = Flask(__name__, template_folder="../templates", static_folder="../static")

# Configuration
MONGODB_URI = os.environ.get('MONGODB_URI')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PIXTRAL_API_KEY = os.environ.get('PIXTRAL_API_KEY')
EMAIL_USER = os.environ.get('EMAIL_USER', 'odop662@gmail.com')
EMAIL_PASS = os.environ.get('EMAIL_PASS', 'zykvuppkoznmpgzn')

# Celery Configuration
app.config['CELERY_BROKER_URL'] = 'redis://localhost:6379/0'
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:6379/0'

# MongoDB setup
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['bajaj']
collection = db['client']

# OpenAI API setup
client = OpenAI(api_key=OPENAI_API_KEY)

# Initialize Celery
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

def encode_image(img):
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

@celery.task(bind=True)
def async_chat_with_pixtral(self, base64_img, mrn_number, user_question, filename):
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
        response = requests.post(api, headers=headers, json=payload, timeout=90)
        if response.status_code == 200:
            response_data = response.json()
            assistant_response = response_data['choices'][0]['message']['content']
        else:
            assistant_response = f"API request failed: {response.status_code} - {response.text}"
    except Exception as e:
        assistant_response = f"An error occurred: {str(e)}"
    
    unique_id = str(uuid.uuid4())
    diagnosis = "Dummy diagnosis for testing"  # Placeholder for diagnosis

    document = {
        'mrn_number': mrn_number,
        'ocr_result': assistant_response,
        'provisional_diagnosis': diagnosis,
        'unique_id': unique_id,
        'got_mode': "plain texts OCR",
        'timestamp': time.time()
    }

    try:
        collection.insert_one(document)
    except Exception as e:
        print(f"Error saving to database: {str(e)}")

    return {'response': assistant_response, 'diagnosis': diagnosis}

@app.route('/chat', methods=['POST'])
def chat():
    image = request.files['image']
    mrn_number = request.form['mrn_number']
    user_question = request.form['user_question']
    filename = image.filename

    img = Image.open(image)
    base64_img = encode_image(img)

    # Launch the task asynchronously
    task = async_chat_with_pixtral.delay(base64_img, mrn_number, user_question, filename)

    return jsonify({'task_id': task.id, 'message': 'Task started, check status later'})

@app.route('/status/<task_id>')
def task_status(task_id):
    task = async_chat_with_pixtral.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {'state': task.state, 'status': 'Processing...'}
    elif task.state != 'FAILURE':
        response = {'state': task.state, 'status': task.info}
    else:
        response = {'state': task.state, 'status': 'Failed'}
    
    return jsonify(response)

if __name__ == '__main__':
    app.run(debug=True)
