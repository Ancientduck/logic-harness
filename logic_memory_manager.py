# logic_memory_manager.py  – pure lexical v4 (accurate recovery focus)
import sqlite3, os, re, time, shlex, math
from datetime import datetime, timedelta
from contextlib import closing
from collections import deque

VALID_TYPES = ("episodic", "semantic", "procedural", "plan")
STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and",
    "any", "are", "aren't", "arrive", "as", "ask", "asked", "at", "be", "because", "been",
    "before", "being", "below", "between", "both", "but", "by", "can", "can't", "cannot",
    "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down",
    "during", "each", "even", "few", "for", "from", "further", "get", "got", "had", "hadn't",
    "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her",
    "here", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll",
    "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "just",
    "know", "let", "like", "look", "looking", "looked", "make", "made", "many", "me", "might",
    "more", "most", "must", "my", "myself", "need", "no", "nor", "not", "now", "of", "off",
    "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over",
    "own", "please", "really", "said", "same", "say", "saying", "see", "seen", "shall",
    "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such",
    "sure", "tell", "than", "that", "that's", "the", "their", "theirs", "them", "themselves",
    "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've",
    "thing", "things", "think", "this", "those", "through", "to", "too", "told", "try",
    "trying", "under", "until", "up", "very", "want", "was", "wasn't", "we", "we'd", "we'll",
    "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where",
    "where's", "which", "while", "who", "who's", "whom", "why", "why's", "will", "with",
    "won't", "would", "wouldn't", "yeah", "yes", "yet", "you", "you'd", "you'll", "you're",
    "you've", "your", "yours", "yourself", "yourselves"
}

CORE_THRESHOLD = 0.75
DECAY_BELOW = 0.28
DECAY_DAYS = 45
WINDOW = 24
UBIQUITY_RATIO = 0.32          # tighter than before
MIN_MEMORIES_FOR_UBIQUITY = 4
MIN_SCORE = 0.48               # hard filter – this is the main accuracy lever
CONTEXT_TURNS = 12             # rolling user-prompt buffer
AUTO_SAVE_CONFIDENCE = 0.62

# ---------- tokenization (same Porter stemmer, cleaned) ----------
def _is_consonant(w, i):
    c = w[i]
    if c in "aeiou":
        return False
    if c == "y":
        return True if i == 0 else not _is_consonant(w, i - 1)
    return True

def _measure(w):
    n = 0
    i = 0
    L = len(w)
    while i < L and _is_consonant(w, i):
        i += 1
    while i < L:
        while i < L and not _is_consonant(w, i):
            i += 1
        if i >= L:
            break
        n += 1
        while i < L and _is_consonant(w, i):
            i += 1
    return n

def _contains_vowel(w):
    return any(not _is_consonant(w, i) for i in range(len(w)))

def _ends_double_consonant(w):
    return len(w) >= 2 and w[-1] == w[-2] and _is_consonant(w, len(w) - 1)

def _cvc(w):
    if len(w) < 3:
        return False
    i = len(w) - 1
    if not (_is_consonant(w, i) and not _is_consonant(w, i - 1) and _is_consonant(w, i - 2)):
        return False
    return w[i] not in "wxy"

