from fastapi import FastAPI, UploadFile, File, HTTPException
from inference import extract_text

app = FastAPI(title="Document OCR Worker", version="1.0.0")


@app.post("/infer")
async def infer(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Expected an image file")
    image_bytes = await file.read()
    result = extract_text(image_bytes)
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
