import os
import sys
import shutil
import subprocess

def run_es(args_list):
    es_bin = shutil.which("es.exe") or r"C:\Program Files (x86)\Everything\es.exe"
    if not os.path.exists(es_bin) and not shutil.which("es.exe"):
        return None
    try:
        cmd = [es_bin] + args_list
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
            return lines
    except Exception:
        pass
    return None

def score_match(path, query):
    p_lower = path.lower()
    q_lower = query.lower().strip()
    name = os.path.basename(path).lower()

    score = 0
    # Penalty for junk directories
    junk_patterns = ["$recycle.bin", r"appdata\local\packages", r"appdata\local\temp", r"site-packages"]
    for j in junk_patterns:
        if j in p_lower:
            score -= 100

    # Exact filename match
    if name == q_lower:
        score += 200
    elif name.startswith(q_lower):
        score += 100
    elif q_lower in name:
        score += 60

    # Shorter paths generally represent higher-level, more relevant root locations
    depth = path.count(os.sep)
    score -= depth * 3
    score -= int(len(path) * 0.1)

    return score

def main():
    if len(sys.argv) < 2:
        print("Usage: find_file.py <search_query>")
        sys.exit(1)

    query = sys.argv[1].strip()
    
    # 1. Fetch matching names/paths
    # We query ES; without -p it matches file/folder names directly
    results = run_es(["-n", "100", query])
    if not results:
        # Fallback to full path match if empty
        results = run_es(["-p", "-n", "100", query])

    if not results:
        sys.exit(0)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for r in results:
        if r not in seen:
            seen.add(r)
            unique.append(r)

    # Rank results
    ranked = sorted(unique, key=lambda p: score_match(p, query), reverse=True)

    # Output top 15 most relevant
    top_results = ranked[:15]
    for r in top_results:
        print(r)

if __name__ == "__main__":
    main()
