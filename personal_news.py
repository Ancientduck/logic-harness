"""personal_news.py - fast personalized news aggregator for Apurbo.
Concurrent Google News RSS fetch, dedup, recency filter, real-link resolution, noise filtering, and a seen-ledger.
Run:   python personal_news.py
Reset: python personal_news.py --reset
"""
import requests, json, os, re, sys, hashlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote

# ---- CONFIG: edit these ----
KEYWORDS = [
    "Bangladesh admission test",
    "HSC exam Bangladesh",
    "Dhaka University admission",
    "Bangladesh university admission",
    "HSC result Bangladesh",
    "Bangladesh education ministry",
]
BLOCKLIST = [
    "samsung", "galaxy", "study in", "medical education", "russian medical",
    "sponsored", "advertisement", "buy now", "promo", "discount",
    "diplomat", "runway", "school meals", "ldc graduation", "nikkei",
    "fashion", "recipe", "movie", "celebrity", "horoscope", "lifestyle",
    "cricket", "football", "concert", "traffic",
]
MAX_AGE_HOURS = 24          # hard ceiling; SAME_DAY_ONLY is stricter
SAME_DAY_ONLY = True        # only stories published today (local date)
CORE_TERMS = [              # title MUST contain at least one of these
    "admission", "hsc", "ssc", "exam", "examination", "result",
    "gpa", "merit list", "seat plan", "question leak", "syllabus",
    "curriculum", "university", "buet", "dhaka university",
    "board", "ugc", "academic", "graduation", "education ministry",
]

RELEVANCE_TERMS = CORE_TERMS
HL, GL, CEID = "en-BD", "BD", "BD:en"
MAX_PER_QUERY = 15
SHOW_SEEN = True
RESOLVE_LINKS = False
RESOLVE_WORKERS = 8
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)
LEDGER = os.path.join(LOGS_DIR, "news_seen.json")
# ----------------------------

BASE = "https://news.google.com/rss/search"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_URL_CACHE = {}

def feed_url(q):
    return f"{BASE}?q={quote(q)}&hl={HL}&gl={GL}&ceid={CEID}"

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=0)
    except Exception as e:
        print(f"  [save failed {path}: {e}]")

def load_ledger_set(path):
    data = load_json(path, {})
    if isinstance(data, dict):
        return set(data.get("seen", []))
    if isinstance(data, list):
        return set(data)
    return set()

def decode_google_news_url(source_url):
    try:
        art_id = source_url.split("/articles/")[1].split("?")[0]
    except IndexError:
        return source_url
    if not art_id:
        return source_url
    if art_id in _URL_CACHE:
        return _URL_CACHE[art_id]
    real = None
    try:
        r = requests.get(f"https://news.google.com/rss/articles/{art_id}",
                         headers=UA, timeout=8)
        sg = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if sg and ts:
            payload = [[
                "Fbv4je",
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",'
                'null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,'
                'null,0,0,null,0],"' + art_id + '",' + ts.group(1) +
                ',"' + sg.group(1) + '"]'
            ]]
            body = "f.req=" + quote(json.dumps(payload))
            r2 = requests.post(
                "https://news.google.com/_/DotsSplashUi/data/batchexecute",
                headers={"content-type":
                         "application/x-www-form-urlencoded;charset=UTF-8", **UA},
                data=body, timeout=8)
            for chunk in r2.text.split("\n\n"):
                chunk = chunk.strip()
                if not chunk or chunk.startswith(")]}'"):
                    continue
                try:
                    arr = json.loads(chunk)
                except Exception:
                    continue
                if isinstance(arr, list) and arr and isinstance(arr[0], list) \
                        and len(arr[0]) > 2 and isinstance(arr[0][2], str) \
                        and "garturlres" in arr[0][2]:
                    real = json.loads(arr[0][2])[1]
                    break
    except Exception:
        pass
    if real and "news.google.com" not in real:
        _URL_CACHE[art_id] = real
        return real
    return source_url

