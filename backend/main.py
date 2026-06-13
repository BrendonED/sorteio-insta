import asyncio
import json
import os
import random
import uuid
from typing import Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from instagrapi import Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for jobs
jobs: Dict[str, Any] = {}

cl = Client()
INSTA_USERNAME = os.getenv("INSTA_USERNAME")
INSTA_PASSWORD = os.getenv("INSTA_PASSWORD")
IS_SIMULATION = os.getenv("IS_SIMULATION", "true").lower() == "true"

if not IS_SIMULATION and INSTA_USERNAME and INSTA_PASSWORD:
    try:
        print(f"Iniciando login no Instagram para a conta: {INSTA_USERNAME}")
        cl.login(INSTA_USERNAME, INSTA_PASSWORD)
        print("Login concluído com sucesso.")
    except Exception as e:
        import traceback
        print(f"Erro ao logar no Instagram:\n{traceback.format_exc()}")

class ScrapeRequest(BaseModel):
    url: str

def get_media_comments_paginated(job_id: str, url: str):
    try:
        if IS_SIMULATION:
            # Simulação para demonstrar a barra de progresso sem depender de login
            jobs[job_id]["status"] = "running"
            total = 3000
            for i in range(0, total, 50):
                jobs[job_id]["progress"] = f"{i}/{total}"
                # Simula tempo de extração
                asyncio.run(asyncio.sleep(1.5))
            
            # Conclui com dados falsos
            fake_comments = [{"user": f"usuario_{i}", "text": f"Comentário de teste {i}"} for i in range(total)]
            jobs[job_id]["comments"] = fake_comments
            jobs[job_id]["progress"] = f"{total}/{total}"
            jobs[job_id]["status"] = "completed"
            return

        # Lógica real com instagrapi
        media_pk = cl.media_pk_from_url(url)
        jobs[job_id]["status"] = "running"
        
        # O instagrapi não oferece uma forma trivial de paginar comentários e devolver a cada chunk facilmente
        # O método media_comments baixa todos de uma vez (limitado por amount). 
        # Num cenário real complexo, usaríamos a private API cursors.
        # Aqui, vamos chamar media_comments com um amount grande (ex: 3000).
        # Nota: ISSO PODE DEMORAR dependendo do volume.
        amount = 3000
        jobs[job_id]["progress"] = "Extraindo (isso pode levar alguns minutos)..."
        
        comments_obj = cl.media_comments(media_pk, amount=amount)
        comments = [{"user": c.user.username, "text": c.text} for c in comments_obj]
        
        jobs[job_id]["comments"] = comments
        jobs[job_id]["progress"] = f"{len(comments)}/{len(comments)}"
        jobs[job_id]["status"] = "completed"
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERRO AO RASPAR COMENTÁRIOS: {error_details}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = f"Erro na extração: {str(e)}"


@app.post("/api/scrape")
async def start_scraping(req: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting",
        "progress": "0/0",
        "comments": [],
        "error": None
    }
    
    background_tasks.add_task(get_media_comments_paginated, job_id, req.url)
    
    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream_progress(job_id: str, request: Request):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
                
            job = jobs[job_id]
            # Convert job dict to JSON string safely
            # We omit full comments array to save bandwidth during streaming if it's large, 
            # except when completed. Actually, let's just send everything for simplicity, 
            # but ideally frontend fetches comments via another endpoint if it's too big.
            data_to_send = {
                "status": job["status"],
                "progress": job["progress"],
                "error": job["error"]
            }
            if job["status"] == "completed":
                data_to_send["comments"] = job["comments"]
                
            yield f"data: {json.dumps(data_to_send)}\n\n"
            
            if job["status"] in ["completed", "error"]:
                break
                
            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
