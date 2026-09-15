from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

import httpx

from app.config import settings

UNKNOWN_PROCESS = "Not established from currently collected source material — review the official ATM documents for exact requirements."

@dataclass
class TenderRecommendation:
    tender_source_id: str
    tender_title: str
    recommendation: Literal["APPLY", "REVIEW", "DO NOT PURSUE"]
    suitability_rationale: str
    evidence_from_history: str
    capability_alignment: str
    material_gaps_risks: str
    why_apply: str
    application_steps: str
    required_documentation: str
    confidence: int

@dataclass
class CompanyProfile:
    company_name: str
    abn: str | None
    what_company_demonstrably_does: str
    key_capability_themes: list[str]
    procurement_categories: list[str]
    historical_evidence: dict
    inference_notes: str

SYSTEM_PROMPT = """You are NixSec's Australian government procurement intelligence analyst.
Your job is to decide which current tenders a specific company should actually consider applying for.
Reason from the company's demonstrated procurement history, contract descriptions, categories and capabilities, then compare that evidence with each tender's actual scope.
Deterministic scores may appear as supporting metadata but MUST NOT determine your recommendation.
Use only supplied facts. Never invent supplier identity, ABNs, contract history, capability, eligibility, tender requirements, values, deadlines, application steps, documents, contacts or URLs.
If application steps or required documents are not present in collected source material, say exactly: 'Not established from currently collected source material — review the official ATM documents for exact requirements.'
Separate evidence from inference. Weak evidence means REVIEW or DO NOT PURSUE, not APPLY.
Return valid JSON only, no markdown fences."""

def _extract_json(text: str):
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)

def build_company_profile(name: str, abn: str | None, capabilities: list[str], categories: list[str], contracts: list[dict]) -> CompanyProfile:
    agencies = sorted({c.get("agency") for c in contracts if c.get("agency")})
    titles = [c.get("title") for c in contracts if c.get("title")][:12]
    demonstrated = []
    if categories:
        demonstrated.append("procurement work in " + ", ".join(categories[:3]))
    if titles:
        demonstrated.append("recorded contract work including " + "; ".join(titles[:5]))
    if agencies:
        demonstrated.append("delivery experience with " + ", ".join(agencies[:5]))
    return CompanyProfile(
        company_name=name,
        abn=abn,
        what_company_demonstrably_does="; ".join(demonstrated) if demonstrated else "Only limited procurement evidence is currently available.",
        key_capability_themes=capabilities[:8],
        procurement_categories=categories[:8],
        historical_evidence={"contract_count": len(contracts), "contracts": contracts[:20], "agencies": agencies},
        inference_notes="Capability themes are inferred from collected procurement records and are not independently verified unless stated elsewhere.",
    )

def _fallback(profile: CompanyProfile, tenders: list[dict]) -> list[TenderRecommendation]:
    return [TenderRecommendation(
        tender_source_id=t.get("source_id") or "unknown",
        tender_title=t.get("title") or "Unknown tender",
        recommendation="REVIEW",
        suitability_rationale="AI recommendation analysis is unavailable, so NixSec is not representing this tender as an APPLY recommendation.",
        evidence_from_history="Review the company's recorded contract history against this tender before deciding to bid.",
        capability_alignment="Not assessed by AI in this fallback result.",
        material_gaps_risks="Mandatory requirements and delivery fit have not been established.",
        why_apply="No APPLY recommendation is made while AI analysis is unavailable.",
        application_steps=UNKNOWN_PROCESS,
        required_documentation=UNKNOWN_PROCESS,
        confidence=20,
    ) for t in tenders]

def analyse_tenders_for_company(profile: CompanyProfile, tenders: list[dict]) -> list[TenderRecommendation]:
    if not settings.ai_enabled or not settings.openrouter_api_key:
        return _fallback(profile, tenders)
    task = """Analyse every tender against this company's demonstrated capability and history. Return a JSON array, one object per tender, using exactly these fields:
tender_source_id, tender_title, recommendation (APPLY|REVIEW|DO NOT PURSUE), suitability_rationale, evidence_from_history, capability_alignment, material_gaps_risks, why_apply, application_steps, required_documentation, confidence (0-100).
APPLY means there is defensible evidence the company should seriously pursue the tender. REVIEW means potentially relevant but a material fact must be checked. DO NOT PURSUE means poor fit or insufficient evidence.
For application_steps and required_documentation, only state requirements actually present in source material; otherwise use the required 'Not established...' wording."""
    facts = {
        "company": {
            "name": profile.company_name, "abn": profile.abn,
            "demonstrated_work": profile.what_company_demonstrably_does,
            "capability_themes": profile.key_capability_themes,
            "categories": profile.procurement_categories,
            "historical_evidence": profile.historical_evidence,
            "inference_notes": profile.inference_notes,
        },
        "live_tenders": tenders,
    }
    payload={"model":settings.openrouter_model,"messages":[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":task+"\nFACTS:\n"+json.dumps(facts,default=str)}],"temperature":0.2}
    try:
        r=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings.openrouter_api_key}","Content-Type":"application/json","HTTP-Referer":settings.nixsec_website,"X-Title":"NixSec Procurement Intelligence"},json=payload,timeout=90)
        r.raise_for_status()
        parsed=_extract_json(r.json()["choices"][0]["message"]["content"])
        if not isinstance(parsed,list): return _fallback(profile,tenders)
        valid_ids={t.get("source_id") for t in tenders}
        out=[]
        for item in parsed:
            if not isinstance(item,dict) or item.get("tender_source_id") not in valid_ids: continue
            rec=str(item.get("recommendation","REVIEW")).upper()
            if rec not in {"APPLY","REVIEW","DO NOT PURSUE"}: rec="REVIEW"
            out.append(TenderRecommendation(
                tender_source_id=item["tender_source_id"], tender_title=item.get("tender_title") or "",
                recommendation=rec,
                suitability_rationale=item.get("suitability_rationale") or "Evidence insufficient.",
                evidence_from_history=item.get("evidence_from_history") or "No specific historical evidence identified.",
                capability_alignment=item.get("capability_alignment") or "Not established.",
                material_gaps_risks=item.get("material_gaps_risks") or "Eligibility and delivery requirements require confirmation.",
                why_apply=item.get("why_apply") or ("Proceed to bid/no-bid review." if rec!="DO NOT PURSUE" else "Do not prioritise this tender."),
                application_steps=item.get("application_steps") or UNKNOWN_PROCESS,
                required_documentation=item.get("required_documentation") or UNKNOWN_PROCESS,
                confidence=max(0,min(100,int(item.get("confidence",50)))),
            ))
        return out if out else _fallback(profile,tenders)
    except Exception:
        return _fallback(profile,tenders)
