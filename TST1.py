import base64
import os
import tempfile
import time
from urllib.parse import urlparse
import requests
import PyPDF2
from flask import Flask, request
import os
import fitz
import requests
from flask import Flask, render_template
from flask import request
from flask_sock import Sock
from retrying import retry
from QC.OCRv4.OCRv4.flask_ocr_app.models.MainOCR_12tst import MainOCR
import datetime
import re
import json
from fuzzywuzzy import fuzz

debug = True
RESULTS_API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_process_qc_collection_data?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_get_qc_collection_details?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"


@retry(wait_fixed=1500, stop_max_attempt_number=1)
def get_with_retry(link):
    print("trying ..")
    return requests.get(link, timeout=3)


app = Flask(__name__)


def normalize_string(value):
    """Normalize a string by converting it to lowercase and stripping leading/trailing whitespace."""
    return '' if value is None else str(value).strip().lower()


def transform_data(aggregated_data):
    try:
        transformed_data = {
            'plaintiff': aggregated_data.get('provider_name'),
            'defendant': aggregated_data.get('insurer_name'),
            'patientName': aggregated_data.get('patient_name'),
            'initial_amt': aggregated_data.get('total_cost'),  # Rename total_cost to initial_amt
            'dos_s': aggregated_data.get('dos_s'),
            'dos_e': aggregated_data.get('dos_e')
        }
        return transformed_data
    except Exception as e:
        print(e)


def normalize_alpha_only(value):
    """Normalize a string by removing non-alphabetic characters (except spaces), converting to lowercase, and stripping whitespace."""
    if value is None:
        return ''
    value = re.sub(r'[^a-zA-Z\s]', '', value)
    # print("cleaned value is: ", value)
    return value.strip().lower()


def clean_date(date_str):
    try:
        """Standardize date format to YYYY-MM-DD for comparison, supporting multiple date formats."""
        date_formats = [
            '%m/%d/%Y', '%m/%d/%y',  # Month/Day/Year with slashes
            '%d/%m/%Y', '%d/%m/%y',  # Day/Month/Year with slashes
            '%Y/%m/%d',  # Year/Month/Day with slashes
            '%m-%d-%Y', '%m-%d-%y',  # Month/Day/Year with dashes
            '%d-%m-%Y', '%d-%m-%y',  # Day/Month/Year with dashes
            '%Y-%m-%d',  # Year/Month/Day with dashes
            '%m.%d.%Y', '%m.%d.%y',  # Month/Day/Year with dots
            '%d.%m.%Y', '%d.%m.%y',  # Day/Month/Year with dots
            '%Y.%m.%d'  # Year/Month/Day with dots
        ]

        for date_format in date_formats:
            try:
                cleaned_date = datetime.datetime.strptime(date_str.strip(), date_format).strftime('%Y-%m-%d')
                return cleaned_date
            except ValueError:
                continue

        print("Failed to clean date, format not recognized.")
        return date_str
    except Exception as e:
        print("Error while formatting date: ", e)
        return date_str


def soft_match(api_value, extracted_value):
    try:
        print("comparing data is : ", api_value, "--", extracted_value)

        # Token set ratio compares the sets of tokens in both strings and accounts for similar tokens in any order
        match_score = fuzz.token_set_ratio(api_value, extracted_value)
        print("match score for extracted data: ", match_score, "comparing with: threshold 70 ")
        return match_score >= 70  # You can adjust the threshold based on your requirements
    except Exception as e:
        print(e)


