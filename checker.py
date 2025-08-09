# checker.py
import re
from typing import List, Dict, Any, Tuple
from rag import SimpleRAGIndex
import os
import openai
import json
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

AMBIGUOUS_TERMS = [
    r"\bmay\b",
    r"\bpossible\b",
    r"\bsubject to\b",
    r"\bas appropriate\b",
    r"\bwhere practicable\b"
]

JURISDICTION_PATTERNS = [
    r"ADGM",
    r"Abu Dhabi Global Market",
    r"ADGM Courts",
    r"\bAbu Dhabi\b"
]

FEDERAL_COURTS_PATTERNS = [
    r"UAE Federal Courts",
    r"Federal Courts of the UAE",
    r"\bUAE Courts\b"
]

SIGNATURE_PATTERNS = [
    r"Signature:",
    r"Signed by",
    r"Signatory",
    r"Signature\s+of"
]

def heuristic_checks(paragraphs: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
    """
    paragraphs: list of (index, text)
    """
    issues = []
    # check for jurisdiction mismatch
    for idx, text in paragraphs:
        if re.search("|".join(FEDERAL_COURTS_PATTERNS), text, re.IGNORECASE):
            issues.append({
                "document_index_paragraph": idx,
                "issue": "References UAE Federal Courts instead of ADGM",
                "section": f"Paragraph {idx}",
                "severity": "High",
                "suggestion": "Replace references to UAE Federal Courts with ADGM Courts (per ADGM Companies Regulations)."
            })
        # ambiguous language
        for pattern in AMBIGUOUS_TERMS:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append({
                    "document_index_paragraph": idx,
                    "issue": f"Ambiguous language: contains '{pattern.strip('\\b')}'",
                    "section": f"Paragraph {idx}",
                    "severity": "Medium",
                    "suggestion": "Consider clarifying to explicit obligation or remove discretionary terms."
                })
        # missing signature detection (if a doc has no mention of signature sections)
    # signature checks at document-level (handled elsewhere)
    return issues

def document_level_checks(paragraphs: List[Tuple[int, str]]) -> List[Dict[str, Any]]:
    issues = []
    combined_text = "\n".join([t for _, t in paragraphs])
    if not re.search("|".join(SIGNATURE_PATTERNS), combined_text, re.IGNORECASE):
        issues.append({
            "document_index_paragraph": None,
            "issue": "No signatory or signature block detected",
            "section": "End of document",
            "severity": "High",
            "suggestion": "Add a clearly labelled signature block for authorized signatories with name, title and date."
        })
    # Jurisdiction absence: flag if no ADGM reference
    if not re.search("|".join(JURISDICTION_PATTERNS), combined_text, re.IGNORECASE):
        issues.append({
            "document_index_paragraph": None,
            "issue": "Jurisdiction not specified as ADGM",
            "section": "Governing Law / Jurisdiction clause",
            "severity": "High",
            "suggestion": "Set governing law and jurisdiction to ADGM and ADGM Courts."
        })
    return issues

def call_llm(prompt: str, model="gpt-4o-mini", max_tokens=512) -> str:
    """
    Simple wrapper for OpenAI. If no key, raises.
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set; LLM call unavailable.")
    openai.api_key = OPENAI_API_KEY
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=0.0
    )
    # extract content
    return resp["choices"][0]["message"]["content"].strip()

def llm_review(paragraphs: List[Tuple[int, str]], rag_index: SimpleRAGIndex, doc_name="Document") -> List[Dict[str, Any]]:
    """
    For each suspicious paragraph we send a small prompt with RAG context and ask for compliance checks.
    """
    issues = []
    for idx, text in paragraphs:
        # heuristic quick filter: only send paragraphs containing ambiguous/jurisdiction/sig patterns
        if re.search("|".join(AMBIGUOUS_TERMS + FEDERAL_COURTS_PATTERNS + SIGNATURE_PATTERNS), text, re.IGNORECASE):
            retrieved = rag_index.retrieve(text, k=3)
            context = "\n\n".join([r[0] for r in retrieved])
            prompt = f"""You are a legal assistant specialized in Abu Dhabi Global Market (ADGM) company regulations.
Review the following paragraph from a {doc_name}:
---PARAGRAPH---
{text}
---END PARAGRAPH---

Here are relevant ADGM references retrieved:
{context}

Please:
1) Identify whether this paragraph violates ADGM practice or contains red flags (jurisdiction, missing clause, ambiguous language).
2) If an issue found, produce a short suggestion and cite the reference using the format [source: filename]. 
3) Return JSON list of objects with keys: paragraph_index, issue, severity, suggestion.
"""
            try:
                out = call_llm(prompt)
                # Attempt to parse as JSON; else store as raw
                import json
                try:
                    parsed = json.loads(out)
                    for p in parsed:
                        p["document_paragraph_idx"] = idx
                    issues.extend(parsed)
                except Exception:
                    issues.append({
                        "document_paragraph_idx": idx,
                        "issue": "LLM review returned non-JSON output",
                        "severity": "Low",
                        "suggestion": out[:400]
                    })
            except Exception as e:
                issues.append({
                    "document_paragraph_idx": idx,
                    "issue": "LLM call failed",
                    "severity": "Low",
                    "suggestion": str(e)
                })
    return issues
