
"""
memory_system.py  v4.0 — Trigger-based memory retrieval
========================================================
Lean rewrite. Memories carry explicit trigger words set at save time.
Retrieval matches query tokens against triggers. No IDF, no concept
expansion, no aspect rules.

Drop-in replacement: same DB (adds `triggers` column), same function
names, same call_tool() interface.
"""

from __future__ import annotations
import json, os, re, sqlite3, sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple, Iterable

# ── config ──────────────────────────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory", "Memory.db")
MIN_SCORE = 0.30          # minimum trigger-overlap score to surface
MAX_HITS  = 8             # default retrieval cap
BST       = timezone(timedelta(hours=6))

FILLER_WORDS = frozenset(
    "i me my mine myself we us our ours ourselves you your yours yourself "
    "he him his she her hers it its they them their theirs a an the this "
    "that these those is am are was were be been being have has had do does "
    "did will would shall should may might can could of in to for on with "
    "at by from as into about between through during before after above "
    "below up down out off over under again then once and but or nor not "
    "no so if when where how what which who whom why very just also still "
    "even too much more most other some any all each every both few many "
    "such than like only own same here there now then already yet really "
    "right well back got get go going been ok okay yes yeah sure thing "
    "things let know think see want need make take come give say tell "
    "try use find look feel seem keep start help show hear run move live "
    "believe happen hold bring turn leave call must shall able "
    "please thanks thank hello hi hey good great nice".split()
)

GREETING_RE = re.compile(
    r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening|night)|"
    r"sup|what'?s?\s*up|yo|howdy|greetings?)\s*[!.?,]*\s*$", re.I
)

SELF_MARKER = re.compile(r"\b(?:i|my|me|mine|myself)\b", re.I)

# ── helpers ─────────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(BST).strftime("%Y-%m-%d %H:%M:%S")

def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())

def content_tokens(text: str) -> List[str]:
    return [t for t in tokenize(text) if t not in FILLER_WORDS and len(t) > 1]

def split_into_sentences(text: str) -> List[str]:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in parts if s.strip()]

def gate_reason(query: str) -> str:
    q = query.strip()
    if not q:
        return "empty"
    if GREETING_RE.match(q):
        return "greeting"
    tokens = content_tokens(q)
    if len(tokens) == 0:
        return "no_content"
    return ""

def is_meta_query(query: str) -> bool:
    q = query.lower().strip()
    return bool(re.search(
        r"\b(?:what\s+(?:do\s+you|did\s+you)\s+(?:know|remember|store|have|saved?)|"
        r"(?:show|list|tell)\s+(?:me\s+)?(?:all|every|my)\s+(?:memor|fact|info)|"
        r"tell\s+me\s+about\s+(?:me|myself))\b|"
        r"^(?:who|what)\s+am\s+i\s*[?.!]*$", q))

def is_user_scoped_memory(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"\buser\b", t))


# ── data classes ────────────────────────────────────────────────────────────
@dataclass
class MemoryRecord:
    id: int
    text: str
    slots: List[str]
    triggers: List[str]
    state_key: str
    state_value: str
    is_current: int
    created_at: str
    updated_at: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "slots": self.slots,
            "triggers": self.triggers, "state_key": self.state_key,
            "state_value": self.state_value, "is_current": self.is_current,
            "created_at": self.created_at, "source": self.source,
        }

@dataclass
class SearchHit:
    sentence_id: int
    text: str
    slots: List[str]
    state_key: str
    score: float
    reasons: List[str]

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.sentence_id, "text": self.text, "score": round(self.score, 4),
                "why": ", ".join(self.reasons)}

def format_hits(hits: Sequence[SearchHit]) -> str:
    if not hits:
        return ""
    lines = []
    for h in hits:
        lines.append(f"  [id={h.sentence_id}] {h.text}  (score={h.score:.2f})  why={'; '.join(h.reasons)}")
    return f"{len(hits)} relevant memor{'y' if len(hits)==1 else 'ies'}:\n" + "\n".join(lines)


