import requests
import pandas as pd
import os
import time
import json
import re
import base64
from datetime import datetime, timedelta

# ==========================================
#             CONFIGURATION
# ==========================================

# KEYWORDS: Using specific phrases prevents "16GB" junk
KEYWORDS = [
    "6G network", 
    "6G wireless", 
    "6G communication", 
    "6G system", 
    "Sixth Generation", 
    "Sub-THz", 
    "O-RAN"
]

CSV_FILE = "6g_patents_usa.csv"

# --- API KEYS (NOW LOADED SECURELY) ---
PATENTSVIEW_API_KEY = os.environ.get("USPTO_API_KEY")
GITHUB_TOKEN = os.environ.get("PAT_TOKEN")
GITHUB_REPO = "stop6g/6G-PATENTS"          

# Endpoint (V1)
USPTO_ENDPOINT = "https://search.patentsview.org/api/v1/patent/"

# ==========================================
#            STRICT FILTER LOGIC
# ==========================================

def is_valid_6g(text):
    """Final safety check. Matches "6G" only as a whole word."""
    if not text: return False
    
    # 1. Allow technical terms immediately
    safe_terms = ["sub-thz", "jcas", "ris", "o-ran", "sixth generation"]
    text_lower = text.lower()
    if any(term in text_lower for term in safe_terms):
        return True
        
    # 2. Strict Regex for "6G" (Avoids 16GB, 6GHz)
    regex_pattern = r'(?i)(?:^|[^a-zA-Z0-9])6g(?:$|[^a-zA-Z0-9])'
    if re.search(regex_pattern, text):
        return True
        
    return False

# ==========================================
#            INCREMENTAL LOGIC
# ==========================================

def get_start_date(filename):
    if not os.path.exists(filename):
        print("📂 No existing file. Starting full historical search from 2020-01-01.")
        return "2020-01-01"
    
    try:
        df = pd.read_csv(filename)
        df['date'] = pd.to_datetime(df['date'])
        last_date = df['date'].max()
        
        if pd.isna(last_date): return "2020-01-01"
            
        # Go back 7 days just to be safe
        resume_date = last_date - timedelta(days=7)
        date_str = resume_date.strftime('%Y-%m-%d')
        print(f"📂 Found existing data. Resuming search from: {date_str}")
        return date_str
    except:
        return "2020-01-01"

def load_existing_ids(filename):
    if not os.path.exists(filename): return set()
    try:
        df = pd.read_csv(filename, usecols=['patent_id'], dtype={'patent_id': str})
        return set(df['patent_id'].tolist())
    except: return set()

# ==========================================
#            DATA FETCHING
# ==========================================

def fetch_uspto_data(keywords, existing_ids, start_date):
    if not PATENTSVIEW_API_KEY:
        print("❌ ERROR: USPTO_API_KEY is not set in the environment.")
        return []

    print(f"--- Starting USPTO Scrape (Since {start_date}) ---")
    
    headers = {
        "X-Api-Key": PATENTSVIEW_API_KEY,
        "Accept": "application/json"
    }

    or_clauses = []
    for kw in keywords:
        or_clauses.append({"_text_phrase": {"patent_abstract": kw}})
        or_clauses.append({"_text_phrase": {"patent_title": kw}})

    query = {
        "_and": [
            {"_or": or_clauses},
            {"_gte": {"patent_date": start_date}}
        ]
    }

    fields = ["patent_id", "patent_title", "patent_abstract", "patent_date", "assignees"]
    
    page = 1
    per_page = 500
    all_new_data = []
    
    while True:
        params = {
            "q": json.dumps(query),
            "f": json.dumps(fields),
            "o": json.dumps({"page": page, "size": per_page, "sort": [{"patent_date": "desc"}]})
        }
        
        try:
            print(f"Fetching page {page}...", end="\r")
            response = requests.get(USPTO_ENDPOINT, headers=headers, params=params, timeout=30)
            
            if response.status_code != 200:
                print(f"\n❌ API Error {response.status_code}: {response.text}")
                break
                
            data = response.json()
            patents = data.get("patents", [])
            
            if not patents:
                print("\n✅ No more results found.")
                break
            
            batch_data = []
            for p in patents:
                p_id = str(p.get('patent_id'))
                
                if p_id in existing_ids: continue
                
                full_text = f"{p.get('patent_title', '')} {p.get('patent_abstract', '')}"
                if not is_valid_6g(full_text):
                    continue

                assignee_name = "Unknown"
                if p.get('assignees'):
                    first = p['assignees'][0]
                    assignee_name = first.get('assignee_organization') or \
                                    f"{first.get('name_first', '')} {first.get('name_last', '')}"

                row = {
                    "patent_id": p_id,
                    "source": "USPTO",
                    "title": p.get('patent_title'),
                    "date": p.get('patent_date'),
                    "assignee": assignee_name,
                    "abstract": p.get('patent_abstract', "")[:1500].replace('\n', ' '),
                    "scraped_at": datetime.now().strftime("%Y-%m-%d")
                }
                batch_data.append(row)
                existing_ids.add(p_id)
            
            all_new_data.extend(batch_data)
            
            if len(batch_data) > 0:
                print(f"Page {page}: +{len(batch_data)} valid patents found.     ")
            
            if len(patents) < per_page:
                print("\n✅ Reached end of results.")
                break

            page += 1
            time.sleep(1.5) 
            
        except Exception as e:
            print(f"\n❌ Connection failed: {e}")
            break
            
    return all_new_data

# ==========================================
#            GITHUB UPLOAD
# ==========================================

def upload_to_github(file_path, repo, token):
    if not token or "YOUR_" in token:
        print("\n⚠️ GitHub Token missing. Skipping upload.")
        return

    print(f"\n--- ☁️ Uploading to GitHub ({repo}) ---")
    file_name = os.path.basename(file_path)
    url = f"https://api.github.com/repos/{repo}/contents/{file_name}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    sha = None
    try:
        r = requests.get(url, headers=headers)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except: pass

    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    data = {
        "message": f"Update USA: {datetime.now().strftime('%Y-%m-%d')}", 
        "content": content, 
        "branch": "main"
    }
    if sha: data["sha"] = sha

    r = requests.put(url, headers=headers, json=data)
    if r.status_code in [200, 201]: print("✅ GitHub Upload Success!")
    else: print(f"❌ GitHub Failed: {r.status_code} - {r.text}")

# ==========================================
#               MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    print(f"🚀 Starting USA Patents Scraper...")
    
    start_date = get_start_date(CSV_FILE)
    seen_ids = load_existing_ids(CSV_FILE)
    
    new_patents = fetch_uspto_data(KEYWORDS, seen_ids, start_date)
    
    if new_patents:
        df_new = pd.DataFrame(new_patents)
        header_needed = not os.path.exists(CSV_FILE)
        df_new.to_csv(CSV_FILE, mode='a', header=header_needed, index=False)
        print(f"💾 Saved {len(new_patents)} new records to {CSV_FILE}")
        upload_to_github(CSV_FILE, GITHUB_REPO, GITHUB_TOKEN)
    else:
        print("💤 No new patents found since " + start_date)
