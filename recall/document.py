import fitz  # PyMuPDF


def prepare_document(pdf_path: str) -> str:
    """Extract all text from PDF with page markers."""
    doc = fitz.open(pdf_path)
    full_text = ""
    for i, page in enumerate(doc):
        full_text += f"\n\n--- PAGE {i+1} ---\n{page.get_text()}"
    doc.close()
    return full_text
