from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
import os
from dotenv import load_dotenv

# Import our custom modules
from app.agent import SHLAgent
import asyncio

load_dotenv(override=True)

app = FastAPI(title="SHL Assessment Recommender API")

@app.on_event("startup")
async def startup_event():
    # Pre-warm the vector store in the background so the first request is instant
    from app.vector_store import get_vector_store
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, get_vector_store().build_index)

# Serve frontend
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Singleton agent
agent = SHLAgent()

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]

class Recommendation(BaseModel):
    name: str
    url: Optional[str] = "#"
    test_type: str
    matched_keywords: Optional[List[str]] = []

class ChatResponse(BaseModel):
    reply: str
    recommendations: List[Recommendation]
    end_of_conversation: bool

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Convert Pydantic models to dicts for the agent
    msgs = [{"role": m.role, "content": m.content} for m in request.messages]
    
    # Check turn limit (8 total messages = 4 user + 4 assistant)
    if len(msgs) >= 8:
        return {
            "reply": "I've reached the conversation limit for this session. Thank you for using SHL Labs!",
            "recommendations": [],
            "end_of_conversation": True
        }
        
    response = await agent.get_reply(msgs)
    # Ensure we only return fields defined in the schema
    return {
        "reply": response.get("reply", ""),
        "recommendations": response.get("recommendations", []),
        "end_of_conversation": response.get("end_of_conversation", False)
    }

class TitleRequest(BaseModel):
    prompt: str

@app.post("/generate_title")
async def generate_title(request: TitleRequest):
    title = await agent.generate_title(request.prompt)
    return {"title": title}

# /process-file removed to stay strictly within assignment scope

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
