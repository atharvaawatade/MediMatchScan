from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from openai import OpenAI
import os
import base64
from io import BytesIO
from PIL import Image
import time
import uuid
import csv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiohttp
import asyncio
import PyPDF2
import docx
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Configuration
MONGODB_URI = os.environ.get('MONGODB_URI')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
PIXTRAL_API_KEY = os.environ.get('PIXTRAL_API_KEY')
EMAIL_USER = os.environ.get('EMAIL_USER')
EMAIL_PASS = os.environ.get('EMAIL_PASS')
SMTP_HOST = 'smtp.gmail.com'
SMTP_PORT = 587

# Initialize these outside of any route to avoid cold start issues
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client['bajaj']
collection = db['client']
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# Use ThreadPoolExecutor for concurrent operations
executor = ThreadPoolExecutor(max_workers=5)

def encode_image(img):
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def save_to_csv(filename, diagnosis):
    csv_file = '/tmp/diagnoses.csv'
    file_exists = os.path.isfile(csv_file)
    
    with open(csv_file, 'a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(['file name', 'Provisional diagnosis'])
        writer.writerow([filename, diagnosis])

def extract_diagnosis_gpt(pixtral_response):
    try:
        completion = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical assistant. Extract the provisional diagnosis from the following text. Provide only the diagnosis without any additional text."},
                {"role": "user", "content": f"Extract the provisional diagnosis from this text: {pixtral_response}"}
            ],
            timeout=10  # Reduced timeout
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

def extract_text_from_file(file):
    filename = file.filename
    content = file.read()
    file.seek(0)  # Reset file pointer

    if filename.endswith('.pdf'):
        return extract_text_from_pdf(BytesIO(content))
    elif filename.endswith('.docx'):
        return extract_text_from_docx(BytesIO(content))
    elif filename.endswith('.csv'):
        return extract_text_from_csv(BytesIO(content))
    elif filename.endswith(('.png', '.jpg', '.jpeg')):
        return None  # Image files don't need text extraction
    else:
        return "Unsupported file type"

def extract_text_from_pdf(file_stream):
    pdf_reader = PyPDF2.PdfReader(file_stream)
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text()
    return text

def extract_text_from_docx(file_stream):
    doc = docx.Document(file_stream)
    return "\n".join([para.text for para in doc.paragraphs])

def extract_text_from_csv(file_stream):
    df = pd.read_csv(file_stream)
    return df.to_string()

async def async_chat_with_pixtral(file_content, mrn_number, user_question, filename):
    api = "https://api.hyperbolic.xyz/v1/chat/completions"
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {PIXTRAL_API_KEY}",
    }

    text_content = await asyncio.to_thread(extract_text_from_file, file_content)
    
    if text_content is None:  # It's an image file
        img = Image.open(file_content)
        base64_img = encode_image(img)
        content = [
            {"type": "text", "text": user_question},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_img}"}}
        ]
    else:
        content = [
            {"type": "text", "text": f"{user_question}\n\nDocument content:\n{text_content}"}
        ]

    payload = {
        "messages": [{"role": "user", "content": content}],
        "model": "mistralai/Pixtral-12B-2409",
        "max_tokens": 2048,
        "temperature": 0.7,
        "top_p": 0.9,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(api, headers=headers, json=payload, timeout=30) as response:
                if response.status == 200:
                    response_data = await response.json()
                    if 'choices' in response_data:
                        assistant_response = response_data['choices'][0]['message']['content']
                        provisional_diagnosis = await asyncio.to_thread(extract_diagnosis_gpt, assistant_response)
                    else:
                        assistant_response = "Response format is incorrect"
                        provisional_diagnosis = "Response format is incorrect"
                else:
                    assistant_response = f"API request failed: {response.status} - {await response.text()}"
                    provisional_diagnosis = "API request failed"
    except asyncio.TimeoutError:
        assistant_response = "Request timed out"
        provisional_diagnosis = "Request timed out"
    except Exception as e:
        assistant_response = f"An error occurred: {str(e)}"
        provisional_diagnosis = "Error occurred"

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
        await asyncio.to_thread(collection.insert_one, document)
        await asyncio.to_thread(save_to_csv, filename, provisional_diagnosis)
    except Exception as e:
        print(f"Error saving to database or CSV: {str(e)}")

    return assistant_response, provisional_diagnosis

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
async def chat():
    file = request.files['file']
    mrn_number = request.form['mrn_number']
    user_question = request.form['user_question']
    filename = file.filename

    response, diagnosis = await async_chat_with_pixtral(file, mrn_number, user_question, filename)

    return jsonify({'response': response, 'diagnosis': diagnosis})

@app.route('/send_email', methods=['POST'])
async def send_email_route():
    data = request.json
    to_email = data.get('email')
    ocr_result = data.get('ocr_result')
    diagnosis = data.get('diagnosis')

    success = await asyncio.to_thread(send_email, to_email, ocr_result, diagnosis)

    if success:
        return jsonify({'success': True, 'message': 'Email sent successfully'})
    else:
        return jsonify({'success': False, 'message': 'Failed to send email'})

if __name__ == '__main__':
    app.run(debug=True)
