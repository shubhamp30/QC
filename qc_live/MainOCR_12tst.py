import cv2
import numpy as np
from pdf2image import convert_from_path
import os, signal
import subprocess, threading
import shutil
import requests
import csv
import pytesseract
from retrying import retry
from .ProcessPdf import ProcessPdf
from .functions import *
from difflib import SequenceMatcher
from datetime import datetime
import time
import re
import json
import fitz  # PyMuPDF
from PyPDF2 import PdfFileReader
from PIL import Image
import pdfplumber

from pdf_orientation_corrector.main import detect_and_correct_orientation

Image.MAX_IMAGE_PIXELS = None


@retry(wait_fixed=1500, stop_max_attempt_number=1)
def get_with_retry(link):
    print("trying ..")
    return requests.get(link, timeout=3, allow_redirects=True)


class Ocrcommand(object):
    def __init__(self, cmd):
        currDir = os.path.abspath(os.curdir) + "/"
        self.currDir = currDir
        self.tempPath = self.currDir + "temp/"
        self.cmd = cmd
        self.process = None

    def run(self):
        self.process = subprocess.Popen(self.cmd, shell=True, preexec_fn=os.setsid)
        self.process.communicate()
        print(self.process.returncode)


class OcrConfidence:
    def __init__(self, img, image_id, process_type):
        self.img = img
        self.imgcopy = img.copy()
        self.image_id = image_id
        self.process_type = process_type

    def run(self):
        def calculateOcrConfidence():
            img = self.imgcopy
            print("OCR Confidence Calculation in Thread")
            # print(lines)
            # Now Add here logic to search the OCR confidenecne using pytesseract.image_to_data()
            #  2021-05-12 09:26:01
            custom_config = r'--oem 3 --psm 11'
            ocrtext = pytesseract.image_to_data(img, output_type='data.frame', config=custom_config, lang='eng')
            # custom_config = r'-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 --psm 6'

            # ocrtext = pytesseract.image_to_data(img, output_type='data.frame', config=custom_config)
            ocrtext = ocrtext[ocrtext.conf != -1]
            # print(ocrtext)
            lines = ocrtext.groupby('block_num')['text'].apply(list)
            conf = ocrtext.groupby(['block_num'])['conf'].mean()

            page_confidence_score = 0
            total_page_lines = 0
            for line_conf in conf:
                # print(line_conf)
                page_confidence_score += line_conf
                total_page_lines += 1
            conf_score = round((page_confidence_score / total_page_lines), 2)
            # print(page_confidence_score)
            # print(total_page_lines)
            print(conf_score)
            #  update image confifdence
            sendToAPIConfidence(conf_score)
            return conf_score

        def sendToAPIConfidence(conf_score):
            API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
            # your API Toekn  here
            API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
            # data to be sent to api
            data = {'atoken': API_KEY, 'process': 'collection', 'req_type': 'updateConf', 'image_id': self.image_id,
                    'conf_score': conf_score}
            finalPost = dict(list(data.items()))
            # print(finalPost)
            r = requests.post(url=API_ENDPOINT, data=finalPost)
            # extracting response text
            pastebin_url = r.text
            print("The response is:%s" % pastebin_url)

        thread = threading.Thread(target=calculateOcrConfidence)
        thread.start()
        thread.join()


