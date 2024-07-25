from flask import Flask, request, jsonify
import asyncio
import aiohttp
import os
import fitz  # PyMuPDF
import tempfile
from concurrent.futures import ThreadPoolExecutor
from QC.OCRv4.OCRv4.flask_ocr_app.models.MainOCR_local import MainOCR
import logging
import time

app = Flask(__name__)
API_URL = "http://127.0.0.1:50901/get_data"
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


semaphore = asyncio.Semaphore(3)  # Limit to 2 concurrent OCR tasks


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


@app.route("/quality_check", methods=['GET'])
async def quality_check():
    key = "expected_key"
    inpkey = request.args.get("key")
    logging.debug("Received request for quality check.")
    if key != inpkey:
        logging.warning("Invalid API key provided.")
        return jsonify({"error": "Invalid API key"}), 401

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL) as response:
                if response.status == 200:
                    data = await response.json()
                    case_files = [(case_id, item['filepath']) for case_id, item in data.items()]
                    split_results = await process_files(case_files)
                    ocr_results = await process_all_files(split_results)
                    print("Final OCR OUT:", ocr_results)
                    return jsonify(ocr_results)
                else:
                    logging.error(f"Failed to fetch data from API with status {response.status}.")
                    return jsonify({"error": "Failed to fetch data"}), 500
    except Exception as e:
        logging.error(f"Exception during processing: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5041, use_reloader=False)