def fetch(q):
    try:
        r = requests.get(feed_url(q), timeout=8, headers=UA)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        return q, [], str(e)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("pubDate")
        src = it.find("source")
        source = src.text.strip() if src is not None and src.text else ""
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        items.append({"title": title, "link": link, "dt": dt, "source": source})
    return q, items, None

def thash(title):
    return hashlib.md5(title.lower().encode("utf-8")).hexdigest()

def blocked(title):
    t = title.lower()
    return any(b in t for b in BLOCKLIST)

def relevant(title):
    t = title.lower()
    return any(term in t for term in CORE_TERMS)


def get_news(quiet=False):
    global _URL_CACHE
    news_path = os.path.join(LOGS_DIR, "news.json")

    if "--reset" in sys.argv:
        if os.path.exists(news_path):
            os.remove(news_path)
        print("history cleared.")
        return "history cleared."

    _URL_CACHE = {}

    # ── load store: {"seen": [hash, ...], "digest": [ {ts, new, total, text}, ... ]}
    store = load_json(news_path, {})
    if isinstance(store, list):                       # legacy bare-list format
        store = {"seen": store, "digest": []}
    seen = set(store.get("seen", []))
    digest = store.get("digest", [])

    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    seen_run, results, errors = set(), [], []

    with ThreadPoolExecutor(max_workers=len(KEYWORDS)) as ex:
        futs = {ex.submit(fetch, q): q for q in KEYWORDS}
        for f in as_completed(futs):
            q, items, err = f.result()
            if err:
                errors.append((q, err)); continue
            for it in items[:MAX_PER_QUERY]:
                key = thash(it["title"])
                if not it["title"] or key in seen_run:            continue
                if it["dt"] and it["dt"] < cutoff:                continue
                if blocked(it["title"]):                          continue
                if not relevant(it["title"]):                     continue
                if SAME_DAY_ONLY and it["dt"]:
                    if it["dt"].astimezone().date() != datetime.now().astimezone().date():
                        continue
                seen_run.add(key)
                it["query"] = q
                it["hash"] = key
                results.append(it)

    results.sort(key=lambda x: x["dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    if RESOLVE_LINKS:
        todo = [r for r in results
                if r["link"].split("/articles/")[-1].split("?")[0] not in _URL_CACHE]
        with ThreadPoolExecutor(max_workers=RESOLVE_WORKERS) as ex:
            futs = {ex.submit(decode_google_news_url, r["link"]): r for r in todo}
            for f in as_completed(futs):
                futs[f]["real_link"] = f.result()
        for r in results:
            r.setdefault("real_link", r["link"])
    else:
        for r in results:
            r["real_link"] = r["link"]

    new_items = [r for r in results if r["hash"] not in seen]
    show = new_items if SHOW_SEEN else results

    out = [f"=== Personal News  |  {len(new_items)} new / {len(results)} total  |  last {MAX_AGE_HOURS}h ===", ""]
    for r in show:
        ts = r["dt"].astimezone().strftime("%d %b %H:%M") if r["dt"] else "  ?  "
        out.append(f"[{ts}] {r['title']}")
        out.append(f"         {r['source']}")
        link = r.get("real_link", "")
        if link and "news.google.com" not in link:
            out.append(f"         {link}")
    if errors:
        out += ["", "-- fetch errors --"] + [f"  {q}: {e}" for q, e in errors]

    text = "\n".join(out)

    # ── persist: update seen hashes + append this run's digest (cap at 200) ──
    seen.update(r["hash"] for r in results)
    if show:
        digest.append({
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "new": len(new_items), "total": len(results), "text": text,
        })
        digest = digest[-200:]
    save_json(news_path, {"seen": sorted(seen), "digest": digest})

    result = text if show else f"(no new news in the last {MAX_AGE_HOURS}h)"
    if not quiet:
        print(result)
        if show:
            print(f"\n  saved -> {news_path}")
    return result

if __name__ == "__main__":
    print(get_news(quiet=True))
