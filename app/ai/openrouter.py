from __future__ import annotations

import json
import httpx
from app.config import settings

SYSTEM="""You are NixSec's procurement writing assistant. Use only facts in the supplied JSON. Never create or alter IDs, ABNs, dates, values, agencies, suppliers, tenders, URLs, or numeric scores. If evidence is absent say Not Available. Return concise prose labelled AI INTERPRETATION."""

def grounded_text(task:str,facts:dict,fallback:str) -> str:
    if not settings.ai_enabled or not settings.openrouter_api_key: return fallback
    payload={"model":settings.openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":task+"\nVERIFIED FACTS:\n"+json.dumps(facts,default=str)}],"temperature":0.1}
    try:
        r=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings.openrouter_api_key}","Content-Type":"application/json","HTTP-Referer":settings.nixsec_website,"X-Title":"NixSec Procurement Intelligence"},json=payload,timeout=45); r.raise_for_status()
        return "AI INTERPRETATION — "+r.json()["choices"][0]["message"]["content"].strip()
    except Exception: return fallback

