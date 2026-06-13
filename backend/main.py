import asyncio
import json
import os
import re
import time
import uuid
import random
import requests
from typing import Dict, Any

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs: Dict[str, Any] = {}

class ScrapeRequest(BaseModel):
    url: Optional[str] = None
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

def shortcode_to_media_id(shortcode: str) -> int:
    """
    Converte o shortcode do Instagram (ex: DLUWkieNc0u) para o media_id numérico.
    Usa o mesmo algoritmo do Instagram (base64url decoding).
    """
    ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    media_id = 0
    for char in shortcode:
        media_id = media_id * 64 + ALPHABET.index(char)
    return media_id

def extract_shortcode(url: str) -> str:
    """
    Extrai o shortcode da URL do post. Funciona com /p/, /reel/ e /tv/
    Exemplos:
    - https://www.instagram.com/p/DLUWkieNc0u/
    - https://www.instagram.com/reel/DLUWkieNc0u/?igsh=...
    """
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Não foi possível extrair o código do post da URL: {url}")
    return match.group(1)

def get_session_headers(session_cookies: dict = None) -> dict:
    """Monta headers que imitam um navegador real."""
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "X-IG-App-ID": "936619743392459",  # ID fixo do app web do Instagram
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
        "Origin": "https://www.instagram.com",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    return headers


def get_media_comments_paginated(job_id: str, url: str):
    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Extraindo shortcode do link..."

        shortcode = extract_shortcode(url)
        media_id = shortcode_to_media_id(shortcode)

        print(f"[JOB {job_id}] Shortcode: {shortcode} | Media ID: {media_id}")

        jobs[job_id]["progress"] = f"Conectando ao Instagram (media_id: {media_id})..."

        session = requests.Session()
        
        # 1. Primeiro, faz uma requisição à página do post para obter cookies válidos (csrftoken)
        page_url = f"https://www.instagram.com/p/{shortcode}/"
        page_res = session.get(
            page_url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
            timeout=20,
        )
        print(f"[JOB {job_id}] Cookies da página: {dict(session.cookies)}")

        all_comments = []
        end_cursor = None
        has_next_page = True
        page_num = 0

        while has_next_page:
            page_num += 1
            params = {
                "can_support_threading": "true",
                "sort_order": "popular",
            }
            if end_cursor:
                params["min_id"] = end_cursor

            api_url = f"https://www.instagram.com/api/v1/media/{media_id}/comments/"

            response = session.get(
                api_url,
                params=params,
                headers=get_session_headers(),
                timeout=20,
            )

            print(f"[JOB {job_id}] Página {page_num} | Status: {response.status_code}")

            if response.status_code == 401:
                # Instagram pedindo login — post privado ou conta bloqueada por IP
                jobs[job_id]["status"] = "error"
                jobs[job_id]["error"] = "Instagram exige login para acessar esse post (post privado ou bloqueio de IP do servidor). Tente um post público diferente ou use uma conta bot."
                return

            if response.status_code == 429:
                # Rate limit — espera e tenta de novo
                print(f"[JOB {job_id}] Rate limit! Aguardando 10s...")
                time.sleep(10)
                continue

            if response.status_code != 200:
                raise Exception(
                    f"Instagram retornou status {response.status_code}: {response.text[:500]}"
                )

            data = response.json()

            # Extrair comentários
            comments_raw = data.get("comments", [])
            for c in comments_raw:
                user = c.get("user", {}).get("username", "desconhecido")
                text = c.get("text", "")
                all_comments.append({"user": user, "text": text})

            # Atualizar progresso
            jobs[job_id]["progress"] = f"{len(all_comments)} comentários extraídos..."
            print(f"[JOB {job_id}] Total até agora: {len(all_comments)}")

            # Paginação — o Instagram usa "next_min_id" ou "has_more_comments"
            has_next_page = data.get("has_more_comments", False)
            end_cursor = data.get("next_min_id", None)

            if has_next_page and end_cursor:
                # Delay aleatório entre 1.5s e 3.5s para não tomar ban
                delay = random.uniform(1.5, 3.5)
                print(f"[JOB {job_id}] Próxima página em {delay:.1f}s...")
                time.sleep(delay)
            else:
                break

        jobs[job_id]["comments"] = all_comments
        jobs[job_id]["progress"] = f"Extração de {len(all_comments)} comentários finalizada!"
        jobs[job_id]["status"] = "completed"
        print(f"[JOB {job_id}] Extração completa. {len(all_comments)} comentários.")

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERRO AO RASPAR COMENTÁRIOS:\n{error_details}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = f"Erro na extração: {str(e)}"


@app.post("/api/scrape")
async def start_scraping(req: ScrapeRequest, background_tasks: BackgroundTasks):
    print(f"[SCRAPE] Body recebido: url={req.url!r}")
    
    if not req.url or not req.url.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Campo 'url' é obrigatório e não pode ser vazio."}
        )

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "starting",
        "progress": "Iniciando...",
        "comments": [],
        "error": None,
    }
    background_tasks.add_task(get_media_comments_paginated, job_id, req.url.strip())
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
            data_to_send = {
                "status": job["status"],
                "progress": job["progress"],
                "error": job["error"],
            }
            if job["status"] == "completed":
                data_to_send["comments"] = job["comments"]

            yield f"data: {json.dumps(data_to_send, ensure_ascii=False)}\n\n"

            if job["status"] in ["completed", "error"]:
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/")
async def root():
    return {"status": "ok", "message": "Sorteio Insta API rodando!"}
