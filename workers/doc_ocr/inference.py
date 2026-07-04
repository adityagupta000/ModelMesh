import easyocr
import numpy as np
import cv2

# Load once at module import, not per-request
reader = easyocr.Reader(['en'], gpu=False)


def extract_text(image_bytes: bytes) -> dict:
    """
    Extract text from image bytes using EasyOCR.
    Returns dict with extracted text, avg confidence, and line count.
    """
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

    if img is None:
        raise ValueError("Invalid image data")

    results = reader.readtext(img)

    lines = [text for (_, text, _) in results]
    confidences = [float(conf) for (_, _, conf) in results]

    return {
        "text": "\n".join(lines),
        "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "line_count": len(lines)
    }