class MainOCR:
    def __init__(self, currDir, url, image_id, process_type):
        self.currDir = currDir
        self.dirtime = self.gettimeStamp()
        self.tempPath = self.currDir + "ocr_work/temp" + self.dirtime + "/"
        self.tempImgPath = self.currDir + "ocr_work/temp_img" + self.dirtime + "/"
        self.createTempDirs()
        self.url = url
        self.image_id = image_id
        self.process_type = process_type
        self.config = ('-l eng')

    def gettimeStamp(self):
        time.time()
        return str(time.time()).replace('.', '_')

    def _getPercentageMatch(self, a, b):
        return SequenceMatcher(None, a, b).ratio()

    def createTempDirs(self):
        if os.path.isdir(self.tempPath):
            shutil.rmtree(self.tempPath)
        os.makedirs(self.tempPath)
        if os.path.isdir(self.tempImgPath):
            shutil.rmtree(self.tempImgPath)
        os.makedirs(self.tempImgPath)

    def loadImg(self, imgPath):
        img = cv2.imread(imgPath)
        return img

    def resizeImg(self, gray, newHeight=1600):
        shape = gray.shape
        oldHeight = shape[0]
        oldWidth = shape[1]
        r = newHeight / oldHeight
        newWidth = int(r * oldWidth)
        dim = (newHeight, newWidth)
        resizedImg = cv2.resize(gray, dim, interpolation=cv2.INTER_AREA)
        return resizedImg

    def getImageFromUrl(self):
        print(self.url)
        try:
            print("pdf failure", self.url)
            r = get_with_retry(self.url)

            # r = requests.get(self.url, allow_redirects=True)
            # time.sleep(0.5)                                   # add retrying functionality
            with open(self.tempPath + "metadata.pdf", "wb") as fp:
                fp.write(r.content)
            try:
                pdf_path = self.tempPath + "metadata.pdf"
                print("path of pdf ", pdf_path)
                if os.path.isfile(self.tempPath + "metadata.pdf"):
                    print("file exists at: ", pdf_path)
                PdfFileReader(open(self.tempPath + "metadata.pdf", "rb"))
            except:
                print("invalid PDF file")
                return
        except:
            if os.path.exists(self.url):
                shutil.copyfile(self.url, self.tempPath + "metadata.pdf")
        pages = convert_from_path(self.tempPath + "metadata.pdf")
        imgCount = 1
        for page in pages:
            page.save(self.tempImgPath + "page-" + str(imgCount) + ".jpg", "JPEG")

    def savevisitedURL(self, file_path, url):
        return False
        hs = open(file_path, "a+")
        hs.write(url + "\n")
        return False

    def check_if_string_in_file(self, file_path, url_to_serach):
        # Open the file in read only mode
        open(file_path, "a+")
        with open(file_path) as read_obj:
            return any(url_to_serach in line for line in read_obj)
        return False

    def getStartEndPoints(self, start, end):
        startPoint = start.split(":")[1].split("(")[1].split(")")[0].split(",")
        startPoint = tuple(startPoint)
        endPoint = end.split(":")[1].split("(")[1].split(")")[0].split(",")
        endPoint = tuple(endPoint)
        return [startPoint, endPoint]

    def recognizeImages(self, img, csvlist):
        result = 0
        imgCount = 1
        # print("Inside recognizeImages",list(csvlist))
        # json_data = self.recognizeImages(img, name)
        # print(json_data)
        for row in csvlist:
            # print(row)
            if len(row) > 0:
                name, start, end = row
                points = self.getStartEndPoints(start, end)
                roi = img[int(points[0][0]):int(points[1][0]), int(points[0][1]):int(points[1][1])]
                # print("Here1")
                text = str(pytesseract.image_to_string(roi, config=self.config))
                # print("Here")
                text = text.replace("\n", "")
                text = text.replace("|", "")
                # print("text = ", text)
                if (len(text) >= len(name)) and text != "" and (
                        (name in text) or self._getPercentageMatch(name, text[0:len(name)]) > 0.8):
                    result = imgCount
                    break
                elif (len(text) < len(name)) and text != "" and (
                        (text in name) or self._getPercentageMatch(text, name[0:len(text)]) > 0.8):
                    result = imgCount
                    break
                imgCount += 1
        return result

    # function to hanlde the complete OCR based text identification
    def recognizeCompleteOcr(self, img, csvlist):
        #  IDenityf page roattion value
        orientation_val = 0
        try:
            print("CHECKING Orientation")
            newdata = pytesseract.image_to_osd(img)
            orientation_val = (re.search('(?<=Rotate: )\d+', newdata).group(0))
            print(orientation_val + "Here")
            if (int(orientation_val) == 180):
                print("PDF ROTATION REQUIRED")
                in_pdf = f"{self.tempPath}metadata.pdf"
                out_pdf = f"{self.tempPath}rotate_metadata.pdf"
                detect_and_correct_orientation(in_pdf, out_pdf, batch_size=10, dpi=300)
                # rotate_pdf(in_pdf, out_pdf)
                self.rotatePDF(self.tempPath + "metadata.pdf", self.tempPath + "rotate_metadata.pdf")
                command = f"mv {self.tempPath}rotate_metadata.pdf {self.tempPath}metadata.pdf"
                os.system(command)
        except:
            print("TESSERACT PAGE EXCPETION")

        doc = fitz.open(self.tempPath + "metadata.pdf")
        page = doc[0]
        dl = page.get_displaylist()
        # tp = dl.get_textpage()
        ### SEARCH
        # logic to ocr complete image file using tesseract 2020-11-24 08:57:14
        # text = str(pytesseract.image_to_string(img,config=self.config))

        # if(page.getText() == ""):
        try:
            self.ocrMyPDF()
            doc = fitz.open(self.tempPath + "metadata.pdf")
            page = doc[0]
            dl = page.get_displaylist()
            # tp = dl.get_textpage()
        except:
            print("PDF ocr issue EXCPETION")

        result = 0
        imgCount = 1
        cordDict = {}
        for row in csvlist:
            if len(row) > 0:
                name, start, end = row
                # name = name.lower()
                text_instances = page.search_for(name)
                print(name)
                points = self.getStartEndPoints(start, end)
                print(points)
                print(text_instances)
                if len(text_instances) > 0:
                    cords_rect = self.textCordMaker(text_instances)
                    print(cords_rect)
                    # Nearest cordinate logic
                    # for inst in cords_rect:
                    x0 = int(cords_rect.x0)
                    y0 = int(cords_rect.y0)
                    cx0 = int(points[0][0])
                    cy0 = int(points[1][0])
                    cordDict[imgCount] = abs(x0 - cx0) + abs(y0 - cy0)
                    # break
                # rlist = tp.search(name)
                # print(rlist)
                # print(text.find(name))
                # if (text.find(name) != -1) and (text != "") and (name in text):
                #     result = imgCount
                #     break
                imgCount += 1
        # Text found and distance calculated
        print(cordDict)
        if (len(cordDict.keys()) > 0):
            minimum_cord_distance = min(cordDict.values())
            res = [key for key in cordDict if cordDict[key] == minimum_cord_distance]
            print("final result for dictionary: ", res)
            result = res[0]
        for i in cordDict:
            if i in [15, 16]:
                result = 16
        if result in [2, 3, 6, 10, 12, 13, 15]:
            text_instances = page.search_for("TOTAL CHARGES TO DATE")
            text_instances_1 = page.search_for("TOTAL CHARGES")
            text_instances_2 = page.search_for("CHARGES")
            if len(text_instances) > 0:
                result = result
            elif len(text_instances_1) > 0:
                result = result
            elif len(text_instances_2) > 0:
                result = result
            else:
                result = 1
        text_instances = page.search_for("ASSIGNMENT OF BENEFITS FORM")
        if (len(text_instances) > 0):
            result = 0

        # check the page ctaeogory by idenitfier
        bill_cat_srh = page.search_for("VERIFICATION OF TREATMENT")
        aob_cat_srh = page.search_for("ASSIGNMENT OF BENEFITS")
        service_cat_srh = page.search_for("Report of services rendered")
        wcb_cat_srh = page.search_for("WCB Rating Code")
        if len(bill_cat_srh) > 0 or len(service_cat_srh) > 0 or len(wcb_cat_srh) > 0:
            cat_type = "BILLS"
        elif len(aob_cat_srh) > 0:
            cat_type = "A.O.B."
        else:
            cat_type = "MEDICAL REPORTS"

        conf_score = 0
        try:
            # conf_score = self.calculateOcrConfidence(img)
            conf_thread = OcrConfidence(img, self.image_id, self.process_type)
            conf_thread.run()
        except:
            print("TESSERACT OCR CONFIDENCE EXCPETION")
        # Dict with data array & page Category type return
        return_data = {}
        return_data["cat_type"] = cat_type
        return_data["result"] = result
        print("appending result : ", result)
        return_data["orientation_val"] = orientation_val
        return_data["conf_score"] = conf_score
        return return_data

    """
    This function id defiend to get the tesseract line wise character
    Confidence on image
    @author Rishab 2021-05-12 12:29:32
    """

    def calculateOcrConfidence(self, img):
        print("OCR Confidence Calculation")
        # print(lines)
        # Now Add here logic to search the OCR confidenecne using pytesseract.image_to_data()
        #  2021-05-12 09:26:01
        custom_config = r'--oem 3 --psm 11'
        ocrtext = pytesseract.image_to_data(img, output_type='data.frame', config=custom_config, lang='eng')
        # custom_config = r'-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 --psm 6'

        # ocrtext = pytesseract.image_to_data(img, output_type='data.frame', config=custom_config)
        ocrtext = ocrtext[ocrtext.conf != -1]
        # print(ocrtext)
        lines = ocrtext.groupby('block_num')['text'].apply(list)
        conf = ocrtext.groupby(['block_num'])['conf'].mean()

        page_confidence_score = 0
        total_page_lines = 0
        for line_conf in conf:
            # print(line_conf)
            page_confidence_score += line_conf
            total_page_lines += 1
        conf_score = round((page_confidence_score / total_page_lines), 2)
        # print(page_confidence_score)
        # print(total_page_lines)
        print(conf_score)
        return conf_score

    def ocrMyPDF(self):
        print("OCRMYPDF Method Enabled")
        command = f"ocrmypdf --force-ocr --optimize 0 --fast-web-view 0 --output-type pdf --skip-big 50 {self.tempPath}metadata.pdf {self.tempPath}ocr_metadata.pdf"
        # os.system(command)
        ocommand = Ocrcommand(command)
        ocommand.run()
        if os.path.exists(f"{self.tempPath}ocr_metadata.pdf"):
            command = f"mv {self.tempPath}ocr_metadata.pdf {self.tempPath}metadata.pdf"
            os.system(command)
        print("OCRMYPDF Method Completed")

    def rotatePDF(self, in_pdf, out_pdf):
        command = f"/usr/local/bin/cpdf -rotate 180 {in_pdf} -o {out_pdf}"
        os.system(command)
        # pdf_in = open(in_pdf, 'rb')
        # pdf_reader = PdfFileReader(pdf_in)
        # pdf_writer = PdfFileWriter()
        # for pagenum in range(pdf_reader.numPages):
        #     page = pdf_reader.getPage(pagenum)
        #     page.rotateClockwise(180)
        #     pdf_writer.addPage(page)
        # pdf_out = open(out_pdf, 'wb')
        # pdf_writer.write(pdf_out)
        # pdf_out.close()
        # pdf_in.close()

    def textFromImages(self, img, filename, filetype=1):
        if filetype in [1, 4, 5, 7, 8, 9, 11, 14]:
            return self.textByCordDistance(img, filename, filetype)
        data = []
        # OLd Amit.v method
        with open(filename, "r") as fp:
            csvreader = csv.reader(fp, delimiter="-")
            for row in csvreader:
                if len(row) > 0:
                    row_data = []
                    name, start, end = row
                    points = self.getStartEndPoints(start, end)
                    roi = img[int(points[0][0]):int(points[1][0]), int(points[0][1]):int(points[1][1])]
                    text = str(pytesseract.image_to_string(roi, config=self.config))
                    # print(text)
                    text_arr = text.split("\n")
                    text_arr = list(filter(lambda a: a.strip() != "", text_arr))
                    row_data.append(name)
                    if len(text_arr) > 1 and filetype in [1, 4, 5, 7, 8, 9]:
                        if name == "Insurer representative":
                            if len(text_arr) > 2:
                                insurer_name = ' '.join(text_arr[2:])
                                row_data.append(insurer_name)
                        elif name == "Insurer name":
                            if self._getPercentageMatch("NAME AND ADDRESS OF INSURER OR SELF-INSURER",
                                                        text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                            else:
                                row_data.append(text_arr[1])
                        elif name == "Provider Name":
                            if self._getPercentageMatch("PROVIDER'S NAME AND ADDRESS", text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                            else:
                                row_data.append(text_arr[1])
                        elif name == "Patient Name":
                            if self._getPercentageMatch("PATIENT'S NAME'", text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                            else:
                                row_data.append(text_arr[1])
                        else:
                            row_data.append(text_arr[1])
                    elif name == "policy number" or name == "claim number":
                        row_data.append('NA')
                    elif len(text_arr) == 1 and filetype in [1, 4, 5, 7, 8, 9]:
                        if name == "Insurer name":
                            if self._getPercentageMatch("NAME AND ADDRESS OF INSURER OR SELF-INSURER",
                                                        text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                        elif name == "Provider Name":
                            if self._getPercentageMatch("PROVIDER'S NAME AND ADDRESS", text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                        elif name == "Patient Name":
                            if self._getPercentageMatch("PATIENT'S NAME'", text_arr[0]) < 0.8:
                                row_data.append(text_arr[0])
                    elif len(text_arr) > 0 and filetype == 2:
                        if name == "Date of service":
                            text_arr.sort(key=lambda date: datetime.strptime(date, "%m/%d/%Y"))
                            row_data.append(text_arr[0] + "-" + text_arr[-1])
                        else:
                            row_data.append(text_arr[0])
                    if len(row_data) > 1:
                        data.append(row_data)
        return data

    def new_format_date(self, input_string):
        try:
            cleaned_string = re.sub(r'\D', '', input_string)
            formatted_date = input_string  # Default to the original input in case of error

            if len(cleaned_string) == 6:
                # Handling MMDDYY format
                month = cleaned_string[:2]
                day = cleaned_string[2:4]
                year = cleaned_string[4:6]
                formatted_date = f"{month}/{day}/{year}"
            elif len(cleaned_string) == 8:
                # Handling MMDDYYYY format
                month = cleaned_string[:2]
                day = cleaned_string[2:4]
                year = cleaned_string[4:8]
                formatted_date = f"{month}/{day}/{year}"

        except Exception as e:
            formatted_date = input_string

        return formatted_date

    def clean_cost(self, cost_str):
        cleaned_str = re.sub(r'[^\d.]', '', cost_str)

        # If there's no dot, assume the last two digits are the cents
        if '.' not in cleaned_str:
            cleaned_str = cleaned_str[:-2] + '.' + cleaned_str[-2:]

        # If the string starts with a dot, add a leading zero
        if cleaned_str[0] == '.':
            cleaned_str = '0' + cleaned_str

        return cleaned_str

    def remove_special_characters(self, input_string):
        try:
            clean_text = re.sub(r'[^a-zA-Z\s0-9]', '', input_string)
            return clean_text.strip()
        except Exception as e:
            return input_string

    def known_provider_data_fetch(self, pdf, i, type):
        print("in new method")
        doc = fitz.open(pdf)
        page = doc[0]
        data = []
        insurer_found = 0
        policy_holder_found = 0
        patient_name_found = 0
        date_of_accident_found = 0
        dob_found = 0
        if type == 1:
            provider_name = i
            if "central park" in provider_name.lower():
                print("here")

                # provider
                row_data = []
                row_data.append("Provider Name")
                row_data.append("Central Park")
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                # insurer
                if insurer_found == 0:
                    insurer_coords = page.search_for("INSURANCE COMPANY")

                    if insurer_coords:

                        i_rect = fitz.Rect(insurer_coords[0].x0 + 90, insurer_coords[0].y0 - 10,
                                           insurer_coords[-1].x1 + 200,
                                           insurer_coords[-1].y1 + 15)
                        provider_name_text = page.get_textbox(i_rect)
                        if provider_name_text:
                            row_data = []
                            row_data.append("Insurer name")
                            provider_name_text = self.remove_special_characters(provider_name_text)
                            row_data.append(provider_name_text.strip().replace("\n", " "))
                            # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                            if len(row_data) > 1:
                                data.append(row_data)
                                insurer_found = 1

                # policyholder
                if policy_holder_found == 0:
                    policy_holder_coords = page.search_for("POLICYHOLDER")
                    if policy_holder_coords:
                        i_rect = fitz.Rect(policy_holder_coords[0].x0, policy_holder_coords[0].y0,
                                           policy_holder_coords[-1].x1 + 100,
                                           policy_holder_coords[-1].y1 + 20)
                        policy_holder_text = page.get_textbox(i_rect)
                        if policy_holder_text:
                            row_data = []
                            row_data.append("policy holder")
                            policy_holder_text = self.remove_special_characters(policy_holder_text)
                            row_data.append(policy_holder_text.replace("\n", "").replace("POLICYHOLDER", "").strip())
                            # row_data.append(self.handlePatientName(name_text))
                            if len(row_data) > 1:
                                data.append(row_data)
                                policy_holder_found = 1

                # POLICY NUMBER
                policy_number_coords = page.search_for("POLICY NUMBER")
                if policy_number_coords:
                    i_rect = fitz.Rect(policy_number_coords[0].x0, policy_number_coords[0].y0,
                                       policy_number_coords[-1].x1 + 120,
                                       policy_number_coords[-1].y1 + 25)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("policy number")
                        row_data.append(
                            policy_number_text.replace("\n", "").replace("POLICY", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                # DATE OF ACCIDENT
                date_of_accident_coords = page.search_for("DATE OF ACCIDENT")
                if date_of_accident_coords:
                    i_rect = fitz.Rect(date_of_accident_coords[0].x0, date_of_accident_coords[0].y0,
                                       date_of_accident_coords[-1].x1 + 100,
                                       date_of_accident_coords[-1].y1 + 25)
                    date_of_accident_text = page.get_textbox(i_rect)
                    if date_of_accident_text:
                        row_data = []
                        row_data.append("accident date")
                        # if "DATE" in date_of_accident_text.strip():
                        #     row_data.append(self.removeDateSpace(self.dateCorrector(date_of_accident_text.strip())))
                        row_data.append(
                            self.get_date(date_of_accident_text).replace("\n", "").replace("DATE", "").replace("OF",
                                                                                                               "").replace(
                                "ACCIDENT",
                                "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                # claim number
                claim_number_coords = page.search_for("FILE NUMBER")
                if claim_number_coords:
                    i_rect = fitz.Rect(claim_number_coords[0].x0, claim_number_coords[0].y0,
                                       claim_number_coords[-1].x1 + 120,
                                       claim_number_coords[-1].y1 + 25)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number")
                        row_data.append(
                            claim_number_text.replace("\n", "").replace("FILE", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                # patient name
                patient_name_coords = page.search_for("PATIENT'S NAME:")
                if patient_name_coords:
                    i_rect = fitz.Rect(patient_name_coords[0].x0 + 50, patient_name_coords[0].y0 - 8,
                                       patient_name_coords[-1].x1 + 185,
                                       patient_name_coords[-1].y1 + 8)
                    patient_name_text = page.get_textbox(i_rect)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        patient_name_text = self.remove_special_characters(patient_name_text)
                        # row_data.append(self.handlePatientName(patient_name_text))
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENT'S", "").replace("NAME:", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                # dob
                dob_coords = page.search_for("DATEOF BIRTH:")
                if dob_coords:
                    i_rect = fitz.Rect(dob_coords[0].x0 + 50, dob_coords[0].y0 - 8,
                                       dob_coords[-1].x1 + 100,
                                       dob_coords[-1].y1 + 8)
                    dob_text = page.get_textbox(i_rect)
                    if dob_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(
                            self.get_date(dob_text).replace("\n", "").replace("PATIENT'S", "").replace("BIRTH:",
                                                                                                       "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                if (insurer_found == 0):
                    row_data = []
                    row_data.append("Insurer name")
                    row_data.append("Test Insurer")
                    data.append(row_data)

                if (policy_holder_found == 0):
                    row_data = []
                    row_data.append("policy holder")
                    for j in data:
                        if j[0] == 'Patient Name':
                            row_data.append(j[1])
                            break
                    else:
                        row_data.append("Test Holder")
                    data.append(row_data)

                print("data from new function: ", data)
                return data
            if "metro point" in provider_name.lower():

                # provider
                row_data = []
                row_data.append("Provider Name")
                row_data.append("Metro Point Medical, PC")
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                insurer_coords = page.search_for("VERIFICATION")
                if insurer_coords:
                    i_rect = fitz.Rect(insurer_coords[0].x0 - 50, insurer_coords[0].y0 + 25, insurer_coords[0].x1 + 80,
                                       insurer_coords[0].y1 + 35)
                    insurer_name_text = page.get_textbox(i_rect)
                    print("insurer:  ", insurer_name_text)
                    if insurer_name_text:
                        row_data = []
                        row_data.append("Insurer name")
                        insurer_name_text = self.remove_special_characters(insurer_name_text)
                        row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", ""))
                        if len(row_data) > 1:
                            data.append(row_data)
                            insurer_found = 1

                # provider_coords = page.search_for("PROVIDER:")
                # if insurer_coords:
                #     i_rect = fitz.Rect(provider_coords[0].x0 - 5, provider_coords[0].y0 + 5, provider_coords[0].x1 + 70,
                #                        provider_coords[0].y1 + 15)
                #     provider_name_text = page.get_textbox(i_rect)
                #     if provider_name_text:
                #         row_data = []
                #         row_data.append("Provider Name")
                #         row_data.append(provider_name_text.strip().replace("\n", " "))
                #         if len(row_data) > 1:
                #             data.append(row_data)

                policy_holder = page.search_for("POLICYHOLDER")
                if policy_holder:
                    i_rect = fitz.Rect(policy_holder[0].x0 - 25, policy_holder[0].y0 + 5,
                                       policy_holder[-1].x1 + 55,
                                       policy_holder[-1].y1 + 20)
                    policy_holder_text = page.get_textbox(i_rect)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("policy holder")
                        policy_holder_text = self.remove_special_characters(policy_holder_text)
                        row_data.append(policy_holder_text.replace("\n", "").replace("POLICYHOLDER", "").strip())
                        if len(row_data) > 1:
                            data.append(row_data)
                            policy_holder_found = 1

                policy_number = page.search_for("POLICY NUMBER")
                if policy_number:
                    i_rect = fitz.Rect(policy_number[0].x0 - 28, policy_number[0].y0 + 5,
                                       policy_number[0].x1 + 31,
                                       policy_number[0].y1 + 20)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("policy number")
                        row_data.append(
                            policy_number_text.replace("\n", "").replace("POLICY", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                date_of_accident = page.search_for("OF ACCIDENT")

                date_of_accident2 = page.search_for("")
                if date_of_accident:
                    i_rect = fitz.Rect(date_of_accident[0].x0 - 35, date_of_accident[0].y0 + 5,
                                       date_of_accident[0].x1 + 40,
                                       date_of_accident[0].y1 + 20)
                    date_of_accident_text = page.get_textbox(i_rect)
                    print("date of : ", date_of_accident_text)
                    if date_of_accident_text:
                        row_data = []
                        row_data.append("accident date")
                        row_data.append(
                            self.get_date(date_of_accident_text).replace("\n", "").replace("DATE", "").replace("OF",
                                                                                                               "").replace(
                                "ACCIDENT",
                                "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                claim_number = page.search_for("CLAIM NUMBER")
                if claim_number:
                    i_rect = fitz.Rect(claim_number[0].x0 - 25, claim_number[0].y0 + 5,
                                       claim_number[-1].x1 + 15,
                                       claim_number[0].y1 + 20)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number")
                        row_data.append(
                            claim_number_text.replace("\n", "").replace("CLAIM", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                patient_name = page.search_for("PATIENT'S NAME")
                if patient_name:
                    i_rect = fitz.Rect(patient_name[0].x0 - 25, patient_name[0].y0 + 5,
                                       patient_name[0].x1 + 75,
                                       patient_name[0].y1 + 17)
                    patient_name_text = page.get_textbox(i_rect)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        patient_name_text = self.remove_special_characters(patient_name_text)
                        try:
                            patient_name_array = patient_name_text.replace("\n", "").replace("PATIENTS", "").replace(
                                "NAME",
                                "").replace(
                                "PATIENTS", "").replace("AND", "").strip().split(" ")

                            if len(patient_name_text) > 2:
                                patient_name_text = str(patient_name_array[0]) + " " + str(patient_name_array[1])
                                print("new test: ", patient_name_text)
                        except Exception as e:
                            print("errpr: ", e)
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            patient_name_found = 1

                dob_coords = page.search_for("PATIENT'S NAME")
                if dob_coords:
                    i_rect = fitz.Rect(dob_coords[0].x0 - 25, dob_coords[0].y0 + 30,
                                       dob_coords[0].x1 + 55,
                                       dob_coords[0].y1 + 46)
                    dob_text = page.get_textbox(i_rect)
                    print("sfsdfsfsdfsdfsd", dob_text)
                    if dob_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(
                            self.get_date(dob_text).replace("\n", "").replace("PATIENT'S", "").replace("BIRTH:",
                                                                                                       "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            dob_found = 1

                if not patient_name and patient_name_found == 0:

                    text_header = page.search_for("CHANGES FROM THE")
                    if text_header:
                        i_rect = fitz.Rect(text_header[0].x0 - 25, text_header[0].y0 + 5,
                                           text_header[0].x1 + 50,
                                           text_header[0].y1 + 35)
                        patient_name_text = page.get_textbox(i_rect)

                        if patient_name_text:
                            row_data = []
                            row_data.append("Patient Name")

                            patient_name_text = self.remove_special_characters(patient_name_text)
                            print("patient name is ", patient_name_text)

                            try:
                                patient_name_array = patient_name_text.replace("\n", "").replace("PATIENTS",
                                                                                                 "").replace(
                                    "NAME", "").replace(
                                    "PATIENTS", "").replace("AND", "").strip().split(" ")
                                print("araays is ", patient_name_array)

                                if len(patient_name_text) > 2:
                                    patient_name_text = str(patient_name_array[0]) + " " + str(patient_name_array[1])
                                    print("new test: ", patient_name_text)
                            except Exception as e:
                                print("errpr: ", e)

                            row_data.append(
                                patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").replace(
                                    "PATIENTS", "").replace("AND", "").strip())

                            if len(row_data) > 1:
                                data.append(row_data)
                                patient_name_found = 1

                if (insurer_found == 0):
                    row_data = []
                    row_data.append("Insurer name")
                    row_data.append("Test Insurer")
                    data.append(row_data)

                if (policy_holder_found == 0):
                    row_data = []
                    row_data.append("policy holder")
                    for j in data:
                        if j[0] == 'Patient Name':
                            row_data.append(j[1])
                            break
                    else:
                        row_data.append("Test Holder")
                    data.append(row_data)

                print("data from new function: ", data)
                return data

            if "health insurance claim form" in provider_name.lower():
                print("in health ins")
                row_data = []
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                insurer_coords = page.search_for("INSURANCE PLAN NAME")
                insurer_coords2 = page.search_for("IS THERE ANOTHER")
                if insurer_coords or insurer_coords2:
                    if insurer_coords:
                        i_rect = fitz.Rect(insurer_coords[0].x0 - 10, insurer_coords[0].y0 + 5,
                                           insurer_coords[0].x1 + 150,
                                           insurer_coords[0].y1 + 15)
                        insurer_name_text = page.get_textbox(i_rect)
                        if insurer_name_text:
                            row_data = []
                            row_data.append("Insurer name")
                            insurer_name_text = self.remove_special_characters(insurer_name_text)
                            row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)
                                insurer_found = 1
                    elif insurer_coords2:
                        i_rect = fitz.Rect(insurer_coords2[0].x0 - 10, insurer_coords2[0].y0 - 25,
                                           insurer_coords2[0].x1 + 150,
                                           insurer_coords2[0].y1 - 5)
                        insurer_name_text = page.get_textbox(i_rect)
                        if insurer_name_text:
                            row_data = []
                            row_data.append("Insurer name")
                            insurer_name_text = self.remove_special_characters(insurer_name_text)
                            row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                provider_coords = page.search_for("BILLING PROVIDER INFO")
                provider_coords2 = page.search_for("TOTAL CHARGE")
                if provider_coords or provider_coords2:
                    if provider_coords:
                        i_rect = fitz.Rect(provider_coords[0].x0 - 15, provider_coords[0].y0 + 10,
                                           provider_coords[0].x1 + 180,
                                           provider_coords[0].y1 + 20)
                        provider_coords_text = page.get_textbox(i_rect)
                        if provider_coords_text:
                            row_data = []
                            row_data.append("Provider name")
                            row_data.append(provider_coords_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)
                    elif provider_coords2:
                        i_rect = fitz.Rect(provider_coords2[0].x0 - 15, provider_coords2[0].y0 + 35,
                                           provider_coords2[0].x1 + 185,
                                           provider_coords2[0].y1 + 45)
                        provider_coords_text = page.get_textbox(i_rect)
                        if provider_coords_text:
                            row_data = []
                            row_data.append("Provider name")
                            row_data.append(provider_coords_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                policy_holder = page.search_for("INSURED’S NAME")
                policy_holder2 = page.search_for("SEX")
                if policy_holder or policy_holder2:
                    if policy_holder:
                        i_rect = fitz.Rect(policy_holder[0].x0 - 10, policy_holder[0].y0 + 10,
                                           policy_holder[0].x1 + 150,
                                           policy_holder[0].y1 + 20)
                        policy_holder_name_text = page.get_textbox(i_rect)
                        if policy_holder_name_text:
                            row_data = []
                            row_data.append("policy holder")
                            policy_holder_name_text = self.remove_special_characters(policy_holder_name_text)
                            row_data.append(policy_holder_name_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                    elif policy_holder2:
                        i_rect = fitz.Rect(policy_holder2[0].x0 + 20, policy_holder2[0].y0 + 5,
                                           policy_holder2[0].x1 + 150,
                                           policy_holder2[0].y1 + 15)
                        policy_holder_name_text = page.get_textbox(i_rect)
                        if policy_holder_name_text:
                            row_data = []
                            row_data.append("policy holder")
                            policy_holder_name_text = self.remove_special_characters(policy_holder_name_text)
                            row_data.append(policy_holder_name_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                policy_num = page.search_for("INSURED’S NAME")
                policy_num2 = page.search_for("OTHER")
                if policy_num or policy_num2:
                    if policy_num:
                        i_rect = fitz.Rect(policy_num[0].x0 - 10, policy_num[0].y0 - 20, policy_num[0].x1 + 150,
                                           policy_num[0].y1 - 5)
                        policy_num_text = page.get_textbox(i_rect)
                        if policy_num_text:
                            row_data = []
                            row_data.append("Policy number")
                            row_data.append(policy_num_text.strip().replace("\n", " ").replace("NAME", ""))
                            if len(row_data) > 1:
                                data.append(row_data)
                    elif policy_num2:
                        if policy_num2:
                            i_rect = fitz.Rect(policy_num2[0].x0 + 15, policy_num2[0].y0 + 5, policy_num2[0].x1 + 150,
                                               policy_num2[0].y1 + 15)
                            policy_num2_text = page.get_textbox(i_rect)
                            if policy_num2_text:
                                row_data = []
                                row_data.append("Policy number")
                                row_data.append(policy_num2_text.strip().replace("\n", " ").replace("NAME", ""))
                                if len(row_data) > 1:
                                    data.append(row_data)

                patients_name = page.search_for("PATIENT’S NAME")
                patients_name2 = page.search_for("MEDICARE")
                if patients_name or patients_name2:
                    if patients_name:
                        i_rect = fitz.Rect(patients_name[0].x0 - 15, patients_name[0].y0 + 5, patients_name[0].x1 + 135,
                                           patients_name[0].y1 + 20)
                        patient_name_text = page.get_textbox(i_rect)
                        if patient_name_text:
                            row_data = []
                            row_data.append("Patient Name")
                            patient_name_text = self.remove_special_characters(patient_name_text)
                            row_data.append(
                                patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").strip())
                            if len(row_data) > 1:
                                data.append(row_data)
                    elif patients_name2:
                        i_rect = fitz.Rect(patients_name2[0].x0 - 15, patients_name2[0].y0 + 30,
                                           patients_name2[0].x1 + 125,
                                           patients_name2[0].y1 + 40)
                        patient_name_text = page.get_textbox(i_rect)
                        if patient_name_text:
                            row_data = []
                            row_data.append("Patient Name")
                            patient_name_text = self.remove_special_characters(patient_name_text)
                            row_data.append(
                                patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").strip())
                            if len(row_data) > 1:
                                data.append(row_data)

                date_of_acc = page.search_for("DATE OF CURRENT")
                date_of_acc2 = page.search_for("NAME OF")
                if date_of_acc or date_of_acc2:
                    if date_of_acc:
                        i_rect = fitz.Rect(date_of_acc[0].x0 - 10, date_of_acc[0].y0 + 5, date_of_acc[0].x1 + 40,
                                           date_of_acc[0].y1 + 15)
                        date_of_acc_text = page.get_textbox(i_rect)
                        if date_of_acc_text:
                            row_data = []
                            row_data.append("Date of accident")
                            date_of_acc_text = self.new_format_date(date_of_acc_text)
                            row_data.append(date_of_acc_text.strip().replace("\n", " "))
                            if len(row_data) > 1:
                                data.append(row_data)

                    elif date_of_acc2:
                        i_rect = fitz.Rect(date_of_acc2[0].x0 - 10, date_of_acc2[0].y0 - 20, date_of_acc2[0].x1 + 40,
                                           date_of_acc2[0].y1 - 5)
                        date_of_acc_text = page.get_textbox(i_rect)
                        if date_of_acc_text:
                            row_data = []
                            row_data.append("Date of accident")
                            date_of_acc_text = self.new_format_date(date_of_acc_text)
                            row_data.append(date_of_acc_text.strip().replace("\n", " "))
                            if len(row_data) > 1:
                                data.append(row_data)

                date_of_service = page.search_for("DATE(S) OF SERVICE")
                date_of_service2 = page.search_for("From")
                if date_of_service or date_of_service2:
                    if date_of_service:
                        i_rect = fitz.Rect(date_of_service[0].x0 - 30, date_of_service[0].y0 + 35,
                                           date_of_service[0].x1,
                                           date_of_service[0].y1 + 40)
                        date_of_service_text = page.get_textbox(i_rect)
                        if date_of_service_text:
                            row_data = []
                            row_data.append("Date of service")
                            date_of_service_text = self.new_format_date(date_of_service_text)
                            row_data.append(date_of_service_text.strip().replace("\n", ""))
                            if len(row_data) > 1:
                                data.append(row_data)
                    elif date_of_service2:
                        i_rect = fitz.Rect(date_of_service2[-1].x0 - 25, date_of_service2[-1].y0 + 25,
                                           date_of_service2[-1].x1 + 30,
                                           date_of_service2[-1].y1 + 40)
                        date_of_service_text = page.get_textbox(i_rect)
                        if date_of_service_text:
                            row_data = []
                            row_data.append("Date of service")
                            date_of_service_text = self.new_format_date(date_of_service_text)
                            row_data.append(date_of_service_text.strip().replace("\n", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                date_of_birth = page.search_for("PATIENT’S BIRTH")
                date_of_birth2 = page.search_for("CHAMPVA")
                if date_of_birth or date_of_birth2:
                    if date_of_birth:
                        i_rect = fitz.Rect(date_of_birth[0].x0 - 10, date_of_birth[0].y0 + 10, date_of_birth[0].x1 + 40,
                                           date_of_birth[0].y1 + 20)
                        date_of_birth_text = page.get_textbox(i_rect)
                        if date_of_birth_text:
                            row_data = []
                            row_data.append("dob")
                            date_of_birth_text = self.new_format_date(date_of_birth_text)
                            row_data.append(date_of_birth_text.strip().replace("\n", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                    elif date_of_birth2:
                        i_rect = fitz.Rect(date_of_birth2[0].x0 + 30, date_of_birth2[0].y0 + 30,
                                           date_of_birth2[0].x1 + 80,
                                           date_of_birth2[0].y1 + 40)
                        date_of_birth_text = page.get_textbox(i_rect)
                        if date_of_birth_text:
                            row_data = []
                            row_data.append("dob")
                            date_of_birth_text = self.new_format_date(date_of_birth_text)
                            row_data.append(date_of_birth_text.strip().replace("\n", ""))
                            if len(row_data) > 1:
                                data.append(row_data)

                total_charges = page.search_for("TOTAL CHARGE")
                total_charges2 = page.search_for("BILLING PROVIDER")
                if total_charges or total_charges2:
                    if total_charges:
                        i_rect = fitz.Rect(total_charges[0].x0 + 20, total_charges[0].y0 + 5, total_charges[0].x1 + 80,
                                           total_charges[0].y1 + 20)
                        total_charges_text = page.get_textbox(i_rect)
                        if total_charges_text:
                            row_data = []
                            row_data.append("Cost")
                            total_charges_text = self.clean_cost(total_charges_text)
                            row_data.append(total_charges_text.strip().replace("\n", " "))
                            if len(row_data) > 1:
                                data.append(row_data)

                    elif total_charges2:
                        i_rect = fitz.Rect(total_charges2[0].x0 + 15, total_charges2[0].y0 - 15,
                                           total_charges2[0].x1 + 50,
                                           total_charges2[0].y1 - 5)
                        total_charges_text = page.get_textbox(i_rect)
                        if total_charges_text:
                            row_data = []
                            row_data.append("Cost")
                            total_charges_text = self.clean_cost(total_charges_text)
                            row_data.append(total_charges_text.strip().replace("\n", " "))
                            if len(row_data) > 1:
                                data.append(row_data)
                print("end health ins")
                print("data from new function: ", data)
                return data

            if "opti health" in provider_name.lower():
                row_data = []
                row_data.append("Provider Name")
                row_data.append("Opti Health Corp")
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                insurer_coords = page.search_for("NAME AND ADDRESS OF INSURER")
                if insurer_coords:
                    i_rect = fitz.Rect(insurer_coords[0].x0 - 15, insurer_coords[0].y0 + 10, insurer_coords[0].x1 + 90,
                                       insurer_coords[0].y1 + 25)
                    insurer_name_text = page.get_textbox(i_rect)
                    if insurer_name_text:
                        row_data = []
                        row_data.append("Insurer name")
                        insurer_name_text = self.remove_special_characters(insurer_name_text)
                        row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", ""))
                        if len(row_data) > 1:
                            data.append(row_data)
                            insurer_found = 1

                policy_number = page.search_for("POLICY NUMBER")
                if policy_number:
                    i_rect = fitz.Rect(policy_number[0].x0 - 40, policy_number[0].y0 + 5, policy_number[0].x1 + 50,
                                       policy_number[0].y1 + 15)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("policy number")
                        row_data.append(policy_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                policy_holder = page.search_for("POLICYHOLDER")
                if policy_holder:
                    i_rect = fitz.Rect(policy_holder[0].x0 - 40, policy_holder[0].y0 + 5, policy_holder[0].x1 + 50,
                                       policy_holder[0].y1 + 15)
                    policy_holder_text = page.get_textbox(i_rect)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(policy_holder_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                date_of_acc = page.search_for("DATE OF ACCIDENT")
                if date_of_acc:
                    i_rect = fitz.Rect(date_of_acc[0].x0 - 40, date_of_acc[0].y0 + 5, date_of_acc[0].x1 + 50,
                                       date_of_acc[0].y1 + 15)
                    date_of_acc_text = page.get_textbox(i_rect)
                    if date_of_acc_text:
                        row_data = []
                        row_data.append("Date of accident")
                        row_data.append(date_of_acc_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                claim_number = page.search_for("CLAIM NUMBER")
                if claim_number:
                    i_rect = fitz.Rect(claim_number[0].x0 - 40, claim_number[0].y0 + 5, claim_number[0].x1 + 80,
                                       claim_number[0].y1 + 15)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number")
                        row_data.append(claim_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                patient_name = page.search_for("PATIENT'S NAME AND ADDRESS")
                if patient_name:
                    i_rect = fitz.Rect(patient_name[0].x0 + 130, patient_name[0].y0 - 5,
                                       patient_name[0].x1 + 180,
                                       patient_name[0].y1 + 5)
                    patient_name_text = page.get_textbox(i_rect)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        patient_name_text = self.remove_special_characters(patient_name_text)
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                dob_coords = page.search_for("DATE OF BIRTH")
                if dob_coords:
                    i_rect = fitz.Rect(dob_coords[0].x0 - 10, dob_coords[0].y0 + 5, dob_coords[0].x1 + 50,
                                       dob_coords[0].y1 + 10)
                    dob_coords_text = page.get_textbox(i_rect)
                    if dob_coords_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(dob_coords_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                print("data from new function: ", data)
                return data

            if "dassa orthopedic" in provider_name.lower():
                row_data = []
                row_data.append("Provider Name")
                row_data.append("Dassa Orthopedic Medical Services P.C.")
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                insurer_coords = page.search_for("NAME AND ADDRESS OF INSURER:")
                if insurer_coords:
                    i_rect = fitz.Rect(insurer_coords[0].x0 + 5, insurer_coords[0].y0 + 10, insurer_coords[0].x1 + 170,
                                       insurer_coords[0].y1 + 25)
                    insurer_name_text = page.get_textbox(i_rect)
                    if insurer_name_text:
                        row_data = []
                        row_data.append("Insurer name")
                        insurer_name_text = self.remove_special_characters(insurer_name_text)
                        row_data.append(insurer_name_text.strip().replace("\n", "").replace("NAME", ""))
                        if len(row_data) > 1:
                            data.append(row_data)
                            insurer_found = 1

                patient_name = page.search_for("PATIENT'S NAME AND ADDRESS")
                if patient_name:
                    i_rect = fitz.Rect(patient_name[0].x0 + 5, patient_name[0].y0 + 10,
                                       patient_name[0].x1 + 80,
                                       patient_name[0].y1 + 25)
                    patient_name_text = page.get_textbox(i_rect)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        patient_name_text = self.remove_special_characters(patient_name_text)
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENTS", "").replace("NAME", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                date_of_acc = page.search_for("DATE OF ACCIDENT")
                if date_of_acc:
                    i_rect = fitz.Rect(date_of_acc[0].x0 - 5, date_of_acc[0].y0 + 5, date_of_acc[0].x1 + 50,
                                       date_of_acc[0].y1 + 25)
                    date_of_acc_text = page.get_textbox(i_rect)
                    if date_of_acc_text:
                        row_data = []
                        row_data.append("date of accident")
                        row_data.append(date_of_acc_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                claim_number = page.search_for("FILE NUMBER")
                if claim_number:
                    i_rect = fitz.Rect(claim_number[0].x0 - 5, claim_number[0].y0 + 5, claim_number[0].x1 + 50,
                                       claim_number[0].y1 + 25)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number")
                        row_data.append(claim_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                policy_holder = page.search_for("POLICY HOLDER")
                if policy_holder:
                    i_rect = fitz.Rect(policy_holder[0].x0 - 5, policy_holder[0].y0 + 5, policy_holder[0].x1 + 60,
                                       policy_holder[0].y1 + 25)
                    policy_holder_text = page.get_textbox(i_rect)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(policy_holder_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                policy_number = page.search_for("POLICY NUMBER")
                if policy_number:
                    i_rect = fitz.Rect(policy_number[0].x0 - 5, policy_number[0].y0 + 5, policy_number[0].x1 + 60,
                                       policy_number[0].y1 + 25)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(policy_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                dob_coords = page.search_for("DATE OF BIRTH")
                if dob_coords:
                    i_rect = fitz.Rect(dob_coords[0].x0 - 10, dob_coords[0].y0 + 5,
                                       dob_coords[0].x1 + 80,
                                       dob_coords[0].y1 + 20)
                    dob_text = page.get_textbox(i_rect)
                    print("dateofbirth", dob_text)
                    if dob_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(
                            self.get_date(dob_text).replace("\n", "").replace("DATE", "").replace("OF", "").replace(
                                "BIRTH", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                print("data from new function: ", data)
                return data
            if "denial of claim" in provider_name.lower():
                print("denial of claim form found")
                row_data = []
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                provider_name = page.search_for("APPLICANT FOR BENEFITS")
                if provider_name:
                    i_rect = fitz.Rect(provider_name[0].x0 - 20, provider_name[0].y0 + 5, provider_name[0].x1 + 80,
                                       provider_name[0].y1 + 15)
                    provider_name_text = page.get_textbox(i_rect)
                    if provider_name_text:
                        row_data = []
                        row_data.append("Provider Name denials")
                        provider_name_text = self.remove_special_characters(provider_name_text)
                        row_data.append(provider_name_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                insurer_coords = page.search_for("NAME, ADDRESS AND NAIC")
                insurer_coords2 = False  # page.search_for("AND ADDRESS OF")
                insurer_coords3 = page.search_for("TO INSURER")
                if insurer_coords or insurer_coords2 or insurer_coords3:
                    if insurer_coords:
                        i_rect = fitz.Rect(insurer_coords[0].x0 - 5, insurer_coords[0].y0 + 8,
                                           insurer_coords[0].x1 + 80,
                                           insurer_coords[0].y1 + 30)
                        insurer_name_text = page.get_textbox(i_rect)
                        print("in 1st", insurer_name_text)
                        if insurer_name_text:
                            row_data = []
                            row_data.append("Insurer name denials")
                            insurer_name_text = self.remove_special_characters(insurer_name_text)
                            row_data.append(
                                insurer_name_text.strip().replace("AND ADDRESS OF SELF", "").replace("\n", " ").replace(
                                    "NAME", "")[:27])
                            if len(row_data) > 1:
                                data.append(row_data)
                                insurer_found = 1
                    elif insurer_coords2:
                        i_rect = fitz.Rect(insurer_coords2[0].x0 - 5, insurer_coords2[0].y0 + 10,
                                           insurer_coords2[0].x1 + 80,
                                           insurer_coords2[0].y1 + 20)
                        insurer_name_text = page.get_textbox(i_rect)
                        print("in 2nd", insurer_name_text)
                        if insurer_name_text:
                            row_data = []
                            row_data.append("Insurer name denials")
                            insurer_name_text = self.remove_special_characters(insurer_name_text)
                            row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", "")[:27])
                            if len(row_data) > 1:
                                data.append(row_data)
                                insurer_found = 1

                    elif insurer_coords3:

                        i_rect = fitz.Rect(insurer_coords3[0].x0 - 5, insurer_coords3[0].y0 + 15,
                                           insurer_coords3[0].x1 + 100,
                                           insurer_coords3[0].y1 + 45)
                        insurer_name_text = page.get_textbox(i_rect)
                        print("in 3rd", insurer_name_text)
                        if insurer_name_text:
                            row_data = []
                            row_data.append("Insurer name denials")
                            insurer_name_text = self.remove_special_characters(insurer_name_text)
                            row_data.append(insurer_name_text.strip().replace("\n", " ").replace("NAME", "")[:27])
                            if len(row_data) > 1:
                                data.append(row_data)
                                insurer_found = 1

                policy_holder = page.search_for("POLICYHOLDER")
                if policy_holder:
                    i_rect = fitz.Rect(policy_holder[0].x0 - 20, policy_holder[0].y0 + 5, policy_holder[0].x1 + 80,
                                       policy_holder[0].y1 + 20)
                    policy_holder_text = page.get_textbox(i_rect)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("Policy holder denials")
                        policy_holder_text = self.remove_special_characters(policy_holder_text)
                        row_data.append(policy_holder_text.strip().replace("\n", " ").replace("NAME", ""))
                        if len(row_data) > 1:
                            data.append(row_data)

                policy_number = page.search_for("POLICY NUMBER")
                if policy_number:
                    i_rect = fitz.Rect(policy_number[0].x0 - 20, policy_number[0].y0 + 5, policy_number[0].x1 + 50,
                                       policy_number[0].y1 + 15)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("Policy number denials")
                        policy_number_text = self.remove_special_characters(policy_number_text)
                        row_data.append(policy_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                date_of_acc = page.search_for("DATE OF ACCIDENT")
                if date_of_acc:
                    i_rect = fitz.Rect(date_of_acc[0].x0 - 20, date_of_acc[0].y0 + 5, date_of_acc[0].x1 + 50,
                                       date_of_acc[0].y1 + 15)
                    date_of_acc_text = page.get_textbox(i_rect)
                    if date_of_acc_text:
                        row_data = []
                        row_data.append("date of accident denials")
                        date_of_acc_text = self.get_date(date_of_acc_text)
                        row_data.append(date_of_acc_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                patients_name = page.search_for("DATE OF ACCIDENT")
                if policy_number:
                    i_rect = fitz.Rect(patients_name[0].x0 + 100, patients_name[0].y0 + 5, patients_name[0].x1 + 180,
                                       patients_name[0].y1 + 15)
                    patients_name_text = page.get_textbox(i_rect)
                    if patients_name_text:
                        row_data = []
                        row_data.append("Patient Name denials")
                        patients_name_text = self.remove_special_characters(patients_name_text)
                        row_data.append(patients_name_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                claim_number = page.search_for("CLAIM NUMBER")
                if claim_number:
                    i_rect = fitz.Rect(claim_number[0].x0 - 20, claim_number[0].y0 + 5, claim_number[0].x1 + 50,
                                       claim_number[0].y1 + 20)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number denials")
                        claim_number_text = self.remove_special_characters(claim_number_text)
                        row_data.append(claim_number_text.strip().replace("\n", " "))
                        if len(row_data) > 1:
                            data.append(row_data)

                text_instance = page.search_for("Period of bill")

                text_instance1 = page.search_for("25. Period of")

                print("period found")
                if text_instance or text_instance1:
                    text_instance = text_instance if text_instance else text_instance1
                    i_rect = fitz.Rect(text_instance[0].x0 - 20, text_instance[0].y0, text_instance[-1].x1 + 55,
                                       text_instance[-1].y1 + 26)
                    dos = page.get_textbox(i_rect)
                    print("dos is: ", dos)
                    if dos:
                        row_data = []
                        row_data.append("Date of service denials")

                        row_data.append(dos.strip().split("\n")[1])
                        if len(row_data) > 1:
                            data.append(row_data)

                text_instance = page.search_for("Amount of bill")
                print("amount found")
                if text_instance:
                    i_rect = fitz.Rect(text_instance[0].x0 - 20, text_instance[0].y0 + 8, text_instance[-1].x1 + 35,
                                       text_instance[-1].y1 + 16)
                    cost = page.get_textbox(i_rect)
                    print("Cost raw: ", cost)
                    if cost:
                        row_data = []
                        row_data.append("Cost denials")
                        if "$" in cost:
                            cost = cost[cost.index("$"):]
                        matches = re.findall(r'[0-9.]+', cost)
                        if matches:
                            row_data.append(matches[0].strip())
                            if len(row_data) > 1:
                                data.append(row_data)

                print("data from new function: ", data)
                return data

            if "englinton" in provider_name.lower() or "lomis" in provider_name.lower() or "ace emergent" in provider_name.lower() or "greater health" in provider_name.lower() or "colin clarke" in provider_name.lower():

                # provider
                row_data = []
                row_data.append("Provider Name")
                englinton = page.search_for("Englinton")
                lomis = page.search_for("LOMIS")
                ace = page.search_for("ACE EMERGENT")
                greater = page.search_for("Greater Health")
                colin = page.search_for("COLIN CLARKE")
                if englinton:
                    row_data.append("Englinton")
                elif lomis:
                    row_data.append("LOMIS")
                elif ace:
                    row_data.append("ACE EMERGENT")
                elif greater:
                    row_data.append("Greater Health Thru")
                elif colin:
                    row_data.append("COLIN CLARKE MD")
                # row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                if len(row_data) > 1:
                    data.append(row_data)

                lomis_insurer = page.search_for("VERIFICATION")
                if lomis_insurer and insurer_found == 0:
                    i_rect = fitz.Rect(lomis_insurer[0].x0 - 100, lomis_insurer[0].y0 + 20, lomis_insurer[0].x1 + 80,
                                       lomis_insurer[0].y1 + 60)
                    insurer_name_text = page.get_textbox(i_rect)

                    if insurer_name_text:
                        row_data = []
                        row_data.append("Insurer name")
                        row_data.append(insurer_name_text.strip().split("\n")[0])
                        if len(row_data) > 1:
                            data.append(row_data)
                            insurer_found = 1

                # lomis_provider = page.search_for("VERIFICATION")
                # if lomis_provider:
                #         i_rect = fitz.Rect(lomis_provider[0].x0 - 50, lomis_provider[0].y0 + 90, lomis_provider[0].x1 + 50,
                #                            lomis_provider[0].y1 + 115)
                #         provider_name_text = page.get_textbox(i_rect)
                #         if provider_name_text:
                #             row_data = []
                #             row_data.append("Provider name")
                #             row_data.append(provider_name_text.strip().replace("\n", " "))
                #             if len(row_data) > 1:
                #                 data.append(row_data)

                policy_holder = page.search_for("POLICY HOLDER")
                if policy_holder and policy_holder_found == 0:
                    i_rect = fitz.Rect(policy_holder[0].x0 - 55, policy_holder[0].y0 + 5,
                                       policy_holder[0].x1 + 50,
                                       policy_holder[-1].y1 + 15)
                    policy_holder_text = page.get_textbox(i_rect)
                    print("policy holder found is : ", policy_holder_text)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(policy_holder_text.replace("\n", "").replace("POLICYHOLDER", "").strip())
                        if len(row_data) > 1:
                            data.append(row_data)
                            policy_holder_found = 1
                if not policy_holder and lomis_insurer and policy_holder_found == 0:
                    i_rect = fitz.Rect(lomis_insurer[0].x0, lomis_insurer[0].y0 + 80,
                                       lomis_insurer[0].x1 + 50,
                                       lomis_insurer[-1].y1 + 95)
                    policy_holder_text = page.get_textbox(i_rect)
                    if policy_holder_text:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(policy_holder_text.replace("\n", "").replace("POLICYHOLDER", "").strip())
                        if len(row_data) > 1:
                            data.append(row_data)
                            policy_holder_found = 1

                policy_number = page.search_for("POLICY NUMBER")
                if policy_number:
                    i_rect = fitz.Rect(policy_number[0].x0 - 25, policy_number[0].y0 + 5,
                                       policy_number[0].x1 + 31,
                                       policy_number[0].y1 + 15)
                    policy_number_text = page.get_textbox(i_rect)
                    if policy_number_text:
                        row_data = []
                        row_data.append("policy number")
                        row_data.append(
                            policy_number_text.replace("\n", "").replace("POLICY", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                date_of_accident = page.search_for("DATE OF ACCIDENT")
                date_of_accident2 = page.search_for("DATE OF")
                if date_of_accident or date_of_accident2:
                    if date_of_accident:
                        i_rect = fitz.Rect(date_of_accident[0].x0 - 15, date_of_accident[0].y0 + 5,
                                           date_of_accident[0].x1 + 40,
                                           date_of_accident[0].y1 + 15)
                        date_of_accident_text = page.get_textbox(i_rect)
                        if date_of_accident_text:
                            row_data = []
                            row_data.append("accident date")
                            row_data.append(
                                self.get_date(date_of_accident_text).replace("\n", "").replace("DATE", "").replace("OF",
                                                                                                                   "").replace(
                                    "ACCIDENT",
                                    "").strip())

                            if len(row_data) > 1:
                                data.append(row_data)
                                date_of_accident_found = 1
                    elif date_of_accident2:
                        i_rect = fitz.Rect(date_of_accident2[0].x0 - 15, date_of_accident2[0].y0 + 5,
                                           date_of_accident2[0].x1 + 40,
                                           date_of_accident2[0].y1 + 15)
                        date_of_accident_text = page.get_textbox(i_rect)
                        if date_of_accident_text:
                            row_data = []
                            row_data.append("accident date")
                            row_data.append(
                                self.get_date(date_of_accident_text).replace("\n", "").replace("DATE", "").replace("OF",
                                                                                                                   "").replace(
                                    "ACCIDENT",
                                    "").strip())

                            if len(row_data) > 1:
                                data.append(row_data)
                                date_of_accident_found = 1

                claim_number = page.search_for("CLAIM NUMBER")
                if claim_number:
                    i_rect = fitz.Rect(claim_number[0].x0 - 35, claim_number[0].y0 + 5,
                                       claim_number[0].x1 + 45,
                                       claim_number[0].y1 + 15)
                    claim_number_text = page.get_textbox(i_rect)
                    if claim_number_text:
                        row_data = []
                        row_data.append("claim number")
                        row_data.append(
                            claim_number_text.replace("\n", "").replace("CLAIM", "").replace("NUMBER", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)

                patient_name = page.search_for("PATIENT'S NAME")

                if patient_name and patient_name_found == 0:
                    i_rect = fitz.Rect(patient_name[0].x0 - 25, patient_name[0].y0 + 5,
                                       patient_name[0].x1 + 90,
                                       patient_name[0].y1 + 15)
                    patient_name_text = page.get_textbox(i_rect)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENT'S", "").replace("NAME:", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            patient_name_found = 1

                if not patient_name and patient_name_found == 0:
                    patient = page.search_for("IF YOU HAVE")
                    i_rect = fitz.Rect(patient[0].x0 - 5, patient[0].y0 + 35,
                                       patient[-1].x1 + 40,
                                       patient[-1].y1 + 50)
                    patient_name_text = page.get_textbox(i_rect)
                    print("patient name from 2: ", patient_name_text)
                    if patient_name_text:
                        row_data = []
                        row_data.append("Patient Name")
                        row_data.append(
                            patient_name_text.replace("\n", "").replace("PATIENT'S", "").replace("NAME:", "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            patient_name_found = 1

                dob_coords = page.search_for("PATIENT'S NAME")
                if dob_coords:
                    i_rect = fitz.Rect(dob_coords[0].x0 - 25, dob_coords[0].y0 + 30,
                                       dob_coords[0].x1 + 55,
                                       dob_coords[0].y1 + 46)
                    dob_text = page.get_textbox(i_rect)
                    if dob_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(
                            self.get_date(dob_text).replace("\n", "").replace("PATIENT'S", "").replace("BIRTH:",
                                                                                                       "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            dob_found = 1

                dob_coords2 = page.search_for("OF BIRTH")

                if not dob_coords and dob_coords2 and dob_found == 0:
                    i_rect = fitz.Rect(dob_coords2[0].x0 - 35, dob_coords2[0].y0 + 5,
                                       dob_coords2[0].x1 + 10,
                                       dob_coords2[0].y1 + 10)
                    dob_text = page.get_textbox(i_rect)
                    print(dob_text)
                    if dob_text:
                        row_data = []
                        row_data.append("dob")
                        row_data.append(
                            self.get_date(dob_text).replace("\n", "").replace("PATIENT'S", "").replace("BIRTH:",
                                                                                                       "").strip())

                        if len(row_data) > 1:
                            data.append(row_data)
                            dob_found = 1

                if (insurer_found == 0):
                    row_data = []
                    row_data.append("Insurer name")
                    row_data.append("Test Insurer")
                    data.append(row_data)

                if (policy_holder_found == 0):
                    row_data = []
                    row_data.append("policy holder")
                    for j in data:
                        if j[0] == 'Patient Name':
                            row_data.append(j[1])
                            break
                    else:
                        row_data.append("Test Holder")
                    data.append(row_data)

                print("data from new function: ", data)
                return data

        elif type == 2:
            # add data fetching logic for second page (central park)
            print("second page")
            data = []
            row_data = []
            try:
                dates = self.date_extractor(pdf)
                if dates:
                    row_data.append("Date of service")
                    row_data.append(str(dates["minimum_date"]) + "-" + str(dates["maximum_date"]))
                    data.append(row_data)
                charges = page.search_for("TOTAL CHARGES TO")
                if charges:
                    i_rect = fitz.Rect(charges[-1].x0 + 50, charges[-1].y0, charges[-1].x1 + 200, charges[-1].y1)
                    total_charges = page.get_textbox(i_rect)
                    print("total chargaeas : ", total_charges)
                    if total_charges:
                        row_data.append("Charges")
                        row_data.append(total_charges)
                        data.append(row_data)
                row_data.append("type")
                row_data.append("2")
                print("daataa: ", data)
                return data
            except Exception as e:
                print("error: ", e)

            pass

    def get_date(self, input_string1):
        date_pattern = r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b'

        # Find all matches of the date pattern in the input string
        matched_dates = re.findall(date_pattern, input_string1)

        if matched_dates:
            return matched_dates[0]
        else:
            return input_string1

    def date_extractor(self, pdf_path):
        date_pattern = r"\d{1,2}/\d{1,2}/\d{4}|\d{1,2}/\d{1,2}/\d{2}|\d{1,2}/[a-zA-Z]{3,}/\d{4}"

        try:
            with pdfplumber.open(pdf_path) as pdf:
                all_dates = []
                for page in pdf.pages:
                    page_text = page.extract_text()

                    dates = re.findall(date_pattern, page_text[page_text.index("REPORT OF"):])
                    all_dates.extend(dates)

                date_objects = [datetime.strptime(date, '%m/%d/%Y') for date in all_dates]
                min_date = min(date_objects)
                max_date = max(date_objects)

            return {"minimum_date": min_date.strftime('%m/%d/%Y'), "maximum_date": max_date.strftime('%m/%d/%Y')}
        except Exception as e:
            print(f"An error occurred while extracting dates: {e}")

    def clean_paragraph(self, paragraph):
        try:
            cleaned_paragraph = re.sub(r'[^a-zA-Z\n\s]', '', paragraph)
            cleaned_paragraph = "\n".join([line for line in cleaned_paragraph.split("\n") if line.strip()])
            return cleaned_paragraph
        except Exception as e:
            return paragraph

    #  Function to calulate the distance based data extraction algorithm
    # author Rishab
    def textByCordDistance(self, img, filename, filetype=1):
        doc = fitz.open(self.tempPath + "metadata.pdf")
        page = doc[0]
        provider_names = ["Central Park", "Metro Point", "Englinton", "LOMIS", "ACE EMERGENT", "Greater Health",
                          "HEALTH INSURANCE CLAIM FORM", "COLIN CLARKE", "Opti Health", "Dassa Orthopedic"
            , "DENIAL OF CLAIM"]

        for i in provider_names:
            provider_name_from_pdf = page.search_for(i)
            print("provider name is: ", provider_name_from_pdf, i)
            second_page_1 = page.search_for("REPORT OF SERVICES RENDERED")
            second_page_2 = page.search_for("TOTAL CHARGES TO")
            if provider_name_from_pdf and not second_page_1 and not second_page_2:
                type = 1
                return self.known_provider_data_fetch(self.tempPath + "metadata.pdf", i, type)
            elif second_page_1 or second_page_2:
                type = 2
                return self.processHTMLFromPDF()

        # blocks = page.getText("blocks")
        # blocks.sort(key=lambda block: block[1])  # sort vertically ascending
        # print("Block")
        # for b in blocks:
        #     print(b[4])  # the text part of each block
        data = []
        cat_type = "Medical Reports"
        patient_name_add = 0
        accident_date_add = 0
        insurer_found = 0
        provider_found = 0
        insurer_rep_found = 0
        policy_holder = 0
        aod_found = 0
        #  Alternate new logic to fetch the data based on text search method 2020-11-25 12:50:37
        if filetype in [1, 4, 5, 7, 8, 9, 14]:
            insurer_text = page.search_for("INSURER:")
            claim_rep_ext = page.search_for("CLAIMS REPRESENTATIVE:")
            name_add_insurer = page.search_for("NAME AND ADDRESS OF INSURER:")
            if len(claim_rep_ext) > 0 and len(insurer_text) > 0 and len(name_add_insurer) == 0:
                text_instances = page.search_for("INSURER:")
                print("INSURER:")
                if len(text_instances) > 0:
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 20, cords_rect.y0 + 5, cords_rect.x1 + 100, cords_rect.y1 + 50)
                    print(text_instances[0])
                    print(i_rect)
                    row_data = []
                    row_data.append("Insurer name")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    insurer_found = 1
                    if (self.cleanString(text_arr[0].strip()) == ""):
                        text_arr[0] = "Test Insurer"
                    row_data.append(self.handleInsurerNameAPI(self.cleanString(text_arr[0].strip())))
                    if len(row_data) > 1:
                        data.append(row_data)
                text_instances = page.search_for("CLAIMS REPRESENTATIVE")
                if len(text_instances) > 0:
                    print("CLAIMS REPRESENTATIVE")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0, cords_rect.y0 + 10, cords_rect.x1 + 50, cords_rect.y1 + 50)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Insurer representative")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    if len(row_data) > 1:
                        insurer_rep_found = 1
                        data.append(row_data)

                text_instances = page.search_for("PROVIDER:")
                if len(text_instances) > 0:
                    print("PROVIDER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y1, cords_rect.x1 + 200, cords_rect.y1 + 20)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Provider Name")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.handleProviderNameAPI(self.cleanString(text_arr[0].strip())))
                    if len(row_data) > 1:
                        provider_found = 1
                        data.append(row_data)

                text_instances = page.search_for("POLICYHOLDER")
                if len(text_instances) > 0:
                    print("POLICYHOLDER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 35, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 30)
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    policyHolder_name = self.cleanString(text_arr[0].strip())
                    try:
                        if self.cleanString(text_arr[1].strip()) != "":
                            policyHolder_name = policyHolder_name + " " + self.cleanString(text_arr[1].strip())
                    except:
                        # nothing
                        policyHolder_name = policyHolder_name
                    if policyHolder_name == ' ' or policyHolder_name == '':
                        pass
                    else:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(self.handlePatientName(policyHolder_name))
                        data.append(row_data)
                        policy_holder = 1

            else:
                text_instances = page.search_for("NAME AND ADDRESS OF INSURER")
                insuresr_exist = 0
                insurer_name = ''
                if len(text_instances) > 0:
                    print("NAME AND ADDRESS OF INSURER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0, cords_rect.x1 + 50, cords_rect.y1 + 60)
                    row_data = []
                    row_data.append("Insurer name")
                    extract_text = (page.get_textbox(i_rect).strip())
                    print("insurer name raw text: ", extract_text)
                    print("cleaned name: ", self.cleanString(extract_text))
                    match = re.search(r"NAME AND ADDRESS OF INSURER\s*\n([^\n]+)", self.cleanString(extract_text))

                    if extract_text:
                        cleaned_string = self.clean_paragraph(extract_text).replace("\n", "")
                        print("cleaned para: ", cleaned_string)
                        if cleaned_string:
                            if "NAME AND ADDRESS OF INSURER OR SELF" in cleaned_string:
                                print("found")
                                insurer_name = cleaned_string.replace(
                                    "NAME AND ADDRESS OF INSURER OR SELF", "")
                            else:
                                insurer_name = cleaned_string.split("\n")[1]
                        print("so the final insurer name is: ", insurer_name)
                    elif match and len(insurer_name) < 1:
                        insurer_name = match.group(1)
                        print("matched insurer name: ", insurer_name)
                    insurer_found = 1
                    if insurer_name == "":
                        insurer_name = "Test Insurer"
                    row_data.append(self.handleInsurerNameAPI(self.cleanString(insurer_name.strip())))
                    if len(row_data) > 1:
                        data.append(row_data)
                        insuresr_exist = 1

                text_instances = page.search_for("ADDRESS OF INSURER OR SELF")
                if len(text_instances) > 0 and insuresr_exist == 0:
                    print("ADDRESS OF INSURER OR SELF")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 40, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 50)
                    print(text_instances[0])
                    print(i_rect)
                    row_data = []
                    row_data.append("Insurer name")
                    extract_text = (page.get_textbox(i_rect).strip())
                    print(extract_text)
                    insurer_found = 1
                    text_arr = extract_text.split("\n")
                    if (self.cleanString(text_arr[0].strip()) == ""):
                        text_arr[0] = "Test Insurer"
                    row_data.append(self.handleInsurerNameAPI(self.cleanString(text_arr[0].strip())))
                    if len(row_data) > 1:
                        data.append(row_data)

                text_instances = page.search_for("NAME, ADDRESS, AND PHONE NUMBER")
                if len(text_instances) > 0:
                    print("NAME, ADDRESS, AND PHONE NUMBER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0, cords_rect.y0 + 20, cords_rect.x1 + 40, cords_rect.y1 + 40)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Insurer representative")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    if len(row_data) > 1:
                        insurer_rep_found = 1
                        data.append(row_data)

                text_instances = page.search_for("INSURER'S CLAIM REPRESENTATIVE")
                if len(text_instances) > 0 and insurer_rep_found == 0:
                    print("CLAIM REPRESENTATIVE")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 55, cords_rect.y0 + 10, cords_rect.x1, cords_rect.y1 + 38)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Insurer representative")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    if len(row_data) > 1:
                        insurer_rep_found = 1
                        data.append(row_data)

                text_instances = page.search_for("CLAIM REPRESENTATIVE")
                if len(text_instances) > 0 and insurer_rep_found == 0:
                    print("CLAIM REPRESENTATIVE")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 45, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 38)
                    # print(text_instances[0])
                    row_data = []
                    row_data.append("Insurer representative")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    if len(row_data) > 1 and insurer_rep_found == 0:
                        insurer_rep_found = 1
                        data.append(row_data)

                text_instances = page.search_for("NAME OF INSURER'S")
                if len(text_instances) > 0 and insurer_rep_found == 0:
                    print("NAME OF INSURER'S")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0, cords_rect.y0 + 10, cords_rect.x1, cords_rect.y1 + 30)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Insurer representative")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    if len(row_data) > 1:
                        insurer_rep_found = 1
                        data.append(row_data)

                text_instances = page.search_for("PROVIDER'S NAME AND ADDRESS")
                if len(text_instances) > 0:
                    print("PROVIDER'S NAME AND ADDRESS")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y0 + 5, cords_rect.x1 + 30, cords_rect.y1 + 50)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("Provider Name")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    # row_data.append(self.handleProviderNameAPI(self.cleanString(text_arr[0].strip())))
                    row_data.append(self.cleanString(text_arr[0].strip()))
                    print("here1611", row_data)
                    if len(row_data) > 1:
                        provider_found = 1
                        data.append(row_data)
                if provider_found == 0:
                    provider_data = page.search_for("Englinton")
                    if provider_data:
                        rect = page.search_for("NEW YORK MOTOR VEHICLE")
                        if rect:
                            rect1 = fitz.Rect(rect[0].x0 - 150, rect[0].y0 + 100, rect[0].x1 + 30, rect[0].y1 + 150)
                            row_data = []
                            row_data.append("Provider Name")
                            extract_text = (page.get_textbox(rect1).strip())
                            text_arr = extract_text.split("\n")
                            print(text_arr)
                            row_data.append(self.handleProviderNameAPI(self.cleanString(text_arr[0].strip())))
                            if len(row_data) > 1:
                                provider_found = 1
                                data.append(row_data)

                elif provider_found == 0:
                    date = page.search_for("DATE")
                    oate_date = page.search_for("OATE")
                    police_holder_coordinate = page.search_for("POLICYHOLDER")
                    policy_number_coordinate = page.search_for("POLICY NUMBER")
                    date_of_accident = page.search_for("DATE OF ACCIDENT")
                    text_instances = page.search_for("PROVIDER:")
                    if len(text_instances) > 0:
                        print("PROVIDER")
                        cords_rect = self.textCordMaker(text_instances)
                        i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y1, cords_rect.x1 + 200, cords_rect.y1 + 20)
                        # print(text_instances[0])
                        # print(i_rect)
                        row_data = []
                        row_data.append("Provider Name")
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        print(text_arr)
                        row_data.append(self.handleProviderNameAPI(self.cleanString(text_arr[0].strip())))
                        if len(row_data) > 1:
                            provider_found = 1
                            data.append(row_data)


                    elif police_holder_coordinate and policy_number_coordinate and date_of_accident:
                        try:

                            if abs(police_holder_coordinate[0][1] - policy_number_coordinate[0][1]) < 3 and abs(
                                    policy_number_coordinate[0][1] - date_of_accident[0][1]) < 3 and (
                                    date[0][0] < police_holder_coordinate[0][0]):

                                # i_rect = fitz.Rect(date[0][0] - 30, date[0][1] + 35, date[0][2] + 150, date[0][3] + 40)
                                if date[0][0] < police_holder_coordinate[0][0]:

                                    i_rect = fitz.Rect(date[0][0] - 15, date[0][1] + 40, date[0][2] + 160,
                                                       date[0][3] + 50)
                                # replace the date[0][2] value from 140 to 160 to get the full provider name    #bicky
                                elif oate_date and oate_date[0][0] < police_holder_coordinate[0][0]:

                                    i_rect = fitz.Rect(oate_date[0][0], oate_date[0][1] + 35, oate_date[0][2] + 150,
                                                       oate_date[0][3] + 40)
                                extract_text = (page.get_textbox(i_rect).strip())

                                text_arr = extract_text.split("\n")

                                text = "".join(text_arr)

                                provider_found = 1
                                row_data = []
                                # text_instances = 0
                                row_data.append("Provider Name")
                                row_data.append(self.handleProviderNameAPI(self.cleanString(text.strip())))
                                if len(row_data) > 1:
                                    data.append(row_data)
                            elif oate_date and provider_found == 0:
                                try:
                                    i_rect = fitz.Rect(oate_date[0][0] - 30, oate_date[0][1] + 35,
                                                       oate_date[0][2] + 150, oate_date[0][3] + 40)
                                    # i_rect = fitz.Rect(oate_date[0][0], oate_date[0][1], oate_date[0][2], oate_date[0][3] + 100)
                                    extract_text = (page.get_textbox(i_rect).strip())
                                    text_arr = extract_text.split("\n")

                                    text = "".join(text_arr)
                                    provider_found = 1
                                    row_data = []
                                    # text_instances = 0
                                    row_data.append("Provider Name")
                                    row_data.append(self.handleProviderNameAPI(self.cleanString(text.strip())))
                                    if len(row_data) > 1:
                                        data.append(row_data)
                                except:
                                    pass

                            elif police_holder_coordinate and provider_found == 0:
                                try:
                                    print("fetching through police_holder_coordinate")
                                    i_rect = fitz.Rect(police_holder_coordinate[0].x0 - 130,
                                                       police_holder_coordinate[0].y0 + 25,
                                                       police_holder_coordinate[0].x1 + 100,
                                                       police_holder_coordinate[0].y1 + 60)
                                    # i_rect = fitz.Rect(police_holder_coordinate[0].x0 - 130,
                                    #                    police_holder_coordinate[0].y0 - 50,
                                    #                    police_holder_coordinate[0].x1 + 10,
                                    #                    police_holder_coordinate[0].y1-3 )

                                    # i_rect = fitz.Rect(oate_date[0][0], oate_date[0][1], oate_date[0][2], oate_date[0][3] + 100)
                                    extract_text = (page.get_textbox(i_rect).strip())
                                    print("fetched text is: ", extract_text)
                                    text_arr = extract_text.split("\n")

                                    text = "".join(text_arr)
                                    provider_found = 1
                                    row_data = []
                                    # text_instances = 0
                                    row_data.append("Provider Name")
                                    row_data.append(self.handleProviderNameAPI(self.cleanString(text.strip())))
                                    if len(row_data) > 1:
                                        data.append(row_data)
                                        provider_found = 1
                                except Exception as e:
                                    pass
                        except Exception as e:
                            pass

                if insurer_found == 0:
                    text_instances = page.search_for("INSURER")
                    # updated Code Start
                    date = page.search_for("DATE")
                    oate_date = page.search_for("OATE")
                    police_holder_coordinate = page.search_for("POLICYHOLDER")
                    policy_number_coordinate = page.search_for("POLICY NUMBER")
                    date_of_accident = page.search_for("DATE OF ACCIDENT")
                    text_instances = page.search_for("INSURER")
                    rect = page.search_for("NEW YORK MOTOR VEHICLE")
                    provider_data = page.search_for("Englinton")

                    if police_holder_coordinate and policy_number_coordinate and date_of_accident:
                        if abs(police_holder_coordinate[0][1] - policy_number_coordinate[0][1]) < 3 and abs(
                                policy_number_coordinate[0][1] - date_of_accident[0][1]) < 3 and (
                                date[0][0] < police_holder_coordinate[0][0]):
                            i_rect = fitz.Rect(date[0][0] - 30, date[0][1] - 50, date[0][2] + 100, date[0][3] - 30)
                            extract_text = (page.get_textbox(i_rect).strip())
                            text_arr = extract_text.split("\n")
                            # print(f"line no 795 {text_arr}")
                            text = "".join(text_arr)
                            insurer_found = 1
                            row_data = []
                            row_data.append("Insurer name")
                            row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                            if len(row_data) > 1:
                                data.append(row_data)
                        elif oate_date and insurer_found == 0:
                            # usning oate_date
                            try:
                                i_rect = fitz.Rect(oate_date[0][0] - 30, oate_date[0][1] - 60,
                                                   oate_date[0][2] + 100,
                                                   oate_date[0][3] - 30)
                                print("irect is: ", i_rect)
                                extract_text = (page.get_textbox(i_rect).strip())
                                print("test is: ", extract_text)
                                text_arr = extract_text.split("\n")

                                text = "".join(text_arr)
                                pattern = re.compile(r'[^a-zA-Z0-9\s]')
                                text = re.sub(pattern, "", text)
                                text = text.replace("INSURER", "")
                                text = text.replace("NAME", "")

                                insurer_found = 1
                                row_data = []
                                row_data.append("Insurer name")
                                row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))
                                if len(row_data) > 1:
                                    data.append(row_data)
                                    insurer_found = 1
                            except Exception as e:
                                print("error: ", e)
                                pass
                        elif police_holder_coordinate and insurer_found == 0:
                            try:
                                print("police_holder_coordinate coords: ", police_holder_coordinate)
                                i_rect = fitz.Rect(police_holder_coordinate[0].x0 - 130,
                                                   police_holder_coordinate[0].y0 - 50,
                                                   police_holder_coordinate[0].x1 + 10,
                                                   police_holder_coordinate[0].y1 - 3)
                                # i_rect = fitz.Rect(police_holder_coordinate[0].x0 - 130,
                                #                    police_holder_coordinate[0].y0 + 25,
                                #                    police_holder_coordinate[0].x1 + 100,
                                #                    police_holder_coordinate[0].y1 + 60)
                                print("irect is: ", i_rect)
                                extract_text = (page.get_textbox(i_rect).strip())
                                text_arr = extract_text.split("\n")

                                text = "".join(text_arr)
                                pattern = re.compile(r'[^a-zA-Z0-9\s]')
                                text = re.sub(pattern, "", text)
                                text = text.replace("INSURER", "")
                                text = text.replace("NAME", "")
                                insurer_found = 1
                                row_data = []
                                row_data.append("Insurer name")
                                row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))

                                if len(row_data) > 1:
                                    data.append(row_data)
                                    insurer_found = 1
                            except Exception as e:
                                pass

                    elif rect and provider_data:
                        # 895
                        rect1 = fitz.Rect(rect[0].x0 - 150, rect[0].y0 + 30, rect[0].x1 + 30, rect[0].y1 + 70)

                        extract_text = (page.get_textbox(rect1).strip())
                        text_arr = extract_text.split("\n")

                        text = "".join(text_arr)
                        pattern = re.compile(r'[^a-zA-Z0-9\s]')
                        text = re.sub(pattern, "", text)
                        text = text.replace("INSURER", "")
                        text = text.replace("NAME", "")

                        insurer_found = 1
                        row_data = []
                        row_data.append("Insurer name")
                        row_data.append(self.handleInsurerNameAPI(self.cleanString(text.strip())))

                        if len(row_data) > 1:
                            data.append(row_data)
                            print("insurer name is :", row_data)
                            insurer_found = 1
                    elif len(text_instances) > 0 and insurer_found == 0:
                        print("INSURER:")
                        cords_rect = self.textCordMaker(text_instances)
                        i_rect = fitz.Rect(cords_rect.x0 - 20, cords_rect.y0 + 5, cords_rect.x1 + 100,
                                           cords_rect.y1 + 50)
                        print(text_instances[0])
                        print(i_rect)
                        row_data = []
                        row_data.append("Insurer name")
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        insurer_found = 1
                        if (self.cleanString(text_arr[0].strip()) == ""):
                            text_arr[0] = "Test Insurer"
                        row_data.append(self.handleInsurerNameAPI(self.cleanString(text_arr[0].strip())))
                        if len(row_data) > 1:
                            data.append(row_data)
                    else:
                        text_instances = page.search_for("ffice Location")
                        if len(text_instances) > 0:
                            print("INSURER:")
                            cords_rect = self.textCordMaker(text_instances)
                            i_rect = fitz.Rect(cords_rect.x0 - 400, cords_rect.y0 - 5, cords_rect.x0 - 120,
                                               cords_rect.y1 + 50)
                            row_data = []
                            row_data.append("Insurer name")
                            extract_text = (page.get_textbox(i_rect).strip())
                            text_arr = extract_text.split("\n")
                            insurer_found = 1
                            if (self.cleanString(text_arr[0].strip()) == ""):
                                text_arr[0] = "Test Insurer"
                            row_data.append(self.handleInsurerNameAPI(self.cleanString(text_arr[0].strip())))
                            if len(row_data) > 1:
                                data.append(row_data)

                text_instances = page.search_for("POLICY HOLDER")
                if len(text_instances) > 0 and policy_holder == 0:
                    print("POLICY HOLDER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 5, cords_rect.y0 + 8, cords_rect.x1 + 60, cords_rect.y1 + 15)
                    text_instances = page.search_for("ffice Location")
                    if len(text_instances) > 0:
                        i_rect = fitz.Rect(cords_rect.x0 - 35, cords_rect.y0 + 8, cords_rect.x1 + 60,
                                           cords_rect.y1 + 15)
                    # print(text_instances[0])
                    # print(i_rect)
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    name_text = self.cleanString(text_arr[0].strip())
                    if name_text == ' ' or name_text == '':
                        pass
                    else:
                        row_data = []
                        row_data.append("policy holder")
                        row_data.append(self.handlePatientName(name_text))
                        if len(row_data) > 1:
                            data.append(row_data)
                            policy_holder = 1

            text_instances = page.search_for("PATIENTS NAME AND ADDRESS")
            if len(text_instances) > 0:
                print("PATIENT’S NAME AND ADDRESS")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 + 130, cords_rect.y0 - 5, cords_rect.x1 + 190, cords_rect.y1 + 20)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and name_text != "":
                        patient_name_add = 1
                        data.append(row_data)
                        row_data = []
                        if policy_holder == 0:
                            policy_holder = 1
                            row_data.append("policy holder")
                            if name_text == ' ' or name_text == '':
                                row_data.append(name_text)
                            else:
                                row_data.append(self.handlePatientName(name_text))
                            data.append(row_data)

            text_instances = page.search_for("PATIENT’S FULL NAME")
            if len(text_instances) > 0:
                print("PATIENT’S FULL NAME")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y0 + 5, cords_rect.x1 + 30, cords_rect.y1 + 50)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and name_text != "" and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)
                        row_data = []
                        if policy_holder == 0:
                            policy_holder = 1
                            row_data.append("policy holder")
                            if name_text == ' ' or name_text == '':
                                row_data.append(name_text)
                            else:
                                row_data.append(self.handlePatientName(name_text))
                            data.append(row_data)

            text_instances = page.search_for("PATIENT'S NAME AND ADDRESS:")
            if len(text_instances) > 0:
                print("PATIENT'S NAME AND ADDRESS:")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x1 + 10, cords_rect.y0, cords_rect.x1 + 100, cords_rect.y1)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if len(row_data) > 1 and name_text != "" and policy_holder == 0:
                    row_data = []
                    row_data.append("policy holder")
                    policy_holder = 1
                    row_data.append(self.handlePatientName(name_text))
                    data.append(row_data)
                i_rect = fitz.Rect(cords_rect.x1, cords_rect.y0 - 5, cords_rect.x1 + 200, cords_rect.y1 + 50)
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                # print(extract_text)
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)
                        row_data = []
                        if policy_holder == 0:
                            policy_holder = 1
                            row_data.append("policy holder")
                            if name_text == ' ' or name_text == '':
                                row_data.append(name_text)
                            else:
                                row_data.append(self.handlePatientName(name_text))
                            data.append(row_data)
            else:
                text_instances = page.search_for("PATIENT'S NAME AND ADpRESsS")
                if len(text_instances) > 0:
                    print("PATIENT'S NAME AND ADDRESS:")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x1 + 10, cords_rect.y0, cords_rect.x1 + 100, cords_rect.y1)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    name_text = self.cleanString(text_arr[0].strip())
                    if len(row_data) > 1 and name_text != "" and policy_holder == 0:
                        row_data = []
                        row_data.append("policy holder")
                        policy_holder = 1
                        row_data.append(self.handlePatientName(name_text))
                        data.append(row_data)
                    i_rect = fitz.Rect(cords_rect.x1, cords_rect.y0 - 5, cords_rect.x1 + 200, cords_rect.y1 + 50)
                    # print(i_rect)
                    extract_text = (page.get_textbox(i_rect).strip())
                    # print(extract_text)
                    text_arr = extract_text.split("\n")
                    name_text = self.cleanString(text_arr[0].strip())
                    if name_text == ' ' or name_text == '':
                        pass
                    else:
                        row_data = []
                        row_data.append("Patient Name")
                        row_data.append(self.handlePatientName(name_text))
                        if len(row_data) > 1 and patient_name_add == 0:
                            patient_name_add = 1
                            data.append(row_data)
                            row_data = []
                            if policy_holder == 0:
                                policy_holder = 1
                                row_data.append("policy holder")
                                if name_text == ' ' or name_text == '':
                                    row_data.append(name_text)
                                else:
                                    row_data.append(self.handlePatientName(name_text))
                                data.append(row_data)

            text_instances = page.search_for("PATIENT'S NAME AND ADDRESS")
            text_instances2 = page.search_for("ffice Location")
            if len(text_instances) > 0 and len(text_instances2) > 0:
                print("PATIENT'S NAME AND ADDRESS")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x1, cords_rect.y0 - 5, cords_rect.x1 + 200, cords_rect.y1 + 50)
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                # print(extract_text)
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)
                        row_data = []
                        if policy_holder == 0:
                            policy_holder = 1
                            row_data.append("policy holder")
                            if name_text == ' ' or name_text == '':
                                row_data.append(name_text)
                            else:
                                row_data.append(self.handlePatientName(name_text))
                            data.append(row_data)

            text_instances = page.search_for("PATIENT'S NAME")
            if len(text_instances) > 0:
                print("PATIENT'S NAME")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 30)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    text_instances = page.search_for("ffice Location")
                    if len(text_instances) > 0:
                        i_rect = fitz.Rect(cords_rect.x1 + 90, cords_rect.y0 - 5, cords_rect.x1 + 190,
                                           cords_rect.y1 + 5)
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        name_text = self.cleanString(text_arr[0].strip())
                        print(name_text)
                        row_data = []
                        row_data.append("Patient Name")
                        print("nameee textttt", name_text)
                        row_data.append(self.handlePatientName(name_text))
                        if len(row_data) > 1 and name_text != "" and patient_name_add == 0:
                            patient_name_add = 1
                            data.append(row_data)
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and name_text != "" and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)

            text_instances = page.search_for("PATIENT’S NAME")
            if len(text_instances) > 0:
                print("PATIENT'S NAME")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 30)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and name_text != "" and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)

            # metro
            text_instances = page.search_for("PATIENTS")
            if len(text_instances) > 0:
                print("PATIENTS")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 30, cords_rect.y0 + 10, cords_rect.x1 + 40, cords_rect.y1 + 30)
                # print(text_instances[0])
                print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                print("name is: ", extract_text)
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                print(name_text)
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data = []
                    row_data.append("Patient Name")
                    row_data.append(self.handlePatientName(name_text))
                    if len(row_data) > 1 and name_text != "" and patient_name_add == 0:
                        patient_name_add = 1
                        data.append(row_data)

            text_instances = page.search_for("POLICYHOLDER")
            if len(text_instances) > 0 and policy_holder == 0:
                print("POLICYHOLDER")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 40, cords_rect.y0 + 5, cords_rect.x1 + 40, cords_rect.y1 + 30)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                # print(text_arr)
                policyHolder_name = self.cleanString(text_arr[0].strip())
                try:
                    if self.cleanString(text_arr[1].strip()) != "":
                        policyHolder_name = policyHolder_name + " " + self.cleanString(text_arr[1].strip())
                except:
                    # nothing
                    policyHolder_name = policyHolder_name
                # row_data.append(self.cleanString(text_arr[0].strip()))
                if policyHolder_name == ' ' or policyHolder_name == '':
                    pass
                else:
                    row_data = []
                    row_data.append("policy holder")
                    row_data.append(self.handlePatientName(policyHolder_name))
                    if len(row_data) > 1:
                        data.append(row_data)
                        policy_holder = 1
                        row_data = []
                        if (patient_name_add == 0):
                            row_data.append("Patient Name")
                            if policyHolder_name == ' ' or policyHolder_name == '':
                                row_data.append(policyHolder_name)
                            else:
                                row_data.append(self.handlePatientName(policyHolder_name))
                            data.append(row_data)

            text_instances = page.search_for("DATE OF BIRTH")
            if len(text_instances) > 0:
                print("DATE OF BIRTH")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 25, cords_rect.y1 + 20)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("dob")
                extract_text = (page.get_textbox(i_rect).strip())
                print(extract_text)
                if extract_text == ' ' or extract_text == '':
                    i_rect = fitz.Rect(cords_rect.x1 + 5, cords_rect.y0 - 5, cords_rect.x1 + 75, cords_rect.y1 + 5)
                    extract_text = (page.get_textbox(i_rect).strip())
                    print(extract_text)
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                row_data.append(self.removeDateSpace(text_arr[0].strip()))
                if len(row_data) > 1 and name_text != "":
                    data.append(row_data)
            else:
                text_instances = page.search_for("ATEOF BIRTH")
                if len(text_instances) > 0:
                    print("DATE OF BIRTH")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 25, cords_rect.y1 + 15)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("dob")
                    extract_text = (page.get_textbox(i_rect).strip())
                    print(extract_text)
                    if extract_text == ' ' or extract_text == '':
                        i_rect = fitz.Rect(cords_rect.x1 + 5, cords_rect.y0 - 5, cords_rect.x1 + 75, cords_rect.y1 + 5)
                        extract_text = (page.get_textbox(i_rect).strip())
                        print(extract_text)
                    text_arr = extract_text.split("\n")
                    name_text = self.cleanString(text_arr[0].strip())
                    row_data.append(self.removeDateSpace(text_arr[0].strip()))
                    if len(row_data) > 1 and name_text != "":
                        data.append(row_data)

            text_instances = page.search_for("POLICY HOLDER")
            if len(text_instances) > 0 and policy_holder == 0:
                print("POLICY HOLDER")
                row_data = []
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 5, cords_rect.y0 + 8, cords_rect.x1 + 60, cords_rect.y1 + 15)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                name_text = self.cleanString(text_arr[0].strip())
                if name_text == ' ' or name_text == '':
                    pass
                else:
                    row_data.append("policy holder")
                    row_data.append(self.handlePatientName(name_text))
                if len(row_data) > 1 and policy_holder == 0:
                    data.append(row_data)
                    row_data = []
                    policy_holder = 1
                    if (patient_name_add == 0):
                        row_data.append("Patient Name")
                        if name_text == '' or name_text == ' ':
                            row_data.append(name_text)
                        else:
                            row_data.append(self.handlePatientName(name_text))
                        data.append(row_data)

            text_instances = page.search_for("ACCIDENT DATE")
            if len(text_instances) > 0:
                print("ACCIDENT DATE")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 10, cords_rect.y0 + 5, cords_rect.x1 + 10, cords_rect.y1 + 20)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("accident date")
                extract_text = (page.get_textbox(i_rect).strip())
                print(extract_text)
                text_arr = extract_text.split("\n")
                row_data.append(self.removeDateSpace(text_arr[0].strip()))
                if len(row_data) > 1:
                    accident_date_add = 1
                    aod_found = 1
                    data.append(row_data)

            text_instances = page.search_for("POLICY NUMBER")
            if len(text_instances) > 0:
                print("ACCIDENT DATE FETCH BY SIDE POLICY")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x1 + 20, cords_rect.y0 + 5, cords_rect.x1 + 90, cords_rect.y1 + 50)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("accident date")
                extract_text = (page.get_textbox(i_rect).strip())
                print(extract_text)
                text_arr = extract_text.split("\n")
                if text_arr[0].strip() == "DATE":
                    row_data.append(self.removeDateSpace(self.dateCorrector(text_arr[1].strip())))
                if len(row_data) > 1:
                    accident_date_add = 1
                    aod_found = 1
                    data.append(row_data)
                print("POLICY NUMBER")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 15, cords_rect.y1 + 30)
                row_data = []
                row_data.append("policy number")
                extract_text = (page.get_textbox(i_rect).strip())
                print(extract_text)
                text_arr = extract_text.split("\n")
                text_arr[0] = "".join(text_arr)
                row_data.append(text_arr[0].strip())
                if len(row_data) > 1:
                    # accident_date_add = 1
                    # aod_found = 1
                    data.append(row_data)
            else:
                text_instances = page.search_for("POLIGY NUMBER")
                if len(text_instances) > 0:
                    print("ACCIDENT DATE FETCH BY SIDE POLICY")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x1 + 20, cords_rect.y0 + 5, cords_rect.x1 + 90, cords_rect.y1 + 50)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("accident date")
                    extract_text = (page.get_textbox(i_rect).strip())
                    print(extract_text)
                    text_arr = extract_text.split("\n")
                    if text_arr[0].strip() == "DATE":
                        row_data.append(self.removeDateSpace(self.dateCorrector(text_arr[1].strip())))
                    if len(row_data) > 1:
                        accident_date_add = 1
                        aod_found = 1
                        data.append(row_data)
                    print("POLICY NUMBER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 15, cords_rect.y1 + 30)
                    row_data = []
                    row_data.append("policy number")
                    extract_text = (page.get_textbox(i_rect).strip())
                    print(extract_text)
                    text_arr = extract_text.split("\n")
                    text_arr[0] = "".join(text_arr)
                    row_data.append(text_arr[0].strip())
                    if len(row_data) > 1:
                        # accident_date_add = 1
                        # aod_found = 1
                        data.append(row_data)

            text_instances = page.search_for("DATE OF ACCIDENT")
            if len(text_instances) > 0:
                print("DATE OF ACCIDENT")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 10, cords_rect.y0 + 5, cords_rect.x1 + 30, cords_rect.y1 + 25)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("accident date")
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                row_data.append(self.removeDateSpace(text_arr[0].strip()))
                if len(row_data) > 1:
                    if (accident_date_add == 0):
                        data.append(row_data)
                        accident_date_add = 1
                        aod_found = 1

            text_instances = page.search_for("ACCIDENT CLAIM")
            if len(text_instances) > 0:
                print("ACCIDENT CLAIM")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 10, cords_rect.y0 + 5, cords_rect.x1 + 30, cords_rect.y1 + 50)
                row_data = []
                row_data.append("accident date")
                extract_text = (page.get_textbox(i_rect).strip())
                try:
                    pattern = re.search("[0-1][0-9]\/[0-1][0-9]\/[0-9][0-9][0-9][0-9]", extract_text)[0]
                    print(pattern)
                    accident_claim = self.removeDateSpace(pattern)
                    row_data.append(accident_claim)
                except:
                    accident_claim = ""
                    row_data.append(accident_claim)
                    # print("Object Errror")
                if len(row_data) > 1:
                    if (accident_date_add == 0 and accident_claim != ""):
                        data.append(row_data)
                        accident_date_add = 1
                        aod_found = 1

            text_instances = page.search_for("CLAIMNUMBER")
            if len(text_instances) > 0:
                print("CLAIMNUMBER")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 25, cords_rect.y1 + 25)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("claim number")
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                claim_text = text_arr[0].strip()
                if (len(text_arr) > 1):
                    claim_text = claim_text + text_arr[1].strip()
                row_data.append(claim_text)
                if len(row_data) > 1:
                    data.append(row_data)

            text_instances = page.search_for("CLAIM NUMBER")
            if len(text_instances) > 0:
                print("CLAIM NUMBER")
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 25, cords_rect.y1 + 25)
                # print(text_instances[0])
                # print(i_rect)
                row_data = []
                row_data.append("claim number")
                extract_text = (page.get_textbox(i_rect).strip())
                text_arr = extract_text.split("\n")
                claim_text = text_arr[0].strip()
                if (len(text_arr) > 1):
                    claim_text = claim_text + text_arr[1].strip()
                row_data.append(claim_text)
                if len(row_data) > 1:
                    data.append(row_data)
            else:
                text_instances = page.search_for("FILE NUMBER")
                if len(text_instances) > 0:
                    print("FILE NUMBER")
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 25, cords_rect.y0 + 5, cords_rect.x1 + 25, cords_rect.y1 + 25)
                    # print(text_instances[0])
                    # print(i_rect)
                    row_data = []
                    row_data.append("claim number")
                    extract_text = (page.get_textbox(i_rect).strip())
                    text_arr = extract_text.split("\n")
                    claim_text = text_arr[0].strip()
                    if (len(text_arr) > 1):
                        claim_text = claim_text + text_arr[1].strip()
                    row_data.append(claim_text)
                    if len(row_data) > 1:
                        data.append(row_data)
        if (insurer_found == 0):
            row_data = []
            row_data.append("Insurer name")
            row_data.append("Test Insurer")
            data.append(row_data)
        if (provider_found == 0):
            row_data = []
            row_data.append("Provider Name")
            row_data.append("Test Provider")
            data.append(row_data)
        if (policy_holder == 0):
            row_data = []
            row_data.append("policy holder")
            for j in data:
                if j[0] == 'Patient Name':
                    row_data.append(j[1])
                    break
            else:
                row_data.append("Test Holder")
            data.append(row_data)

        # if (aod_found == 0):
        # row_data = []
        # row_data.append("accident date")
        # row_data.append(getCurrentDate())
        # data.append(row_data)

        return data

    # function to get the exact cordinate value from array of Rect
    # author Rishab
    def textCordMaker(self, cordList):
        cordRect = fitz.Rect(cordList[0].x0, cordList[0].y0, cordList[-1].x1, cordList[-1].y1)
        return cordRect

    def processImages(self):
        images = os.listdir(self.tempImgPath)
        # print(images)
        data = []
        for image in images:
            img = self.loadImg(self.tempImgPath + image)
            result = img.copy()
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
            horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
            remove_horizontal = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, horizontal_kernel, iterations=2)
            cnts = cv2.findContours(remove_horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cnts = cnts[0] if len(cnts) == 2 else cnts[1]
            for c in cnts:
                cv2.drawContours(result, [c], -1, (255, 255, 255), 5)
            vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 40))
            remove_vertical = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, vertical_kernel, iterations=2)
            cnts = cv2.findContours(remove_vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cnts = cnts[0] if len(cnts) == 2 else cnts[1]
            for c in cnts:
                cv2.drawContours(result, [c], -1, (255, 255, 255), 5)
            resizedGray = self.resizeImg(result)
            # print(self.currDir + "recognize.csv")
            with open(self.currDir + "recognize.csv", "r") as csvfile:
                csvreader = csv.reader(csvfile, delimiter="-")
                # number = self.recognizeImages(resizedGray, csvreader)
                catData = self.recognizeCompleteOcr(resizedGray, csvreader)
                number = catData['result']
                cat_type = catData['cat_type']
                orientation_val = catData['orientation_val']
                conf_score = catData['conf_score']
                print("Number = " + str(number))
                if number == 1:
                    filename = self.currDir + "result.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 4:
                    filename = self.currDir + "result-4.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 5:
                    filename = self.currDir + "result-5.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 7:
                    filename = self.currDir + "result-6.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 8:
                    filename = self.currDir + "result-7.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 9:
                    filename = self.currDir + "result-8.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 14:
                    filename = self.currDir + "result-10.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number == 11:

                    filename = self.currDir + "result-9.csv"
                    data = self.textFromImages(resizedGray, filename, number)
                elif number in [2, 3, 6, 10, 12, 13, 15, 16]:

                    # filename = self.currDir + "result2.csv"
                    data = self.processHTMLFromPDF()
                    # print(data)
            print("data is ", data)
            if cat_type == "":
                cat_type = "MEDICAL REPORTS"
            if self.process_type == "collection":
                cat_type = cat_type
            else:
                cat_type = "BILLS"
            # Deafault bills for RPA all images on request BPM 2021-03-03 09:31:42

            for subdata in data:
                if "Date of service" in subdata[0] or "Cost" in subdata[0]:
                    number = 2

            if data and len(data) > 0 and self.validateReturnData(data, number) == 0:
                type_val = 0
                if number in [1, 4, 5, 7, 8, 9, 14]:
                    type_val = 1
                elif number in [2, 3, 6, 10, 12, 13, 15]:
                    type_val = 2
                elif number == 11:
                    type_val = 3

                if self.process_type == "collection":
                    # Data found sent to API 2021-03-18 15:49:05
                    conv_data = self.convertToParams(data)

                    result = {'type': type_val, 'cat_type': cat_type, 'orientation_val': orientation_val,
                              'conf_score': conf_score, "out_data": conv_data}
                    return result
                    sentoAPI = dict(list(result.items()) + list(conv_data.items()))
                    print("data to be sent to SentToAPI", sentoAPI)
                    self.sendToAPICollection(sentoAPI, "add_data_extract", 1)
                    print(sentoAPI)
                    return {'orientation_val': orientation_val}
                else:
                    print({'type': type_val, 'data': data, 'cat_type': cat_type, 'orientation_val': orientation_val,
                           'conf_score': conf_score})
                    self.savevisitedURL(self.currDir + "visited_url.txt", self.url)
                    self.sendToAPI({'type': type_val, 'cat_type': cat_type, 'data': json.dumps(data),
                                    'orientation_val': orientation_val, 'conf_score': conf_score}, "add_logs")

                    # Send to RPA save records ACTIVE
                    conv_data = self.convertToParams(data)
                    result = {'type': type_val, 'cat_type': cat_type, 'orientation_val': orientation_val,
                              'conf_score': conf_score}
                    sentoAPI = dict(list(result.items()) + list(conv_data.items()))

                    print("data to be sent to SentToAPI", sentoAPI)
                    self.sendToAPICollection(sentoAPI, "add_data_extract", 2)
                    return {'type': type_val, 'data': data, 'cat_type': cat_type, 'orientation_val': orientation_val,
                            'conf_score': conf_score}
            else:
                if self.process_type == "collection":
                    self.savevisitedURL(self.currDir + "visited_url.txt", self.url)
                    self.sendToAPI({'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score},
                                   "add_logs")
                    # no data found 2021-03-18 15:48:56
                    result = {'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score}
                    sentoAPI = dict(list(result.items()))
                    self.sendToAPICollection(sentoAPI, "add_data_extract", 1)
                    print(sentoAPI)
                    return {'orientation_val': orientation_val, 'conf_score': conf_score}
                else:
                    self.savevisitedURL(self.currDir + "visited_url.txt", self.url)
                    print({'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score})
                    self.sendToAPI({'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score},
                                   "add_logs")

                    # Send to RPA save records ACTIVE
                    result = {'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score}
                    sentoAPI = dict(list(result.items()))
                    self.sendToAPICollection(sentoAPI, "add_data_extract", 2)
                    return {'cat_type': cat_type, 'orientation_val': orientation_val, 'conf_score': conf_score}
                # return None

    def convertToParams(self, data):
        # print(data)
        return_dict = {}
        print("Coneversion Started")
        for x in data:
            print(x[0].lower().replace(" ", "_"))
            print(x[1])
            return_dict[x[0].lower().replace(" ", "_")] = x[1]
        # print(return_dict)
        return return_dict

    def sendToAPICollection(self, postData, reqType, r_type):

        if r_type == 2:
            API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        else:
            API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        # your API Toekn  here
        API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"

        # data to be sent to api
        data = {'atoken': API_KEY, 'process': 'collection', 'req_type': reqType, 'image_id': self.image_id,
                'r_type': r_type}
        finalPost = dict(list(data.items()) + list(postData.items()))
        print("data is sent to api :", finalPost)
        # print(finalPost)
        r = requests.post(url=API_ENDPOINT, data=finalPost)
        # extracting response text
        pastebin_url = r.text
        print("The response is:%s" % pastebin_url)

    def sendToAPI(self, postData, reqType):
        # defining the api-endpoint
        API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        # your API Toekn  here
        API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"

        # data to be sent to api
        data = {'atoken': API_KEY, 'process': 'collection', 'req_type': reqType, 'image_id': self.image_id}
        finalPost = dict(list(data.items()) + list(postData.items()))
        print(finalPost)
        r = requests.post(url=API_ENDPOINT, data=finalPost)
        # extracting response text
        pastebin_url = r.text
        # print("The response is:%s"%pastebin_url)

    def getFromAPI(self, reqType):
        # defining the api-endpoint
        API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        # your API Toekn  here
        API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
        # data to be sent to api
        data = {'atoken': API_KEY, 'req_type': reqType, 'process': 'collection', 'image_id': self.image_id}
        r = requests.post(url=API_ENDPOINT, data=data)
        return r

    def writeDataToCSV(self, data, filename):
        with open(self.currDir + filename, "w") as fp:
            csvwrite = csv.writer(fp, delimiter="-")
            csvwrite.writerow(['Name', 'Text'])
            csvwrite.writerows(data)

    def startProcess(self):
        # print("Stat Wait")
        # time.sleep(60)
        # print("Stat Wait END")
        if self.process_type == "collection":
            # collection proceess 2021-03-11 08:53:10
            print("COlection")
            if (self.check_if_string_in_file(self.currDir + "visited_url.txt", self.url)):
                print("URL ALREADY VISITED")
                # Fetch the Details from curl website API GM
                print("API Fetch Saved Collection Data")
                try:
                    response = self.getFromAPI("get_logs_collection")
                    # extracting response text
                    json_data = response.json()
                    print(json_data)
                    if json_data['error'] == "1":
                        self.getImageFromUrl()
                        return self.processImages()
                    else:
                        return {'orientation_val': json_data['orientation_val']}
                except:
                    print("Old Data fetch Problem")
                    self.getImageFromUrl()
                    return self.processImages()
            else:
                self.getImageFromUrl()
                return self.processImages()
        else:
            if (self.check_if_string_in_file(self.currDir + "visited_url.txt", self.url)):
                print("URL ALREADY VISITED")
                # Fetch the Details from curl website API GM
                print("API Fetch Saved Data")
                try:
                    response = self.getFromAPI("get_logs")
                    # extracting response text
                    json_data = response.json()
                    print(json_data)
                    if json_data['error'] == "1":
                        self.getImageFromUrl()
                        return self.processImages()
                    elif json_data['r_data'] is not None:
                        data = json.loads(json_data['r_data'])
                        return {'saved_data': '1', 'cat_type': json_data['cat_type'],
                                'orientation_val': json_data['orientation_val'], 'data': data,
                                'type': json_data['type']}
                    else:
                        return {'saved_data': '1', 'cat_type': json_data['cat_type'],
                                'orientation_val': json_data['orientation_val'], 'type': json_data['type']}
                        # return
                except:
                    print("Old Data fetch Problem")
                    self.getImageFromUrl()
                    return self.processImages()
            else:
                # self.savevisitedURL(self.currDir + "visited_url.txt", self.url)
                self.getImageFromUrl()
                return self.processImages()

    def sendForProcess(self):
        API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        # your API Toekn  here
        API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"

        # data to be sent to api
        data = {'atoken': API_KEY, 'process': 'collection', 'req_type': 'collection_create', 'image_id': self.image_id}
        finalPost = dict(list(data.items()))
        # print(finalPost)
        r = requests.post(url=API_ENDPOINT, data=finalPost)
        # extracting response text
        pastebin_url = r.text
        print("The response is:%s" % pastebin_url)

    def sendForProcessActive(self):
        API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
        # your API Toekn  here
        API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"

        # data to be sent to api
        data = {'atoken': API_KEY, 'process': 'collection', 'image_id': self.image_id}
        finalPost = dict(list(data.items()))
        # print(finalPost)
        r = requests.post(url=API_ENDPOINT, data=finalPost)

        # data to be sent to api
        data = {'atoken': API_KEY, 'process': 'collection', 'req_type': 'active_create', 'image_id': self.image_id}
        finalPost = dict(list(data.items()))
        # print(finalPost)
        # r = requests.post(url = API_ENDPOINT, data = finalPost)
        # # extracting response text
        # pastebin_url = r.text
        # print("The response is:%s"%pastebin_url)

    def processHTMLFromPDF(self):
        # try:
        pdfObj = ProcessPdf(self.tempPath + "metadata.pdf", 2)
        pdfObj.setDataListFromHtml()
        # print(pdfObj)
        imp_data_list = pdfObj.getImpDataList()
        print("ImpDataList: ", imp_data_list)
        print("PDF PRocess Filter 1")
        relevant_data = self.__filterRelevantData(imp_data_list)
        print("PDF PRocess Filter 2")
        print(relevant_data)
        final_results = self.__processResults(relevant_data)
        print("PDF PRocess Filter Completed")
        return final_results
        # except:
        #     print("PDF PRocess Filter Error")
        #     final_results = []
        #     return final_results

    def sort_dates(self, date_list):

        date_formats = ['%m/%d/%Y', '%m/%d/%y']
        date_objects = []
        try:
            for date_str in date_list:
                for date_format in date_formats:
                    try:
                        date_obj = datetime.strptime(date_str, date_format)
                        date_objects.append(date_obj)
                        break
                    except ValueError:
                        continue
                else:
                    print(f"Error processing date: {date_str}")
            sorted_date_objects = sorted(date_objects)
            sorted_dates = [date.strftime('%m/%d/%Y') for date in sorted_date_objects]
            return sorted_dates
        except Exception as e:
            return date_list

    def __filterRelevantData(self, imp_data_list):
        results = []
        service_dates = []
        date_val = []
        charge_pixel = ""
        reach_total_charge = 0
        for pixels in imp_data_list.keys():
            pattern = re.compile("[0-1][0-9]\/[0-3]\s*[0-9]\/[0-9][0-9][0-9][0-9]")
            pattern2 = re.compile("[0-1][0-9]\/[0-3][0-9]\/[0-9][0-9]")
            pattern3 = re.compile("[0-1][0-9]\/[0-9]\/[0-9][0-9]")
            pattern4 = re.compile("[0-9]\/[0-3][0-9]\/[0-9][0-9]")
            date_list1 = is_list_match(imp_data_list[pixels], pattern)
            date_list2 = is_list_match(imp_data_list[pixels], pattern2)
            date_list3 = is_list_match(imp_data_list[pixels], pattern3)
            date_list4 = is_list_match(imp_data_list[pixels], pattern4)
            # print(imp_data_list[pixels])
            date_list1 = [i.replace(" ", "") for i in date_list1]
            date_list2 = [i.replace(" ", "") for i in date_list2]

            date_list1 = self.sort_dates(date_list1)
            print("sorted date list 1 = ", date_list1)
            date_list2 = self.sort_dates(date_list2)
            print("sorted date list 2 = ", date_list2)
            try:
                if len(date_list2) > 0 and len(date_list1) == 0:
                    date_list2 = list(map(lambda x: datetime.strptime(x, "%m/%d/%y").strftime("%m/%d/%Y"), date_list2))
            except:
                date_list2 = date_list1
            if (len(date_list1) > 0):
                date_val = date_list1.copy()
                date_list1 = [self.dateCorrector(date_list1[0])]
                date_list2 = [self.dateCorrector(date_val[-1])]
            if len(date_list3) > 0 and not date_list1:
                date_list2 = [self.dateCorrector(date_list3[0])]
                date_list1 = [self.dateCorrector(date_list3[0])]
            if (len(date_list2) > 0 and not date_list2):
                date_list2 = [self.dateCorrector(date_list2[0])]
            if (len(date_list4) > 0) and not (date_list1 or date_list2 or date_list3):
                date_list2 = [self.dateCorrector(date_list4[0])]
                date_list1 = [self.dateCorrector(date_list4[0])]
            if len(imp_data_list[pixels]) > 0 and (len(date_list1) > 0 or len(
                    date_list2) > 0 or "TOTAL CHARGES TO DATE".lower() in self.removeExtraSpace(
                imp_data_list[pixels][0].replace("\n",
                                                 "").lower()) or "TOTAL CHARGES".lower() in self.removeExtraSpace(
                imp_data_list[pixels][0].replace("\n", "").lower())):
                charge_val = ""
                print("reach_total_charge: ", reach_total_charge)
                if (len(date_list1) > 0 or len(date_list2) > 0):
                    final_date_list = remove_duplicate_dates(date_list1, date_list2)
                if "TOTAL CHARGES TO DATE".lower() in self.removeExtraSpace(imp_data_list[pixels][0].replace("\n",
                                                                                                             "").lower()) or "TOTAL CHARGES".lower() in self.removeExtraSpace(
                    imp_data_list[pixels][0].replace("\n", "").lower()):
                    reach_total_charge = 1
                    print("reach_total_charge: ", reach_total_charge)
                    # print(imp_data_list)
                    # print(pixels)
                    if len(imp_data_list[pixels]) > 1 and is_number(
                            imp_data_list[pixels][1].replace("\n", "").replace(",", "").replace("$", "")):
                        charge_val = imp_data_list[pixels][1].replace("\n", "").replace("$", "").replace(",", "")
                    elif len(imp_data_list[pixels]) == 1:
                        temp = list(imp_data_list)
                        print(temp)
                        if len(temp) > 1:
                            # next_key = temp[temp.index(pixels) +1]
                            # charge_val = imp_data_list[next_key][0].replace("\n","")
                            print(imp_data_list[pixels])
                            charge_val = imp_data_list[pixels][0].replace("\n", "").replace("$", "").replace(",", "")
                    if (len(date_list1) > 0 or len(date_list2) > 0):
                        service_dates[len(service_dates):len(service_dates)] = final_date_list
                if ((len(date_list1) > 0 or len(date_list2) > 0) and len(
                        final_date_list) > 0) and reach_total_charge == 0:
                    # print(final_date_list)
                    filtered_imp_data = list(filter(pattern.search, imp_data_list[pixels]))
                    filtered_imp_data[len(filtered_imp_data):len(filtered_imp_data)] = list(
                        filter(pattern2.search, imp_data_list[pixels]))
                    print(filtered_imp_data)
                    # print(filtered_imp_data[0].split(" ")[0].lower() )
                    if len(filtered_imp_data) > 0 and (
                            "Date".lower() not in filtered_imp_data[0].split(" ")[0].lower() or "Service".lower() in
                            filtered_imp_data[0].lower()):
                        service_dates[len(service_dates):len(service_dates)] = final_date_list
                    elif len(filtered_imp_data) > 0:
                        service_dates[len(service_dates):len(service_dates)] = final_date_list
                    elif len(date_list3) > 0:
                        service_dates[len(service_dates):len(service_dates)] = final_date_list
                    elif len(date_list4) > 0:
                        service_dates[len(service_dates):len(service_dates)] = final_date_list
                if is_number(charge_val) and float(charge_val) > 0:
                    list_name = [imp_data_list[pixels][0].replace("\n", "") + ':' + (str(float(charge_val)))]
                else:
                    list_name = imp_data_list[pixels]
                if (charge_val == ""):
                    # search charge value using ocr box method
                    # author Rishab 2020-11-30 16:27:04
                    doc = fitz.open(self.tempPath + "metadata.pdf")
                    page = doc[0]
                    text_instances = page.search_for("TOTAL CHARGES TO DATE")
                    text_instances_1 = page.search_for("TOTAL CHARGES")
                    text_instances_2 = page.search_for("CHARGES")
                    print("TOTAL CHARGES TO DATE")
                    print(text_instances)
                    if len(text_instances) > 0:
                        cords_rect = self.textCordMaker(text_instances)
                        i_rect = fitz.Rect(cords_rect.x1 + 10, cords_rect.y0 - 5, cords_rect.x1 + 200,
                                           cords_rect.y1 + 50)
                        # print(text_instances[0])
                        # print(i_rect)
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        list_name = [
                            "TOTAL CHARGES TO DATE".replace("\n", "") + ':' + str(text_arr[0].strip()).replace(":",
                                                                                                               "").replace(
                                "|", "").replace(",", "").replace("$", "")]
                    elif len(text_instances_1) > 0:
                        cords_rect = self.textCordMaker(text_instances_1)
                        i_rect = fitz.Rect(cords_rect.x1 + 10, cords_rect.y0 - 5, cords_rect.x1 + 200,
                                           cords_rect.y1 + 50)
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        list_name = [
                            "TOTAL CHARGES TO DATE".replace("\n", "") + ':' + str(text_arr[0].strip()).replace(":",
                                                                                                               "").replace(
                                "|", "").replace(",", "").replace("$", "")]
                    elif len(text_instances_2) > 0:
                        cords_rect = self.textCordMaker(text_instances_2)
                        i_rect = fitz.Rect(cords_rect.x0 - 10, cords_rect.y0 + 600, cords_rect.x1 + 10,
                                           cords_rect.y1 + 640)
                        extract_text = (page.get_textbox(i_rect).strip())
                        text_arr = extract_text.split("\n")
                        list_name = [
                            "TOTAL CHARGES TO DATE".replace("\n", "") + ':' + str(text_arr[0].strip()).replace(":",
                                                                                                               "").replace(
                                "|", "").replace(",", "").replace("$", "")]

                results.append(list_name)
                print("Dates", service_dates)

        # FETCH the Service type from page 2 bills 2021-04-01 16:41:59
        doc = fitz.open(self.tempPath + "metadata.pdf")
        page = doc[0]
        text_instances = page.search_for("TREATING PROVIDER'S")
        print("TREATING PROVIDER'S")
        print(text_instances)
        service_type = ""
        relation_type = 'Owner'
        if len(text_instances) > 0:
            cords_rect = self.textCordMaker(text_instances)
            i_rect = fitz.Rect(cords_rect.x1 + 27, cords_rect.y0 + 10, cords_rect.x1 + 100, cords_rect.y1 + 40)
            # print(text_instances[0])
            # print(i_rect)
            extract_text = (page.get_textbox(i_rect).strip())
            extract_text = re.sub("\d+", "", extract_text)
            text_arr = extract_text.split("\n")
            service_type = text_arr
            text_instances = page.search_for("Independent")
            if len(text_instances) > 0:
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x0 - 60, cords_rect.y0 + 10, cords_rect.x1 + 100, cords_rect.y1 + 25)
                extract_text = (page.get_textbox(i_rect).strip())
                extract_text = re.sub("\d+", "", extract_text)
                text_arr = extract_text.split("\n")
                if text_arr != ['']:
                    samples = ['Employee', 'Independent Contractor', 'Owner', 'Other']
                    r3 = re.compile("(?i).*owner*")
                    r2 = re.compile("(?i).*independent*")
                    r1 = re.compile("(?i)[x]")
                    r4 = re.compile("(?i).*employee*")
                    newlist = list(filter(r1.match, text_arr))
                    if newlist != []:
                        ind = text_arr.index(newlist[0])
                        relation_type = samples[ind]
                    elif list(filter(r2.match, text_arr)) != []:
                        relation_type = 'Independent Contractor'
                    elif list(filter(r3.match, text_arr)) != []:
                        relation_type = 'Owner'
                    elif list(filter(r4.match, text_arr)) != []:
                        relation_type = 'Employee'
            else:
                text_instances = page.search_for("INDEPENDENT")
                if len(text_instances) > 0:
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 60, cords_rect.y0 + 10, cords_rect.x1 + 100, cords_rect.y1 + 25)
                    extract_text = (page.get_textbox(i_rect).strip())
                    extract_text = re.sub("\d+", "", extract_text)
                    text_arr = extract_text.split("\n")
                    if text_arr != ['']:
                        samples = ['Employee', 'Independent Contractor', 'Owner', 'Other']
                        r3 = re.compile("(?i).*owner*")
                        r2 = re.compile("(?i).*independent*")
                        r1 = re.compile("(?i)[x]")
                        r4 = re.compile("(?i).*employee*")
                        newlist = list(filter(r1.match, text_arr))
                        if newlist != []:
                            ind = text_arr.index(newlist[0])
                            relation_type = samples[ind]
                        elif list(filter(r2.match, text_arr)) != []:
                            relation_type = 'Independent Contractor'
                        elif list(filter(r3.match, text_arr)) != []:
                            relation_type = 'Owner'
                        elif list(filter(r4.match, text_arr)) != []:
                            relation_type = 'Employee'

        else:
            text_instances = page.search_for("Treating providers's")
            if len(text_instances) > 0:
                cords_rect = self.textCordMaker(text_instances)
                i_rect = fitz.Rect(cords_rect.x1 + 35, cords_rect.y0 + 10, cords_rect.x1 + 150, cords_rect.y1 + 40)
                # print(text_instances[0])
                # print(i_rect)
                extract_text = (page.get_textbox(i_rect).strip())
                extract_text = re.sub("\d+", "", extract_text)
                text_arr = extract_text.split("\n")
                service_type = text_arr
                text_instances = page.search_for("Independent")
                if len(text_instances) > 0:
                    cords_rect = self.textCordMaker(text_instances)
                    i_rect = fitz.Rect(cords_rect.x0 - 60, cords_rect.y0 + 10, cords_rect.x1 + 100, cords_rect.y1 + 25)
                    extract_text = (page.get_textbox(i_rect).strip())
                    extract_text = re.sub("\d+", "", extract_text)
                    text_arr = extract_text.split("\n")
                    if text_arr != ['']:
                        samples = ['Employee', 'Independent Contractor', 'Owner', 'Other']
                        r3 = re.compile("(?i).*owner*")
                        r2 = re.compile("(?i).*independent*")
                        r1 = re.compile("(?i)[x]")
                        r4 = re.compile("(?i).*employee*")
                        newlist = list(filter(r1.match, text_arr))
                        if newlist != []:
                            ind = text_arr.index(newlist[0])
                            relation_type = samples[ind]
                        elif list(filter(r2.match, text_arr)) != []:
                            relation_type = 'Independent Contractor'
                        elif list(filter(r3.match, text_arr)) != []:
                            relation_type = 'Owner'
                        elif list(filter(r4.match, text_arr)) != []:
                            relation_type = 'Employee'
                else:
                    text_instances = page.search_for("INDEPENDENT")
                    if len(text_instances) > 0:
                        cords_rect = self.textCordMaker(text_instances)
                        i_rect = fitz.Rect(cords_rect.x0 - 60, cords_rect.y0 + 10, cords_rect.x1 + 100,
                                           cords_rect.y1 + 25)
                        extract_text = (page.get_textbox(i_rect).strip())
                        extract_text = re.sub("\d+", "", extract_text)
                        text_arr = extract_text.split("\n")
                        if text_arr != ['']:
                            samples = ['Employee', 'Independent Contractor', 'Owner', 'Other']
                            r3 = re.compile("(?i).*owner*")
                            r2 = re.compile("(?i).*independent*")
                            r1 = re.compile("(?i)[x]")
                            r4 = re.compile("(?i).*employee*")
                            newlist = list(filter(r1.match, text_arr))
                            if newlist != []:
                                ind = text_arr.index(newlist[0])
                                relation_type = samples[ind]
                            elif list(filter(r2.match, text_arr)) != []:
                                relation_type = 'Independent Contractor'
                            elif list(filter(r3.match, text_arr)) != []:
                                relation_type = 'Owner'
                            elif list(filter(r4.match, text_arr)) != []:
                                relation_type = 'Employee'
        service_dates = list(dict.fromkeys(service_dates))
        return {'results': results, 'service_dates': service_dates, 'service_type': service_type,
                'relation_type': relation_type}

    def __processResults(self, relevant_data):
        final_results = []
        service_type = relevant_data['service_type']
        service_dates = relevant_data['service_dates']
        results = relevant_data['results']
        relation_type = relevant_data['relation_type']
        total_charges = ""
        for result in results:
            # print(result)
            pattern = re.compile('\s*TOTAL CHARGES TO DATE(S|$|§){0,1}(\s)*[$|§|:|\s]*(\s){0,1}([0-9])*(\.)*([0-9])*',
                                 re.IGNORECASE)
            filtered_result = list(filter(pattern.search, result))
            print("filteredresult:  ", filtered_result)
            if len(filtered_result) > 0:
                total_charges_match = re.search(pattern, filtered_result[0].replace("\n", ""))
                total_charges_str = total_charges_match.group(0)
                total_charges_arr = total_charges_str.split(":")
                print("total_charges_str", total_charges_arr)
                #  Remove blank space from array 2020-11-25 11:19:00
                total_charges_arr = [x.strip(' ') for x in total_charges_arr]
                total_charges_arr[:] = [x for x in total_charges_arr if x]
                print("total_charges_str", total_charges_arr)
                if len(total_charges_arr) == 1:
                    total_charges_arr = total_charges_arr[0].split("$")
                    if len(total_charges_arr) == 1:
                        total_charges_arr = total_charges_arr[0].split(" ")
                        if len(total_charges_arr) > 1:
                            total_charges = total_charges_arr[1]
                        else:
                            total_charges = total_charges_arr[0]
                    else:
                        total_charges = total_charges_arr[1]
                else:
                    total_charges = total_charges_arr[1]
                    total_charges = re.findall('[0-9.]+', total_charges.replace(" ", ""))[0]
            if not is_number(total_charges):
                pattern = re.compile('\s*totalchargestodate*', re.IGNORECASE)
                total_charges = re.findall('[0-9.a-z:]+', result[0].lower())
                total_charges = "".join(total_charges)
                excnt = total_charges.count(":")
                if excnt > 1:
                    total_charges = total_charges.replace(":", "", excnt - 1)
                filtered_result = list(filter(pattern.search, [total_charges]))
                if len(filtered_result) > 0:
                    total_charges_arr = filtered_result[0].split(":")
                    if len(total_charges_arr) == 1:
                        total_charges_arr = total_charges_arr[0].split("$")
                        if len(total_charges_arr) == 1:
                            total_charges_arr = total_charges_arr[0].split(" ")
                            if len(total_charges_arr) > 1:
                                total_charges = total_charges_arr[1]
                            else:
                                total_charges = total_charges_arr[0]
                        else:
                            total_charges = total_charges_arr[1]
                    elif total_charges_arr[1] != '':
                        total_charges = total_charges_arr[1]
                        try:
                            total_charges = re.findall('[0-9.]+', total_charges.replace(" ", ""))[0]
                        except:
                            total_charges = []
        service_date_str = ""
        cost_found = 0
        if len(service_dates) == 1:
            service_date_str = service_dates[0] + "-" + service_dates[0]
        elif len(service_dates) > 1:
            service_dates.sort(key=lambda date: datetime.strptime(date, '%m/%d/%Y'))
            service_date_str = service_dates[0] + "-" + service_dates[-1]
        if service_date_str != "":
            final_results.append(['Date of service', service_date_str])
        if total_charges != "" and is_number(total_charges) and cost_found == 0:
            final_results.append(['Cost', float(total_charges)])
            cost_found = 1

        # updated code for fetching cost by adding all the charges
        if "Cost" not in final_results and cost_found == 0:
            final_results.append(["Cost", ""])
            # Addition logic
            # try:
            #     try:
            #         self.ocrMyPDF()
            #         doc = fitz.open(self.tempPath + "metadata.pdf")
            #         page = doc[0]
            #     except:
            #         print("PDF ocr issue EXCPETION")
            #     t_instance = page.search_for("CHARGES")
            #     try:
            #         if t_instance:
            #             i_rect = fitz.Rect(t_instance[0].x0 - 22, t_instance[0].y0, t_instance[0].x1 + 35,
            #                                t_instance[0].y1 + 90)
            #             text = page.get_textbox(i_rect)
            #             print("full fetched text charges are: ", text)
            #             res1 = extract_numbers(text.lower().split("charges")[1] if "charges" in text.lower() else text)
            #             result = sum_of_numbers(res1)
            #             final_results.append(["Cost", result])
            #             print("ans is: ", result)
            #         else:
            #             final_results.append(["Cost", ""])
            #     except Exception as e:
            #         final_results.append(["Cost", ""])
            #         print(e)
            # except Exception as e:
            #     final_results.append(["Cost", ""])

        final_results.append(['service_type', service_type])
        final_results.append(['relation_type', relation_type])
        return final_results

    def __filterBlackColor(self, img):
        # Convert the image to hsv
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # Create range for black color
        lrange = np.array([0, 0, 0])
        # hrange = np.array([179,100,130])
        hrange = np.array([179, 145, 130])
        # Threshold hsv to get only black hsv color
        mask = cv2.inRange(hsv, lrange, hrange)

        # bitwise AND mask and image
        res = cv2.bitwise_and(img, img, mask=mask)

        # invert mask to get black letters on white backgrounds
        res2 = cv2.bitwise_not(mask)
        return res2

    def __convertImg(self, roi):
        roi = cv2.resize(roi, (900, 150), interpolation=cv2.INTER_AREA)
        edged = cv2.Canny(roi, 30, 200)
        contours, hierarchy = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        contours, boundingBoxes = sort_contours(contours)
        word = ""
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = cv2.contourArea(cnt)
            if area > 20:
                roi1 = roi[y - 3: y + h + 4, x - 3: x + w + 3]
                r_height = roi1.shape[0]
                r_width = roi1.shape[1]
                white_bg = 255 * np.ones([h + 20, w + 20], dtype=np.uint8)
                white_bg[6:r_height + 6, 6:r_width + 6] = roi1
                white_bg = cv2.medianBlur(white_bg, 3)
                _, white_bg1 = cv2.threshold(white_bg, 200, 255, cv2.THRESH_BINARY)
                text = pytesseract.image_to_string(white_bg1, config=("-l eng"))
                text = text.strip()
                if not text.isalnum():
                    try:
                        cv2.imwrite(f"{self.tempImgPath}/test-1.jpg", white_bg1)
                        command = f"convert {self.tempImgPath}/test-1.jpg -background white -flatten -resize 60% {self.tempImgPath}/test-1_2.jpg"
                        os.system(command)
                        shutil.copyfile(f"{self.tempImgPath}/test-1_2.jpg", "test-1_2.jpg")
                        img = cv2.imread(f"{self.tempImgPath}/test-1_2.jpg")
                        text = pytesseract.image_to_string(img, config=("-l eng"))
                        text = text.strip()
                    except Exception as e:
                        print(e)
                word += text
        return word

    def __processHealthInsuranceForm(self, img, filename, number=10):
        with open(filename, "r") as fp:
            csv_read = csv.reader(fp, delimiter="-")
            data = []
            for row in csv_read:
                actual_text = ""
                if len(row) > 0:
                    conv_flag = True
                    row_data = []
                    name, start, end = row
                    points = self.getStartEndPoints(start, end)
                    roi = img[int(points[0][0]):int(points[1][0]), int(points[0][1]):int(points[1][1])]
                    if name == "Cost":
                        cv2.imwrite(f"{self.tempImgPath}/cost-1.jpg", roi)
                        command = f"convert {self.tempImgPath}/cost-1.jpg -background white -flatten -resize 200% {self.tempImgPath}/cost-1_2.jpg"
                        os.system(command)
                        roi = cv2.imread(f"{self.tempImgPath}/cost-1_2.jpg")
                    elif name == "Date of service":
                        roi = cv2.adaptiveThreshold(roi, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
                    elif name == "claim number":
                        text = self.__convertImg(roi)
                        conv_flag = False
                    if conv_flag:
                        text = str(pytesseract.image_to_string(roi))
                    text = text.strip()
                    text_arr = text.split("\n")
                    text_arr = list(filter(lambda a: a.strip() != "", text_arr))
                    row_data.append(name)
                    if name == "Patient Name":
                        actual_text_arr = text_arr[0].split(",")
                        actual_text = actual_text_arr[1] + " " + actual_text_arr[0]
                    elif name == "Insurer name":
                        actual_text = text_arr[0]
                    elif name == "Date of service":
                        dates_arr = []
                        for lines in text_arr:
                            if lines.strip() != "":
                                characters = lines.split(" ")
                                i = 0
                                for character in characters:
                                    # print("Char = ", character)
                                    # print("len of char = ", len(character))
                                    # print( ("o" in character))
                                    if len(character) == 2 and "o" in character:
                                        character = character.replace("o", "0")
                                    elif len(character) > 2 and "o" in character:
                                        character = character.replace("o", "")
                                    characters[i] = character
                                    i += 1
                                # print(characters)
                                date1 = characters[0] + "/" + characters[1] + "/" + characters[2]
                                date2 = characters[3] + "/" + characters[4] + "/" + characters[5]
                                dates_arr.append(self.dateCorrector(date1))
                                dates_arr.append(self.dateCorrector(date2))
                        try:
                            dates_arr.sort(key=lambda date: datetime.strptime(date, "%m/%d/%Y"))
                        except:
                            print("Date Format Error")
                            # print(dates_arr)
                        actual_text = dates_arr[0] + "-" + dates_arr[-1]
                    elif name == "Cost":
                        text_arr[0] = text_arr[0].replace(",", "")
                        actual_text_arr = text_arr[0].split(" ")
                        actual_text = actual_text_arr[0] + "." + actual_text_arr[1]
                    elif name == "accident date":
                        actual_text = self.dateCorrector(text)
                    else:
                        actual_text = text
                    actual_text = actual_text.strip()
                    row_data.append(actual_text)
                    if len(row_data) > 1:
                        data.append(row_data)
        return data

    def __del__(self):
        shutil.rmtree(self.tempPath)
        shutil.rmtree(self.tempImgPath)
        # pass

    def dateCorrector(self, date_string):
        date_arr = date_string.split('/')
        # print(date_arr)
        month = date_arr[0]
        day = date_arr[1]
        year = date_arr[2]
        # month correction
        # print(month)
        # print(type(month))
        size = len(month)
        size_day = len(day)
        size_year = len(year)
        if (size > 2):
            month = month[-2:]
        if (size_day == 1):
            day = str(day)
            day = day.zfill(2)
        if (size_year == 2 and int(str(year)) >= 20):
            year = int(str(year))
            year = year + 2000
            year = str(year)
        if (size_year == 3 and int(str(year)) >= 202):
            year = int(str(year))
            year = year * 10
            year = str(year)
        if (int(str(month)) > 12):
            orig_first_dig = int(str(month)[:1])
            first_dig = (int(str(month)[:1]) * 10)
            second_dig = (int(str(month)[1]))
            # print(first_dig)
            # print(orig_first_dig)
            # print(second_dig)
            if (second_dig == 0):
                first_dig = first_dig - 10
            elif (first_dig > 10):
                month = str(int(month) - first_dig)
            elif (second_dig > 2):
                second_dig = 2
                month = str(orig_first_dig) + str(second_dig)
                month = str(month)
                # print(month)
            month = month.zfill(2)
        corrected_date = str(month).strip() + "/" + day.strip() + "/" + year.strip()
        # print(corrected_date)
        return corrected_date

    def removeDateSpace(self, dateString):
        date_arr = dateString.split('/')
        if (len(date_arr) > 0):
            try:
                month = str(date_arr[0])
                day = str(date_arr[1])
                year = str(date_arr[2])
                corrected_date = month.strip().replace(" ", "").replace("Q", "0") + "/" + day.strip().replace(" ",
                                                                                                              "").replace(
                    "Q", "0") + "/" + year.strip().replace(" ", "").replace("Q", "0")
                # print(corrected_date)
                return corrected_date
            except:
                dateString
        else:
            # check if date contains any alphabets
            return dateString

    def cleanString(self, incomingString):
        newstring = incomingString
        newstring = newstring.replace("!", "")
        newstring = newstring.replace("@", "")
        newstring = newstring.replace("#", "")
        newstring = newstring.replace("$", "")
        newstring = newstring.replace("%", "")
        newstring = newstring.replace("^", "")
        newstring = newstring.replace("&", "and")
        newstring = newstring.replace("*", "")
        newstring = newstring.replace("(", "")
        newstring = newstring.replace(")", "")
        newstring = newstring.replace("+", "")
        newstring = newstring.replace("=", "")
        newstring = newstring.replace("?", "")
        newstring = newstring.replace("\'", "")
        newstring = newstring.replace("\"", "")
        newstring = newstring.replace("{", "")
        newstring = newstring.replace("}", "")
        newstring = newstring.replace("[", "")
        newstring = newstring.replace("]", "")
        newstring = newstring.replace("<", "")
        newstring = newstring.replace(">", "")
        newstring = newstring.replace("~", "")
        newstring = newstring.replace("`", "")
        newstring = newstring.replace(":", "")
        newstring = newstring.replace(";", "")
        newstring = newstring.replace("|", "")
        newstring = newstring.replace("\\", "")
        newstring = newstring.replace("/", "")
        newstring = newstring.replace('"', '')
        newstring = newstring.replace('‘', '')
        newstring = newstring.strip(' ')

        if (newstring == ""):
            newstring = ""
        return newstring

    def validateReturnData(self, data, number):
        value_missing = 0
        if number in [2, 3, 6, 10, 12, 13, 15]:
            return value_missing
        for internal_val in data:
            key = internal_val[0]
            value = internal_val[1]
            if (key == "Insurer name" and value == ""):
                value_missing = 0
            elif (key == "Patient Name" and value == ""):
                value_missing = 0
            elif (key == "Provider Name" and value == ""):
                value_missing = 0

        insurer_found = 0
        patient_found = 0
        accident_found = 0
        provider = 0
        for internal_val in data:
            key = internal_val[0]
            value = internal_val[1]
            if (key == "Insurer name"):
                insurer_found = 1
            elif (key == "Patient Name"):
                patient_found = 1
            elif (key == "Provider Name"):
                provider = 1
        if (insurer_found == 0 or patient_found == 0 or provider == 0):
            value_missing = 1
        return value_missing

    def removeExtraSpace(self, str_val):
        return " ".join(str_val.split())

    def handlePatientName(self, name):
        name = name.strip(' ')
        actual_text_arr = name.split(",")
        print(actual_text_arr)
        try:
            if actual_text_arr[1] != "" and actual_text_arr[0] != "":
                actual_text = actual_text_arr[1].strip() + " " + actual_text_arr[0].strip()
                return actual_text
            if actual_text_arr[1] != "" and actual_text_arr[0] == "":
                actual_text = actual_text_arr[1].strip()
                return actual_text
        except:
            name = name
        return name

    def handleInsurerNameAPI(self, insurer_name):
        # return insurer_name
        try:
            API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
            # your API Toekn  here
            API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
            # data to be sent to api
            data = {'atoken': API_KEY, 'process': 'collection', 'insurer_name': insurer_name,
                    'get_alternative_insurer': 1}
            r = requests.post(url=API_ENDPOINT, data=data)
            json_data = r.json()
            print(json_data)
            data = json_data['alt_insurer_name']
            if json_data["error"] == 0 and len(data) > 0:
                print(data)
                return data
            else:
                return insurer_name
        except:
            return insurer_name

    def handleProviderNameAPI(self, provider_name):
        # return provider_name
        try:
            API_ENDPOINT = "https://gmtest.neuralit.com/liberation/functions/rpa_update_status"
            # your API Toekn  here
            API_KEY = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
            # data to be sent to api
            data = {'atoken': API_KEY, 'process': 'collection', 'provider_name': provider_name,
                    'get_alternative_provider': 1}
            r = requests.post(url=API_ENDPOINT, data=data)
            json_data = r.json()
            print(json_data)
            data = json_data['alt_provider_name']
            if json_data["error"] == 0 and len(data) > 0:
                print(data)
                return data
            else:
                return provider_name
        except:
            return provider_name
