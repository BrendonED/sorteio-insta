import asyncio
import json
import os
import re
import time
import uuid
import random
import requests
from typing import Dict, Any, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
]

class ScrapeRequest(BaseModel):
    url: Optional[str] = None


def extract_shortcode(url: str) -> str:
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Não foi possível extrair o código do post da URL: {url}")
    return match.group(1)


def get_media_comments_paginated(job_id: str, url: str):
    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Extraindo shortcode do link..."

        shortcode = extract_shortcode(url)
        print(f"[JOB {job_id}] Shortcode: {shortcode}")

        session = requests.Session()
        session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })

        # ── PASSO 1: visitar instagram.com para obter cookies base ──────────────
        jobs[job_id]["progress"] = "Obtendo cookies do Instagram..."
        try:
            session.get("https://www.instagram.com/", timeout=15)
        except Exception:
            pass  # cookies parciais ainda podem ser úteis

        # ── PASSO 2: visitar a página do post para refinar cookies + csrftoken ──
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        jobs[job_id]["progress"] = "Abrindo página do post..."
        page_res = session.get(post_url, timeout=20)
        print(f"[JOB {job_id}] Página do post: {page_res.status_code}")
        print(f"[JOB {job_id}] Cookies: {dict(session.cookies)}")

        csrf = session.cookies.get("csrftoken", "")

        # ── PASSO 3: scraping via GraphQL ────────────────────────────────────────
        # Hash do query de comentários do Instagram Web (estável há anos)
        COMMENTS_QUERY_HASH = "bc3296d1ce80a24b1b6e40b1e72903f5"

        all_comments = []
        end_cursor = None
        has_next = True
        page_num = 0

        graphql_headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "*/*",
            "Accept-Language": "pt-BR,pt;q=0.9",
            "Referer": post_url,
            "X-CSRFToken": csrf,
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
        }

        while has_next:
            page_num += 1
            jobs[job_id]["progress"] = f"Baixando página {page_num} de comentários... ({len(all_comments)} até agora)"

            variables = {
                "shortcode": shortcode,
                "first": 50,
            }
            if end_cursor:
                variables["after"] = end_cursor

            params = {
                "query_hash": COMMENTS_QUERY_HASH,
                "variables": json.dumps(variables, separators=(",", ":")),
            }

            resp = session.get(
                "https://www.instagram.com/graphql/query/",
                params=params,
                headers=graphql_headers,
                timeout=25,
            )

            print(f"[JOB {job_id}] GraphQL pág {page_num} | HTTP {resp.status_code} | Tam: {len(resp.text)}")
            print(f"[JOB {job_id}] Resposta (primeiros 500 chars): {resp.text[:500]}")

            if resp.status_code == 429:
                print(f"[JOB {job_id}] Rate limit! Aguardando 15s...")
                time.sleep(15)
                continue

            if resp.status_code != 200:
                raise Exception(f"Instagram retornou HTTP {resp.status_code}. Resposta: {resp.text[:300]}")

            # Tentar parsear JSON — se falhar, o HTML retornado vai aparecer no log
            try:
                data = resp.json()
            except Exception:
                print(f"[JOB {job_id}] Resposta NÃO é JSON! Conteúdo: {resp.text[:800]}")
                raise Exception(
                    f"Instagram retornou HTML (não JSON) na página {page_num}. "
                    "Isso indica bloqueio por IP de servidor. "
                    "Verifique os logs do Render para ver a resposta completa."
                )

            # Navegar na estrutura do JSON
            media = (
                data.get("data", {})
                    .get("shortcode_media", {})
            )
            edge_media_to_comment = media.get("edge_media_to_comment", {})
            edges = edge_media_to_comment.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                username = node.get("owner", {}).get("username", "desconhecido")
                text = node.get("text", "")
                all_comments.append({"user": username, "text": text})

            page_info = edge_media_to_comment.get("page_info", {})
            has_next = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", None)

            jobs[job_id]["progress"] = f"{len(all_comments)} comentários extraídos..."
            print(f"[JOB {job_id}] Total: {len(all_comments)} | has_next: {has_next}")

            if has_next and end_cursor:
                delay = random.uniform(2.0, 4.0)
                print(f"[JOB {job_id}] Próxima página em {delay:.1f}s...")
                time.sleep(delay)
            else:
                break

        if not all_comments:
            # Pode ser que o Instagram retornou dados mas em estrutura diferente
            # Tentar estrutura alternativa: edge_media_preview_comment
            print(f"[JOB {job_id}] Nenhum comentário encontrado. Estrutura do JSON: {json.dumps(data, indent=2)[:1000]}")

        jobs[job_id]["comments"] = all_comments
        jobs[job_id]["progress"] = f"Extração de {len(all_comments)} comentários finalizada!"
        jobs[job_id]["status"] = "completed"
        print(f"[JOB {job_id}] CONCLUÍDO. {len(all_comments)} comentários.")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"ERRO AO RASPAR COMENTÁRIOS:\n{tb}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = f"{str(e)}"


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
