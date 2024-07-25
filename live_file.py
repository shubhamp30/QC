from flask import Flask, request, jsonify
import asyncio
import aiohttp
import os
import fitz  # PyMuPDF
import tempfile
from concurrent.futures import ThreadPoolExecutor
# from flask_ocr_app.models.MainOCR_local import MainOCR
from QC.OCRv4.OCRv4.flask_ocr_app.models.MainOCR_local import MainOCR
import logging
import requests
import datetime
import time
import re
from fuzzywuzzy import fuzz

app = Flask(__name__)
RESULTS_API_URL = ''
# RESULTS_API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_process_qc_collection_data?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
API_URL = "https://gabrielmoroff.com/liberation/functions/rpa_get_qc_collection_details?atoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
debug = True
temp_folder = tempfile.mkdtemp()
logging.basicConfig(level=logging.INFO)
logging.getLogger('pdfminer').setLevel(logging.WARNING)


async def async_download_file(session, case_file):
    case_id, file_url = case_file
    file_name = os.path.join(temp_folder, file_url.split('/')[-1])
    logging.debug(f"Attempting to download file from URL: {file_url}")
    try:
        async with session.get(file_url) as response:
            if response.status == 200:
                content = await response.read()
                with open(file_name, 'wb') as file:
                    file.write(content)
                logging.info(f"Successfully downloaded {file_name}.")
                return case_id, file_name
            else:
                logging.error(f"Failed to download {file_url} with HTTP status {response.status}.")
                return case_id, None
    except Exception as e:
        logging.error(f"Exception during file download {file_url}: {e}")
        return case_id, None


def clean_date(date_str):
    try:
        date_str = str(date_str)
        if debug: print("Date string is: ", date_str)
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
                cleaned_date = datetime.datetime.strptime(date_str.strip(), date_format).strftime('%m/%d/%Y')
                if debug: print("Cleaned Date is: ", cleaned_date)
                return cleaned_date
            except ValueError:

                continue

        print("Failed to clean date, format not recognized.")
        return date_str
    except Exception as e:
        print("Error while formatting date: ", e)
        return date_str


def split_pdf_with_fitz(case_file_name):
    case_id, file_name = case_file_name
    if not file_name:
        logging.warning(f"No file to split for case ID {case_id}.")
        return case_id, []
    logging.debug(f"Starting to split the PDF file: {file_name}")
    try:
        doc = fitz.open(file_name)
        output_files = []
        for page_num in range(len(doc)):
            output_filename = os.path.join(temp_folder, f"{os.path.basename(file_name)[:-4]}_page_{page_num + 1}.pdf")
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            new_doc.save(output_filename)
            new_doc.close()
            output_files.append(output_filename)
            logging.debug(f"Saved split page {output_filename}.")
        doc.close()
        os.remove(file_name)
        logging.info(f"Completed splitting and cleaning up {file_name}.")
        return case_id, output_files
    except Exception as e:
        logging.error(f"Error splitting {file_name}: {e}")
        return case_id, []


async def process_files(case_files):
    async with aiohttp.ClientSession() as session:
        download_tasks = [async_download_file(session, cf) for cf in case_files]
        downloaded_files = await asyncio.gather(*download_tasks)
        executor = ThreadPoolExecutor(max_workers=4)
        split_tasks = [executor.submit(split_pdf_with_fitz, df) for df in downloaded_files if df[1] is not None]
        split_results = await asyncio.gather(*[asyncio.wrap_future(task) for task in split_tasks])
        return dict(split_results)


semaphore = asyncio.Semaphore(1)  # Limit to 2 concurrent OCR tasks


async def process_ocr_for_file(file_path, image_id, curr_dir):
    async with semaphore:  # Use semaphore to limit concurrency
        logging.debug(f"Starting OCR process for file: {file_path}")
        executor = ThreadPoolExecutor(max_workers=3)  # Limit threads for each OCR task
        try:
            main_ocr = MainOCR(curr_dir, file_path, image_id, "collection")
            result = await asyncio.get_event_loop().run_in_executor(executor, main_ocr.startProcess)
            logging.info(f"OCR result for {file_path}: {result}")
            return result
        except Exception as e:
            logging.error(f"OCR processing failed for {file_path}: {e}")
            return None