def calculate_correctness(api_data, extracted_data):
    try:
        correct_count = 0
        total_fields = len(api_data) - (
            1 if 'filepath' in api_data else 0)  # Exclude 'filepath' from the total fields count.
        incorrect_fields = {}

        for key, value in api_data.items():
            if key == "filepath":
                continue  # Skip checking the filepath

            api_value_normalized = normalize_string(value)
            extracted_value_normalized = normalize_string(extracted_data.get(key, "N/A"))

            # Handling for specific fields with softer matching criteria
            if key in ["plaintiff", "defendant", "patientName"]:
                if key not in ["patientName"]:
                    api_value_normalized = normalize_alpha_only(api_value_normalized)[:7]
                    extracted_value_normalized = normalize_alpha_only(extracted_value_normalized)[:7]
                api_value_normalized = normalize_alpha_only(api_value_normalized)
                extracted_value_normalized = normalize_alpha_only(extracted_value_normalized)
                if soft_match(api_value_normalized, extracted_value_normalized):
                    print("adding correct count: ", correct_count)
                    correct_count += 1
                else:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")


            elif key in ["dos_s", "dos_e"]:  # Strict date comparison after normalization
                api_value_normalized = clean_date(api_value_normalized)
                extracted_value_normalized = clean_date(extracted_value_normalized)
                if api_value_normalized == extracted_value_normalized:
                    print("adding correct count: ", correct_count)
                    correct_count += 1
                else:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")


            elif key == "initial_amt":  # Numerical comparison
                try:
                    if float(api_value_normalized) == float(extracted_value_normalized):
                        print("adding correct count: ", correct_count)
                        correct_count += 1
                    else:
                        incorrect_fields[key] = extracted_data.get(key, "N/A")
                except ValueError:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")
            else:  # Default strict comparison
                if api_value_normalized == extracted_value_normalized:
                    print("adding correct count: ", correct_count)
                    correct_count += 1
                else:
                    incorrect_fields[key] = extracted_data.get(key, "N/A")

        correctness_percentage = (correct_count / total_fields) * 100
        return correctness_percentage, incorrect_fields
    except Exception as e:
        print(e)


def extract_data_from_text(output):
    try:
        # Extract data blocks
        if debug:
            print("processing output: ")
        data_blocks = [block for block in output]
        if debug:
            print("got the data blocks")

        # Initialize data for aggregation
        total_cost = 0
        providers = set()
        insurers = set()
        patients = set()
        date_ranges = []

        if debug: print(
            "####/home/neural/PycharmProjects/automation_AAA/Answers_suffolk/live/2024-05-03/splitted_pdfs_05-03-24%20answers%20batch%203/119-138_05-03-24%20answers%20batch%203_006.pdf###")
        if debug: print(f"Data blocks = {data_blocks}")
        if debug: print("###############################################################")

        # Aggregate data
        for i in range(len(data_blocks)):
            if 'provider_name' in data_blocks[i]['out_data']:
                if i == 0:
                    providers.add(data_blocks[i]['out_data']['provider_name'])
                    print("all provider values", providers)
            if 'insurer_name' in data_blocks[i]['out_data']:
                if i == 0:
                    insurers.add(data_blocks[i]['out_data']['insurer_name'])
                    print("all insurers values", insurers)
            if 'patient_name' in data_blocks[i]['out_data']:
                if i == 0:
                    patients.add(data_blocks[i]['out_data']['patient_name'])
                    print("all patients values", patients)
            if 'cost' in data_blocks[i]['out_data']:
                total_cost += float(data_blocks[i]['out_data']['cost']) if data_blocks[i]['out_data']['cost'] else 0
            if 'date_of_service' in data_blocks[i]['out_data']:
                # Handle multiple date formats potentially split by "-"
                date_ranges.extend(data_blocks[i]['out_data']['date_of_service'].split('-'))

        # Convert string dates to date objects
        date_objects = [datetime.datetime.strptime(date.strip(), "%m/%d/%Y") for date in date_ranges]

        # Determine the minimum and maximum dates
        min_date = min(date_objects).strftime("%m/%d/%Y") if date_objects else None
        max_date = max(date_objects).strftime("%m/%d/%Y") if date_objects else None

        # Prepare final aggregated data
        aggregated_data = {
            'provider_name': next(iter(providers)) if providers else None,
            'insurer_name': next(iter(insurers)) if insurers else None,
            'patient_name': next(iter(patients)) if patients else None,
            'dos_s': min_date if min_date else None,
            'dos_e': max_date if max_date else None,
            'total_cost': format(total_cost, '.2f') if total_cost else None
        }

        return aggregated_data
    except Exception as e:
        print(e)


