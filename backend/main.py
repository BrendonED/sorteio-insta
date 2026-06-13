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

# ── Configuração ──────────────────────────────────────────────────────────────
# Coloque o valor do cookie "sessionid" do Instagram nas variáveis de ambiente do Render
INSTAGRAM_SESSION_ID = os.getenv("INSTAGRAM_SESSION_ID", "")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]


class ScrapeRequest(BaseModel):
    url: Optional[str] = None


def extract_shortcode(url: str) -> str:
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Não foi possível extrair o código do post da URL: {url}")
    return match.group(1)


def build_session() -> requests.Session:
    """
    Cria uma session com cookies de autenticação do Instagram.
    O 'sessionid' é obtido do navegador do usuário e configurado via env var no Render.
    """
    if not INSTAGRAM_SESSION_ID:
        raise Exception(
            "INSTAGRAM_SESSION_ID não configurado! "
            "Vá no painel do Render → Environment Variables e adicione seu sessionid do Instagram. "
            "Veja as instruções no README do projeto."
        )

    session = requests.Session()
    session.cookies.set("sessionid", INSTAGRAM_SESSION_ID, domain=".instagram.com")
    session.cookies.set("ig_did", str(uuid.uuid4()), domain=".instagram.com")

    return session


def get_media_comments_paginated(job_id: str, url: str):
    try:
        jobs[job_id]["status"] = "running"
        jobs[job_id]["progress"] = "Extraindo shortcode do link..."

        shortcode = extract_shortcode(url)
        print(f"[JOB {job_id}] Shortcode: {shortcode}")

        # Criar sessão autenticada
        session = build_session()

        # ── Passo 1: visitar a página do post para obter csrftoken e demais cookies ──
        jobs[job_id]["progress"] = "Abrindo página do post no Instagram..."
        ua = random.choice(USER_AGENTS)
        page_res = session.get(
            f"https://www.instagram.com/p/{shortcode}/",
            headers={
                "User-Agent": ua,
                "Accept-Language": "pt-BR,pt;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=25,
        )
        print(f"[JOB {job_id}] Página do post: HTTP {page_res.status_code}")

        csrf = session.cookies.get("csrftoken", "")
        print(f"[JOB {job_id}] csrftoken obtido: {csrf[:10]}...")

        # ── Passo 2: buscar comentários via GraphQL ───────────────────────────────────
        # Hash estável do query de comentários do Instagram Web
        COMMENTS_QUERY_HASH = "bc3296d1ce80a24b1b6e40b1e72903f5"

        all_comments = []
        end_cursor = None
        has_next = True
        page_num = 0

        while has_next:
            page_num += 1
            jobs[job_id]["progress"] = f"{len(all_comments)} comentários extraídos... (página {page_num})"

            variables = {
                "shortcode": shortcode,
                "first": 50,
            }
            if end_cursor:
                variables["after"] = end_cursor

            resp = session.get(
                "https://www.instagram.com/graphql/query/",
                params={
                    "query_hash": COMMENTS_QUERY_HASH,
                    "variables": json.dumps(variables, separators=(",", ":")),
                },
                headers={
                    "User-Agent": ua,
                    "Accept": "*/*",
                    "Accept-Language": "pt-BR,pt;q=0.9",
                    "Referer": f"https://www.instagram.com/p/{shortcode}/",
                    "X-CSRFToken": csrf,
                    "X-IG-App-ID": "936619743392459",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=25,
            )

            print(f"[JOB {job_id}] GraphQL pág {page_num} | HTTP {resp.status_code} | {len(resp.text)} bytes")

            if resp.status_code == 429:
                wait = 15
                print(f"[JOB {job_id}] Rate limit! Aguardando {wait}s...")
                jobs[job_id]["progress"] = f"Rate limit... aguardando {wait}s ({len(all_comments)} comentários até agora)"
                time.sleep(wait)
                continue

            if resp.status_code == 401:
                raise Exception(
                    "Sessão inválida ou expirada (HTTP 401). "
                    "Renove o INSTAGRAM_SESSION_ID no Render com um novo cookie do seu navegador."
                )

            if resp.status_code != 200:
                print(f"[JOB {job_id}] Resposta inesperada: {resp.text[:500]}")
                raise Exception(f"Instagram retornou HTTP {resp.status_code}: {resp.text[:300]}")

            try:
                data = resp.json()
            except Exception:
                print(f"[JOB {job_id}] Resposta não-JSON: {resp.text[:800]}")
                raise Exception("Instagram não retornou JSON válido. Verifique o sessionid.")

            # Navegar na estrutura do GraphQL
            edge_media_to_comment = (
                data.get("data", {})
                    .get("shortcode_media", {})
                    .get("edge_media_to_comment", {})
            )
            edges = edge_media_to_comment.get("edges", [])

            for edge in edges:
                node = edge.get("node", {})
                username = node.get("owner", {}).get("username", "desconhecido")
                text = node.get("text", "")
                if text:
                    all_comments.append({"user": username, "text": text})

            page_info = edge_media_to_comment.get("page_info", {})
            has_next = page_info.get("has_next_page", False)
            end_cursor = page_info.get("end_cursor", None)

            print(f"[JOB {job_id}] Total: {len(all_comments)} | has_next: {has_next}")

            if has_next and end_cursor:
                delay = random.uniform(2.0, 4.0)
                print(f"[JOB {job_id}] Próxima página em {delay:.1f}s...")
                time.sleep(delay)
            else:
                break

        jobs[job_id]["comments"] = all_comments
        jobs[job_id]["progress"] = f"✅ {len(all_comments)} comentários extraídos com sucesso!"
        jobs[job_id]["status"] = "completed"
        print(f"[JOB {job_id}] CONCLUÍDO. {len(all_comments)} comentários.")

    except Exception as e:
        import traceback
        print(f"ERRO AO RASPAR COMENTÁRIOS:\n{traceback.format_exc()}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/api/scrape")
async def start_scraping(req: ScrapeRequest, background_tasks: BackgroundTasks):
    print(f"[SCRAPE] url recebida: {req.url!r}")

    if not req.url or not req.url.strip():
        return JSONResponse(
            status_code=400,
            content={"error": "Campo 'url' é obrigatório."}
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
    return {
        "status": "ok",
        "session_configured": bool(INSTAGRAM_SESSION_ID),
    }