async def process_all_files(split_files_dict):
    results = {}
    for case_id, files in split_files_dict.items():
        logging.debug(f"Queueing OCR tasks for case ID {case_id}.")
        tasks = [process_ocr_for_file(file_path, file_index, os.path.abspath(os.curdir))
                 for file_index, file_path in enumerate(files)]
        # Await all OCR tasks to complete for the current case ID and collect results directly
        case_results = await asyncio.gather(*tasks)
        if debug: print("case_results: ", case_results)
        results[case_id] = case_results
    if debug: print("Results: ", results)
    return results


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
        total_cost_denials = 0
        providers = set()
        provider_denials = set()
        insurers = set()
        insurer_denials = set()
        patients = set()
        patients_denials = set()
        date_ranges = []
        final_min_date = ''
        final_max_date = ''
        date_ranges_denials = []
        min_date_found = 0
        max_date_found = 0
        total_cost_found = 0
        final_total_cost = None
        bill_found = False

        if debug: print(f"Data blocks = {data_blocks}")

        # Aggregate data
        try:
            for i in range(len(data_blocks)):
                try:
                    if debug: print("out data is: ", data_blocks[i]['out_data'])

                    # Fetching from Bills

                    if 'provider_name' in data_blocks[i]['out_data']:
                        providers.add(data_blocks[i]['out_data']['provider_name'])
                        bill_found = True
                        if debug: print("all provider values", providers)

                    if 'insurer_name' in data_blocks[i]['out_data']:
                        insurers.add(data_blocks[i]['out_data']['insurer_name'])
                        bill_found = True
                        if debug: print("all insurers values", insurers)

                    if 'patient_name' in data_blocks[i]['out_data']:
                        patients.add(data_blocks[i]['out_data']['patient_name'])
                        bill_found = True
                        if debug: print("all patients values", patients)
                    if 'policy_holder' in data_blocks[i]['out_data']:
                        patients.add(data_blocks[i]['out_data']['policy_holder'])
                        bill_found = True
                        if debug: print("all patients values", patients)

                    if 'cost' in data_blocks[i]['out_data']:
                        total_cost += float(data_blocks[i]['out_data']['cost']) if data_blocks[i]['out_data'][
                            'cost'] else 0
                        bill_found = True
                    if 'date_of_service' in data_blocks[i]['out_data']:
                        # Handle multiple date formats potentially split by "-"
                        date_ranges.extend(data_blocks[i]['out_data']['date_of_service'].split('-'))
                        bill_found = True

                    # Fetching from denials if not bills

                    if 'provider_name_denials' in data_blocks[i]['out_data']:
                        provider_denials.add(data_blocks[i]['out_data']['provider_name_denials'])
                        # providers.add(data_blocks[i]['out_data']['provider_name_denials'])

                    if 'insurer_name_denials' in data_blocks[i]['out_data']:
                        insurer_denials.add(data_blocks[i]['out_data']['insurer_name_denials'])
                        # insurers.add(data_blocks[i]['out_data']['insurer_name_denials'])

                    if 'patient_name_denials' in data_blocks[i]['out_data']:
                        patients_denials.add(data_blocks[i]['out_data']['patient_name_denials'])

                    if 'policy_holder_denials' in data_blocks[i]['out_data']:
                        patients_denials.add(data_blocks[i]['out_data']['policy_holder_denials'])

                    if 'cost_denials' in data_blocks[i]['out_data']:
                        total_cost_denials += float(data_blocks[i]['out_data']['cost_denials']) if \
                            data_blocks[i]['out_data'][
                                'cost_denials'] else 0
                    if 'date_of_service_denials' in data_blocks[i]['out_data']:
                        # Handle multiple date formats potentially split by "-"
                        date_ranges_denials.extend(data_blocks[i]['out_data']['date_of_service_denials'].split('-'))
                except Exception as e:
                    print("Error code 5: ", e)

        except Exception as e:
            print("Error code 4: ", e)

        try:
            # Convert string dates to date objects
            print("date ranges: ", date_ranges)
            date_objects = [clean_date(date) for date in date_ranges if date_ranges]
            date_objects = [datetime.datetime.strptime(date, "%m/%d/%Y") for date in date_objects]
            print("date_objects: ", date_objects)
            date_objects_denials = [clean_date(date) for date in date_ranges_denials if date_ranges_denials]
            if debug: print("all dates extracted: ", date_objects_denials)
            # Determine the minimum and maximum dates
            min_date = min(date_objects) if date_objects else None
            max_date = max(date_objects) if date_objects else None
            print(f"minimum date: {min_date}\nmax date:{max_date}")

            min_date_denials = min(date_objects_denials) if date_objects_denials else None
            max_date_denials = max(date_objects_denials) if date_objects_denials else None

            if min_date:
                final_min_date = min_date
                if debug:
                    print("final min date from bills: ", final_min_date)
                min_date_found = 1
            if max_date:
                final_max_date = max_date
                if debug:
                    print("final max date from bills: ", final_max_date)
                max_date_found = 1

            if min_date_denials and min_date_found == 0:
                final_min_date = min_date_denials
                if debug:
                    print("final min date from denials: ", final_min_date)
            if max_date_denials and max_date_found == 0:
                final_max_date = max_date_denials
                if debug:
                    print("final max date from denials: ", final_max_date)
        except Exception as e:
            print("Error processing dates: ", e)

        try:
            if total_cost:
                final_total_cost = total_cost
                if debug:
                    print("final cost from bills: ", final_total_cost)
            elif total_cost_denials and total_cost_found == 0:
                final_total_cost = total_cost_denials
                if debug:
                    print("final cost from denials: ", total_cost_denials)
        except Exception as e:
            print("Error processing Cost : ", e)

        aggregated_data_bills = {
            'provider_name': providers,
            'insurer_name': insurers,
            'patient_name': patients,
            'dos_s': final_min_date.strftime("%m/%d/%Y") if final_min_date else None,
            'dos_e': final_max_date.strftime("%m/%d/%Y") if final_max_date else None,
            'total_cost': format(final_total_cost, '.2f') if final_total_cost else None
        }

        aggregated_data_denials = {
            'provider_name': provider_denials,
            'insurer_name': insurer_denials,
            'patient_name': patients_denials,
            'dos_s': final_min_date if final_min_date else None,
            'dos_e': final_max_date if final_max_date else None,
            'total_cost': format(final_total_cost, '.2f') if final_total_cost else None
        }

        return aggregated_data_bills, aggregated_data_denials, bill_found
    except Exception as e:
        print(e)


