import re


def clean_paragraph(paragraph):
    # Remove non-alphabetic characters and keep newlines
    cleaned_paragraph = re.sub(r'[^a-zA-Z\n]', '', paragraph)

    # Remove empty lines
    cleaned_paragraph = "\n".join([line for line in cleaned_paragraph.split("\n") if line.strip()])

    return cleaned_paragraph


# Example usage:
paragraph = """
This is a test paragraph! It contains some numbers: 12345 and symbols: @#$%^&*().
There are also some empty lines:

This should be cleaned up.
"""

cleaned_paragraph = clean_paragraph(paragraph)
print(cleaned_paragraph)
