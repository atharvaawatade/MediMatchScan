import base64
import os
import csv
import time
import uuid
import logging
import re
import asyncio
from io import BytesIO
from PIL import Image
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from dotenv import load_dotenv
import requests
from huggingface_hub import InferenceClient

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Load environment variables
load_dotenv()

app = Flask(__name__, template_folder="../templates")

# MongoDB setup
mongo_client = MongoClient(os.environ.get('MONGODB_URI'))
db = mongo_client['bajaj']
collection = db['client']

# API endpoints
OCR_API_URL = "https://real-incredibly-snapper.ngrok-free.app/api/extract_text"

# Hugging Face API setup
HF_API_KEY = os.environ.get('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')
HF_MODEL = "meta-llama/Llama-3.2-3B-Instruct"
inference_client = InferenceClient(api_key=HF_API_KEY)

def encode_image(img):
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def save_to_csv(filename, extracted_output, corrected_output):
    try:
        csv_file = os.path.join(os.getcwd(), 'output.csv')
        file_exists = os.path.isfile(csv_file)

        with open(csv_file, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['file_name', 'extracted_output', 'corrected_output'])

            writer.writerow([filename, extracted_output, corrected_output])
            logging.info(f"Data successfully saved to {csv_file}: {filename}, {extracted_output}, {corrected_output}")

    except Exception as e:
        logging.error(f"Error saving to CSV: {str(e)}")

def extract_provisional_diagnosis(text):
    diag_match = re.search(r'Provisional diagnosis:\s*(.*?)(?:\.|$)', text, re.IGNORECASE | re.DOTALL)
    if diag_match:
        return diag_match.group(1).strip()
    
    prompt = f"""Extract the provisional diagnosis from the following text. If there's no clear diagnosis, respond with 'No clear diagnosis found'. Only provide the extracted diagnosis without any additional explanation.

Text: {text}

Extracted diagnosis:"""

    messages = [{"role": "user", "content": prompt}]
    
    try:
        response = ""
        for message in inference_client.chat_completion(
            model=HF_MODEL,
            messages=messages,
            max_tokens=50,
            stream=True,
        ):
            response += message.choices[0].delta.content or ""
        
        extracted_diagnosis = ' '.join(response.strip().split())
        return extracted_diagnosis if extracted_diagnosis else "No clear diagnosis found"
    except Exception as e:
        logging.error(f"Error in LLaMA extraction: {str(e)}")
        return "No clear diagnosis found"

def enhance_diagnosis(extracted_diagnosis):
    prompt = f"""As a medical spell-checker and corrector, improve this diagnosis:

"{extracted_diagnosis}"

Guidelines:
1. Correct all spelling errors, including medical terms.
2. Remove any references to ICD codes or irrelevant information.
3. Replace commonly misspelled words with their correct forms (e.g., "CASTROLS" to "CATARACT", "EYR" to "EYE", "NIVER" to "LIVER").
4. Ensure anatomical directions are spelled correctly (e.g., "RIGHT", "LEFT").
5. Expand abbreviations if they are clear (e.g., "RE" to "RIGHT EYE").
6. Maintain the original structure and intent of the diagnosis.
7. If the diagnosis is already correct, return it unchanged.
8. Provide only the corrected diagnosis without any additional text or explanations.
9. Do not give any additional suggetions or content just diagnosis.

Corrected diagnosis:"""

    messages = [{"role": "user", "content": prompt}]
    
    try:
        response = ""
        for message in inference_client.chat_completion(
            model=HF_MODEL,
            messages=messages,
            max_tokens=50,
            stream=True,
        ):
            response += message.choices[0].delta.content or ""
        
        enhanced_diagnosis = ' '.join(response.strip().split())
        
        if enhanced_diagnosis.lower() != extracted_diagnosis.lower():
            logging.info(f"Diagnosis changed from '{extracted_diagnosis}' to '{enhanced_diagnosis}'")
        else:
            logging.info(f"Diagnosis unchanged: '{extracted_diagnosis}'")
        
        return enhanced_diagnosis
    except Exception as e:
        logging.error(f"Error in enhance_diagnosis: {str(e)}")
        return "Error: Unable to enhance diagnosis"

def process_image(img, mrn_number, filename, save_data):
    try:
        img_byte_arr = BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()

        files = {"file": ('image.png', img_byte_arr, 'image/png')}
        logging.info(f"Sending request to OCR API: {OCR_API_URL}")
        response = requests.post(OCR_API_URL, files=files)
        
        if response.status_code != 200:
            raise Exception(f"OCR API request failed with status code {response.status_code}")

        api_result = response.json()
        extracted_text = api_result.get('extracted_text', '')
        extracted_diagnosis = extract_provisional_diagnosis(extracted_text)
        corrected_diagnosis = enhance_diagnosis(extracted_diagnosis)

        save_to_csv(filename, extracted_diagnosis, corrected_diagnosis)

        if save_data and mrn_number:
            unique_id = str(uuid.uuid4())
            document = {
                'mrn_number': mrn_number,
                'extracted_diagnosis': extracted_diagnosis,
                'corrected_diagnosis': corrected_diagnosis,
                'unique_id': unique_id,
                'got_mode': "API OCR + LLaMA",
                'timestamp': time.time()
            }
            result = collection.insert_one(document)
            logging.info(f"Document inserted with ID: {result.inserted_id}")

        return extracted_diagnosis, corrected_diagnosis
    except Exception as e:
        logging.error(f"Error in process_image: {str(e)}")
        return "", ""

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scan', methods=['POST'])
def scan():
    try:
        image = request.files['image']
        mrn_number = request.form.get('mrn_number', '')
        save_data = request.form.get('save_data', 'false').lower() == 'true'
        filename = image.filename

        logging.info(f"Received scan request. MRN: {mrn_number}, Save Data: {save_data}")

        img = Image.open(image)
        extracted_diagnosis, corrected_diagnosis = process_image(img, mrn_number, filename, save_data)
        
        response = {
            'extracted_provisional_diagnosis': extracted_diagnosis,
            'corrected_provisional_diagnosis': corrected_diagnosis
        }
        
        logging.info(f"Scan response: {response}")
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error in scan route: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'message': 'Server is running fine'})

@app.route('/api/test', methods=['POST'])
def test_api():
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400
    
    image_file = request.files['image']
    
    if image_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    if image_file:
        try:
            img_bytes = image_file.read()
            img = Image.open(BytesIO(img_bytes))
            
            extracted_diagnosis, corrected_diagnosis = process_image(img, None, image_file.filename, False)
            
            response = {
                'extracted_provisional_diagnosis': extracted_diagnosis,
                'corrected_provisional_diagnosis': corrected_diagnosis
            }
            
            return jsonify(response)
        except Exception as e:
            logging.error(f"Error processing image: {str(e)}")
            return jsonify({'error': 'Error processing image'}), 500

if __name__ == '__main__':
    app.run(debug=True)
