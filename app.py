# app.py
import os
import uuid
import json
from typing import List, Tuple, Dict, Any
import gradio as gr
from docx_utils import extract_structured_text, insert_comment_simulation, sanitize_filename
from rag import SimpleRAGIndex
from checker import heuristic_checks, document_level_checks, llm_review
import shutil

# Load checklist
import json as _json
with open("checklist.json","r",encoding="utf-8") as f:
    CHECKLISTS = _json.load(f)

# configure reference folder for RAG
REF_FOLDER = "adgm_refs"
rag_index = None
if os.path.exists(REF_FOLDER):
    rag_index = SimpleRAGIndex()
    rag_index.build_from_folder(REF_FOLDER)
else:
    rag_index = None
    print("Warning: adgm_refs folder not found. RAG disabled until you add reference .txt files to adgm_refs/")

UPLOAD_DIR = "uploads"
OUT_DIR = "outputs"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

DOC_TYPE_KEYWORDS = {
    "Articles of Association": ["articles of association", "articles", "aoa"],
    "Memorandum of Association": ["memorandum of association", "memorandum", "moa", "mou"],
    "Incorporation Application Form": ["incorporation application", "incorporation form"],
    "UBO Declaration Form": ["ubo declaration", "ubo form"],
    "Register of Members and Directors": ["register of members", "register of directors", "register of members and directors"]
}

def detect_document_type(text: str) -> str:
    txt = text.lower()
    for doc_type, kws in DOC_TYPE_KEYWORDS.items():
        for kw in kws:
            if kw in txt:
                return doc_type
    # fallback simple guesses
    if "article" in txt and "association" in txt:
        return "Articles of Association"
    if "memorandum" in txt:
        return "Memorandum of Association"
    return "Unknown Document Type"

def process_uploaded_files(files):
    """
    files: list of (tempfile path, filename)
    Returns paths to saved uploaded files
    """
    saved = []
    for f in files:
        tmp_path = f.name if hasattr(f, "name") else f[0]
        original_name = getattr(f, "filename", None) or (f[1] if isinstance(f, tuple) else os.path.basename(tmp_path))
        base = sanitize_filename(original_name)
        dest = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}_{base}")
        shutil.copy(tmp_path, dest)
        saved.append(dest)
    return saved

def infer_process_and_checklist(doc_types: List[str]) -> Dict[str, Any]:
    """
    Determine which ADGM process the user is attempting based on uploaded doc types.
    Very simple logic: if many incorporation docs, assume Company Incorporation.
    """
    matches = 0
    for t in doc_types:
        if t in CHECKLISTS.get("Company Incorporation", []):
            matches += 1
    # if at least two match, assume incorporation
    if matches >= 2:
        required = CHECKLISTS["Company Incorporation"]
        missing = [d for d in required if d not in doc_types]
        return {
            "process": "Company Incorporation",
            "documents_uploaded": len(doc_types),
            "required_documents": len(required),
            "missing_documents": missing
        }
    else:
        return {
            "process": "Unknown",
            "documents_uploaded": len(doc_types),
            "required_documents": None,
            "missing_documents": []
        }

def analyze_documents(saved_paths: List[str], use_llm: bool=False):
    summary = []
    doc_types = []
    all_annotations = {}  # filepath -> annotations
    all_issues = []
    for path in saved_paths:
        paras = extract_structured_text(path)  # list of (idx, text)
        combined_text = "\n".join([t for _, t in paras])
        doc_type = detect_document_type(combined_text)
        doc_types.append(doc_type)
        issues = heuristic_checks(paras)
        issues.extend(document_level_checks(paras))
        # optional LLM review if rag_index available & use_llm True
        if use_llm and rag_index is not None:
            try:
                issues_from_llm = llm_review(paras, rag_index, doc_name=doc_type)
                issues.extend(issues_from_llm)
            except Exception as e:
                issues.append({
                    "document": os.path.basename(path),
                    "issue": "LLM review failed",
                    "severity": "Low",
                    "suggestion": str(e)
                })
        # prepare annotations for docx injection: use paragraph indices found in heuristic issues
        annotations = []
        for it in issues:
            para_idx = it.get("document_index_paragraph") if it.get("document_index_paragraph") is not None else (it.get("document_paragraph_idx") if it.get("document_paragraph_idx") is not None else None)
            if para_idx is None:
                # attach to end of document
                para_idx = len(paras)-1 if paras else 0
            annotations.append({
                "paragraph_index": para_idx,
                "match_text": None,
                "comment": f"{it.get('issue')}: {it.get('suggestion')}"
            })
        all_annotations[path] = annotations
        all_issues.extend([{
            "document": os.path.basename(path),
            "doc_type": doc_type,
            "section": it.get("section"),
            "issue": it.get("issue"),
            "severity": it.get("severity"),
            "suggestion": it.get("suggestion")
        } for it in issues])
        summary.append({
            "file": os.path.basename(path),
            "type": doc_type,
            "issues_found": len(issues)
        })

    # infer process
    proc = infer_process_and_checklist(doc_types)
    # create reviewed docx files
    reviewed_files = []
    for path, annotations in all_annotations.items():
        outname = os.path.join(OUT_DIR, f"reviewed_{os.path.basename(path)}")
        insert_comment_simulation(path, outname, annotations)
        reviewed_files.append(outname)

    result = {
        "process": proc.get("process"),
        "documents_uploaded": proc.get("documents_uploaded"),
        "required_documents": proc.get("required_documents"),
        "missing_documents": proc.get("missing_documents"),
        "summary": summary,
        "issues": all_issues,
        "reviewed_files": reviewed_files
    }
    return result

def handle_upload(*files, use_llm=False):
    # files is a tuple from gradio (list of file objects)
    # Save files
    saved = process_uploaded_files(files)
    result = analyze_documents(saved, use_llm=use_llm)
    # Save JSON
    json_path = os.path.join(OUT_DIR, f"analysis_{uuid.uuid4().hex}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    # return paths for download (reviewed zip maybe)
    return result

# Build Gradio app
with gr.Blocks() as demo:
    gr.Markdown("# ADGM Corporate Agent — Document Reviewer (MVP)")
    with gr.Row():
        with gr.Column():
            uploaded = gr.File(label="Upload one or more .docx files", file_count="multiple", file_types=[".docx"])
            use_llm = gr.Checkbox(label="Enable LLM-backed review (requires OPENAI_API_KEY set on server)", value=False)
            run_btn = gr.Button("Analyze & Review")
            output_json = gr.JSON()
            reviewed_files_out = gr.File(label="Download reviewed .docx (one at a time)")
        with gr.Column():
            gr.Markdown("**Instructions**: Upload `.docx` files relevant to your ADGM process (e.g., Articles of Association, Incorporation Form). Optionally add ADGM reference `.txt` files to `adgm_refs/` and enable LLM review.")
    def run_process(files, use_llm_flag):
        if not files:
            return {"error":"No files uploaded."}
        try:
            res = handle_upload(*files, use_llm=use_llm_flag)
            # For simplicity return first reviewed file as downloadable
            first_reviewed = res["reviewed_files"][0] if res["reviewed_files"] else None
            out = {"result": res, "first_reviewed": first_reviewed}
            return out
        except Exception as e:
            return {"error": str(e)}

    run_btn.click(fn=run_process, inputs=[uploaded, use_llm], outputs=[output_json])

if __name__ == "__main__":
    demo.launch(share=False, server_name="0.0.0.0", server_port=7860)
