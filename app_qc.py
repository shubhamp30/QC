import datetime
import json
import logging
import os
import re
import requests
from flask import Flask, request
from retrying import retry
from fuzzywuzzy import fuzz

import PyPDF2
import fitz

# Setup logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_get_qc_collection_details?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
RESULTS_API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_process_qc_collection_data?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"

app = Flask(__name__)


@retry(wait_fixed=1500, stop_max_attempt_number=1)
def get_with_retry(link):
    try:
        logging.info(f"Trying to fetch data from {link}")
        return requests.get(link, timeout=3)
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to fetch data from {link}: {e}")
        raise


def normalize_string(value):
    return '' if value is None else str(value).strip().lower()


def transform_data(aggregated_data):
    try:
        return {
            'plaintiff': aggregated_data.get('provider_name'),
            'defendant': aggregated_data.get('insurer_name'),
            'patientName': aggregated_data.get('patient_name'),
            'dos_s': aggregated_data.get('dos_s'),
            'dos_e': aggregated_data.get('dos_e'),
            'initial_amt': aggregated_data.get('total_cost')  # Rename total_cost to initial_amt
        }
    except Exception as e:
        logging.error(f"Data transformation failed: {e}")
        raise


def calculate_correctness(api_data, extracted_data):
    try:
        correct_count = 0
        new_extr_data = {}
        total_fields = 6
        incorrect_fields = {}

        for key, value in api_data.items():
            if key == "filepath":
                continue  # Skip checking the filepath

            api_value_normalized = normalize_string(value)
            extracted_value_normalized = normalize_string(extracted_data.get(key, "N/A"))

            # Use fuzzy matching for names and strict matching for dates and amounts
            if key in ["plaintiff", "defendant", "patientName"]:
                api_value_normalized = re.sub(r'[^a-zA-Z\s]', '', api_value_normalized).strip().lower()
                extracted_value_normalized = re.sub(r'[^a-zA-Z\s]', '', extracted_value_normalized).strip().lower()
                if fuzz.token_set_ratio(api_value_normalized, extracted_value_normalized) >= 70:
                    correct_count += 1
                    new_extr_data[key] = extracted_value_normalized
                else:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")
            else:
                if api_value_normalized == extracted_value_normalized:
                    correct_count += 1
                    new_extr_data[key] = extracted_value_normalized
                else:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")

        correctness_percentage = (correct_count / total_fields) * 100
        logging.debug(f"Calculated correctness: {correctness_percentage}%")
        return correctness_percentage, new_extr_data, incorrect_fields
    except Exception as e:
        logging.error(f"Error calculating correctness: {e}")
        raise


@app.route("/quality_check", methods=['GET'])
def quality_check():
    inpkey = request.args.get("key")
    if inpkey == "VmxxcWJ3OWd4TnlWTEozSHA3YUlwNzh1WWhSVVY0WVFGQXlFczFLOGV1Yw==":
        try:
            data = {
                "800842": {
                    "filepath": "/path/to/file.pdf",
                    "plaintiff": "Wesley Diversified Chiropractic P.C.*",
                    "defendant": "GEICO",
                    "patientName": "MICHAEL THOMPSON",
                    "dos_s": "02/02/2024",
                    "dos_e": "02/02/2024",
                    "initial_amt": "418.30"
                }
            }
            for case_id, record in data.items():
                file_url = record.get("filepath")
                if file_url:
                    # Here, you would call your PDF processing functions
                    extracted_data = {}  # Example: Extract data from the PDF
                    correctness_percentage, new_extr_data, incorrect_fields = calculate_correctness(record,
                                                                                                    extracted_data)
                    result = {
                        "case_id": case_id,
                        "filepath": record['filepath'],
                        "status": 1 if correctness_percentage >= 95 else 0,
                        "percentage": str(correctness_percentage).split(".")[0],
                        "api_response": record,
                        "ocr_response": new_extr_data
                    }
                    if correctness_percentage < 85:
                        result["incorrect_fields"] = incorrect_fields
                    logging.info(f"Result for case {case_id}: {result}")
        except Exception as e:
            logging.error(f"An error occurred during quality check: {e}")
    else:
        logging.warning("Invalid access attempt detected")
    return "Quality check complete."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5021, debug=True)