# ── store ───────────────────────────────────────────────────────────────────
class MemoryStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DB_PATH
        if self.db_path and self.db_path != ":memory:" and not self.db_path.startswith("file:"):
            parent = os.path.dirname(os.path.abspath(self.db_path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._setup()
        self._cache: Optional[List[MemoryRecord]] = None
        self._sent_ids: set[int] = set()
        self.sent_dedup_enabled = True

    def _setup(self):
        cur = self._conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                slots TEXT NOT NULL DEFAULT '',
                triggers TEXT NOT NULL DEFAULT '',
                state_key TEXT NOT NULL DEFAULT '',
                state_value TEXT NOT NULL DEFAULT '',
                state_kind TEXT NOT NULL DEFAULT '',
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                source TEXT NOT NULL DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # migrate: add triggers column if missing
        cols = {row[1] for row in cur.execute("PRAGMA table_info(sentences)")}
        if "triggers" not in cols:
            cur.execute("ALTER TABLE sentences ADD COLUMN triggers TEXT NOT NULL DEFAULT ''")
        # migrate: ensure history has created_at
        h_cols = {row[1] for row in cur.execute("PRAGMA table_info(history)")}
        if "created_at" not in h_cols:
            if "at" in h_cols:
                try:
                    cur.execute("ALTER TABLE history RENAME COLUMN at TO created_at")
                except Exception:
                    cur.execute("ALTER TABLE history ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
            else:
                cur.execute("ALTER TABLE history ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP")
        self._conn.commit()

    def _invalidate(self):
        self._cache = None

    def _all_records(self) -> List[MemoryRecord]:
        if self._cache is not None:
            return self._cache
        rows = self._conn.execute("SELECT * FROM sentences ORDER BY id").fetchall()
        self._cache = [self._record_from_row(r) for r in rows]
        return self._cache

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        slots = [s for s in (row["slots"] or "").split(",") if s]
        triggers = [t.strip() for t in (row["triggers"] or "").split(",") if t.strip()]
        return MemoryRecord(
            id=row["id"], text=row["text"], slots=slots, triggers=triggers,
            state_key=row["state_key"] or "", state_value=row["state_value"] or "",
            is_current=int(row["is_current"] or 0),
            created_at=row["created_at"] or "", updated_at=row["updated_at"] or "",
            source=row["source"] or "",
        )

    def _row(self, memory_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM sentences WHERE id=?", (memory_id,)).fetchone()

    def _snapshot(self, memory_id: int) -> Optional[Dict[str, Any]]:
        row = self._row(memory_id)
        if not row:
            return None
        rec = self._record_from_row(row)
        return rec.to_dict()

    def _log(self, memory_id: int, action: str, before: Optional[Dict], after: Optional[Dict], reason: str = ""):
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(history)")}
        time_col = "created_at" if "created_at" in cols else "at" if "at" in cols else None
        if time_col:
            self._conn.execute(
                f"INSERT INTO history (memory_id, action, before_json, after_json, reason, {time_col}) "
                "VALUES (?,?,?,?,?,?)",
                (memory_id, action,
                 json.dumps(before) if before else None,
                 json.dumps(after) if after else None,
                 reason, _now()))
        else:
            self._conn.execute(
                "INSERT INTO history (memory_id, action, before_json, after_json, reason) "
                "VALUES (?,?,?,?,?)",
                (memory_id, action,
                 json.dumps(before) if before else None,
                 json.dumps(after) if after else None,
                 reason))

    # ── sent dedup (in-memory only, no extra files) ─────────────────────────
    def reset_sent(self) -> Dict[str, Any]:
        self._sent_ids.clear()
        return {"ok": True, "action": "reset_sent"}

    def sent_ids(self) -> set:
        return set(self._sent_ids)

    def mark_sent(self, memory_ids: Iterable[int]):
        self._sent_ids.update(int(i) for i in memory_ids)

    def _unmark_sent(self, memory_ids: Iterable[int]):
        self._sent_ids.difference_update(int(i) for i in memory_ids)

    # ── scoring ─────────────────────────────────────────────────────────────
    def _score_prompt(self, prompt: str) -> Tuple[List[SearchHit], Dict[str, Any]]:
        records = self._all_records()
        diag: Dict[str, Any] = {
            "prompt": prompt, "total_memories": len(records),
            "gate": "open", "query_tokens": [], "hits": [],
        }

        q = prompt.strip()
        if not q:
            diag["gate"] = "empty_prompt"
            return [], diag

        if len(records) == 0:
            diag["gate"] = "empty_memory"
            return [], diag

        # meta query — return all user-scoped memories
        if is_meta_query(q):
            diag["gate"] = "meta_query"
            hits = [SearchHit(r.id, r.text, r.slots, r.state_key, 1.0, ["meta_query"])
                    for r in records if is_user_scoped_memory(r.text)]
            diag["hits"] = [h.as_dict() for h in hits]
            return hits, diag

        # gate check
        gated = gate_reason(q)
        if gated:
            diag["gate"] = gated
            return [], diag

        q_tokens = content_tokens(q)
        q_token_set = set(q_tokens)
        diag["query_tokens"] = q_tokens

        if not q_tokens:
            diag["gate"] = "no_content_tokens"
            return [], diag

        scored: List[SearchHit] = []
        for rec in records:
            if not rec.triggers:
                # fallback: match against content tokens of the memory text
                mem_tokens = set(content_tokens(rec.text))
                if not mem_tokens:
                    continue
                overlap = q_token_set & mem_tokens
                if not overlap:
                    continue
                overlap_count = len(overlap)
                score = 0.35 if overlap_count == 1 else 0.60 if overlap_count == 2 else 0.85 if overlap_count >= 3 else 0.0
                scored.append(SearchHit(
                    rec.id, rec.text, rec.slots, rec.state_key,
                    min(score, 1.0), [f"text_fallback:{','.join(sorted(overlap))}"]
                ))
                continue

            # trigger matching
            matched_triggers = set()
            q_lower = q.lower()
            for t in rec.triggers:
                t_clean = t.strip().lower()
                if not t_clean:
                    continue
                # 1. Exact phrase in prompt
                if t_clean in q_lower:
                    matched_triggers.add(t_clean)
                    continue
                # 2. All content tokens of trigger present in query tokens
                t_tokens = set(content_tokens(t_clean))
                if t_tokens and t_tokens.issubset(q_token_set):
                    matched_triggers.add(t_clean)

            if not matched_triggers:
                continue

            overlap_count = len(matched_triggers)
            score = 0.50 if overlap_count == 1 else 0.75 if overlap_count == 2 else 0.90 if overlap_count >= 3 else 0.0

            if score >= MIN_SCORE:
                scored.append(SearchHit(
                    rec.id, rec.text, rec.slots, rec.state_key,
                    score, [f"triggers:{','.join(sorted(matched_triggers))}"]
                ))

        scored.sort(key=lambda h: h.score, reverse=True)
        diag["hits"] = [h.as_dict() for h in scored]
        return scored, diag

    def retrieve_hits(self, prompt: str, limit: Optional[int] = None, *,
                      include_sent: bool = False,
                      mark_sent: bool = True) -> Tuple[List[SearchHit], int]:
        hits, diag = self._score_prompt(prompt)

        hidden = 0
        # Never send already sent memories
        if self.sent_dedup_enabled and not include_sent and self._sent_ids:
            before_len = len(hits)
            hits = [h for h in hits if h.sentence_id not in self._sent_ids]
            hidden = before_len - len(hits)

        if not hits:
            return [], hidden

        if limit is not None:
            hits = hits[:max(0, int(limit))]
        elif diag.get("gate") != "meta_query":
            hits = hits[:MAX_HITS]

        if mark_sent and hits:
            self.mark_sent(h.sentence_id for h in hits)

        return hits, hidden

    def retrieve(self, prompt: str, limit: Optional[int] = None, *,
                 include_sent: bool = False, mark_sent: bool = True) -> str:
        hits, _ = self.retrieve_hits(prompt, limit, include_sent=include_sent, mark_sent=mark_sent)
        return format_hits(hits)

    def explain(self, prompt: str) -> Dict[str, Any]:
        hits, diag = self._score_prompt(prompt)
        diag["already_sent"] = [h.sentence_id for h in hits if h.sentence_id in self._sent_ids]
        return {"ok": True, "action": "explain", **diag}

    # ── save ────────────────────────────────────────────────────────────────
    def _insert(self, sentence: str, slots: Optional[List[str]], triggers: Optional[List[str]],
                source: str, reason: str = "created") -> Dict[str, Any]:
        slot_str = ",".join(slots) if slots else ""
        trigger_str = ",".join(triggers) if triggers else ""
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO sentences (text, slots, triggers, state_key, state_value, state_kind, "
            "is_current, created_at, updated_at, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sentence, slot_str, trigger_str, "", "", "", 0, now, now, source))
        mid = int(cur.lastrowid)
        after = self._snapshot(mid)
        self._log(mid, "create", None, after, reason)
        self._invalidate()
        return after or {}

    def save(self, text: Any, *, slots: Optional[List[str]] = None,
             triggers: Optional[List[str]] = None,
             source: str = "", auto_update: bool = True) -> Dict[str, Any]:
        if isinstance(text, (list, tuple)):
            chunks = []
            for item in text:
                chunks.extend(split_into_sentences(str(item)))
        else:
            chunks = split_into_sentences(str(text or ""))

        if not chunks:
            return {"ok": False, "action": "empty", "error": "nothing to save",
                    "saved": [], "updated": [], "count": 0}

        saved = []
        for sentence in chunks:
            rec = self._insert(sentence, slots, triggers, source)
            saved.append(rec)

        self._conn.commit()
        return {
            "ok": True, "action": "save", "saved": saved,
            "count": len(saved),
            "ids": [r["id"] for r in saved],
            "message": f"created {len(saved)} memories",
        }

    # ── update ──────────────────────────────────────────────────────────────
    def update(self, memory_id: Optional[int] = None, text: str = "", *,
               match: Optional[str] = None, slots: Optional[List[str]] = None,
               triggers: Optional[List[str]] = None,
               all_matches: bool = False, reason: str = "manual_update") -> Dict[str, Any]:
        targets = []
        if memory_id is not None:
            row = self._row(memory_id)
            if not row:
                return {"ok": False, "error": f"memory {memory_id} not found"}
            targets = [row]
        elif match:
            rows = self._conn.execute(
                "SELECT * FROM sentences WHERE text LIKE ?", (f"%{match}%",)).fetchall()
            if not rows:
                return {"ok": False, "error": f"no memory matching '{match}'"}
            if len(rows) > 1 and not all_matches:
                return {"ok": False, "error": "multiple matches, specify id or use all_matches=True",
                        "candidates": [{"id": r["id"], "text": r["text"]} for r in rows]}
            targets = rows
        else:
            return {"ok": False, "error": "provide memory_id or match"}

        updated = []
        for row in targets:
            before = self._snapshot(row["id"])
            sets, vals = [], []
            if text:
                sets.append("text=?"); vals.append(text)
            if slots is not None:
                sets.append("slots=?"); vals.append(",".join(slots))
            if triggers is not None:
                sets.append("triggers=?"); vals.append(",".join(triggers))
            sets.append("updated_at=?"); vals.append(_now())
            vals.append(row["id"])
            self._conn.execute(f"UPDATE sentences SET {','.join(sets)} WHERE id=?", vals)
            after = self._snapshot(row["id"])
            self._log(row["id"], "update", before, after, reason)
            updated.append({"id": row["id"], "before": before, "after": after})

        self._conn.commit()
        self._invalidate()
        if text or slots is not None or triggers is not None:
            self._unmark_sent(u["id"] for u in updated)
        return {"ok": True, "action": "update", "updated": updated, "count": len(updated)}

    # ── delete ──────────────────────────────────────────────────────────────
    def delete(self, target: Any, *, mode: str = "auto") -> Dict[str, Any]:
        if target is None:
            return {"ok": False, "error": "no target specified"}

        # by id
        if isinstance(target, int) or (isinstance(target, str) and target.isdigit()):
            mid = int(target)
            before = self._snapshot(mid)
            if not before:
                return {"ok": False, "error": f"memory {mid} not found"}
            self._conn.execute("DELETE FROM sentences WHERE id=?", (mid,))
            self._log(mid, "delete", before, None, "deleted")
            self._conn.commit()
            self._invalidate()
            self._unmark_sent([mid])
            return {"ok": True, "action": "delete", "deleted": [before]}

        # by word match
        if isinstance(target, str):
            rows = self._conn.execute(
                "SELECT * FROM sentences WHERE text LIKE ?", (f"%{target}%",)).fetchall()
            if not rows:
                return {"ok": False, "error": f"no memory matching '{target}'"}
            deleted = []
            for row in rows:
                before = self._record_from_row(row).to_dict()
                self._conn.execute("DELETE FROM sentences WHERE id=?", (row["id"],))
                self._log(row["id"], "delete", before, None, f"deleted by match: {target}")
                deleted.append(before)
            self._conn.commit()
            self._invalidate()
            self._unmark_sent(d["id"] for d in deleted)
            return {"ok": True, "action": "delete", "deleted": deleted, "count": len(deleted)}

        return {"ok": False, "error": "invalid target type"}

    # ── list / get / find ───────────────────────────────────────────────────
    def list_all(self) -> Dict[str, Any]:
        records = self._all_records()
        return {"ok": True, "action": "list", "count": len(records),
                "memories": [r.to_dict() for r in records]}

    def get(self, memory_id: int) -> Dict[str, Any]:
        snap = self._snapshot(memory_id)
        if not snap:
            return {"ok": False, "error": f"memory {memory_id} not found"}
        return {"ok": True, "action": "get", **snap}

    def find(self, word: str, mode: str = "word") -> Dict[str, Any]:
        records = self._all_records()
        hits = [r for r in records if word.lower() in r.text.lower()]
        return {"ok": True, "action": "find", "count": len(hits),
                "memories": [r.to_dict() for r in hits]}

    # ── history / undo ──────────────────────────────────────────────────────
    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def undo_last(self) -> Dict[str, Any]:
        rows = self._conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT 1").fetchall()
        if not rows:
            return {"ok": False, "error": "no history to undo"}
        entry = rows[0]
        action = entry["action"]
        mid = entry["memory_id"]
        if action == "create":
            before = self._snapshot(mid)
            self._conn.execute("DELETE FROM sentences WHERE id=?", (mid,))
            self._log(mid, "undo_create", before, None, "undo")
        elif action == "delete" and entry["before_json"]:
            snap = json.loads(entry["before_json"])
            self._conn.execute(
                "INSERT INTO sentences (id, text, slots, triggers, state_key, state_value, "
                "state_kind, is_current, created_at, updated_at, source) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (snap["id"], snap["text"], ",".join(snap.get("slots", [])),
                 ",".join(snap.get("triggers", [])),
                 snap.get("state_key", ""), snap.get("state_value", ""),
                 "", snap.get("is_current", 0),
                 snap.get("created_at", ""), _now(), snap.get("source", "")))
            self._log(mid, "undo_delete", None, snap, "undo")
        elif action == "update" and entry["before_json"]:
            snap = json.loads(entry["before_json"])
            self._conn.execute(
                "UPDATE sentences SET text=?, slots=?, triggers=?, updated_at=? WHERE id=?",
                (snap["text"], ",".join(snap.get("slots", [])),
                 ",".join(snap.get("triggers", [])), _now(), mid))
            self._log(mid, "undo_update", self._snapshot(mid), snap, "undo")
        else:
            return {"ok": False, "error": f"cannot undo action '{action}'"}
        self._conn.commit()
        self._invalidate()
        self._unmark_sent([mid])
        return {"ok": True, "action": "undo", "undone_action": action, "memory_id": mid}

    def clear(self, keep_history: bool = False) -> Dict[str, Any]:
        self._conn.execute("DELETE FROM sentences")
        if not keep_history:
            self._conn.execute("DELETE FROM history")
        self._conn.commit()
        self._invalidate()
        self.reset_sent()
        return {"ok": True, "action": "clear"}

    def close(self):
        self._conn.close()

    # ── process_prompt (compat) ─────────────────────────────────────────────
    def process_prompt(self, prompt: str, *, auto_update: bool = True,
                       include_sent: bool = False) -> Dict[str, Any]:
        hits, hidden = self.retrieve_hits(prompt, include_sent=include_sent)
        return {"ok": True, "action": "process", "count": len(hits),
                "sent_hidden": hidden,
                "memories": [h.as_dict() for h in hits],
                "rendered": format_hits(hits)}

    def update_from_prompt(self, prompt: str, *, source: str = "prompt") -> Dict[str, Any]:
        return {"ok": True, "action": "update_from_prompt", "note": "deprecated in v4"}


# ── tool handlers ───────────────────────────────────────────────────────────
def _h_save(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.save(a.get("text", ""), slots=a.get("slots"),
                      triggers=a.get("triggers"),
                      source=a.get("source", ""),
                      auto_update=bool(a.get("auto_update", True)))

def _h_retrieve(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    hits, hidden = store.retrieve_hits(
        a.get("prompt", ""), a.get("limit"),
        include_sent=bool(a.get("include_sent", False)),
        mark_sent=bool(a.get("mark_sent", True)))
    return {"ok": True, "action": "retrieve", "count": len(hits),
            "sent_hidden": hidden,
            "memories": [h.as_dict() for h in hits], "rendered": format_hits(hits)}

def _h_process(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.process_prompt(a.get("prompt", ""),
                                auto_update=bool(a.get("auto_update", True)),
                                include_sent=bool(a.get("include_sent", False)))

def _h_update_prompt(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.update_from_prompt(a.get("prompt", ""), source=a.get("source", "prompt"))

def _h_list(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.list_all()

def _h_get(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.get(int(a["id"]))

def _h_find(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.find(a.get("word", ""), a.get("mode", "word"))

def _h_explain(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.explain(a.get("prompt", ""))

def _h_update(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.update(a.get("id"), a.get("text", ""), match=a.get("match"),
                        slots=a.get("slots"), triggers=a.get("triggers"),
                        all_matches=bool(a.get("all_matches", False)))

def _h_delete(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.delete(a.get("target"), mode=a.get("mode", "auto"))

def _h_history(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    rows = store.history(int(a.get("limit", 20)))
    return {"ok": True, "action": "history", "count": len(rows), "entries": rows}

def _h_undo(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.undo_last()

def _h_clear(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.clear(keep_history=bool(a.get("keep_history", False)))

def _h_reset_sent(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.reset_sent()


TOOL_HANDLERS = {
    "memory_save": _h_save,
    "memory_retrieve": _h_retrieve,
    "memory_process": _h_process,
    "memory_update_prompt": _h_update_prompt,
    "memory_list": _h_list,
    "memory_get": _h_get,
    "memory_find": _h_find,
    "memory_explain": _h_explain,
    "memory_update": _h_update,
    "memory_delete": _h_delete,
    "memory_history": _h_history,
    "memory_undo": _h_undo,
    "memory_clear": _h_clear,
    "memory_reset_sent": _h_reset_sent,
}

# ── public API ──────────────────────────────────────────────────────────────
_store_instance: Optional[MemoryStore] = None

def configure(db_path: str):
    global _store_instance
    if _store_instance is not None:
        _store_instance.close()
    _store_instance = MemoryStore(db_path)

def _default_store() -> MemoryStore:
    global _store_instance
    if _store_instance is None:
        _store_instance = MemoryStore()
    return _store_instance

def reset_sent() -> Dict[str, Any]:
    return _default_store().reset_sent()

def sent_ids() -> set:
    return _default_store().sent_ids()

def call_tool(name: str, arguments: Any = None, *, store: Optional[MemoryStore] = None,
              **kwargs: Any) -> Dict[str, Any]:
    args = arguments if arguments is not None else kwargs
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"arguments is not valid JSON: {exc}"}
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be an object / JSON object"}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown tool '{name}'",
                "available": sorted(TOOL_HANDLERS)}
    try:
        result = handler(store or _default_store(), args)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("ok", True)
        return result
    except Exception as exc:
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None):
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        print("memory_system v4.0 (interactive mode)")
        print("Type a message to retrieve memories.")
        print("Commands:")
        print("  /reset, /reset_sent -> reset sent memories tracking")
        print("  /list               -> list all memories")
        print("  /clear              -> clear all memories")
        store = _default_store()
        while True:
            try:
                user_msg = input(">> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("Exiting.")
                break
            if not user_msg:
                continue
            if user_msg.lower() in ("exit", "quit", "q"):
                break
            if user_msg.lower() in ("/reset", "/reset_sent", "reset"):
                store.reset_sent()
                print("  [Sent memories reset - all memories can be sent again]")
                continue
            if user_msg.lower() in ("/list", "list"):
                recs = store._all_records()
                if not recs:
                    print("  [No memories stored]")
                else:
                    for rec in recs:
                        trigs = ",".join(rec.triggers) if rec.triggers else "(none)"
                        print(f"  [{rec.id}] {rec.text}  | triggers: {trigs}")
                    print()
                continue
            if user_msg.lower() in ("/clear", "clear"):
                store.clear()
                print("  [All memories cleared]")
                continue

            # Standard retrieval: do not return already sent, mark new hits as sent
            hits, hidden = store.retrieve_hits(user_msg, include_sent=False, mark_sent=True)
            rendered = format_hits(hits)
            if not rendered:
                if hidden:
                    print(f"  [NOTHING - {hidden} matching memor{'y' if hidden == 1 else 'ies'} already sent]")
                else:
                    print("  [NOTHING - no relevant memory]")
            else:
                print(rendered)
                print()
        return 0

    cmd = args[0].lower().replace("-", "_")
    rest = args[1:]
    store = _default_store()

    if cmd == "save" and rest:
        text = rest[0]
        triggers = rest[1].split(",") if len(rest) > 1 else None
        r = store.save(text, triggers=triggers, source="cli")
        print(json.dumps(r, indent=2))
    elif cmd == "retrieve" and rest:
        hits, _ = store.retrieve_hits(rest[0], include_sent=True, mark_sent=False)
        print(format_hits(hits))
    elif cmd == "list":
        for rec in store._all_records():
            trigs = ",".join(rec.triggers) if rec.triggers else "(none)"
            print(f"  [{rec.id}] {rec.text}  | triggers: {trigs}")
    elif cmd == "get" and rest:
        print(json.dumps(store.get(int(rest[0])), indent=2))
    elif cmd == "find" and rest:
        print(json.dumps(store.find(rest[0]), indent=2))
    elif cmd == "delete" and rest:
        print(json.dumps(store.delete(rest[0]), indent=2))
    elif cmd == "update" and len(rest) >= 2:
        print(json.dumps(store.update(int(rest[0]), rest[1]), indent=2))
    elif cmd == "explain" and rest:
        print(json.dumps(store.explain(rest[0]), indent=2))
    elif cmd == "history":
        print(json.dumps(store.history(), indent=2))
    elif cmd == "undo":
        print(json.dumps(store.undo_last(), indent=2))
    elif cmd == "clear":
        print(json.dumps(store.clear(), indent=2))
    elif cmd in ("reset_sent", "reset"):
        print(json.dumps(store.reset_sent(), indent=2))
    else:
        print(f"Unknown command or missing args: {cmd}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())