def _porter_stem(w):
    w = w.lower()
    if len(w) <= 2:
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]
    flag = False
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and _contains_vowel(w[:-2]):
        w = w[:-2]
        flag = True
    elif w.endswith("ing") and _contains_vowel(w[:-3]):
        w = w[:-3]
        flag = True
    if flag:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif _ends_double_consonant(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif _measure(w) == 1 and _cvc(w):
            w += "e"
    if w.endswith("y") and _contains_vowel(w[:-1]):
        w = w[:-1] + "i"
    for suf, rep in (("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
                     ("izer", "ize"), ("bli", "ble"), ("alli", "al"), ("entli", "ent"), ("eli", "e"),
                     ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"), ("ator", "ate"),
                     ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"),
                     ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble"), ("logi", "log")):
        if w.endswith(suf) and _measure(w[:-len(suf)]) > 0:
            w = w[:-len(suf)] + rep
            break
    for suf, rep in (("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
                     ("ical", "ic"), ("ful", ""), ("ness", "")):
        if w.endswith(suf) and _measure(w[:-len(suf)]) > 0:
            w = w[:-len(suf)] + rep
            break
    for suf in ("al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent",
                "ion", "ou", "ism", "ate", "iti", "ous", "ive", "ize"):
        if w.endswith(suf):
            stem = w[:-len(suf)]
            if suf == "ion" and (not stem or stem[-1] not in "st"):
                continue
            if _measure(stem) > 1:
                w = stem
            break
    if w.endswith("e"):
        stem = w[:-1]
        m = _measure(stem)
        if m > 1 or (m == 1 and not _cvc(stem)):
            w = stem
    if w.endswith("ll") and _measure(w) > 1:
        w = w[:-1]
    return w

def _tokenize(text):
    out = []
    for w in re.findall(r"[a-zA-Z0-9]+", text.lower()):
        if len(w) <= 2 or w in STOPWORDS:
            continue
        s = _porter_stem(w)
        if s:
            out.append(s)
    return out

def _unique_tokens(text):
    seen, out = set(), []
    for t in _tokenize(text):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out

# ---------- MemoryManager ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "memory", "memory.db")

class MemoryManager:
    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        self.sent_ids = set()
        self.context = deque(maxlen=CONTEXT_TURNS)   # recent user prompts (token lists)
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)

        with closing(self._conn()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema(conn)
            self._migrate(conn)
            self._backfill_tokens(conn)
            self._purge(conn)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init_schema(self, conn):
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            memory_type TEXT DEFAULT 'semantic',
            importance REAL DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            last_accessed TEXT,
            expires_at TEXT,
            created_at TEXT DEFAULT (datetime('now')))""")
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, content='memories', content_rowid='id', tokenize='porter')""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
            INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
        END""")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_tokens (
            memory_id INTEGER NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mt_token ON memory_tokens(token)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_mt_mem ON memory_tokens(memory_id)")

    def _migrate(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        if "memory_type" not in cols:
            conn.execute("ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT 'semantic'")
            conn.execute("ALTER TABLE memories ADD COLUMN importance REAL DEFAULT 0.5")
            conn.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
            conn.execute("UPDATE memories SET memory_type='episodic', importance=0.4")
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('rebuild')")
            print("[memory] migrated legacy rows")
        if "last_accessed" not in cols:
            conn.execute("ALTER TABLE memories ADD COLUMN last_accessed TEXT")

    def _backfill_tokens(self, conn):
        have = {r[0] for r in conn.execute("SELECT DISTINCT memory_id FROM memory_tokens")}
        rows = conn.execute("SELECT id, content FROM memories").fetchall()
        added = 0
        for mid, content in rows:
            if mid in have:
                continue
            for pos, tok in enumerate(_tokenize(content)):
                conn.execute("INSERT INTO memory_tokens(memory_id, position, token) VALUES (?,?,?)",
                             (mid, pos, tok))
            added += 1
        if added:
            print("[memory] tokenized " + str(added) + " memories")

    def _purge(self, conn):
        n1 = conn.execute("DELETE FROM memories WHERE expires_at IS NOT NULL AND date(expires_at) < date('now')").rowcount
        n2 = conn.execute("""DELETE FROM memories WHERE importance < ? AND access_count = 0
                             AND created_at < datetime('now', ?)""",
                          (DECAY_BELOW, "-" + str(DECAY_DAYS) + " days")).rowcount
        if n1 or n2:
            conn.execute("DELETE FROM memory_tokens WHERE memory_id NOT IN (SELECT id FROM memories)")
            print("[memory] purged " + str(n1) + " expired, " + str(n2) + " decayed")

    # ---------- public API that Logic should use ----------

    def observe_turn(self, user_prompt: str, auto_save: bool = True):
        """
        Call this every turn with the raw user prompt.
        - Updates the rolling context buffer
        - Optionally auto-extracts + saves high-confidence facts
        """
        if not user_prompt or not user_prompt.strip():
            return
        tokens = _tokenize(user_prompt)
        self.context.append(tokens)

        if auto_save:
            candidates = self._extract_candidates(user_prompt)
            for text, conf in candidates:
                if conf >= AUTO_SAVE_CONFIDENCE:
                    self.save(text, memory_type="episodic", importance=min(0.72, 0.45 + conf * 0.35))

    def get_relevant(self, user_prompt: str, limit: int = 3) -> list:
        """
        The main retrieval method. Only returns memories that clear the hard score threshold.
        """
        if not user_prompt or not user_prompt.strip():
            return []
        # build query from current prompt + recent context
        q_tokens = _unique_tokens(user_prompt)
        for past in list(self.context)[-6:]:
            for t in past:
                if t not in q_tokens:
                    q_tokens.append(t)
        if not q_tokens:
            return []

        with closing(self._conn()) as conn, conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            if total == 0:
                return []

            # document frequency for rarity
            ph = ",".join("?" * len(q_tokens))
            df = {t: c for t, c in conn.execute(
                "SELECT token, COUNT(DISTINCT memory_id) FROM memory_tokens "
                "WHERE token IN (" + ph + ") GROUP BY token", q_tokens).fetchall()}

            # keep only non-ubiquitous tokens
            if total >= MIN_MEMORIES_FOR_UBIQUITY:
                meaningful = [t for t in q_tokens if 0 < df.get(t, 0) and df.get(t, 0) / total <= UBIQUITY_RATIO]
            else:
                meaningful = [t for t in q_tokens if df.get(t, 0) > 0]
            if not meaningful:
                return []

            # candidate memories that contain at least one meaningful token
            ph = ",".join("?" * len(meaningful))
            cands = [r[0] for r in conn.execute(
                "SELECT DISTINCT memory_id FROM memory_tokens WHERE token IN (" + ph + ")",
                meaningful).fetchall()]
            if not cands:
                return []

            # load token streams
            ph2 = ",".join("?" * len(cands))
            rows = conn.execute(
                "SELECT memory_id, token FROM memory_tokens WHERE memory_id IN (" + ph2 + ") "
                "ORDER BY memory_id, position", cands).fetchall()
            streams = {}
            for mid, tok in rows:
                streams.setdefault(mid, []).append(tok)

            # load metadata
            info = {r[0]: r for r in conn.execute(
                "SELECT id, content, memory_type, importance, access_count, created_at, last_accessed "
                "FROM memories WHERE id IN (" + ph2 + ")", cands).fetchall()}

            qset = set(meaningful)
            scored = []
            for mid, toks in streams.items():
                if mid in self.sent_ids:
                    continue
                row = info.get(mid)
                if not row:
                    continue
                _, content, mtype, imp, ac, created, last_acc = row

                # --- lexical score ---
                distinct = {t for t in toks if t in qset}
                if not distinct:
                    continue
                # best window overlap
                best_win = 0
                for i in range(len(toks)):
                    win = {x for x in toks[i:i + WINDOW] if x in qset}
                    if len(win) > best_win:
                        best_win = len(win)
                coverage = best_win / max(1, len(meaningful))
                # rare-token bonus
                rare_bonus = sum(1.0 / max(1, df.get(t, 1)) for t in distinct) / max(1, len(distinct))
                lexical = 0.55 * coverage + 0.45 * (len(distinct) / max(1, len(meaningful))) + 0.15 * rare_bonus
                lexical = min(1.0, lexical)

                # recency (0–1)
                try:
                    created_dt = datetime.fromisoformat(created.replace("Z", ""))
                    age_days = max(0.0, (datetime.now() - created_dt).total_seconds() / 86400)
                    recency = math.exp(-age_days / 40.0)
                except Exception:
                    recency = 0.5

                # final score
                score = (
                    0.52 * lexical +
                    0.22 * float(imp) +
                    0.16 * recency +
                    0.10 * min(1.0, math.log1p(ac) / 3.0)
                )
                if score < MIN_SCORE:
                    continue
                scored.append((score, mid, content, mtype, imp, best_win, len(meaningful)))

            if not scored:
                return []

            scored.sort(key=lambda x: -x[0])
            picked = scored[:limit]
            mark = [p[1] for p in picked]

            # reinforce
            now = datetime.now().isoformat()
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ? "
                "WHERE id IN (" + ",".join("?" * len(mark)) + ")",
                [now] + mark)
            self.sent_ids.update(mark)

            out = []
            for score, mid, content, mtype, imp, hit, total_m in picked:
                out.append(
                    f"[score {score:.2f} | {hit}/{total_m}] ({mtype}|imp {imp:.1f}) {content}"
                )
            return out

    def reinforce(self, memory_ids):
        """Call after you actually used some of the returned memories."""
        if not memory_ids:
            return
        with closing(self._conn()) as conn, conn:
            now = datetime.now().isoformat()
            for mid in memory_ids:
                conn.execute(
                    "UPDATE memories SET access_count = access_count + 1, "
                    "importance = MIN(1.0, importance + 0.04), last_accessed = ? WHERE id = ?",
                    (now, int(mid)))

    # ---------- classic methods (kept for compatibility + CLI) ----------

    def save(self, text, memory_type="semantic", importance=0.5, expires_days=None):
        text = text.strip()
        if not text:
            return ""
        if memory_type not in VALID_TYPES:
            return "Invalid type. Use: " + ", ".join(VALID_TYPES)
        importance = max(0.0, min(1.0, float(importance)))
        with closing(self._conn()) as conn, conn:
            dup = self._find_duplicate(conn, text)
            if dup:
                return "Rejected, near-duplicate of [" + str(dup[0]) + "]: " + dup[1][:60]
            exp = (datetime.now() + timedelta(days=float(expires_days))).isoformat() if expires_days else None
            cur = conn.execute(
                "INSERT INTO memories (content, memory_type, importance, expires_at) VALUES (?,?,?,?)",
                (text, memory_type, importance, exp))
            mid = cur.lastrowid
            for pos, tok in enumerate(_tokenize(text)):
                conn.execute("INSERT INTO memory_tokens(memory_id, position, token) VALUES (?,?,?)",
                             (mid, pos, tok))
        return "Saved (" + memory_type + " | imp " + format(importance, ".1f") + "): " + text[:70]

    def _find_duplicate(self, conn, text, threshold=0.78):
        words = _tokenize(text)
        if not words:
            return None
        q = " OR ".join('"' + w + '"' for w in words)
        try:
            rows = conn.execute("""SELECT memories.id, memories.content FROM memories_fts
                                   JOIN memories ON memories.id = memories_fts.rowid
                                   WHERE memories_fts MATCH ? LIMIT 10""", (q,)).fetchall()
        except sqlite3.OperationalError:
            return None
        for rid, content in rows:
            if self._jaccard(words, _tokenize(content)) >= threshold:
                return (rid, content)
        return None

    @staticmethod
    def _jaccard(a, b):
        sa, sb = set(a), set(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def delete(self, text):
        with closing(self._conn()) as conn, conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM memories WHERE content LIKE ?",
                                              ("%" + text + "%",))]
            if ids:
                ph = ",".join("?" * len(ids))
                conn.execute("DELETE FROM memory_tokens WHERE memory_id IN (" + ph + ")", ids)
            cur = conn.execute("DELETE FROM memories WHERE content LIKE ?", ("%" + text + "%",))
        return "Deleted " + str(cur.rowcount) + " entries matching '" + text + "'"

    def search(self, query, limit=5, memory_type=None, min_importance=None):
        # kept for CLI / debugging – routes through the same tight scorer
        results = self.get_relevant(query, limit=limit)
        if memory_type or min_importance is not None:
            # simple post-filter
            filtered = []
            for line in results:
                if memory_type and f"({memory_type}|" not in line:
                    continue
                filtered.append(line)
            return filtered
        return results

    def get_core(self, limit=8, threshold=CORE_THRESHOLD):
        with closing(self._conn()) as conn:
            rows = conn.execute("""SELECT memory_type, content FROM memories
                                   WHERE importance >= ?
                                   ORDER BY importance DESC, created_at DESC LIMIT ?""",
                                (threshold, limit)).fetchall()
        return ["(" + t + ") " + c for t, c in rows]

    def update(self, mid, importance=None, memory_type=None, expires_days=None, never_expires=False):
        sets, params = [], []
        if importance is not None:
            sets.append("importance=?")
            params.append(max(0.0, min(1.0, float(importance))))
        if memory_type:
            if memory_type not in VALID_TYPES:
                return "Invalid type."
            sets.append("memory_type=?")
            params.append(memory_type)
        if never_expires:
            sets.append("expires_at=NULL")
        elif expires_days is not None:
            sets.append("expires_at=?")
            params.append((datetime.now() + timedelta(days=float(expires_days))).isoformat())
        if not sets:
            return "Nothing to update."
        params.append(int(mid))
        with closing(self._conn()) as conn, conn:
            cur = conn.execute("UPDATE memories SET " + ", ".join(sets) + " WHERE id=?", params)
        return "Updated " + str(cur.rowcount) + " row(s)."

    def reset_sent(self):
        n = len(self.sent_ids)
        self.sent_ids.clear()
        return "Reset: " + str(n) + " memory ids cleared."

    def show_all(self):
        with closing(self._conn()) as conn:
            rows = conn.execute("""SELECT id, content, memory_type, importance, access_count,
                                          COALESCE(expires_at,'-'), created_at
                                   FROM memories ORDER BY importance DESC, created_at DESC""").fetchall()
        if not rows:
            return ["Memory is empty."]
        return ["[" + str(rid) + "] (" + t + "|imp " + format(imp, ".1f") + "|used " + str(ac)
                + "|exp " + exp + ") " + content
                for rid, content, t, imp, ac, exp, ts in rows]

    def stats(self):
        with closing(self._conn()) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_type = conn.execute("SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type").fetchall()
            core = conn.execute("SELECT COUNT(*) FROM memories WHERE importance >= ?",
                                (CORE_THRESHOLD,)).fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM memory_tokens").fetchone()[0]
        lines = ["Total: " + str(total) + " | core (imp>=" + str(CORE_THRESHOLD) + "): " + str(core)
                 + " | tokens: " + str(chunks)]
        lines += ["  " + t + ": " + str(c) for t, c in by_type]
        return lines

    def ingest_session(self, user_prompts: list, max_per_turn: int = 2):
        """
        Bootstrap from a list of past user prompts.
        Safe – only high-confidence candidates are kept.
        """
        saved = 0
        for prompt in user_prompts:
            cands = self._extract_candidates(prompt)
            cands = sorted(cands, key=lambda x: -x[1])[:max_per_turn]
            for text, conf in cands:
                if conf >= AUTO_SAVE_CONFIDENCE - 0.05:
                    r = self.save(text, memory_type="episodic",
                                  importance=min(0.68, 0.40 + conf * 0.35))
                    if r.startswith("Saved"):
                        saved += 1
        return f"Ingested session → {saved} new memories"

    # ---------- cheap auto-extract (rule-based) ----------
    def _extract_candidates(self, text: str):
        """
        General heuristic extractor – no hard-coded sentence types.
        Splits into chunks, scores how memorable/fact-like each one is,
        returns only the good ones.
        """
        if not text or len(text.strip()) < 8:
            return []

        # light clean
        text = re.sub(r"^(logic|hey logic|oi logic|bro|yo|hey)\s*[,:]?\s*", "", text, flags=re.I).strip()
        low = text.lower()

        # split into sentence-ish chunks
        chunks = re.split(r"[.!?\n]+", text)
        chunks = [c.strip() for c in chunks if len(c.strip()) > 10]

        if not chunks:
            chunks = [text]

        # strong positive signals (general, not type-specific)
        POSITIVE = [
            r"\b(remind|remember|don't forget|dont forget|note that|make a note|for future)\b",
            r"\b(i (?:am|i'm|live|work|use|prefer|like|love|hate|always|never|want|need))\b",
            r"\b(my (?:name|birthday|bday|timezone|project|deadline|favorite))\b",
            r"\b(birthday|bday|deadline|result day|born|december|january|october)\b",
            r"\b(never again|only use|always use|from now on|stick to)\b",
            r"\b(dark mode|light mode|python|py 3|uv |vscode|dhaka|alex)\b",
        ]

        # strong negative signals (noise)
        NEGATIVE = [
            r"^\s*(what|why|how|when|where|who|is|are|can|could|would|do|does)\b",  # pure questions
            r"\b(lol|lmao|haha|wtf|omg|idk|tbh|imo)\b",
            r"^(ok|okay|sure|yeah|yep|nah|hi|hey|yo|sup|thanks|thank you)\s*$",
        ]

        candidates = []
        for chunk in chunks:
            clow = chunk.lower()
            score = 0.35  # base

            # length sweet spot
            ln = len(chunk)
            if 15 <= ln <= 120:
                score += 0.15
            elif ln > 180:
                score -= 0.15

            # positive cues
            for pat in POSITIVE:
                if re.search(pat, clow):
                    score += 0.22
                    break

            # first-person + content words
            tokens = _tokenize(chunk)
            if any(t in ("i", "my", "me") for t in tokens) and len(tokens) >= 3:
                score += 0.12

            # negative / noise
            for pat in NEGATIVE:
                if re.search(pat, clow):
                    score -= 0.35
                    break

            # pure short questions or reactions
            if clow.endswith("?") and len(tokens) < 6:
                score -= 0.25

            score = max(0.0, min(0.95, score))

            if score >= 0.58:          # only keep decent ones
                candidates.append((chunk[:200], score))

        # sort best first, dedup
        candidates.sort(key=lambda x: -x[1])
        seen = set()
        final = []
        for c, s in candidates:
            key = c.lower()
            if key not in seen:
                seen.add(key)
                final.append((c, s))
        return final[:3]   # max 3 per message


# ---------- CLI (unchanged interface) ----------
def run_timed(label, func, *args):
    start = time.perf_counter()
    result = func(*args)
    elapsed = (time.perf_counter() - start) * 1000
    if isinstance(result, list):
        for line in result:
            print(line)
    elif result:
        print(result)
    print("--- " + label + " took " + format(elapsed, ".3f") + " ms ---")

def _parse_flags(arg):
    parts = shlex.split(arg)
    text, flags, i = [], {}, 0
    while i < len(parts):
        p = parts[i]
        if p == "--type" and i + 1 < len(parts):
            flags["memory_type"] = parts[i + 1]
            i += 2
        elif p == "--imp" and i + 1 < len(parts):
            flags["importance"] = float(parts[i + 1])
            i += 2
        elif p == "--exp" and i + 1 < len(parts):
            v = parts[i + 1]
            if v.lower() == "never":
                flags["never_expires"] = True
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                target = datetime.fromisoformat(v + "T23:59:59")
                flags["expires_days"] = max(0.0001, (target - datetime.now()).total_seconds() / 86400)
            else:
                flags["expires_days"] = float(v)
            i += 2
        elif p == "--min" and i + 1 < len(parts):
            flags["min_importance"] = float(parts[i + 1])
            i += 2
        else:
            text.append(p)
            i += 1
    return " ".join(text), flags

memorymanager = MemoryManager()

if __name__ == "__main__":
    mem = MemoryManager()
    print("Memory Manager v4 (accurate lexical) | observe | relevant | save | delete | search | core | set | stats | all | reset | ingest | exit")
    print("flags: --type episodic|semantic|procedural|plan  --imp 0.0-1.0  --exp <YYYY-MM-DD | days | never>")
    while True:
        try:
            raw = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            continue
        parts = raw.split(" ", 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "exit":
            break
        elif cmd == "observe" and arg:
            run_timed("observe", mem.observe_turn, arg)
        elif cmd == "relevant" and arg:
            run_timed("relevant", mem.get_relevant, arg, 3)
            mem.reset_sent()
        elif cmd == "save" and arg:
            text, f = _parse_flags(arg)
            run_timed("save", mem.save, text, f.get("memory_type", "semantic"),
                      f.get("importance", 0.5), f.get("expires_days"))
        elif cmd == "delete" and arg:
            run_timed("delete", mem.delete, arg)
        elif cmd == "search" and arg:
            text, f = _parse_flags(arg)
            run_timed("search", mem.search, text, 5, f.get("memory_type"), f.get("min_importance"))
            mem.reset_sent()
        elif cmd == "core":
            run_timed("core", mem.get_core)
        elif cmd == "set" and arg:
            p2 = arg.split(" ", 1)
            _, flags = _parse_flags(p2[1] if len(p2) > 1 else "")
            run_timed("set", mem.update, int(p2[0]), flags.get("importance"),
                      flags.get("memory_type"), flags.get("expires_days"),
                      flags.get("never_expires", False))
        elif cmd == "stats":
            run_timed("stats", mem.stats)
        elif cmd == "reset":
            run_timed("reset_sent", mem.reset_sent)
        elif cmd == "all":
            run_timed("show_all", mem.show_all)
        elif cmd == "ingest" and arg:
            # expect a file path containing one user prompt per line
            try:
                with open(arg, encoding="utf-8") as f:
                    prompts = [ln.strip() for ln in f if ln.strip()]
                run_timed("ingest", mem.ingest_session, prompts)
            except Exception as e:
                print("ingest error:", e)
        else:
            print("Unknown command. Try: observe / relevant / save / search / stats / all / ingest <file>")