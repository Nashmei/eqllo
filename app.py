import asyncio
import json
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

load_dotenv()

API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
MODEL = os.getenv("NVIDIA_MODEL", "meta/llama-3.3-70b-instruct")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "").strip()
MIN_CONFIDENCE = int(os.getenv("MIN_AI_CONFIDENCE", "65"))
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT_SECONDS", "20"))
DB_PATH = os.getenv("DATABASE_PATH", "signals.db")
DASHBOARD_PATH = Path(__file__).with_name("dashboard.html")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            received_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            setup TEXT NOT NULL,
            price REAL,
            payload TEXT NOT NULL,
            status TEXT NOT NULL,
            decision TEXT,
            confidence INTEGER,
            rationale TEXT,
            risk_flags TEXT,
            model TEXT,
            completed_at TEXT,
            error TEXT
        )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_received ON signals(received_at DESC)")


class SignalPayload(BaseModel):
    signal_id: str | None = None
    symbol: str = Field(default="XAUUSD", max_length=40)
    timeframe: str = Field(default="1", max_length=8)
    setup: str = Field(default="unknown", max_length=80)
    price: float | None = None
    atr: float | None = None
    trend_m5: str = Field(default="unknown", max_length=30)
    demand_zone: list[float] | None = None
    supply_zone: list[float] | None = None
    entry_candidate: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    risk_reward_target: float | None = None
    bar_time: str | None = None
    indicators: dict[str, Any] = Field(default_factory=dict)


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model response did not contain a JSON object")
    result = json.loads(text[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("Model response was not a JSON object")
    return result


async def analyze_with_nvidia(payload: dict[str, Any]) -> dict[str, Any]:
    if not API_KEY:
        raise RuntimeError("NVIDIA_API_KEY is missing. Configure it in the local .env file.")
    system_prompt = (
        "You are a cautious market-setup filter, not an execution system. "
        "Evaluate only the supplied structured snapshot; do not invent market data. "
        "Return JSON only with keys: decision (APPROVE, REJECT, WAIT), confidence (integer 0-100), "
        "rationale (short string), risk_flags (array of short strings). "
        "Reject or WAIT if data is insufficient, setup conflicts with M5 trend, or stop/entry values "
        "are invalid. Confidence is a model score, not a calibrated probability of profit. "
        "Never claim certainty or profitability."
    )
    user_prompt = "Evaluate this candidate setup snapshot:\n" + json.dumps(payload, ensure_ascii=False)
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 300,
    }
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=AI_TIMEOUT) as client:
        response = await client.post(f"{BASE_URL}/chat/completions", headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = parse_json_object(content)

    decision = str(parsed.get("decision", "WAIT")).upper()
    if decision not in {"APPROVE", "REJECT", "WAIT"}:
        decision = "WAIT"
    try:
        confidence = max(0, min(100, int(parsed.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0
    rationale = str(parsed.get("rationale", "No rationale returned"))[:700]
    flags = parsed.get("risk_flags", [])
    if not isinstance(flags, list):
        flags = ["Invalid risk_flags format"]
    flags = [str(x)[:180] for x in flags[:10]]

    # Deterministic safety gates override the model's approval.
    if payload.get("symbol", "").upper() not in {"XAUUSD", "GOLD"}:
        decision = "REJECT"
        flags.append("Symbol is not recognized as XAUUSD/GOLD")
    if payload.get("entry_candidate") is not None and payload.get("stop_loss") is not None:
        entry, stop = float(payload["entry_candidate"]), float(payload["stop_loss"])
        setup = str(payload.get("setup", "")).lower()
        if ("bull" in setup and stop >= entry) or ("bear" in setup and stop <= entry):
            decision = "REJECT"
            flags.append("Stop-loss side conflicts with setup direction")
    if confidence < MIN_CONFIDENCE and decision == "APPROVE":
        decision = "WAIT"
        flags.append(f"Confidence below configured threshold ({MIN_CONFIDENCE})")

    return {
        "decision": decision,
        "confidence": confidence,
        "rationale": rationale,
        "risk_flags": flags,
        "model": MODEL,
    }


async def run_analysis(signal_id: str, payload: dict[str, Any]) -> None:
    try:
        result = await analyze_with_nvidia(payload)
        with db_connect() as conn:
            conn.execute("""
                UPDATE signals SET status='complete', decision=?, confidence=?, rationale=?,
                risk_flags=?, model=?, completed_at=?, error=NULL WHERE id=?
            """, (
                result["decision"], result["confidence"], result["rationale"],
                json.dumps(result["risk_flags"], ensure_ascii=False), result["model"], utc_now(), signal_id
            ))
    except Exception as exc:
        # Keep error details useful but never include headers or API keys.
        message = f"{type(exc).__name__}: {str(exc)[:500]}"
        with db_connect() as conn:
            conn.execute(
                "UPDATE signals SET status='error', error=?, completed_at=? WHERE id=?",
                (message, utc_now(), signal_id)
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="XAUUSD NVIDIA AI Signal Filter", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    if not DASHBOARD_PATH.exists():
        raise HTTPException(500, "dashboard.html is missing")
    return HTMLResponse(DASHBOARD_PATH.read_text(encoding="utf-8"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "nvidia_key_configured": bool(API_KEY),
        "webhook_token_configured": bool(WEBHOOK_TOKEN),
        "model": MODEL,
        "time_utc": utc_now(),
    }


@app.get("/api/signals")
async def get_signals(limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(300, limit))
    with db_connect() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()
    output = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item["payload"])
        except Exception:
            item["payload"] = {}
        try:
            item["risk_flags"] = json.loads(item["risk_flags"] or "[]")
        except Exception:
            item["risk_flags"] = []
        output.append(item)
    return {"signals": output, "count": len(output)}


@app.post("/webhook/{token}")
async def receive_webhook(token: str, request: Request) -> JSONResponse:
    if not WEBHOOK_TOKEN or token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        raw = await request.json()
        payload = SignalPayload.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON payload: {str(exc)[:180]}")

    signal = payload.model_dump()
    if signal["symbol"].upper() not in {"XAUUSD", "GOLD"}:
        raise HTTPException(status_code=422, detail="Only XAUUSD/GOLD payloads are accepted")
    signal_id = str(signal.get("signal_id") or uuid.uuid4())
    signal["signal_id"] = signal_id

    with db_connect() as conn:
        existing = conn.execute("SELECT id FROM signals WHERE id=?", (signal_id,)).fetchone()
        if existing:
            return JSONResponse({"accepted": True, "duplicate": True, "signal_id": signal_id}, status_code=202)
        conn.execute("""
            INSERT INTO signals(id, received_at, symbol, timeframe, setup, price, payload, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued')
        """, (
            signal_id, utc_now(), signal["symbol"], signal["timeframe"], signal["setup"],
            signal["price"], json.dumps(signal, ensure_ascii=False)
        ))

    # Return immediately; AI work is scheduled separately.
    asyncio.create_task(run_analysis(signal_id, signal))
    return JSONResponse({"accepted": True, "signal_id": signal_id, "status": "queued"}, status_code=202)
