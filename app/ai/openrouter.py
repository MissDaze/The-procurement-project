from __future__ import annotations

import json
import re

import httpx

from app.config import settings

SYSTEM = """You are NixSec's Australian government procurement intelligence analyst.
Write for a time-poor business owner, managing director, sales director or bid lead.
Use ONLY facts in the supplied JSON. Never invent or alter IDs, ABNs, dates, values,
agencies, suppliers, tenders, URLs, capabilities, contract history or numeric scores.
Separate source facts from NixSec inference. Never imply that an opportunity is suitable
merely because it is live. State unknown eligibility or capability gaps plainly.
Do not describe an entity as independently verified unless the facts explicitly say so.
Avoid generic sales copy. Every sentence must tell the recipient what was found, why it
matters, what uncertainty remains, or what action should be taken.
Return valid JSON only, with no Markdown fences."""


def _fallback_analysis(facts: dict) -> dict:
    matches = facts.get("opportunities") or []
    watches = facts.get("expiry_watches") or []
    company = facts.get("company_name_as_recorded") or "the recipient"
    coverage = facts.get("historical_coverage") or {}

    if matches:
        top = matches[0]
        decision = "ACT NOW" if int(top.get("score") or 0) >= 60 else "REVIEW"
        executive = (
            f"NixSec found {len(matches)} current source-verified opportunity"
            f"{'ies' if len(matches) != 1 else ''} that passed relevance checks for {company}. "
            f"The strongest current lead is {top.get('title', 'Not Available')}, closing {top.get('closes_at', 'Not Available')}; "
            "confirm the tender eligibility and mandatory requirements before committing bid effort."
        )
    else:
        decision = "MONITOR"
        executive = (
            f"No current live tender passed NixSec's minimum relevance threshold for {company}. "
            "The appropriate action is continued monitoring rather than spending time on weak-fit opportunities."
        )

    findings = []
    if coverage.get("from") or coverage.get("to"):
        findings.append(
            f"Historical contract metrics in this report cover records held from {coverage.get('from') or 'Not Available'} "
            f"to {coverage.get('to') or 'Not Available'}; they are not represented as lifetime totals."
        )
    if matches:
        findings.append(f"{len(matches)} live opportunity records passed the deterministic relevance gate at report time.")
    if watches:
        findings.append(f"{len(watches)} contract-expiry watch item{'s' if len(watches) != 1 else ''} passed the expiry relevance gate; these are not advertised tenders unless separately confirmed.")

    opportunity_insights = {}
    for item in matches:
        source_id = item.get("source_id") or item.get("title") or "unknown"
        evidence = item.get("evidence") or {}
        reasons = []
        if evidence.get("Exact UNSPSC match"):
            reasons.append("exact UNSPSC alignment")
        if evidence.get("Exact category match"):
            reasons.append("exact procurement-category alignment")
        keywords = evidence.get("Matched keywords") or []
        if keywords:
            reasons.append("shared terms: " + ", ".join(keywords[:5]))
        why = "; ".join(reasons) if reasons else "The deterministic score met the reporting threshold, but the available evidence is limited."
        opportunity_insights[source_id] = {
            "why_fit": why,
            "action": "Open the official source, check mandatory eligibility and scope, then make a bid/no-bid decision.",
            "risk": "NixSec has not independently confirmed the recipient's ability to satisfy every participation or delivery requirement.",
        }

    return {
        "decision_signal": decision,
        "executive_summary": executive,
        "key_findings": findings[:4],
        "opportunity_insights": opportunity_insights,
        "monitoring_value": "NixSec can keep the official tender feed, relevant contract expiries and source changes under review so the recipient spends time on stronger-fit opportunities instead of manually scanning broad procurement listings.",
        "caveats": [
            "Opportunity scores are NixSec decision-support scores, not government evaluation scores.",
            "Expiry watches indicate contracts approaching an end date; they do not prove that a new tender will be issued.",
        ],
    }


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return json.loads(cleaned)


def grounded_report_analysis(facts: dict) -> dict:
    fallback = _fallback_analysis(facts)
    if not settings.ai_enabled or not settings.openrouter_api_key:
        return fallback

    task = """Create a concise decision-maker analysis using this exact JSON schema:
{
  "decision_signal": "ACT NOW|REVIEW|MONITOR",
  "executive_summary": "2-4 sentences",
  "key_findings": ["maximum 4 short findings"],
  "opportunity_insights": {
    "<source_id>": {
      "why_fit": "specific evidence-based reason, or say evidence is insufficient",
      "action": "specific next action",
      "risk": "specific gap, uncertainty or eligibility issue"
    }
  },
  "monitoring_value": "1-2 sentences explaining the practical value of continued monitoring for this recipient",
  "caveats": ["maximum 4 material caveats"]
}
Do not repeat the report title. Do not write generic marketing slogans. A weak or uncertain match must be described as weak or uncertain."""

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": task + "\nFACTS:\n" + json.dumps(facts, default=str)},
        ],
        "temperature": 0.1,
    }
    try:
        response = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": settings.nixsec_website,
                "X-Title": "NixSec Procurement Intelligence",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        parsed = _extract_json(response.json()["choices"][0]["message"]["content"])
        if not isinstance(parsed, dict):
            return fallback
        for key in ("decision_signal", "executive_summary", "key_findings", "opportunity_insights", "monitoring_value", "caveats"):
            if key not in parsed:
                return fallback
        if parsed.get("decision_signal") not in {"ACT NOW", "REVIEW", "MONITOR"}:
            parsed["decision_signal"] = fallback["decision_signal"]
        return parsed
    except Exception:
        return fallback


def grounded_text(task: str, facts: dict, fallback: str) -> str:
    """Backward-compatible helper for any older call sites."""
    if not settings.ai_enabled or not settings.openrouter_api_key:
        return fallback
    payload={"model":settings.openrouter_model,"messages":[{"role":"system","content":SYSTEM},{"role":"user","content":task+"\nFACTS:\n"+json.dumps(facts,default=str)}],"temperature":0.1}
    try:
        r=httpx.post("https://openrouter.ai/api/v1/chat/completions",headers={"Authorization":f"Bearer {settings.openrouter_api_key}","Content-Type":"application/json","HTTP-Referer":settings.nixsec_website,"X-Title":"NixSec Procurement Intelligence"},json=payload,timeout=45)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return fallback
