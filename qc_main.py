from flask import Flask, request, jsonify
import asyncio
import aiohttp
import os
import fitz  # Import PyMuPDF
import tempfile
from concurrent.futures import ThreadPoolExecutor
from QC.OCRv4.OCRv4.flask_ocr_app.models.MainOCR_local import MainOCR
import asyncio

app = Flask(__name__)
API_URL = "http://127.0.0.1:50901/get_data"  # Update this to your actual API URL
debug = True
temp_folder = tempfile.mkdtemp()  # Create a temporary directory


# Example OCR function using ThreadPoolExecutor inside an asyncio coroutine
async def process_ocr_for_file(file_path, image_id, curr_dir):
    loop = asyncio.get_running_loop()
    print("Hererererererer")
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = await loop.run_in_executor(
            executor,
            perform_ocr,  # This is the synchronous function that calls your OCR logic
            file_path, image_id, curr_dir
        )
    return result


def perform_ocr(file_path, image_id, curr_dir):
    # Simulate the OCR processing
    # Replace this with the actual call to your OCR processing logic
    mainOcr = MainOCR(curr_dir, file_path, image_id, "collection")
    return mainOcr.startProcess()


async def process_all_files(split_files_dict):
    results = {}
    for case_id, files in split_files_dict.items():
        if debug: print(f"processing {case_id}: {files}")
        case_results = []
        for file_path in files:
            if debug: print(f"processing {file_path}")
            ocr_result = await process_ocr_for_file(file_path, files.index(file_path), os.path.abspath(os.curdir))
            case_results.append(ocr_result)
        results[case_id] = case_results
    print("REULSTS", results)
    return results


# Asynchronous function to download and save the PDF file
async def async_download_file(case_file):
    case_id, file_url = case_file
    file_name = os.path.join(temp_folder, file_url.split('/')[-1])
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as response:
                if response.status == 200:
                    content = await response.read()
                    with open(file_name, 'wb') as file:
                        file.write(content)
                    if debug: print(f"Downloaded {file_name} successfully.")
                    return (case_id, file_name)
                else:
                    if debug: print(f"Failed to download {file_url} with HTTP status {response.status}.")
                    raise Exception(f"Failed to download file with status {response.status}")
    except Exception as e:
        if debug: print(f"Exception occurred during file download {file_url}: {str(e)}")
        raise


# Function to split the PDF using PyMuPDF
def split_pdf_with_fitz(case_file_name):
    case_id, file_name = case_file_name
    try:
        doc = fitz.open(file_name)
        output_files = []
        if debug: print(f"Opened document {file_name} for splitting.")
        for page_num in range(len(doc)):
            output_filename = os.path.join(temp_folder, f"{os.path.basename(file_name)[:-4]}_page_{page_num + 1}.pdf")
            new_doc = fitz.open()
            new_doc.insert_pdf(doc, from_page=page_num, to_page=page_num)
            new_doc.save(output_filename)
            new_doc.close()
            output_files.append(output_filename)
            if debug: print(f"Saved split page {output_filename}.")
        doc.close()
        # os.remove(file_name)
        # if debug: print(f"All pages split and original file {file_name} removed.")
        return (case_id, output_files)
    except Exception as e:
        if debug: print(f"Error splitting {file_name}: {str(e)}")
        raise


# Process files with asynchronous download and synchronous split
async def process_files(case_files):
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = [asyncio.create_task(async_download_file(case_file)) for case_file in case_files]
        downloaded_files = await asyncio.gather(*tasks, return_exceptions=True)
        split_files = await asyncio.gather(
            *(asyncio.get_event_loop().run_in_executor(executor, split_pdf_with_fitz, case_file_name) for case_file_name
              in downloaded_files if not isinstance(case_file_name, Exception)),
            return_exceptions=True)
        return {case_id: files for case_id, files in split_files if not isinstance(files, Exception)}


@app.route("/quality_check", methods=['GET'])
async def quality_check():
    key = "expected_key"
    print("STARTED..")  # Your actual expected key
    inpkey = request.args.get("key")
    if str(key) != str(inpkey):
        if debug: print("Invalid API key provided.")
        return {"error": "Invalid API key"}, 401

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(API_URL) as response:
                if response.status == 200:
                    data = await response.json()
                    case_files = [(case_id, item['filepath']) for case_id, item in data.items()]
                    results = await process_files(case_files)
                    ocr_results = await process_all_files(results)
                    if debug: print("Final", jsonify(ocr_results))
                    return jsonify(results)

                else:
                    if debug: print(f"Failed to fetch data from API with status {response.status}.")
                    return {"error": "Failed to fetch data"}, 500
    except Exception as e:
        if debug: print(f"Exception during processing: {str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5040, use_reloader=False)
