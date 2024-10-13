import base64
import os
import csv
import time
import uuid
import logging
import re
from io import BytesIO
from PIL import Image
from flask import Flask, request, jsonify, render_template
from pymongo import MongoClient
from dotenv import load_dotenv
import requests
from openai import OpenAI

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

# OpenAI API setup
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
client = OpenAI(api_key=OPENAI_API_KEY)

def normalize_text(text):
    # Remove extra whitespace and newlines
    text = re.sub(r'\s+', ' ', text).strip()
    # Convert to lowercase
    text = text.lower()
    # Remove any special characters except alphanumeric and spaces
    text = re.sub(r'[^a-z0-9\s]', '', text)
    return text

def extract_provisional_diagnosis_from_text(text):
    normalized_text = normalize_text(text)
    
    prompt = f"""Extract the provisional diagnosis from the following normalized text. If there's no clear diagnosis, respond with 'No clear diagnosis found'. Only provide the extracted diagnosis without any additional explanation.

Text: {normalized_text}

def encode_image(img):
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")

def save_to_csv(filename, extracted_output, corrected_output, icd_code):
    try:
        csv_file = os.path.join(os.getcwd(), 'output.csv')
        file_exists = os.path.isfile(csv_file)

        with open(csv_file, 'a', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(['file_name', 'extracted_output', 'corrected_output', 'icd_code'])

            writer.writerow([filename, extracted_output, corrected_output, icd_code])
            logging.info(f"Data successfully saved to {csv_file}: {filename}, {extracted_output}, {corrected_output}, {icd_code}")

    except Exception as e:
        logging.error(f"Error saving to CSV: {str(e)}")

def extract_provisional_diagnosis(text):
    diag_match = re.search(r'Provisional diagnosis:\s*(.*?)(?:\.|$)', text, re.IGNORECASE | re.DOTALL)
    if diag_match:
        diagnosis = diag_match.group(1).strip()
        logging.debug(f"Diagnosis extracted using regex: {diagnosis}")
        return diagnosis, True
    
    logging.debug("Regex extraction failed, sending full text to GPT for diagnosis extraction.")
    prompt = f"""Extract the provisional diagnosis from the following text. If there's no clear diagnosis, respond with 'No clear diagnosis found'. Only provide the extracted diagnosis without any additional explanation.

Text: {text}

Extracted diagnosis:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical expert tasked with extracting diagnoses from medical texts."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50
        )
        
        extracted_diagnosis = response.choices[0].message.content.strip()
        logging.debug(f"GPT response: {extracted_diagnosis}")
        
        if extracted_diagnosis.lower() == "no clear diagnosis found":
            logging.debug("No diagnosis found via GPT.")
            return text, False
        else:
            return extracted_diagnosis, True
    except Exception as e:
        logging.error(f"Error in GPT extraction: {str(e)}")
        return text, False

def get_icd_code(diagnosis):
    prompt = f"""As a medical coding expert, provide the most appropriate ICD-10 code for the following diagnosis:

"{diagnosis}"

Guidelines:
1. If an exact ICD-10 code exists for the diagnosis, provide it.
2. If no exact code exists, provide the most relevant code.
3. Include only the ICD-10 code without any additional explanation.
4. If multiple codes are applicable, provide the most specific one.
5. If no appropriate code can be determined, respond with "No specific ICD code found".

ICD-10 code:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical coding expert specialized in ICD-10 codes."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=20
        )
        
        icd_code = response.choices[0].message.content.strip()
        logging.info(f"ICD code retrieved for '{diagnosis}': {icd_code}")
        return icd_code
    except Exception as e:
        logging.error(f"Error in get_icd_code: {str(e)}")
        return "Error: Unable to retrieve ICD code"

def enhance_diagnosis(extracted_output):
    prompt = f"""As a medical spell-checker and corrector, improve this diagnosis:

"{extracted_output}"

Guidelines:
1. Correct all spelling errors, including medical terms.
2. Remove any references to ICD codes or irrelevant information.
3. Replace commonly misspelled words with their correct forms (e.g., "CASTROLS" to "CATARACT", "BROLON" to "Brown" "EYR" to "EYE", "NIVER" to "LIVER").
4. Ensure anatomical directions are spelled correctly (e.g., "RIGHT", "LEFT").
5. Expand abbreviations if they are clear (e.g., "RE" to "RIGHT EYE").
6. Maintain the original structure and intent of the diagnosis.
7. If the diagnosis is already correct, return it unchanged, if it's written in some code then reply directly with those words, do not add the full form or anything extra (e.g., CF, CKD, etc.).
8. Provide only the corrected diagnosis without any additional text or explanations.
9. Do not give any additional suggestions or content, just the diagnosis.

