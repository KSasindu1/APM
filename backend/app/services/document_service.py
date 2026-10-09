from pathlib import Path

import pymupdf


def extract_text_from_pdf(file_path: str) -> str:
    """
    Extract text from all pages of a PDF document.
    """

    pdf_path = Path(file_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported at this stage.")

    document = pymupdf.open(file_path)

    pages = []

    try:
        for page in document:
            text = page.get_text()

            if text:
                pages.append(text)

    finally:
        document.close()

    return "\n".join(pages)