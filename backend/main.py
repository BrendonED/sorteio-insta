import asyncio
import json
import os
import uuid
import requests
from typing import Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: Dict[str, Any] = {}

# Chave da RapidAPI que o usuário precisa configurar no Render
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

class ScrapeRequest(BaseModel):
    url: str

def get_media_comments_paginated(job_id: str, url: str):
    try:
        # Extrair o media_code da URL (ex: https://www.instagram.com/p/DLUWkieNc0u/ -> DLUWkieNc0u)
        # Lógica simples de split
        parts = [p for p in url.split("/") if p]
        media_code = parts[-1]
        if media_code == "comments":
             media_code = parts[-2]
             
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Conectando à RapidAPI..."
        
        if not RAPIDAPI_KEY:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["error"] = "RAPIDAPI_KEY não configurada no servidor (Render)."
            return

        all_comments = []
        end_cursor = ""
        has_next_page = True
        
        headers = {
            "x-rapidapi-host": "instagram-scraper-stable-api.p.rapidapi.com",
            "x-rapidapi-key": RAPIDAPI_KEY,
            "Content-Type": "application/json"
        }
        
        while has_next_page:
            api_url = f"https://instagram-scraper-stable-api.p.rapidapi.com/get_post_comments.php?media_code={media_code}&sort_order=popular"
            if end_cursor:
                api_url += f"&end_cursor={end_cursor}"
                
            response = requests.get(api_url, headers=headers)
            
            if response.status_code != 200:
                raise Exception(f"Erro na RapidAPI: {response.status_code} - {response.text}")
                
            data = response.json()
            
            # Formato esperado da resposta da RapidAPI (baseado no comum desse endpoint)
            # Pode variar, mas normalmente os comentários ficam em data.items ou data.comments
            # Vamos assumir que venham numa lista 'items' ou 'data'
            items = data.get("data", []) or data.get("items", [])
            
            for item in items:
                # Ajuste de acordo com os campos reais da API (geralmente user.username e text)
                username = item.get("user", {}).get("username", "desconhecido")
                text = item.get("text", "")
                all_comments.append({"user": username, "text": text})
            
            # Atualizar a tela do front
            jobs[job_id]["progress"] = f"{len(all_comments)} comentários extraídos..."
            
            # Verificar paginação
            page_info = data.get("page_info", {})
            has_next_page = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", "")
            
            # Esperar 2 segundos antes de pedir a próxima página para não tomar block de Rate Limit da RapidAPI
            if has_next_page:
                import time
                time.sleep(2)
        
        jobs[job_id]["comments"] = all_comments
        jobs[job_id]["progress"] = f"Extração de {len(all_comments)} comentários finalizada!"
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