Corrected diagnosis:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical expert tasked with correcting and improving medical diagnoses."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100
        )
        
        enhanced_output = response.choices[0].message.content.strip()
        
        if enhanced_output.lower() != extracted_output.lower():
            logging.info(f"Output changed from '{extracted_output}' to '{enhanced_output}'")
        else:
            logging.info(f"Output unchanged: '{extracted_output}'")
        
        # Get ICD code for the enhanced diagnosis
        icd_code = get_icd_code(enhanced_output)
        
        return enhanced_output, icd_code
    except Exception as e:
        logging.error(f"Error in enhance_diagnosis: {str(e)}")
        return "Error: Unable to enhance diagnosis", "Error: Unable to retrieve ICD code"

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
        logging.debug(f"Extracted text from OCR: {extracted_text}")
        
        extracted_diagnosis, found = extract_provisional_diagnosis(extracted_text)
        
        if found:
            corrected_diagnosis, icd_code = enhance_diagnosis(extracted_diagnosis)
            logging.debug("Diagnosis was found, enhanced, and ICD code retrieved.")
        else:
            logging.debug("No diagnosis found; sending full text to GPT for enhancement and ICD code retrieval.")
            corrected_diagnosis, icd_code = enhance_diagnosis(extracted_text)
            extracted_diagnosis = extracted_text
        
        # Save to CSV
        save_to_csv(filename, extracted_diagnosis, corrected_diagnosis, icd_code)

        if save_data and mrn_number:
            unique_id = str(uuid.uuid4())
            document = {
                'mrn_number': mrn_number,
                'extracted_provisional_diagnosis': extracted_diagnosis,
                'corrected_provisional_diagnosis': corrected_diagnosis,
                'icd_code': icd_code,
                'unique_id': unique_id,
                'got_mode': "API OCR + GPT",
                'timestamp': time.time()
            }
            result = collection.insert_one(document)
            logging.info(f"Document inserted with ID: {result.inserted_id}")

        return extracted_text, extracted_diagnosis, corrected_diagnosis, icd_code
    except Exception as e:
        logging.error(f"Error in process_image: {str(e)}")
        return "", "", "", ""

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
        extracted_text, extracted_diagnosis, corrected_diagnosis, icd_code = process_image(img, mrn_number, filename, save_data)
        
        response = {
            'extracted_ocr_text': extracted_text,
            'extracted_provisional_diagnosis': extracted_diagnosis,
            'corrected_provisional_diagnosis': corrected_diagnosis,
            'icd_code': icd_code
        }
        
        logging.info(f"Scan response: {response}")
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error in scan route: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'up'})

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
            
            extracted_text, extracted_diagnosis, corrected_diagnosis, icd_code = process_image(img, None, image_file.filename, False)
            
            response = {
                'extracted_ocr_text': extracted_text,
                'extracted_provisional_diagnosis': extracted_diagnosis,
                'corrected_provisional_diagnosis': corrected_diagnosis,
                'icd_code': icd_code
            }
            
            return jsonify(response)
        except Exception as e:
            logging.error(f"Error processing image: {str(e)}")
            return jsonify({'error': 'Error processing image'}), 500

def extract_provisional_diagnosis_from_text(text):
    normalized_text = normalize_text(text)
    
    prompt = f"""Extract the provisional diagnosis from the following normalized text. If there's no clear diagnosis, respond with 'No clear diagnosis found'. Only provide the extracted diagnosis without any additional explanation.

Text: {normalized_text}

Extracted diagnosis:"""

    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a medical expert tasked with extracting diagnoses from medical texts."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=50
        )
        
        extracted_diagnosis = response.choices[0].message.content.strip()
        logging.debug(f"GPT response for text input: {extracted_diagnosis}")
        
        if extracted_diagnosis.lower() == "no clear diagnosis found":
            logging.debug("No diagnosis found via GPT for text input.")
            return normalized_text, False
        else:
            return extracted_diagnosis, True
    except Exception as e:
        logging.error(f"Error in GPT extraction for text input: {str(e)}")
        return normalized_text, False

@app.route('/api/process_text', methods=['POST'])
def process_text():
    try:
        data = request.json
        if 'text' not in data:
            return jsonify({'error': 'No text provided'}), 400
        
        input_text = data['text']
        mrn_number = data.get('mrn_number', '')
        save_data = data.get('save_data', False)
        
        logging.info(f"Received text processing request. MRN: {mrn_number}, Save Data: {save_data}")
        
        extracted_diagnosis, found = extract_provisional_diagnosis_from_text(input_text)
        
        if found:
            corrected_diagnosis, icd_code = enhance_diagnosis(extracted_diagnosis)
            logging.debug("Diagnosis was found, enhanced, and ICD code retrieved from text input.")
        else:
            logging.debug("No diagnosis found; using full text for enhancement and ICD code retrieval.")
            corrected_diagnosis, icd_code = enhance_diagnosis(input_text)
            extracted_diagnosis = input_text
        
        # Save to CSV
        save_to_csv("text_input", extracted_diagnosis, corrected_diagnosis, icd_code)
        
        if save_data and mrn_number:
            unique_id = str(uuid.uuid4())
            document = {
                'mrn_number': mrn_number,
                'extracted_provisional_diagnosis': extracted_diagnosis,
                'corrected_provisional_diagnosis': corrected_diagnosis,
                'icd_code': icd_code,
                'unique_id': unique_id,
                'got_mode': "Text Input + GPT",
                'timestamp': time.time()
            }
            result = collection.insert_one(document)
            logging.info(f"Document inserted with ID: {result.inserted_id}")
        
        response = {
            'extracted_provisional_diagnosis': extracted_diagnosis,
            'corrected_provisional_diagnosis': corrected_diagnosis,
            'icd_code': icd_code
        }
        
        logging.info(f"Text processing response: {response}")
        return jsonify(response)
    except Exception as e:
        logging.error(f"Error in process_text route: {str(e)}")
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