def download_and_split_pdf(file_url):
    try:
        print("herer")
        response = get_with_retry(file_url)
        print("here")
        file_name = file_url.split('/')[-1]
        with open(file_name, 'wb') as file:
            file.write(response.content)
    except Exception as e:
        print("trying ", file_url)
        file_name = file_url
    print("openeing ")
    try:
        with open(file_name, 'rb') as file:
            print("opened")
            reader = PyPDF2.PdfReader(file)
            print("len", len(reader.pages))
            final = list()
            out = None
            type_1_found = 0
            type_2_found = 0
            for page_num in range(len(reader.pages)):
                print("opened ", page_num)
                writer = PyPDF2.PdfWriter()
                writer.add_page(reader.pages[page_num])

                output_filename = f"{file_name[:-4]}_page_{page_num + 1}.pdf"
                with open(output_filename, 'wb') as output_file:
                    writer.write(output_file)
                # todo
                file_url = output_filename
                image_id = page_num
                process_type = "collection"
                currDir = os.path.abspath(os.curdir) + "/"
                mainOcr = MainOCR(currDir, file_url, image_id, process_type)
                out = mainOcr.startProcess()
                if out:
                    for i in out.keys():
                        if "type" == str(i) and str(out[i]) == str(1):
                            final.append(out)
                            type_1_found = 1
                        elif "type" == str(i) and str(out[i]) == str(2):
                            final.append(out)
                            type_2_found = 1
                    # print("out: ", out)
                # process_pdf(output_filename)
            print("final : ", final)
        return final
    except Exception as e:
        print("error processing file: ", e)

    # os.remove(file_name)  # Clean up downloaded file


@app.route("/quality_check", methods=['GET'])
def aaa_fetch_data():
    failed_files = dict()
    # print(f"key{inpkey},ticket_id {start_ticket_id}")
    key = "fca88adc8578dd3c2be^bcfafe7387df3d57124e286e$1ef17199f9517c5fdc9ad<72583335b5104c8f04ad4ce7d63efd0a4aafff3a6189ba5802f88e7727919fde"
    inpkey = request.args.get("key")
    print(f'found {inpkey} {type(inpkey)}')
    print(f'{key} {type(key)}')

    if str(key) == str(inpkey):
        try:

            url = API_URL
            data = requests.get(url)
            data = data.json()
            # data = {"800842": {
            #     "filepath": "/home/neural/Documents/Sample_docs/GM_Templates/collections_new_formats/REFUA RX.PDF",
            #     'plaintiff': 'Refua Rx Inc', 'defendant': 'State Farm Insurance Company',
            #     'patientName': 'Carlos I Vicioso', 'initial_amt': '1801.10', 'dos_s': '01/19/2024',
            #     'dos_e': '01/22/2024'
            # }}
            for case_id, record in data.items():
                try:
                    file_url = record.get("filepath")
                    if file_url:
                        final_out = download_and_split_pdf(file_url)
                        datap = extract_data_from_text(final_out)
                        print("raw data: ", datap)
                        transformed_data = transform_data(datap)
                        print("final out data aggregated: ", transformed_data)
                        print("record data: ", record)
                        correctness_percentage, incorrect_fields = calculate_correctness(record, transformed_data)
                        result = {
                            "case_id": case_id,
                            "filepath": record['filepath'],
                            "status": 1 if correctness_percentage >= 95 else 0,
                            "percentage": correctness_percentage
                        }
                        # if correctness_percentage < 85:
                        #     result["incorrect_fields"] = incorrect_fields
                        if True:
                            del record['filepath']
                            result["api_response"] = record
                            result["ocr_response"] = transformed_data

                        print("final res: ", result)

                        # Post the result for this case
                        post_response = requests.post(RESULTS_API_URL, json=[result])
                        print("post response: ", post_response.status_code)
                        if post_response.status_code != 200:
                            print(f"Failed to post results for case ID {case_id}")
                except Exception as e:
                    print(f"An error occurred while processing case ID {case_id}: {e}")
        except Exception as e:
            print(f"An error occurred : {e}")

        return "None"


app.run(host="0.0.0.0", port=5021, debug=True)
