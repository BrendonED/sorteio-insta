"use client";

import { useState, useRef, useEffect } from "react";

interface Comment {
  user: string;
  text: string;
}

export default function Home() {
  const [url, setUrl] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "completed" | "error">("idle");
  const [comments, setComments] = useState<Comment[]>([]);
  const [error, setError] = useState("");
  const [winner, setWinner] = useState<Comment | null>(null);
  const [isDrawing, setIsDrawing] = useState(false);

  // Garantir que a URL base não termine com barra e comece com http
  let rawUrl = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
  if (!rawUrl.startsWith("http")) {
    rawUrl = "https://" + rawUrl;
  }
  const BACKEND_URL = rawUrl.replace(/\/$/, "");

  const handleLoadComments = async () => {
    if (!url) {
      setError("Por favor, insira o link do post.");
      return;
    }

    setError("");
    setWinner(null);
    setComments([]);
    setIsLoading(true);
    setStatus("running");
    setProgress("Iniciando conexão...");

    try {
      const res = await fetch(`${BACKEND_URL}/api/scrape`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });

      if (!res.ok) {
        throw new Error("Falha ao iniciar o processo no servidor.");
      }

      const data = await res.json();
      const jobId = data.job_id;

      // Iniciar SSE
      const eventSource = new EventSource(`${BACKEND_URL}/api/stream/${jobId}`);

      eventSource.onmessage = (event) => {
        const payload = JSON.parse(event.data);
        setStatus(payload.status);
        setProgress(payload.progress);

        if (payload.status === "error") {
          setError(payload.error || "Ocorreu um erro desconhecido.");
          eventSource.close();
          setIsLoading(false);
        } else if (payload.status === "completed") {
          setComments(payload.comments || []);
          eventSource.close();
          setIsLoading(false);
        }
      };

      eventSource.onerror = () => {
        setError("Perda de conexão com o servidor.");
        eventSource.close();
        setIsLoading(false);
        setStatus("error");
      };
    } catch (err: any) {
      setError(err.message);
      setIsLoading(false);
      setStatus("error");
    }
  };

  const handleDrawWinner = () => {
    if (comments.length === 0) return;

    setIsDrawing(true);
    setWinner(null);

    // Efeito de suspense
    let count = 0;
    const interval = setInterval(() => {
      const randomIndex = Math.floor(Math.random() * comments.length);
      setWinner(comments[randomIndex]);
      count++;
      
      if (count > 20) {
        clearInterval(interval);
        setIsDrawing(false);
        // Sorteio final
        const finalIndex = Math.floor(Math.random() * comments.length);
        setWinner(comments[finalIndex]);
      }
    }, 100);
  };

  // Calcular porcentagem para a barra
  let percent = 0;
  if (progress) {
    const parts = progress.split("/");
    if (parts.length === 2 && !isNaN(Number(parts[0])) && !isNaN(Number(parts[1]))) {
      const current = Number(parts[0]);
      const total = Number(parts[1]);
      if (total > 0) {
        percent = Math.min((current / total) * 100, 100);
      }
    } else if (status === "completed") {
      percent = 100;
    }
  }

  return (
    <main className="container">
      <div className="header">
        <h1>Sorteio Premium</h1>
        <p>Insira o link do post do Instagram e realize o sorteio de forma justa.</p>
      </div>

      <div className="card">
        <div className="input-group">
          <label htmlFor="url">Link da Publicação</label>
          <input
            id="url"
            type="url"
            className="input-field"
            placeholder="https://www.instagram.com/p/..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={isLoading}
          />
        </div>

        {status === "idle" || status === "error" ? (
          <button 
            className="btn" 
            onClick={handleLoadComments}
            disabled={isLoading || !url}
          >
            {isLoading ? <div className="spinner"></div> : "Carregar Comentários"}
          </button>
        ) : null}

        {error && <div className="error-message">{error}</div>}

        {(status === "running" || status === "completed") && (
          <div className="progress-container">
            <div className="progress-header">
              <span>Progresso</span>
              <span>{progress}</span>
            </div>
            <div className="progress-bar-bg">
              <div 
                className="progress-bar-fill" 
                style={{ width: `${percent}%` }}
              ></div>
            </div>
            <div className="progress-status">
              {status === "running" ? "Processando comentários..." : "Extração concluída com sucesso!"}
            </div>
          </div>
        )}

        {status === "completed" && (
          <div style={{ marginTop: '2rem' }}>
            <button 
              className="btn" 
              onClick={handleDrawWinner}
              disabled={isDrawing || comments.length === 0}
            >
              {isDrawing ? "Sorteando..." : "Realizar Sorteio"}
            </button>
          </div>
        )}

        {winner && (
          <div className="winner-card">
            <div className="winner-title">🎉 Ganhador do Sorteio 🎉</div>
            <div className="winner-username">@{winner.user}</div>
            <div className="winner-comment">"{winner.text}"</div>
          </div>
        )}
      </div>
    </main>
  );
}
