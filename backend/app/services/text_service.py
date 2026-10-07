import re


def clean_text(text: str) -> str:
    """
    Clean extracted project-document text.
    """

    if not text:
        return ""

    # Normalize line endings
    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")

    # Remove excessive whitespace
    text = re.sub(r"[ \t]+", " ", text)

    # Reduce excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove spaces at the beginning/end of lines
    lines = [line.strip() for line in text.split("\n")]

    text = "\n".join(lines)

    return text.strip()