def soft_match(api_value, extracted_value):
    print(f"matching {api_value} --> {extracted_value}")
    try:
        print("comparing data is : ", api_value, "--", extracted_value)

        # Token set ratio compares the sets of tokens in both strings and accounts for similar tokens in any order
        match_score = fuzz.token_set_ratio(api_value, extracted_value)
        print("match score for extracted data: ", match_score, "comparing with: threshold 70 ")
        if match_score >= 70:  # You can adjust the threshold based on your requirements
            return True
        elif api_value.split()[0] in extracted_value.split()[0]:
            return True
        else:
            return False
    except Exception as e:
        print(e)


def normalize_alpha_only(value):
    """Normalize a string by removing non-alphabetic characters (except spaces), converting to lowercase, and stripping whitespace."""
    if value is None:
        return ''
    value = re.sub(r'[^a-zA-Z\s]', '', value)
    # print("cleaned value is: ", value)
    return value.strip().lower()


def normalize_string(value):
    return '' if value is None else str(value).strip().lower()


def calculate_correctness(api_data, transformed_data_bills, transformed_data_denials):
    try:
        correct_count = 0
        new_extr_data = dict()
        format_type = dict()
        patient_found = 0
        total_fields = 6  # Exclude 'filepath' from the total fields count.
        incorrect_fields = {}
        all_data = {}
        all_data["bills"] = transformed_data_bills
        all_data["denials"] = transformed_data_denials
        for type, extracted_data in all_data.items():
            if type == "bills":
                for key, value in api_data.items():
                    try:
                        if key == "filepath":
                            continue  # Skip checking the filepath

                        api_value_normalized = normalize_string(value)
                        extracted_value_normalized = normalize_string(extracted_data.get(key, "N/A"))

                        # Handling for specific fields with softer matching criteria
                        if key in ["plaintiff", "defendant", "patientName"]:
                            api_value_normalized = normalize_alpha_only(api_value_normalized)
                            if key == "patientName":
                                for each_entity in extracted_data[key]:

                                    extracted_value_normalized_1 = normalize_alpha_only(each_entity)
                                    if soft_match(api_value_normalized, extracted_value_normalized_1):
                                        print("adding correct count patinet from bills: ", correct_count)
                                        new_extr_data[key] = each_entity
                                        correct_count += 1
                                        format_type["bills"] += 1
                                        patient_found = 1
                                    else:
                                        pass

                            else:
                                for each_entity in extracted_data[key]:

                                    extracted_value_normalized_1 = normalize_alpha_only(each_entity)
                                    if soft_match(api_value_normalized[:7], extracted_value_normalized_1[:7]):
                                        print(f"adding correct count for {key}: ", correct_count)
                                        new_extr_data[key] = each_entity
                                        correct_count += 1
                                        format_type = "bill"
                                    else:
                                        pass
                                        # incorrect_fields[key] = extracted_data.get(key, "N/A")

                        elif key in ["dos_s", "dos_e"]:  # Strict date comparison after normalization
                            api_value_normalized = clean_date(api_value_normalized)
                            extracted_value_normalized = clean_date(extracted_value_normalized)
                            if api_value_normalized == extracted_value_normalized:
                                print(f"adding correct count for {key}: ", correct_count)
                                new_extr_data[key] = extracted_value_normalized
                                correct_count += 1
                                format_type = "bill"
                            else:
                                pass
                                # incorrect_fields[key] = extracted_data.get(key, "N/A")


                        elif key == "initial_amt":  # Numerical compariso
                            try:

                                if debug:
                                    print(
                                        f"comparing amount >> {api_value_normalized} with >> {extracted_value_normalized}")
                                if float(api_value_normalized) == float(extracted_value_normalized):
                                    if debug: print("adding correct count FOR AMOUNT: ", correct_count)
                                    new_extr_data[key] = extracted_value_normalized
                                    correct_count += 1
                                    format_type = "bill"

                            except ValueError:
                                print("Error: ", ValueError)

                        else:  # Default strict comparison

                            new_extr_data[key] = extracted_value_normalized
                            if api_value_normalized == extracted_value_normalized:
                                if debug: print("adding correct count in else part: ", correct_count)
                                new_extr_data[key] = extracted_value_normalized
                                correct_count += 1

                    except Exception as e:
                        print("Error Matching: ", e)
                        continue

                # correctness_percentage = (len(new_extr_data) / total_fields) * 100
                # if debug: print(
                #     f"calculated correctness percentage {(len(new_extr_data))}/{total_fields}*100 = {correctness_percentage}")
                # return correctness_percentage, new_extr_data, type
            elif type == "denials":
                for key, value in api_data.items():
                    try:
                        if key == "filepath":
                            continue  # Skip checking the filepath

                        api_value_normalized = normalize_string(value)
                        extracted_value_normalized = normalize_string(extracted_data.get(key, "N/A"))

                        # Handling for specific fields with softer matching criteria
                        if key in ["plaintiff", "defendant", "patientName"]:
                            api_value_normalized = normalize_alpha_only(api_value_normalized)
                            if key == "patientName":
                                for each_entity in extracted_data[key]:

                                    extracted_value_normalized_1 = normalize_alpha_only(each_entity)
                                    if soft_match(api_value_normalized, extracted_value_normalized_1):
                                        print("adding correct count patinet from bills: ", correct_count)
                                        new_extr_data[key] = each_entity
                                        correct_count += 1
                                        format_type["bills"] += 1
                                        patient_found = 1
                                    else:
                                        pass

                            else:
                                for each_entity in extracted_data[key]:

                                    extracted_value_normalized_1 = normalize_alpha_only(each_entity)
                                    if soft_match(api_value_normalized[:7], extracted_value_normalized_1[:7]):
                                        print(f"adding correct count for {key}: ", correct_count)
                                        new_extr_data[key] = each_entity
                                        correct_count += 1
                                        format_type = "bill"
                                    else:
                                        pass
                                        # incorrect_fields[key] = extracted_data.get(key, "N/A")

                        elif key in ["dos_s", "dos_e"]:  # Strict date comparison after normalization
                            api_value_normalized = clean_date(api_value_normalized)
                            extracted_value_normalized = clean_date(extracted_value_normalized)
                            if api_value_normalized == extracted_value_normalized:
                                print(f"adding correct count for {key}: ", correct_count)
                                new_extr_data[key] = extracted_value_normalized
                                correct_count += 1
                                format_type = "bill"
                            else:
                                pass
                                # incorrect_fields[key] = extracted_data.get(key, "N/A")


                        elif key == "initial_amt":  # Numerical compariso
                            try:

                                if debug:
                                    print(
                                        f"comparing amount >> {api_value_normalized} with >> {extracted_value_normalized}")
                                if float(api_value_normalized) == float(extracted_value_normalized):
                                    if debug: print("adding correct count FOR AMOUNT: ", correct_count)
                                    new_extr_data[key] = extracted_value_normalized
                                    correct_count += 1
                                    format_type = "bill"

                            except ValueError:
                                print("Error: ", ValueError)

                        else:  # Default strict comparison

                            new_extr_data[key] = extracted_value_normalized
                            if api_value_normalized == extracted_value_normalized:
                                if debug: print("adding correct count in else part: ", correct_count)
                                new_extr_data[key] = extracted_value_normalized
                                correct_count += 1

                    except Exception as e:
                        print("Error Matching: ", e)
                        continue

                correctness_percentage = (len(new_extr_data) / total_fields) * 100
                if debug: print(
                    f"calculated correctness percentage {(len(new_extr_data))}/{total_fields}*100 = {correctness_percentage}")
                return correctness_percentage, new_extr_data, type
    except Exception as e:
        print(e)


