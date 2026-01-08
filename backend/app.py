from fastapi import FastAPI
from services.voice_agent import router as voice_router

app = FastAPI()

app.include_router(voice_router)

@app.get("/")
def health_check():
    return {"status": "backend running"}

from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def health_check():
    return {"status": "backend running"}
