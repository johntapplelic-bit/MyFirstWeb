import fitz
import requests
import sqlite3
import json
import time
from difflib import SequenceMatcher
from pathlib import Path

# ============================================================
# 1. PDF TEXT EXTRACTION (PyMuPDF)
# ============================================================


def extract_pdf_text(pdf_path):
    doc = fitz.open(pdf_path)
    text = ""

    try:
        for page in doc:
            text += page.get_text("text") + "\n"
    finally:
        doc.close()

    return text


def extract_input_text(input_path):
    input_file = Path(input_path)
    suffix = input_file.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(str(input_file))

    if suffix in {".txt", ".md"}:
        return input_file.read_text(encoding="utf-8", errors="ignore")

    raise ValueError(f"Unsupported input format: {input_file.suffix}")


# ============================================================
# 2. TEXT CHUNKING
# ============================================================


def chunk_text(text, min_length=80):
    raw_chunks = text.split(".")
    chunks = [c.strip() for c in raw_chunks if len(c.strip()) >= min_length]
    return chunks


# ============================================================
# 3. ACADEMIC SEARCH ENGINES (NO API KEYS REQUIRED)
# ============================================================

# --- CrossRef Search ---
def search_crossref(query):
    url = "https://api.crossref.org/works"
    params = {"query": query, "rows": 5}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("message", {}).get("items", [])
        return data
    except Exception:
        return []


# --- OpenLibrary Search ---
def search_openlibrary(query):
    url = "https://openlibrary.org/search.json"
    params = {"q": query}
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json().get("docs", [])[:5]
        return data
    except Exception:
        return []


# --- Semantic Scholar Search ---
def search_semantic_scholar(query):
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {
        "query": query,
        "limit": 5,
        "fields": "title,abstract,authors,year,url",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception:
        return []


# ============================================================
# 4. SIMILARITY SCORING
# ============================================================


def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ============================================================
# 5. SQLITE DATABASE
# ============================================================


def init_db(db_path="verification_results.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk TEXT,
            source TEXT,
            title TEXT,
            year TEXT,
            url TEXT,
            similarity REAL,
            timestamp TEXT
        )
    """
    )
    conn.commit()
    conn.close()


def save_match(chunk, source, title, year, url, similarity, db_path="verification_results.db"):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO matches (chunk, source, title, year, url, similarity, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """,
        (chunk, source, title, year, url, similarity),
    )
    conn.commit()
    conn.close()


# ============================================================
# 6. DEEP VERIFICATION PIPELINE
# ============================================================


def _extract_crossref_year(item):
    for key in ("published-print", "published-online", "issued"):
        date_parts = item.get(key, {}).get("date-parts", [])
        if date_parts and date_parts[0]:
            return date_parts[0][0]
    return None


def verify_chunks(chunks, db_path="verification_results.db", threshold=0.25, delay_seconds=1.0):
    results = []

    for chunk in chunks:
        print(f"Searching for: {chunk[:60]}...")

        # CrossRef
        for item in search_crossref(chunk):
            title = (item.get("title") or [""])[0]
            abstract = item.get("abstract", "") or ""
            score = similarity(chunk, title + " " + abstract)

            if score > threshold:
                result = {
                    "chunk": chunk,
                    "source": "CrossRef",
                    "title": title,
                    "year": _extract_crossref_year(item),
                    "url": item.get("URL"),
                    "similarity": round(score, 3),
                }
                results.append(result)
                save_match(**result, db_path=db_path)

        # OpenLibrary
        for item in search_openlibrary(chunk):
            title = item.get("title", "")
            score = similarity(chunk, title)

            if score > threshold:
                result = {
                    "chunk": chunk,
                    "source": "OpenLibrary",
                    "title": title,
                    "year": item.get("first_publish_year"),
                    "url": f"https://openlibrary.org{item.get('key', '')}",
                    "similarity": round(score, 3),
                }
                results.append(result)
                save_match(**result, db_path=db_path)

        # Semantic Scholar
        for item in search_semantic_scholar(chunk):
            title = item.get("title", "")
            abstract = item.get("abstract", "") or ""
            score = similarity(chunk, title + " " + abstract)

            if score > threshold:
                result = {
                    "chunk": chunk,
                    "source": "SemanticScholar",
                    "title": title,
                    "year": item.get("year"),
                    "url": item.get("url"),
                    "similarity": round(score, 3),
                }
                results.append(result)
                save_match(**result, db_path=db_path)

        time.sleep(delay_seconds)  # polite rate limiting

    return results


# ============================================================
# 7. HEATMAP + REPORT GENERATION
# ============================================================


def generate_report(results, output="verification_report.json"):
    with open(output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=4)

    print(f"\nReport saved to {output}")


def generate_heatmap(results):
    print("\n=== CONFIDENCE HEATMAP ===")
    for r in results:
        bars = int(r["similarity"] * 20)
        print(f"{r['similarity']:.2f} | " + ("#" * bars))


# ============================================================
# 8. MAIN PIPELINE
# ============================================================


def run_pipeline(input_path, db_path="verification_results.db", report_path="verification_report.json"):
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Input file not found: {input_file}")
        return []

    init_db(db_path=db_path)

    print("\nExtracting input text...")
    text = extract_input_text(str(input_file))

    print("Chunking text...")
    chunks = chunk_text(text)

    print(f"Total chunks: {len(chunks)}")

    print("\nRunning deep academic verification...")
    results = verify_chunks(chunks, db_path=db_path)

    generate_report(results, output=report_path)
    generate_heatmap(results)

    print("\nDone.")
    return results


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    default_input = "input.pdf" if Path("input.pdf").exists() else "input.txt"
    run_pipeline(default_input)