def transform_data(aggregated_data):
    try:
        transformed_data = {
            'plaintiff': aggregated_data.get('provider_name'),
            'defendant': aggregated_data.get('insurer_name'),
            'patientName': aggregated_data.get('patient_name'),
            'dos_s': aggregated_data.get('dos_s'),
            'dos_e': aggregated_data.get('dos_e'),
            'initial_amt': aggregated_data.get('total_cost')

        }
        return transformed_data
    except Exception as e:
        print(e)


@app.route("/")
def home():
    print("started")
    time.sleep(30)
    print("awake!")
    return "hello from 94"


@app.route("/quality_check", methods=['GET'])
async def quality_check():
    key = "VmxxcWJ3OWd4TnlWTEozSHA3YUlwNzh1WWhSVVY0WVFGQXlFczFLOGV1Yw=="
    inpkey = request.args.get("key")
    cases_id = request.args.get("url_data")
    username = request.args.get(("username"))
    API_URL1 = API_URL + f"&case_ids={cases_id}&username={username}&server=10.10.0.94"
    logging.debug("Received request for quality check.")
    if key != inpkey:
        logging.warning("Invalid API key provided.")
        return jsonify({"error": "Invalid API key"}), 401
    st = time.time()
    resp = None
    try:

        resp = requests.get(API_URL1)
        print("Calling..")
        filelog123 = open("/home/neuralit/quality_check_logs.txt", "a")
        filelog123.write(str(resp))
        filelog123.close()

        async with aiohttp.ClientSession() as session:

            if resp:
                # data = await response.json()

                data = resp.json()
                case_files = [(case_id, item['filepath']) for case_id, item in data.items()]
                split_results = await process_files(case_files)
                ocr_results = await process_all_files(split_results)
                for case_id, record in data.items():
                    if debug: print(f"processing for case id: {case_id}", ocr_results.get(case_id))
                    data_bills, data_denials, is_bill = extract_data_from_text(ocr_results.get(case_id))
                    print("raw data: ", data_bills, data_denials, is_bill)
                    transformed_data_bills = transform_data(data_bills)
                    transformed_data_denials = transform_data(data_denials)
                    print("final out data aggregated: ", transformed_data_bills, transformed_data_denials)
                    print("record data: ", record)
                    correctness_percentage, new_extr_data, format_type = calculate_correctness(record,
                                                                                               transformed_data_bills,
                                                                                               transformed_data_denials)
                    result = {
                        "case_id": case_id,
                        "filepath": record['filepath'],
                        "status": 1 if correctness_percentage >= 95 else 0,
                        "percentage": str(correctness_percentage).split(".")[0],
                        "format_type": "bills" if is_bill else "denials",
                        "username": username,

                    }

                    if True:
                        del record['filepath']
                        result["api_response"] = record
                        result["ocr_response"] = new_extr_data

                    print("final res: ", result)
                    # TODO
                    # Post the result for this case
                    if True:
                        if debug: print("Total execution time: ", time.time() - st)
                        post_response = requests.post(RESULTS_API_URL, json=[result])
                        print("post response: ", post_response.status_code)
                        if post_response.status_code != 200:
                            print(f"Failed to post results for case ID {case_id}")
                        # # pass
                    print("Final OCR OUT:", ocr_results)

            else:
                logging.error(f"Failed to fetch data from API.")
                return jsonify({"error": "Failed to fetch data"}), 500

    except Exception as e:
        logging.error(f"Exception during processing: {e}")
        return jsonify({"error": str(e)}), 500
    resp = requests.post(
        f"https://gabrielmoroff.com/liberation/functions/check_qcprocess_status?&key=LBATQrQzP8qp2YVxWfHlJFV3VZ1mWIO7&username={username}")
    print("final response: ", resp)
    return {"process_status": 0}


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5026)
