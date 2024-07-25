import re
from datetime import datetime
import cv2
import numpy as np
from PyPDF2 import PdfReader, PdfWriter


def extract_numbers(text):
    pattern = r'(?<!\w)[-+]?\b\d*\.\d+\b|\b\d+\b(?!\w)'
    numbers = re.findall(pattern, text)
    numbers = [float(num) if '.' in num else int(num) for num in numbers]
    return numbers


def sum_of_numbers(numbers):
    # Use the built-in sum() function to calculate the sum of the numbers in the list
    total = sum(numbers)
    return total


def rotate_pdf(input_pdf, output_pdf):
    try:
        print("rfginsgkjsnvg")
        reader = PdfReader(input_pdf)
        writer = PdfWriter()
        print("opened")

        for page_num in range(len(reader.pages)):
            page = reader.pages[page_num]
            page.rotate(180)  # Rotate by specified angle
            writer.addPage(page)
            print("doneeeeeee")

        with open(output_pdf, 'wb') as output_file:
            print("writing")
            writer.write(output_file)
            print("succcessssss")
    except Exception as e:
        print("error in rotation: ", e)


def is_number(string):
    try:
        typ = str(type(string))
        if "list" not in typ.lower():
            float(string)
            return True
    except ValueError as e:
        print("error", e)
        return False


def get_filtered_data(data):
    pattern = re.compile("^(?!.*?^\n).*")
    if pattern.search(data) and data != "":
        return True
    else:
        return False


def is_list_match(list_var, pattern):
    match_list = []
    for list_el in list_var:
        try:
            if "REPORT OF SERVICES" in list_el:
                list_el_1 = list_el[list_el.index("REPORT OF SERVICES"):]
                print("searching in page 2")
            elif "DATE Date" in list_el:
                regex = r'DATE Date ((?:\d{1,2}/\d{1,2}/\d{2,4}\s*)+)'
                matches = re.findall(regex, list_el)
                list_el_1 = " "
                for match in matches:
                    list_el_1 += str(match)
                    print("for page 4 dates: ", list_el_1)

        except Exception as e:
            list_el_1 = list_el
            print("Error: ", e)
        matches = re.findall(pattern, list_el_1)
        if len(matches) > 0:
            match_list[len(match_list):len(match_list)] = matches
    print("all dates match list: ", match_list)
    return match_list


def remove_duplicate_dates(date_list1, date_list2):
    final_date_list = []
    if len(date_list1) == 0:
        return list(dict.fromkeys(date_list2))
    elif len(date_list2) == 0:
        return list(dict.fromkeys(date_list1))
    else:
        for date_el1 in date_list1:
            date_time1 = datetime.strptime(date_el1, '%m/%d/%Y')
            for date_el2 in date_list2:
                date_time2 = datetime.strptime(date_el2, '%m/%d/%Y')
                if date_time1 == date_time2:
                    final_date_list.append(date_el1)
                else:
                    final_date_list.append(date_el1)
                    final_date_list.append(date_el2)
        final_date_list = list(dict.fromkeys(final_date_list))
        return final_date_list


def sort_contours(cnts, method="left-to-right"):
    # initialize the reverse flag and sort index
    reverse = False
    i = 0
    # handle if we need to sort in reverse
    if method == "right-to-left" or method == "bottom-to-top":
        reverse = True
    # handle if we are sorting against the y-coordinate rather than
    # the x-coordinate of the bounding box
    if method == "top-to-bottom" or method == "bottom-to-top":
        i = 1
    # construct the list of bounding boxes and sort them from top to
    # bottom
    boundingBoxes = [cv2.boundingRect(c) for c in cnts]
    (cnts, boundingBoxes) = zip(*sorted(zip(cnts, boundingBoxes),
                                        key=lambda b: b[1][i], reverse=reverse))
    # return the list of sorted contours and bounding boxes
    return (cnts, boundingBoxes)
