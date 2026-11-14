
"""
sentence_memory_retriever.py
============================

A deterministic, **no-LLM**, no-network memory layer for AI agents.

WHAT IT DOES
------------
1. Takes a raw user prompt and returns **ALL** memories that are genuinely
   relevant to it (with their IDs). Unrelated prompts return NOTHING.
2. Can read a prompt and *supersede* a stale, single-valued fact
   ("I'm playing Valheim now" -> "I'm playing Crimson Desert right now")
   by OVERWRITING the overlapping memory in place. Only for explicit,
   first-person, present-tense state changes on the same relation.
3. Exposes every operation as a small, JSON-in / JSON-out tool call so an
   AI agent can save / retrieve / update / delete memories.
4. Understands BOTH first-person ("my name is X") and agent-written
   third-person ("user's name is X") memories, and detects "what do you know
   about me?" by intent rather than an exact string (so lead-in words and
   paraphrases work).
5. Matches SIMILAR WORDS generally, not one hardcoded example: "gym",
   "workout", "lifting", "fitness" and "training" reach the same memory;
   "goal", "motivation", "ambition" and "what do i want to do" reach the
   same memories. Uses a small curated controlled vocabulary, phrase/stem
   normalization, conservative typo correction and STRICT fallback gating.
6. Ships an interactive REPL (plus --selftest and --demo) so you can test
   all of it yourself, including *why* a memory was or wasn't returned.

DESIGN (why there are no false triggers)
---------------------------------------
* Retrieval is precision-first, not recall-first. A memory is returned only
  if the query contains a **distinctive shared anchor** (an IDF-rare,
  non-generic word that really occurs in that memory), or the prompt is a
  strictly-matched first-person question about a specific attribute of the
  user ("what's my name?", "what game am I playing?").
* v3.4 -- SENT-DEDUP: memory_retrieve / memory_process_prompt remember which
  IDs they already returned (an in-memory set) and never send the same
  memory to the agent twice; the response reports how many were held back
  ('sent_hidden'). reset_sent() clears the tracker,
  retrieve(..., include_sent=True) bypasses it, and superseding or deleting
  a memory un-marks it (an UPDATED fact is sent again). v3.6: meta answers
  are dedup'd like every other answer.
* v3.5 -- USER-SCOPED META + SLASH-ONLY COMMANDS: identity queries ("who am
  i?", "what do you know about me?") answer with the USER's own memories
  only -- never the whole database. Notes about other subjects ("pizza is
  great with friends", "he is playing valheim", "the protest is on friday")
  stay out of meta answers; they are still retrievable by topic, and
  /list / memory_list still show everything. Bare-word commands ("list",
  "memories", "exit") are gone from the REPL: slash commands are the only
  commands, and bare words are just normal queries.
* v3.6 -- STRICT ALL-OR-NOTHING DEDUP + DEFAULT DB LOCATION: no meta
  exemption anymore -- "who am i" answers are dedup'd like everything else.
  Dedup is all-or-nothing: if an answer would repeat even ONE already-shown
  memory, the WHOLE answer is suppressed ([]) and nothing new is marked
  sent (fresh memories stay sendable after reset_sent()). The default
  database is now <script dir>/memory/Memory.db (folder created on demand).
* v3.7 -- FALSE-INJECTION FIXES (root causes audited from a live agent
  session): (1) the raise-family (raise/raises/raised/raising) is GENERIC
  now -- a construction verb ("raise an earth wall in Valheim") can never
  anchor the "raised concerns about rainfall" memory; (2) money_finance no
  longer tags bare "raise" (only unambiguous phrases: "pay raise",
  "pay rise", "raise funds"), and concept tags are recomputed from the
  text at load time, so an existing database heals itself; (3) the typo
  corrector never rewrites real conversational verbs anymore ("sending"
  used to become "spending" and drag finance memories into messenger
  requests).
* v3.8 -- REVERTED the harness-scaffolding stripper briefly added in v3.7:
  the CALLER sanitises assembled turns (SYSTEM:/YOU: blocks) before calling
  the retriever, so the retriever scores EXACTLY the string it is given,
  like every version before v3.7. Fixes (1)-(3) above are kept.
* v3.3 -- "user"-reference fixes: the token "user" is the SUBJECT of every
  third-person memory, so it has near-zero IDF and must never open a topic
  ("what's user favourite thing?" used to return the WHOLE library at 0.70
  via topic_anchor:user). It is now stripped from query content like a
  corpus-specific stopword, while "user"-phrased lookups gained first-class
  routes: the interests/preferences aspects accept "what does the user
  like?", "what's the user's favourite thing?", etc., and strict agent-facing
  meta requests ("tell me about the user") still dump the profile on purpose.
* v3.2 -- CONVERSATIONAL RETRIEVAL: the retriever sits between a user and an
  AI agent, so plain first-person STATEMENTS ("I am going to workout now")
  now also surface related memories, not just questions. Precision is kept
  by matching on a CURATED, CONTEXT-AWARE thesaurus: strong (unambiguous)
  query words match any form of the same concept; ambiguous weak words only
  count in clearly verbal usage ("gonna go train" = training, "the train"
  = transport); generic weak words ("like", "play", "food") can never open
  a topic alone; a strong concept in the query suppresses weak coincidental
  ones; instruments/sports suppress the gaming concept ("playing guitar" is
  music, not a game). The lexicon covers ~28 life-domain families (training,
  family, work, education, tech, food, sleep, health, mood, money, travel,
  transport, home, pets, sports, art, music, movies, books, weather,
  romance, social, language, ...) -- extend with add_concept().
* v3.2 -- relation fixes: studying-for-an-EXAM and learning-a-SKILL are now
  separate relations (no more silent overwrite); "use a <phone|laptop|car>"
  writes possession.* (so "which phone do I use?" finds it); "going to X"
  only stores a school fact when X actually is a school; "what do I want
  to do with my life?" is an aspiration question, not a full profile dump.
* Generic relation words (like, play, work, game, job...) can NEVER be an
  anchor. A query whose only overlap is "like" returns nothing.
* Words that do not exist anywhere in memory contribute zero weight.
* Content-generation commands ("write a poem about X") are suppressed
  unless the prompt refers to the user's own memory.
* There is no arbitrary top-K and no relative-margin cut: every memory that
  passes the gate is returned, sorted by score.
* Similar-word matching is a CURATED, CLOSED thesaurus used as a strict
  fallback (user-directed, no exact anchor/aspect/intent answer, no
  scope-shifting word on a LOOKUP). No WordNet/all-senses expansion, no
  embeddings, no relaxed score floor -- global expansion is what causes
  topic drift and false triggers.

UPDATES / SUPERSESSION
----------------------
Facts are stored as (subject=user, relation=state_key, object=value), e.g.
("user", "activity.game", "Valheim"). A new assertion with the SAME relation
and a different value retires the old one deterministically -- no similarity
threshold, no model call. This is the same "deterministic supersession over a
temporal ledger" idea used by temporal-validity memory research, minus the
database machinery. Updates are guarded by: first person + affirmative +
present state + current/change cue + exactly one same-relation candidate +
not a question. Multi-valued things (plain likes, random notes) are NEVER
auto-overwritten. Every write is logged to `history` and can be undone.

GUARANTEES
----------
* Python standard library ONLY. No LLM, no embeddings, no HTTP, no network.
* SQLite for persistence, one file (default: memory/Memory.db, next to this
  script; created on demand).
* Existing Memory.db files written by older versions are migrated in place.

QUICK START
-----------
    python sentence_memory_retriever.py                 # interactive REPL
    python sentence_memory_retriever.py --selftest     # built-in tests
    python sentence_memory_retriever.py --demo         # Valheim -> Crimson Desert
    python sentence_memory_retriever.py --tools        # print tool schemas (JSON)
    python sentence_memory_retriever.py --tool memory_save \
        --args '{"text": "I am playing Valheim now"}'

FROM AN AGENT (in-process)
--------------------------
    from sentence_memory_retriever import call_tool, TOOL_SPECS
    call_tool("memory_save",    {"text": "I live in Rajshahi"})
    result = call_tool("memory_retrieve", {"prompt": user_prompt})
    for m in result["memories"]:
        print(m["id"], m["text"])
    call_tool("memory_delete", {"target": "protest"})
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

__all__ = [
    "MemoryStore", "SearchHit", "MemoryRecord", "StateFact",
    "call_tool", "TOOL_SPECS", "TOOL_HANDLERS", "tool_specs_json",
    "format_hits", "run_repl", "run_selftest", "run_demo",
    "normalize_text", "tokenize", "stem", "detect_slots",
    "extract_state_fact", "is_meta_query", "reset_sent", "sent_ids",
    "CONCEPT_LEXICON", "add_concept", "remove_concept", "concept_profile",
    "phrase_keys", "compact", "correct_typo", "normalize_query",
]

VERSION = "3.8"
# v3.6: the default database lives NEXT TO THIS SCRIPT, in a memory/ folder
# (created on demand by MemoryStore). Override with --db PATH or the
# MEMORY_DB_PATH environment variable.
DEFAULT_DB = str(Path(__file__).resolve().parent / "memory" / "Memory.db")


# ===========================================================================
# 1. Text primitives
# ===========================================================================

def normalize_text(text: str) -> str:
    """NFKD-normalise, collapse whitespace, keep punctuation intact."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


# ASCII alnum plus any non-ASCII run. Using \w alone would SPLIT Bengali
# ('বাংলাদেশ' -> ব, ল, দ, শ) because vowel signs are combining marks.
# This keeps a whole non-ASCII word intact while still splitting on hyphens,
# apostrophes and ASCII punctuation.
_WORD_RE = re.compile(r"[A-Za-z0-9\u0080-\uFFFF]+")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9\u0080-\uFFFF]+")


def tokenize(text: str) -> List[str]:
    """Lowercase word tokens. Apostrophes split ('I'm' -> i, m)."""
    return [m.group(0).lower() for m in _WORD_RE.finditer(normalize_text(text) or "")]


# Short / irregular verbs whose "-ing" stem is too short for the generic
# suffix rules. Keeps "use/using", "go/going", "make/making" etc. aligned.
SHORT_IRREGULAR: Dict[str, str] = {
    "using": "use", "used": "use", "uses": "use", "useful": "us",
    "going": "go", "gone": "go", "went": "go", "goes": "go",
    "doing": "do", "done": "do", "being": "be", "been": "be",
    "having": "hav", "has": "hav", "had": "hav",
    "seeing": "see", "saw": "see", "making": "mak", "made": "mak",
    "taking": "tak", "took": "tak", "giving": "giv", "gave": "giv",
    "saying": "say", "said": "say", "telling": "tell", "told": "tell",
    "singing": "sing", "sang": "sing", "sung": "sing",
    "getting": "get", "got": "get", "sitting": "sit", "sat": "sit",
    "writing": "writ", "wrote": "writ", "eating": "eat", "ate": "eat",
    "putting": "put", "cutting": "cut", "setting": "set", "letting": "let",
    "running": "run", "ran": "run", "winning": "win", "won": "win",
    "coming": "com", "came": "com",
}


def stem(word: str) -> str:
    """
    Small, dependency-free morphological normaliser (Porter-flavoured).

    It is deliberately *not* a full linguistic stemmer; it only needs to make
    inflected/base forms meet: play/plays/played/playing -> play,
    like/likes/liked -> lik, game/games/gaming -> gam, protest/protesting ->
    protest, study/studies/studying -> study. Both the base form and the
    inflected form are reduced to the same shape, including silent-e removal.
    """
    w = word.lower().strip("'")
    if w in SHORT_IRREGULAR:
        return SHORT_IRREGULAR[w]
    if len(w) <= 3:
        return w
    for _ in range(4):
        before = w
        if len(w) > 5 and w.endswith("ies"):
            w = w[:-3] + "y"
            continue
        if len(w) > 5 and w.endswith("sses"):
            w = w[:-2]
            continue
        if len(w) > 5 and w.endswith("ing"):
            base = w[:-3]
            if len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "aeiou":
                base = base[:-1]                      # running -> run
            w = base if len(base) >= 3 else (base + "e" if base == "us" else base)
            continue
        if len(w) > 4 and w.endswith("ied"):
            w = w[:-3] + "y"                              # studied -> study, carried -> carry
            continue
        if len(w) > 4 and w.endswith("ed"):
            base = w[:-2]
            if len(base) >= 3 and base[-1] == base[-2] and base[-1] not in "aeiou":
                base = base[:-1]
            w = base if len(base) >= 3 else (base + "e" if base == "us" else base)
            continue
        if len(w) > 4 and w.endswith("es") and not w.endswith(("ses", "xes", "zes", "ches", "shes")):
            w = w[:-2]
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
            continue
        if w == before:
            break
    if len(w) > 3 and w.endswith("e") and not w.endswith("ee"):
        w = w[:-1]                                    # like/likes -> lik, game/games -> gam
    return w


def _bigrams(seq: Sequence[str]) -> Set[Tuple[str, str]]:
    return {(seq[i], seq[i + 1]) for i in range(len(seq) - 1)}


def split_into_sentences(text: str) -> List[str]:
    """Split on sentence punctuation / newlines; drop noise-only fragments."""
    cleaned = normalize_text(text)
    if not cleaned:
        return []
    raw = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
    out: List[str] = []
    for part in raw:
        part = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", part).strip()
        if len(part) > 2 and re.search(r"[A-Za-z0-9]", part):
            out.append(part)
    return out


# --- vocabularies ----------------------------------------------------------

# Function words: removed completely from both query and document content.
STOP_WORDS: Set[str] = set("""
a about above after again against all am an and any are as at be because been before being below
between both but by can cannot could did do does doing down during each few for from further had has
have having he her here hers herself him himself his how i if in into is it its itself let me more
most my myself no nor not of off on once only or other ought our ours ourselves out over own same
she should so some such than that the their theirs them themselves then there these they this those
through to too under until up very was we were what when where which while who whom why with would
you your yours yourself yourselves
s t m d ll ve re don didn isn wasn aren couldn wouldn shouldn won cannot could would should will
shall may might must us lets ok okay yeah yep nope yes nope hi hey hello thanks thank please sorry
bye hmm um uh well actually basically literally really just quite pretty maybe perhaps anyway also
too even ever yet soon here there etc per via onto upon within across around among toward towards
behind beyond beside inside outside near far another others same different various several
anything something everything nothing anyone someone everyone whatever else
""".split())

# Conversational / task-agnostic verbs and courtesies. These can never create
# a candidate memory. Domain words (play, work, like, game, protest...) are
# deliberately NOT here -- they stay as content so real queries can match.
FILLER_WORDS: Set[str] = STOP_WORDS | set("""
tell know remember recall remind think thought mean explain show list give want need help make
send sends sent sending deliver delivers delivered
speak talk ask answer question questions info information detail details topic subject stuff thing
things regard regarding regards about day today tonight yesterday tomorrow now currently right
moment present presently lately recent recently one two three first last next previous new old
good bad great nice fine sure correct wrong true false best better worse main bit lot kind sort
way ways part parts own self only always usually sometimes often never still already just quite
actually basically really very much many some any all both each either neither another other
""".split())

# Generic words: they may contribute to a score but can NEVER be the anchor
# that makes a memory "relevant" on their own. This is the main false-trigger
# killer ("do you like coffee?" must not trigger "I like pizza").
GENERIC_WORDS: Set[str] = set("""
like likes liked love loves loved enjoy enjoys prefer prefers favorite favourite hate hates
dislike dislikes play plays played playing work works worked working live lives lived living
have has had having want wants need needs use uses used using watch watches watched watching
read reads reading feel feels felt think thinks thought know knows knew go goes went going
make makes made get gets got take takes took see sees saw say says said tell remember recall
raise raises raised raising lower lowered lowering
start started stop stopped keep kept try tried come came run ran find found look looked show
call called name names named age old new job jobs career profession company employer city cities
country location address home hometown place places person people friend friends family
game games food movie movies film films music song songs book books color colour sport sports
phone laptop computer pet dog cat wife husband partner day days week weeks month months year
years time times thing things stuff one two three first last next good bad great nice really
very much many some any all always usually sometimes often never still also just actually maybe
bit lot kind sort way ways part parts today now currently moment present recent recently
player players user users me my i you we they he she it this that these those there here about
into from with without for of in on at to and or but what which who when where why how do does
did is am are was were can could would should will shall may might must please thanks hello
hey okay ok yeah yes no not more most other another same different own self only even quite
pretty well right left big small long short high low best better worse main current favourite
move moves moved moving visit visits visited visiting start begin started began change changed
open opened opens opening close closed closes closing explain explained explains describe
described define defined teach taught elaborate walk walkthrough aim aims aimed aiming
purpose purposes goal goals target targets
""".split())

GENERIC_STEMS: Set[str] = {stem(w) for w in GENERIC_WORDS}

# v3.3 -- "user" is the SUBJECT of every third-person memory ("user lives in
# ..."), so it occurs in ~every document and has near-zero IDF. A term that
# carries no discrimination must be treated as a corpus-specific stopword on
# the QUERY side: otherwise "what's user favourite thing?" matched every
# memory through the single shared token "user" (topic_anchor:user -> whole-
# library dump at 0.70). Queries phrased with "user" are still recognised as
# self-directed lookups via _USER_REF (aspects, meta patterns) -- only the
# matching/scoring path ignores the token.
SELF_REF_QUERY_TOKENS = {"user", "users"}
_USER_REF = re.compile(r"\b(?:the\s+)?users?\b", re.I)

# v3.3 -- "user" is the SUBJECT of every third-person memory ("user lives in
# ..."), so it occurs in ~every document and has near-zero IDF. A term that
# carries no discrimination must be treated as a corpus-specific stopword on
# the QUERY side: otherwise "what's user favourite thing?" matched every
# memory through the single shared token "user" (topic_anchor:user -> whole-
# library dump at 0.70). Queries phrased with "user" are still recognised as
# self-directed lookups via _USER_REF (aspects, meta patterns) -- only the
# matching/scoring path ignores the token.
SELF_REF_QUERY_TOKENS = {"user", "users"}
_USER_REF = re.compile(r"\b(?:the\s+)?users?\b", re.I)

# Words that mean "playing" is NOT about a game (guards against
# "I'm playing guitar" overwriting "I'm playing Valheim").
NON_GAME_OBJECTS = re.compile(
    r"\b(?:guitars?|pianos?|violins?|cellos?|drums?|flutes?|banjos?|saxophones?|trumpets?|"
    r"chess|cards|poker|football|cricket|basketball|tennis|soccer|hockey|baseball|golf|"
    r"rugby|volleyball|badminton|the\s+role\s+of|hide\s+and\s+seek)\b", re.I)

# Strict whole-prompt "dump everything about me" requests.
META_QUERY_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"^\s*what\s+do\s+you\s+know\s+about\s+me\s*[?\.!]*\s*$",
        r"^\s*what\s+do\s+you\s+remember\s+about\s+me\s*[?\.!]*\s*$",
        r"^\s*what\s+(?:information|info|data)\s+do\s+you\s+have\s+on\s+me\s*[?\.!]*\s*$",
        r"^\s*tell\s+me\s+about\s+myself\s*[?\.!]*\s*$",
        # v3.3: agent-facing full-dump phrasings about "the user". Kept STRICT
        # (anchored, no trailing noun) so "what do you know about the user's
        # exam?" stays a topic lookup.
        r"^\s*(?:please\s+)?tell\s+me\s+about\s+(?:the\s+)?user\s*[?\.!]*\s*$",
        r"^\s*(?:everything|all)\s+about\s+(?:the\s+)?user\s*[?\.!]*\s*$",
        r"^\s*who\s+is\s+(?:the\s+)?user\s*[?\.!]*\s*$",
        r"^\s*what\s+(?:do|can)\s+you\s+(?:know|tell\s+me)\s+about\s+(?:the\s+)?user\s*[?\.!]*\s*$",
        r"^\s*who\s+am\s+i\s*[?\.!]*\s*$",
        r"^\s*my\s+profile\s*[?\.!]*\s*$",
        r"^\s*(?:everything|all)\s+about\s+me\s*[?\.!]*\s*$",
        r"^\s*(?:list|show|dump|print|give\s+me)\s+(?:all\s+)?(?:of\s+)?(?:my\s+)?memor(?:y|ies)\s*[?\.!]*\s*$",
        r"^\s*(?:tell|show|give)\s+me\s+(?:all\s+)?(?:of\s+)?(?:my\s+)?memor(?:y|ies)\s*[?\.!]*\s*$",
        r"^\s*what\s+(?:memories|things)\s+do\s+you\s+have\s*(?:about\s+me)?\s*[?\.!]*\s*$",
        # v3.5: the BARE forms ("list", "show", "dump", "print", "memories",
        # "show all", "memories please") were REMOVED on purpose. A one-word
        # command is a SLASH command (/list in the REPL); a bare word typed
        # to the agent is just a normal query and must never open the meta
        # path. Explicit-object phrasings ("list my memories") stay meta.
    ]
]

# First-person self-attribute questions. Strictly anchored: no bare "like",
# no bare "job", no third-person prompts. These are the ONLY way to retrieve
# a memory with zero distinctive word overlap.
INTENT_RULES: List[Tuple[re.Pattern, List[str], str]] = [
    (re.compile(r"\b(?:what(?:'s|\s+is)\s+my\s+name|who\s+am\s+i|what\s+do\s+(?:you|people)\s+call\s+me|"
                r"remind\s+me\s+of\s+my\s+name|what\s+name\s+do\s+i\s+go\s+by)\b", re.I),
     ["identity.name"], "identity.name"),
    (re.compile(r"\b(?:how\s+old\s+am\s+i|what(?:'s|\s+is)\s+my\s+age|my\s+age)\b", re.I),
     ["identity.age"], "identity.age"),
    (re.compile(r"\b(?:when(?:'s|\s+is)\s+my\s+birthday|when\s+was\s+i\s+born|my\s+(?:birthday|bday|dob|date\s+of\s+birth))\b", re.I),
     ["identity.birthday"], "identity.birthday"),
    (re.compile(r"\b(?:where\s+do\s+i\s+live|where\s+am\s+i\s+living|where\s+did\s+i\s+move\s+to|"
                r"where\s+am\s+i\s+(?:based|from)|what(?:'s|\s+is)\s+my\s+(?:address|location|city|hometown|country|home))\b", re.I),
     ["identity.location"], "identity.location"),
    (re.compile(r"\b(?:what(?:'s|\s+is)\s+my\s+(?:job|profession|career|occupation)|"
                r"what\s+do\s+i\s+do\s+for\s+a\s+living|where\s+do\s+i\s+work|what\s+do\s+i\s+work\s+as)\b", re.I),
     ["identity.profession", "work.employer"], "identity.profession"),
    # NOTE: "what FOOD do i like?" is deliberately NOT here. Naming a category
    # must narrow the answer to that category via its own anchor, not dump
    # every like/dislike memory. Only the bare "what do i like" is broad.
    (re.compile(r"\bwhat\s+do\s+i\s+(?:like|love|enjoy)\b", re.I),
     ["preference.likes"], "preference.likes"),
    (re.compile(r"\bwhat\s+do\s+i\s+(?:dislike|hate)\b", re.I),
     ["preference.dislikes"], "preference.dislikes"),
    (re.compile(r"\b(?:what(?:'s|\s+is)\s+my\s+(?:favorite|favourite)|"
                r"my\s+(?:preferences|favorites|favourites|tastes))\b", re.I),
     ["preference"], "preference"),
    (re.compile(r"\b(?:what|which)\s+(?:game|games)\s+(?:am\s+i|do\s+i)\s+(?:currently\s+)?(?:playing|play)\b|"
                r"\bwhat\s+am\s+i\s+playing\b|\bwhat\s+do\s+i\s+play\b", re.I),
     ["activity.game", "preference.favorite_game"], "activity.game"),
    (re.compile(r"\b(?:what|which)\s+(?:book|books)\s+(?:am\s+i|do\s+i)\s+(?:reading|read)\b|\bwhat\s+am\s+i\s+reading\b", re.I),
     ["activity.book"], "activity.book"),
    (re.compile(r"\b(?:what|which)\s+(?:show|series|movie|film)\s+(?:am\s+i|do\s+i)\s+watching\b|\bwhat\s+am\s+i\s+watching\b", re.I),
     ["activity.show"], "activity.show"),
    # Split by verb: one broad rule made "what am i learning?" also return
    # music/project/tool memories. Each verb now maps to ONLY its own key.
    (re.compile(r"\bwhat\s+am\s+i\s+working\s+on\b", re.I),
     ["activity.project"], "activity.project"),
    (re.compile(r"\bwhat\s+am\s+i\s+studying\s+for\b|\bwhat\s+am\s+i\s+preparing\s+for\b", re.I),
     ["activity.study.exam"], "activity.study.exam"),
    (re.compile(r"\bwhat\s+am\s+i\s+(?:studying(?!\s+for)|learning)\b", re.I),
     ["activity.study.skill"], "activity.study.skill"),
    # family members have their own narrow lookups
    (re.compile(r"\b(?:how\s+(?:is|are|was)\s+my\s+|how'?s\s+my\s+|tell\s+me\s+about\s+my\s+|"
                r"remind\s+me\s+about\s+my\s+|what\s+about\s+my\s+|how\s+is\s+my\s+)"
                r"(?:mother|mom|mum|father|dad|brother|sister|son|daughter|grandmother|"
                r"grandma|grandfather|grandpa|uncle|aunt|cousin|family|parents)"
                r"(?:\s+(?:doing|these\s+days|now))?\b", re.I),
     ["family"], "family"),
    (re.compile(r"\bwhat\s+am\s+i\s+using\b", re.I),
     ["tech.tool", "possession.phone", "possession.laptop", "possession.car"],
     "tech.tool"),
    (re.compile(r"\bwhat\s+am\s+i\s+listening\s+to\b", re.I),
     ["activity.music"], "activity.music"),
    (re.compile(r"\b(?:what|which)\s+(?:company|employer)\s+do\s+i\s+work\s+(?:at|for)\b|\bwho\s+do\s+i\s+work\s+for\b", re.I),
     ["work.employer"], "work.employer"),
    (re.compile(r"\b(?:what(?:'s|\s+is)\s+my\s+(?:education|education\s+level|grade|class|level)|"
                r"what\s+(?:exam|exams|board)\s+am\s+i\s+(?:in|taking|preparing\s+for)|"
                r"am\s+i\s+(?:an?\s+)?(?:hsc|college|university|high\s+school)\s+(?:student|candidate))\b", re.I),
     ["education.level"], "education.level"),
    (re.compile(r"\b(?:where\s+(?:do|would)\s+i\s+want\s+to\s+(?:study|get\s+admitted)|"
                r"what(?:'s|\s+is)\s+my\s+(?:admission|university|study)\s+(?:goal|plan|target|aim)|"
                r"which\s+university\s+(?:do|would)\s+i\s+(?:want|like|plan))\b", re.I),
     ["education.admission_goal"], "education.admission_goal"),
    (re.compile(r"\b(?:when\s+did\s+i\s+(?:start|started|begin)\s+(?:gym|training|workout|lifting)|"
                r"how\s+long\s+have\s+i\s+been\s+(?:training|lifting|working\s+out)|"
                r"since\s+when\s+have\s+i\s+been\s+(?:training|lifting)|"
                r"what(?:'s|\s+is)\s+my\s+(?:gym|training|workout)\s+(?:history|routine))\b", re.I),
     ["activity.training"], "activity.training"),
    # Everyday "Jarvis" attributes. Each maps to a relation key that the
    # writer also derives from the memory text (see RELATION_KEYWORDS).
    (re.compile(r"\b(?:do\s+i\s+have\s+(?:a|an|any)\s+(?:pet|dog|cat)|"
                r"what\s+pet\s+do\s+i\s+have|do\s+i\s+own\s+(?:a|an)\s+(?:dog|cat))\b", re.I),
     ["pet.name"], "pet.name"),
    (re.compile(r"\b(?:who(?:'s|\s+is)\s+my\s+best\s+friend|"
                r"what(?:'s|\s+is)\s+my\s+best\s+friend'?s?\s+name)\b", re.I),
     ["relationship.friend"], "relationship.friend"),
    (re.compile(r"\b(?:what\s+education\s+level\s+am\s+i|what\s+level\s+am\s+i\s+in)\b", re.I),
     ["education.level"], "education.level"),
    (re.compile(r"\b(?:what\s+(?:unfair|protest|complaint)\w*\s+thing\s+happened|"
                r"what\s+happened\s+(?:with|about)\s+the\s+(?:protest|exam|complaint))\b", re.I),
     ["exam_grievance"], "exam_grievance"),
    (re.compile(r"\b(?:what\s+phone\s+do\s+i\s+have|which\s+phone\s+do\s+i\s+use)\b", re.I),
     ["possession.phone"], "possession.phone"),
    (re.compile(r"\b(?:what\s+laptop\s+do\s+i\s+(?:have|use)|"
                r"what(?:'s|\s+is)\s+my\s+laptop)\b", re.I),
     ["possession.laptop"], "possession.laptop"),
    (re.compile(r"\b(?:what\s+languages?\s+do\s+i\s+speak|"
                r"which\s+languages?\s+do\s+i\s+know|"
                r"what\s+languages?\s+am\s+i\s+fluent\s+in)\b", re.I),
     ["identity.languages"], "identity.languages"),
    (re.compile(r"\b(?:where\s+did\s+i\s+go\s+last\s+year|where\s+did\s+i\s+travel|"
                r"where\s+have\s+i\s+been)\b", re.I),
     ["travel.past"], "travel.past"),
]

# Dynamic, general "what's my <attribute>?" routing: no hand-written list can
# cover every relation, so map the noun onto the same keys used at write time.
WHAT_MY_RE = re.compile(
    r"\bwhat(?:'s|\s+is)\s+my\s+(?P<noun>[A-Za-z][A-Za-z' ]{0,24}?)\s*(?:\?|$|[,.!])", re.I)
# "do you remember my name?", "tell me about my dog", "is my birthday in May?"
RECALL_MY_RE = re.compile(
    r"\bmy\s+(?P<noun>[A-Za-z][A-Za-z' ]{0,24}?)\s*(?:\?|$|[,.!])", re.I)
LOOKUP_PREFIX = re.compile(
    r"^\s*(?:tell|show|list|remind|give|recall|dump|print|what|which|who|where|when|how|"
    r"do|does|did|am|is|are|was|were|can|could|would)\b", re.I)

# Content-generation commands: not memory lookups unless the user refers to
# their own memory. Suppressing these prevents "write a poem about protest"
# from surfacing a memory that merely happens to contain the word protest.
GENERATION_TASK = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?(?:write|compose|draft|generate|translate|summari[sz]e|"
    r"rewrite|proofread|paraphrase|code|program|debug|fix|solve|calculate|compute|"
    r"convert|create|build|implement|outline|format|spellcheck|sing|draw|paint|design|"
    r"explain|describe|define|teach|elaborate|walk\s+me\s+through|tell\s+me\s+how)\b", re.I)
SELF_REFERENCE = re.compile(
    r"\b(?:i|my|mine|myself|about\s+me|remember|memory|memories|you\s+know\s+about\s+me)\b", re.I)
SELF_MARKER = re.compile(
    r"\b(?:i|my|mine|myself|me|remember|recall|memory|memories|know\s+about\s+me)\b", re.I)
RECALL_CUE = re.compile(
    r"\b(?:remember|recall|remind|memor(?:y|ies)|you\s+know|know\s+about|"
    r"tell\s+me\s+about|what\s+do\s+you\s+know|do\s+you\s+know)\b", re.I)
# "give me three ideas...", "suggest a recipe..." -- content requests, not
# memory lookups, even when a shared noun happens to appear.
TASK_REQUEST = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?(?:give|suggest|recommend|provide|list|name|write|make|"
    r"create|draft|generate|come\s+up\s+with|plan|prepare|build|implement|translate|"
    r"summari[sz]e|compute|calculate|solve|debug|fix|convert|proofread|rewrite|compose|"
    r"code|program|draw|paint|design|sing|brainstorm|outline)\b", re.I)
# "how do I bake a cake?" is an instruction request, not a memory lookup --
# it must not surface food-preference memories just because "bake" is a
# food word. "how am i doing", "how's my X" stay open (not covered here).
HOW_TO_TASK = re.compile(
    r"^\s*how\s+(?:do|does|did|can|could|should|would|to)\b", re.I)
# Yes/no questions about the world ("is pizza a vegetable?") are not about
# the user's memory unless they contain a first-person marker or recall cue.
WORLD_QUESTION = re.compile(
    r"^\s*(?:is|are|was|were|do|does|did|can|could|will|would|has|have|should|might|must)\b", re.I)


def gate_reason(query: str) -> Optional[str]:
    """Return why a prompt must NOT trigger retrieval, or None to proceed."""
    q = prepare_query(query)
    if not q:
        return "empty_prompt"
    recall = bool(RECALL_CUE.search(q))
    # A bare possessive ("fix MY code") must NOT exempt a task from the gate:
    # "my" alone is not a memory request. Only an explicit recall cue does.
    if GENERATION_TASK.search(q) and not recall:
        return "content_generation_command"
    if HOW_TO_TASK.search(q) and not recall:
        return "how_to_instruction"
    if TASK_REQUEST.search(q) and not recall:
        return "task_request_not_a_memory_lookup"
    if WORLD_QUESTION.search(q) and not SELF_MARKER.search(q) and not recall:
        return "world_question_no_self_reference"
    return None

# --- cues for state-change detection --------------------------------------

TIME_CUE = re.compile(
    r"\b(?:right\s+now|now|currently|at\s+the\s+moment|at\s+present|presently|these\s+days|"
    r"lately|as\s+of\s+(?:now|today)|from\s+now\s+on|this\s+week|this\s+month|this\s+year)\b", re.I)
CHANGE_CUE = re.compile(
    r"\b(?:started|switched|changed|moved|no\s+longer|not\s+anymore|anymore|any\s+more|"
    r"instead|replaced|quit|stopped|left|joined|new\s+job|updated|from\s+now\s+on|"
    r"these\s+days|lately|used\s+to|begin|began)\b", re.I)
QUESTION_START = re.compile(
    r"^\s*(?:what|who|whom|whose|where|when|why|how|do|does|did|is|am|are|was|were|"
    r"can|could|would|should|will|shall|may|might|have|has|had|which|any|isn't|aren't)\b", re.I)


# ===========================================================================
# 1b. LEXICAL NORMALIZATION  (curated thesaurus + typo tolerance, no LLM)
#
# Why this shape: a controlled vocabulary / thesaurus fixes the "vocabulary
# mismatch" problem (query says "gym", memory says "strength training"), but
# global automatic query expansion is well known to DESTROY precision through
# topic drift. So this is:
#   * a small, hand-curated, EDITABLE concept lexicon (synonym sets);
#   * used ONLY as a FALLBACK when exact lexical matching finds nothing;
#   * with strong/weak form weights so ambiguous words cannot trigger it;
#   * plus NFKD + stemming + split/join phrase keys ("work out" == "workout"),
#     contraction expansion ("whats" -> "what is") and Damerau-Levenshtein
#     typo correction against the CLOSED vocabulary (exact-first, threshold 1).
# No model, no embeddings, no external files, no network, stdlib only.
# ===========================================================================

CONTRACTIONS: Dict[str, str] = {
    # texting / casual speech ("i am gonna go train now")
    "gonna": "going to", "wanna": "want to", "gotta": "got to",
    "cuz": "because", "imo": "in my opinion",
    "whats": "what is", "whos": "who is", "whens": "when is", "wheres": "where is",
    "hows": "how is", "whys": "why is", "im": "i am", "ive": "i have",
    "dont": "do not", "doesnt": "does not", "didnt": "did not", "cant": "can not",
    "wont": "will not", "isnt": "is not", "arent": "are not", "wasnt": "was not",
    "werent": "were not", "couldnt": "could not", "wouldnt": "would not",
    "shouldnt": "should not", "lets": "let us", "havent": "have not",
    "what's": "what is", "who's": "who is", "when's": "when is",
    "where's": "where is", "how's": "how is", "why's": "why is",
    "i'm": "i am", "i've": "i have", "don't": "do not",
    "doesn't": "does not", "didn't": "did not", "can't": "can not",
    "isn't": "is not", "aren't": "are not", "it's": "it is",
    "that's": "that is", "there's": "there is", "let's": "let us",
}
# Longest keys first so "what's" wins over "whats" if both ever co-occur.
_CONTRACTION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in
                        sorted(CONTRACTIONS, key=len, reverse=True)) + r")\b", re.I)


def expand_contractions(text: str) -> str:
    return _CONTRACTION_RE.sub(lambda m: CONTRACTIONS[m.group(0).lower()], text)


def compact(text: str) -> str:
    """'work out', 'work-out', 'workout' -> 'workout' (Unicode-safe)."""
    return _NON_WORD_RE.sub("", text.lower())


def phrase_keys(text: str, max_n: int = 3) -> Set[str]:
    """
    All 1..3-gram keys of a text, in two forms: raw-compact and stem-compact.
    This automatically bridges hyphenation and word-boundary differences
    ("open-source" / "open source", "weightlifting" / "weight lifting").
    """
    words = tokenize(text)
    out: Set[str] = set()
    for n in range(1, min(max_n, len(words)) + 1):
        for i in range(len(words) - n + 1):
            chunk = words[i:i + n]
            out.add(compact(" ".join(chunk)))
            out.add("".join(stem(w) for w in chunk))
    return out


# --- the editable thesaurus ------------------------------------------------
# strong forms unambiguously denote the concept (weight 1.0). weak forms are
# ambiguous (0.4) and can NEVER trigger an expansion on their own.
# Add a new family with add_concept("name", strong=[...], weak=[...]).
CONCEPT_LEXICON: Dict[str, Dict[str, List[str]]] = {
    "physical_training": {
        "strong": ["gym", "gymnasium", "workout", "work out", "working out", "workouts",
                   "strength training", "strength-training", "weight training",
                   "weightlifting", "weight lifting", "lifting", "lift", "lifts",
                   "lift weights",
                   "resistance training", "bodybuilding", "body building",
                   "powerlifting", "calisthenics", "bench press", "deadlift",
                   "squats", "weights", "gains", "exercise", "exercising",
                   "exercises", "fitness", "workout routine", "physical training",
                   "dumbbell", "dumbbells", "barbell", "treadmill", "cardio",
                   "crossfit", "hiit", "push ups", "pushups", "pull ups",
                   "sit ups", "jog", "jogging", "marathon", "half marathon",
                   "leg day", "bulking", "cutting weight", "protein shake",
                   "pre workout", "post workout"],
        "weak": ["training", "train", "trained", "muscle", "muscles",
                 "reps", "sets", "sore", "flexibility"],
    },
    "aspiration_goal": {
        "strong": ["goal", "goals", "ambition", "dream", "dreams", "aspiration",
                   "motivation", "purpose", "want to do", "want to become",
                   "aim", "aims", "aim to", "aiming for", "aims to study",
                   "get admitted", "admission", "university admission",
                   "career goal", "career goals", "career plan", "career plans",
                   "life goal", "life goals", "life purpose", "future plan",
                   "future plans", "study at a public university",
                   "plan to study", "plans to study", "hope to study",
                   "intend to", "intends to", "plan to", "plans to",
                   "want to do with my life", "doing with my life",
                   "what i want to become", "where i see myself",
                   "5 year plan", "five year plan", "dream job"],
        "weak": ["aimed", "plan", "plans", "planned", "intend",
                 "wish", "hope", "future", "career", "want", "wants"],
    },
    "interests_likes": {
        "strong": ["interested in", "interests", "interest", "hobbies", "hobby",
                   "passion", "passionate about", "keen on", "fascinated by",
                   "curious about", "enjoys", "enjoy", "fan of"],
        "weak": ["like", "likes", "love", "loves", "prefer", "prefers",
                 "favorite", "favourite", "into"],
    },
    "exam_grievance": {
        "strong": ["protest", "protests", "protested", "objected", "objection",
                   "complaint", "complaints", "complained", "unfair", "injustice",
                   "treated unfairly", "incorrect questions", "insufficient exam time",
                   "difficult questions", "raised concerns", "concerns about",
                   "demonstration", "rally"],
        "weak": ["concern", "concerns", "reported", "criticized", "issue", "issues"],
    },
    "education_history": {
        "strong": ["hsc", "ssc", "gpa", "examination", "examinations", "board exam",
                   "higher mathematics", "studied", "completed ssc", "completed hsc",
                   "merit position", "udvash", "batch", "subjects", "result", "results",
                   "semester", "tuition", "tutorial", "revision", "syllabus",
                   "homework", "assignment", "assignments", "coursework",
                   "lecture", "lectures", "textbook", "scholarship",
                   "school", "college", "university", "academy", "campus",
                   "classroom", "kindergarten", "phd", "masters", "master's",
                   "bachelor", "bachelor's", "undergraduate", "postgraduate",
                   "diploma", "degree", "graduation", "viva", "midterm",
                   "study abroad", "group study", "night before the exam"],
        "weak": ["exam", "exams", "grade", "grades", "class", "classes",
                 "test", "tests", "subject", "student", "study", "studies",
                 "studying", "teacher", "physics", "chemistry", "biology",
                 "mathematics", "math", "department", "faculty", "lesson",
                 "lessons", "quiz", "quizzes", "science", "literature",
                 "history", "geography", "economics"],
    },
    "computing_interest": {
        "strong": ["computer science", "cse", "software engineering",
                   "artificial intelligence", "autonomous ai agents", "open-source",
                   "open source", "computer hardware", "pc gaming",
                   "machine learning", "deep learning", "programming", "coding",
                   "new technologies", "programmer", "coder", "developer",
                   "computer", "laptop", "pc", "macbook", "smartphone",
                   "phone", "mobile phone", "cell phone", "tablet", "ipad",
                   "console", "keyboard", "monitor", "gpu", "cpu", "ssd",
                   "hard drive", "motherboard", "hardware", "software",
                   "operating system", "windows", "linux", "macos", "ubuntu",
                   "browser", "chrome", "firefox", "wifi", "internet",
                   "website", "terminal", "command line", "git", "github",
                   "gitlab", "python", "javascript", "typescript", "java",
                   "react", "node", "sql", "database", "api", "devops",
                   "algorithm", "algorithms", "data structures", "vscode",
                   "docker", "server", "backend", "frontend", "full stack",
                   "full-stack", "debugging", "startup"],
        "weak": ["ai", "agents", "autonomous", "technology", "technologies",
                 "software", "hardware", "engineering", "computer", "tech",
                 "device", "devices", "gadget", "gadgets", "app", "apps",
                 "battery", "screen", "download", "install", "update",
                 "digital", "online", "automated"],
    },
    "gaming_preference": {
        "strong": ["exploration-focused games", "competitive multiplayer",
                   "multiplayer games", "pc gaming", "single-player",
                   "story-driven games", "video game", "video games",
                   "console gaming", "rpg", "mmorpg", "fps", "shooter",
                   "shooters", "moba", "esports", "speedrun", "speedrunning",
                   "ranked", "matchmaking", "boss fight", "side quest",
                   "main quest", "quest", "gamer", "gaming", "game",
                   "games", "steam", "twitch", "save file"],
        "weak": ["play", "plays", "playing", "played", "session"],
    },
    "location_home": {
        # A place NAME alone is not "where the user lives": "user went to
        # Dhaka last year" is a trip, not a residence. Only relational phrases
        # ("lives in", "based in", "hometown") are strong.
        "strong": ["lives in", "live in", "living in", "located in", "based in",
                   "reside", "resides", "residing", "hometown",
                   "country", "city", "reside in", "resides in", "moved to",
                   "relocated to", "settled in", "moved house"],
        "weak": ["from", "place", "home", "bangladesh", "dhaka", "rajshahi",
                 "moved", "moving", "relocate", "relocated", "relocating",
                 "settled", "settle", "native", "district"],
    },
    # ---- new v3.2 families: broad life domains ---------------------------
    "family": {
        "strong": ["family", "mother", "mom", "mum", "mama", "father", "dad",
                   "papa", "daddy", "parents", "parent", "brother", "brothers",
                   "sister", "sisters", "sibling", "siblings", "son", "sons",
                   "daughter", "daughters", "grandmother", "grandma", "nana",
                   "grandfather", "grandpa", "granddad", "grandparents",
                   "grandson", "granddaughter", "uncle", "aunt", "auntie",
                   "cousin", "cousins", "nephew", "niece", "in-laws",
                   "in laws", "family member", "family members", "relatives",
                   "family dinner", "family trip", "back home",
                   "hometown family"],
        "weak": ["relative", "relatively"],
    },
    "romance": {
        "strong": ["girlfriend", "boyfriend", "fiance", "fiancee", "wife",
                   "husband", "spouse", "partner", "marriage", "married",
                   "marry", "wedding", "weddings", "engaged", "engagement",
                   "dating", "relationship", "relationships", "breakup",
                   "broke up", "crush", "anniversary", "honeymoon",
                   "valentine", "proposal", "proposed", "love life"],
        "weak": ["romance", "romantic", "single", "date", "dates",
                 "tinder", "ex", "flirting"],
    },
    "work_career": {
        "strong": ["career", "office", "workplace", "employer", "employee",
                   "colleague", "colleagues", "coworker", "coworkers",
                   "co-worker", "co-workers", "boss", "manager", "manager",
                   "promotion", "promoted", "demoted", "overtime",
                   "freelance", "freelancing", "freelancer", "unemployed",
                   "unemployment", "retired", "retirement", "resigned",
                   "resignation", "fired", "laid off", "hired", "hiring",
                   "job hunting", "interview", "interviews", "internship",
                   "intern", "workload", "deadline"],
        "weak": ["work", "works", "working", "worked", "job", "jobs",
                 "profession", "professional", "occupation", "salary",
                 "shift", "shifts", "client", "clients", "meeting",
                 "boss's", "startup"],
    },
    "food_eating": {
        "strong": ["breakfast", "lunch", "dinner", "supper", "brunch",
                   "snack", "snacks", "meal", "meals", "meal prep",
                   "cooking", "cook", "cooked", "cuisine", "restaurant",
                   "restaurants", "cafe", "diner", "dining", "takeout",
                   "takeaway", "street food", "fast food", "home cooking",
                   "baking", "bake", "diet", "dieting", "vegan",
                   "vegetarian", "keto", "fasting", "hungry", "starving",
                   "dessert", "desserts", "sweets", "sweet tooth",
                   "chocolate", "coffee", "tea", "juice", "smoothie",
                   "pizza", "burger", "burgers", "sandwich", "pasta",
                   "noodles", "ramen", "sushi", "curry", "biryani",
                   "biriyani", "rice", "beef", "mutton", "kebab", "kebabs",
                   "spicy food", "recipe", "recipes"],
        "weak": ["food", "foods", "eat", "eats", "eating", "ate",
                 "appetite", "taste", "tasty", "delicious", "flavor",
                 "flavour", "leftovers", "grocery", "groceries",
                 "vegetables", "fruit", "fruits", "cook"],
    },
    "sleep_rest": {
        "strong": ["sleep", "sleeps", "slept", "asleep", "sleepless",
                   "insomnia", "bedtime", "nap", "naps", "napping",
                   "oversleeping", "overslept", "nightmare", "nightmares",
                   "alarm", "alarm clock", "night owl", "early bird",
                   "jet lag", "jetlag", "sleep schedule"],
        "weak": ["tired", "sleepy", "exhausted", "fatigue", "fatigued",
                 "rest", "rests", "resting", "doze", "dozing", "drowsy",
                 "yawn", "yawning", "bed"],
    },
    "health_medical": {
        "strong": ["health", "sick", "sickness", "illness", "ill", "doctor",
                   "doctors", "physician", "hospital", "hospitals", "clinic",
                   "dentist", "dental", "medicine", "medicines", "medication",
                   "medications", "prescription", "prescriptions", "pharmacy",
                   "fever", "headache", "headaches", "migraine", "migraines",
                   "cough", "coughing", "sore throat", "flu", "influenza",
                   "allergy", "allergies", "allergic", "injury", "injuries",
                   "injured", "wound", "pain", "therapy", "physiotherapy",
                   "checkup", "check-up", "check up", "vaccination", "vaccine",
                   "vaccinated", "surgery", "operation", "blood pressure",
                   "diabetes", "diabetic", "asthma", "cholesterol",
                   "mental health", "depression", "depressed", "anxiety",
                   "anxious", "panic attack", "counseling", "therapist",
                   "ambulance", "emergency", "x-ray", "blood test",
                   "symptoms", "diagnosis", "diagnosed", "treatment",
                   "treatments", "vitamin", "vitamins", "supplement",
                   "supplements", "eyesight", "glasses", "back pain",
                   "knee pain", "toothache", "weight loss"],
        "weak": ["healthy", "unwell", "dizzy", "nausea", "nauseous",
                 "wellness", "medical", "recovering", "recovery",
                 "recovered", "sneezing", "runny nose", "hydration",
                 "hydrated", "physically fit"],
    },
    "mood_emotion": {
        "strong": ["happy", "happiness", "sad", "sadness", "unhappy",
                   "angry", "anger", "frustrated", "frustration", "annoyed",
                   "annoying", "upset", "excited", "excitement", "thrilled",
                   "bored", "boredom", "boring", "lonely", "loneliness",
                   "grateful", "gratitude", "thankful", "calm", "relaxed",
                   "peaceful", "stressed", "mood", "moods", "moody",
                   "emotional", "overwhelmed", "burnt out", "burnout",
                   "burned out", "nostalgic", "nostalgia", "homesick",
                   "jealous", "envious", "proud", "pride", "ashamed",
                   "embarrassed", "guilty", "hopeful", "pessimistic",
                   "optimistic", "confident", "insecure", "shy", "grief",
                   "grieving", "heartbroken", "cheerful", "joyful",
                   "miserable", "good mood", "bad mood", "terrible mood"],
        "weak": ["feeling", "feelings", "emotions", "cry", "crying",
                 "cried", "tears", "smile", "smiling", "laugh",
                 "laughing", "laughed", "vibe", "vibes", "moody"],
    },
    "money_finance": {
        "strong": ["money", "income", "salary", "wages", "wage",
                   "paycheck", "budget", "budgeting", "savings",
                   "expenses", "expense", "rent", "bills", "bill", "debt",
                   "debts", "loan", "loans", "student loan", "bank",
                   "banking", "bank account", "investment", "investments",
                   "investing", "stock market", "stocks", "shares", "crypto",
                   "cryptocurrency", "bitcoin", "taxes", "tax", "broke",
                   "expensive", "cheap", "afford", "affordable",
                   "subscription", "subscriptions", "fee", "fees", "refund",
                   "discount", "discounts", "allowance", "pocket money",
                   "installments", "installment", "emi", "down payment",
                   "mortgage", "insurance", "cash", "wallet", "atm",
                   "transfer", "provident fund", "pay raise", "pay rise",
                   "raise funds", "fundraiser"],
        "weak": ["pay", "paid", "paying", "payment", "payments",
                 "purchase", "purchasing", "bought", "spend", "spending",
                 "spent", "rich", "poor", "wealth", "fortune", "save",
                 "saving"],
    },
    "travel": {
        "strong": ["travel", "travels", "travelling", "traveling",
                   "travelled", "traveled", "trip", "trips", "journey",
                   "journeys", "vacation", "vacations", "holiday",
                   "holidays", "flight", "flights", "flying", "fly",
                   "airport", "airports", "airplane", "plane", "visa",
                   "passport", "boarding pass", "luggage", "suitcase",
                   "backpacking", "backpacker", "tourist", "tourism",
                   "tour", "tours", "abroad", "sightseeing", "road trip",
                   "roadtrip", "hotel", "hotels", "hostel", "hostels",
                   "airbnb", "resort", "cruise", "departure", "arrival",
                   "layover", "connecting flight", "immigration",
                   "customs", "souvenir", "souvenirs", "beach", "beaches",
                   "getaway", "exploring"],
        "weak": ["visit", "visits", "visiting", "visited", "explore",
                 "trip planning", "guide", "itinerary"],
    },
    "transport_commute": {
        "strong": ["commute", "commutes", "commuting", "commuter", "bus",
                   "buses", "metro", "subway", "train station", "railway",
                   "station", "bicycle", "bike", "bikes", "motorcycle",
                   "motorbike", "scooter", "rickshaw", "cng", "taxi",
                   "taxis", "cab", "cabs", "uber", "pathao", "carpool",
                   "traffic", "traffic jam", "fuel", "petrol", "gasoline",
                   "diesel", "parking", "highway", "route", "routes",
                   "ferry", "boat", "garage", "driving license",
                   "driving licence", "mileage", "engine", "tyre", "tyres",
                   "brake", "brakes", "helmet", "flat tire", "punctured",
                   "driving test"],
        "weak": ["car", "cars", "drive", "drives", "drove", "driver",
                 "vehicle", "vehicles", "ride", "rides", "riding",
                 "driving", "road", "roads"],
    },
    # NOTE: bare "tire/tires" deliberately NOT here: stem("tired") == "tire",
    # so "i'm so tired" would look like a flat-tire question. "tyre/tyres"
    # (and the phrase "flat tire") are safe.,
    "home_living": {
        "strong": ["apartment", "flat", "flats", "house", "houses",
                   "duplex", "condo", "dorm", "dormitory", "landlord",
                   "landlady", "tenant", "roommate", "roommates", "flatmate",
                   "flatmates", "furniture", "sofa", "couch", "table",
                   "chair", "wardrobe", "curtain", "curtains", "kitchen",
                   "bedroom", "bathroom", "balcony", "garden", "backyard",
                   "lawn", "cleaning", "cleaned", "laundry", "chores",
                   "dishes", "dishwashing", "vacuum", "sweeping", "mopping",
                   "dusting", "tidying", "neighbor", "neighbour",
                   "neighbors", "neighbours", "neighborhood",
                   "neighbourhood", "utilities", "decorating", "renovation",
                   "renovating", "plumbing", "leaking", "leak", "roof",
                   "utility bill", "electricity bill", "water bill",
                   "internet bill", "moving house", "new place"],
        "weak": ["home", "homes", "household", "housework", "chore",
                 "moved", "moving", "relocate", "relocated", "relocating",
                 "furnished", "unfurnished", "utility", "utilities bill"],
    },
    "social_friends": {
        "strong": ["friend", "friends", "friendship", "friendships",
                   "best friend", "close friend", "buddy", "buddies",
                   "bestie", "bff", "pal", "pals", "mate", "mates",
                   "hangout", "hang out", "hanging out", "party", "parties",
                   "meetup", "meet up", "meeting up", "gathering", "reunion",
                   "catch up", "catching up", "socializing", "socialising",
                   "social life", "guest", "guests", "hosting", "invite",
                   "invited", "invitation", "club", "clubs", "clubbing"],
        "weak": ["social", "socials", "meet", "meets", "met", "community",
                 "communities", "crowd", "group", "groups"],
    },
    "pets": {
        "strong": ["dog", "dogs", "puppy", "puppies", "cat", "cats",
                   "kitten", "kittens", "pet", "pets", "parrot",
                   "parrots", "hamster", "hamsters", "rabbit", "rabbits",
                   "bunny", "aquarium", "goldfish", "pigeon", "pigeons",
                   "vet", "veterinary", "dog park", "litter", "litter box",
                   "pet food", "breed", "breeds", "leash", "collar",
                   "walk the dog", "petting zoo"],
        "weak": ["animal", "animals", "adopt", "adopted", "adopting",
                 "rescue", "kennel", "paw", "paws", "tail", "fur",
                 "meow", "bark", "barking", "purring", "petted"],
    },
    "sports": {
        "strong": ["football", "soccer", "cricket", "basketball",
                   "tennis", "hockey", "baseball", "badminton", "rugby",
                   "volleyball", "golf", "swimming", "swimmer", "cycling",
                   "cyclist", "boxing", "wrestling", "martial arts",
                   "taekwondo", "karate", "judo", "kung fu", "mma", "ufc",
                   "athlete", "athletes", "athletics",
                   # v3.4: bare "match/matches" REMOVED -- "nothing matches",
                   # "match this pattern", "best match" are everyday non-sport
                   # uses and fired the concept at full strength. Unambiguous
                   # compounds carry the sports sense instead.
                   "football match", "cricket match", "tennis match",
                   "boxing match", "wrestling match", "match day", "test match",
                   "tournament", "tournaments", "championship", "league",
                   "leagues", "coach", "coaching",
                   "striker", "goalkeeper", "defender", "midfielder",
                   "referee", "fifa",
                   "world cup", "olympics", "medal", "medals", "trophy",
                   "trophies", "stadium", "skating", "skiing",
                   "snowboarding", "surfing", "rowing", "archery", "fencing"],
        "weak": ["sport", "sports", "player", "players", "fan", "fans",
                 "supporter", "goal", "kick", "won", "lost", "defeat",
                 "victory",
                 # v3.4 demoted: also used in work/exam/music contexts
                 # ("my team at work", "exam score", "sales pitch").
                 "team", "teams", "score", "scores", "scoring", "pitch"],
    },
    "music_audio": {
        "strong": ["music", "song", "songs", "album", "albums", "track",
                   "tracks", "playlist", "guitar", "electric guitar",
                   "acoustic guitar", "piano", "violin", "cello", "flute",
                   "drums", "saxophone", "trumpet", "banjo", "band",
                   "singing", "singer", "vocal", "vocals", "choir",
                   "concert", "concerts", "gig", "gigs", "genre", "lo-fi",
                   "edm", "rock", "metal", "jazz", "classical music",
                   "hip hop", "rap", "pop music", "country music", "spotify",
                   "headphones", "earbuds", "speaker", "speakers", "audio",
                   "sound system", "ukulele", "harmonica"],
        "weak": ["listen", "listens", "listening", "melody", "tune",
                 "lyrics", "chorus", "instrument", "instruments",
                 "acoustic", "busking"],
    },
    "movies_shows": {
        "strong": ["movie", "movies", "film", "films", "cinema",
                   "theater", "theatre", "series", "tv series", "tv show",
                   "anime", "episode", "episodes", "season finale",
                   "finale", "netflix", "hulu", "prime video", "hbo",
                   "disney", "documentary", "documentaries", "sitcom",
                   "drama", "thriller", "horror movie", "comedy", "romcom",
                   "action movie", "sci-fi", "scifi", "biopic", "trailer",
                   "trailers", "binge-watching", "binge watching", "binge",
                   "actor", "actress", "director", "screenplay", "oscar",
                   "oscars", "box office"],
        "weak": ["watch", "watches", "watching", "watched", "plot",
                 "storyline", "subtitles", "dubbed", "reality show",
                 "talk show", "korean drama", "kdrama"],
    },
    "books_reading": {
        "strong": ["novel", "novels", "novella", "fiction", "nonfiction",
                   "non-fiction", "manga", "comics", "comic",
                   "graphic novel", "memoir", "autobiography", "biography",
                   "ebook", "e-book", "kindle", "library", "libraries",
                   "bookstore", "literature", "poetry", "poem", "poems",
                   "author", "chapter", "chapters", "reading list",
                   "bestseller", "paperback", "hardcover", "short story",
                   "short stories", "storybook"],
        "weak": ["book", "books", "read", "reads", "reading", "novelist",
                 "bookshelf", "page", "pages", "bookworm", "bibliophile"],
    },
    "weather_nature": {
        "strong": ["weather", "rain", "rains", "rainy", "raining",
                   "monsoon", "drizzle", "drizzling", "snow",
                   "snows", "snowy", "snowing", "sunny", "sunshine",
                   "sunlight", "cloudy", "clouds", "overcast", "storm",
                   "storms", "thunderstorm", "thunder", "lightning", "fog",
                   "foggy", "mist", "misty", "humid", "humidity",
                   "heatwave", "heat", "breeze", "breezy", "windy", "winds",
                   "temperature", "degrees", "forecast", "autumn",
                   "summer", "winter", "scorching", "freezing", "chilly",
                   "rainstorm", "downpour", "flood", "floods", "flooded",
                   "river", "lake", "mountain", "mountains", "forest",
                   "nature", "sunset", "sunrise", "moon", "stars",
                   "gardening", "plants", "flowers", "trees"],
        "weak": ["season", "seasons", "seasonal", "spring", "outdoor",
                 "outside", "cold", "warm", "hot", "cool"],
    },
    "art_creative": {
        "strong": ["art", "arts", "artist", "drawing", "draw", "draws",
                   "sketch", "sketches", "sketching", "painting",
                   "paintings", "paint", "paints", "watercolor",
                   "watercolors", "acrylic", "canvas", "easel", "sculpture",
                   "sculpting", "clay", "pottery", "photography",
                   "photographer", "photo", "photos", "picture", "pictures",
                   "camera", "dslr", "photo editing", "design", "designer",
                   "graphic design", "illustration", "illustrator", "craft",
                   "crafts", "crafting", "diy", "knitting", "crochet",
                   "sewing", "embroidery", "origami", "calligraphy",
                   "creative", "creativity", "writing", "journal",
                   "journaling", "diary", "blog", "blogging", "vlog",
                   "vlogging", "storytelling"],
        "weak": ["artwork", "masterpiece", "doodle", "doodling",
                 "sketchbook", "handmade", "handcraft", "brush", "pencil",
                 "drawing tablet"],
    },
    "language_learning": {
        "strong": ["spanish", "french", "german", "japanese", "chinese",
                   "mandarin", "korean", "arabic", "hindi", "urdu",
                   "bengali", "italian", "russian", "portuguese", "dutch",
                   "swedish", "fluent", "bilingual", "multilingual",
                   "native speaker", "second language", "foreign language",
                   "language exchange", "duolingo"],
        "weak": ["language", "languages", "accent", "dialect",
                 "translation", "translate"],
    },
}

# Closed vocabulary used for typo correction (single-word forms + query words).
_QUERY_SIDE_WORDS: Set[str] = set("""
logic hey hello hi okay alright please thanks actually currently remember recall know
tell show list when where what which who how why started start began begin since long
doing doing work working out gym workout workouts lifting weights exercise training
train trained interested interest interests hobbies hobby enjoy enjoys prefer
prefers like likes love enjoys goal goals ambition dream dreams aspiration motivation
admitted admission university college study studied want wants plan plans aim aims
protest protests objected complaint complaints unfair concerns exam exams hsc ssc gpa
physics chemistry biology mathematics country city hometown bangladesh dhaka
technology technologies software hardware computer gaming games multiplayer exploration
open source tech cse favorite favourite
""".split())

CONCEPT_KEY_MAP: Dict[str, Tuple[str, float]] = {}
# Query-side subset of the map: strong forms + weak forms that are SPECIFIC
# enough to open a topic. A weak single word that is also a GENERIC word
# ("like", "play", "food", "work", "book"...) is NOT registered here, so
# it can never open a concept on the query side -- the number one source of
# false triggers if thesaurus matching gets loose.
CONCEPT_QUERY_KEY_MAP: Dict[str, Tuple[str, float]] = {}


def rebuild_concept_index() -> None:
    """
    Rebuild key -> (concept, weight) after editing CONCEPT_LEXICON.

    IMPORTANT: only WHOLE forms and multiword n-grams are registered. Registering
    unigrams taken out of a phrase would map "do"/"a"/"to" to a concept and make
    "tell me a joke" look like a goal question. Single words from multiword
    phrases (stopwords, short words) are deliberately dropped.
    """
    CONCEPT_KEY_MAP.clear()
    CONCEPT_QUERY_KEY_MAP.clear()
    for name, spec in CONCEPT_LEXICON.items():
        for weight, bucket in ((1.0, "strong"), (0.4, "weak")):
            for form in spec.get(bucket, []):
                words = tokenize(form)
                if not words:
                    continue
                single_word = len(words) == 1
                # A weak single word that is also generic ("like", "play",
                # "food", "work", "book", "watch" ...) may never OPEN a topic
                # on the query side. It still matches on the MEMORY side.
                weak_generic = (weight < 1.0 and single_word
                                and words[0] in GENERIC_WORDS)
                keys: Set[str] = set()
                if single_word:
                    w = words[0]
                    if len(w) >= 3 and w not in FILLER_WORDS:
                        keys.update({compact(w), stem(w)})
                else:
                    # whole form, both raw-compact and stem-compact
                    keys.add(compact(" ".join(words)))
                    keys.add("".join(stem(w) for w in words))
                    # meaningful 2/3-word sub-phrases. A chunk made ONLY of
                    # stopwords ("at a", "want to") is garbage -- registering
                    # "ata" once made "works at a small studio" look like a
                    # life-goal memory. Drop those.
                    for n in (2, 3):
                        for i in range(len(words) - n + 1):
                            chunk = words[i:i + n]
                            if all(w in FILLER_WORDS for w in chunk):
                                continue
                            keys.add(compact(" ".join(chunk)))
                            keys.add("".join(stem(w) for w in chunk))
                for key in keys:
                    if len(key) < 3 or key in FILLER_WORDS:
                        continue
                    prev = CONCEPT_KEY_MAP.get(key)
                    if prev is None or weight > prev[1]:
                        CONCEPT_KEY_MAP[key] = (name, weight)
                    if not weak_generic:
                        qprev = CONCEPT_QUERY_KEY_MAP.get(key)
                        if qprev is None or weight > qprev[1]:
                            CONCEPT_QUERY_KEY_MAP[key] = (name, weight)
    global _QUERY_VOCAB, _SAFE_WORDS
    _SAFE_WORDS = None                 # invalidate the "known good word" cache
    _QUERY_VOCAB = set(_QUERY_SIDE_WORDS)
    # Derive the typo-correction vocabulary from EVERY word of EVERY form,
    # not just whole one-word forms. Otherwise "strength" (which only ever
    # appears inside "strength training") would be missing and a typo like
    # "strngth" could not be repaired. This keeps the list DATA-driven: add a
    # phrase to CONCEPT_LEXICON and its words become typo-correctable for free.
    for spec in CONCEPT_LEXICON.values():
        for bucket in ("strong", "weak"):
            for form in spec.get(bucket, []):
                for w in tokenize(form):
                    if len(w) >= 5:
                        _QUERY_VOCAB.add(w)
                if len(form) >= 5:
                    _QUERY_VOCAB.add(compact(form))
    for key in list(CONCEPT_KEY_MAP):
        if len(key) >= 5:
            _QUERY_VOCAB.add(key)


_QUERY_VOCAB: Set[str] = set()
_SAFE_WORDS: Optional[Set[str]] = None
rebuild_concept_index()


def add_concept(name: str, strong: Sequence[str] = (), weak: Sequence[str] = ()) -> None:
    """Teach the retriever a new synonym family at runtime. No model involved."""
    CONCEPT_LEXICON[name] = {"strong": list(strong), "weak": list(weak)}
    rebuild_concept_index()


def remove_concept(name: str) -> None:
    CONCEPT_LEXICON.pop(name, None)
    rebuild_concept_index()


def concept_profile(text: str) -> Dict[str, float]:
    """Which concepts does this text mention, and how strongly? (memory side)"""
    profile: Dict[str, float] = {}
    for key in phrase_keys(text):
        hit = CONCEPT_KEY_MAP.get(key)
        if hit and hit[1] > profile.get(hit[0], 0.0):
            profile[hit[0]] = hit[1]
    return profile


# Verb/noun ambiguous words where the NOUN sense belongs to a DIFFERENT topic:
# "gonna go train" (verb -> physical training) vs "took the train" (noun ->
# transport). A weak ambiguous word only counts when used verbally, i.e. when
# the previous word is not an article/possessive.
AMBIGUOUS_NOUN_WORDS: Set[str] = {"train", "trains"}
_NOUN_CONTEXT = {"the", "a", "an", "my", "your", "his", "her", "our",
                 "their", "this", "that", "these", "those", "some", "any",
                 "one", "every", "each", "by", "at", "on", "in", "next",
                 "last", "new", "old", "first", "same", "other"}


def query_concept_profile(query: str) -> Dict[str, float]:
    """
    Concept profile of a QUERY, context-aware (v3.2).

    * noun usage of an ambiguous weak word ("the train") never counts;
    * strong forms always count;
    * weak GENERIC words ("like", "play", "food"...) are already excluded
      by CONCEPT_QUERY_KEY_MAP;
    * if the query names a concept STRONGLY, weak coincidental concepts are
      dropped (strong dominance -- "i'm watching football" is sports, not
      movies);
    * instruments/sport objects ("playing guitar", "playing cricket") suppress
      the gaming concept.
    """
    words = tokenize(query)
    cleaned: List[str] = []
    for i, w in enumerate(words):
        if w in AMBIGUOUS_NOUN_WORDS and i > 0 and words[i - 1] in _NOUN_CONTEXT:
            cleaned.append("zzzz")          # noun usage: different topic
        else:
            cleaned.append(w)
    strong: Dict[str, float] = {}
    weak: Dict[str, float] = {}
    for key in phrase_keys(" ".join(cleaned)):
        hit = CONCEPT_QUERY_KEY_MAP.get(key)
        if not hit:
            continue
        name, w = hit
        if w >= 1.0:
            strong[name] = 1.0
        elif w > weak.get(name, 0.0):
            weak[name] = w
    if NON_GAME_OBJECTS.search(query):
        strong.pop("gaming_preference", None)
        weak.pop("gaming_preference", None)
    if strong:
        return strong                      # strong dominance
    return weak


def _damerau_levenshtein(a: str, b: str, cap: int = 2) -> int:
    """Bounded Damerau-Levenshtein (insert/delete/substitute/transpose)."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if (i > 1 and j > 1 and a[i - 1] == b[j - 2]
                    and a[i - 2] == b[j - 1]):
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def _single_edit_kind(a: str, b: str) -> Optional[Tuple[str, int]]:
    """
    Classify a distance-1 pair. Returns (kind, index) or None.
    kind: 'trans' (adjacent swap), 'ins', 'del', 'sub'.
    """
    la, lb = len(a), len(b)
    if la == lb:
        diff = [i for i in range(la) if a[i] != b[i]]
        if len(diff) == 1:
            return ("sub", diff[0])
        if (len(diff) == 2 and diff[1] == diff[0] + 1
                and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]):
            return ("trans", diff[0])
        return None
    if lb == la + 1:  # one insertion into a
        for i in range(lb):
            if a[:i] == b[:i] and a[i:] == b[i + 1:]:
                return ("ins", i)
        return None
    if la == lb + 1:  # one deletion from a
        for i in range(la):
            if a[:i] == b[:i] and a[i + 1:] == b[i:]:
                return ("del", i)
        return None
    return None


def _known_words() -> Set[str]:
    """
    Every word the system already treats as a real English word (its own
    tables + stopword lists). Built lazily because VERB_KEYS / NOUN_ALIASES /
    RELATION_KEYWORDS are defined further down the module. A typo corrector
    must NEVER rewrite a legitimate word: "drive" -> "driven" would silently
    break "what car do i drive?".
    """
    global _SAFE_WORDS
    if _SAFE_WORDS is None:
        words = (set(_QUERY_VOCAB) | set(GENERIC_WORDS) | set(FILLER_WORDS)
                 | set(STOP_WORDS))
        for table in (VERB_KEYS, MY_NOUN_KEYS, NOUN_ALIASES):
            for k in table:
                words.update(tokenize(k))
        for phrases in RELATION_KEYWORDS.values():
            for phrase in phrases:
                words.update(tokenize(phrase))
        _SAFE_WORDS = {w for w in words if len(w) >= 3}
    return _SAFE_WORDS


def correct_typo(token: str) -> str:
    """
    Exact-first, conservative typo fix against the CLOSED vocabulary only.
    Same initial letter + length >= 5 + exactly one edit.

    v3.2 hardened rules -- the big lexicon made the old rules too eager
    ("rises" was "corrected" to "rides", "compute" to "computer"):

    * TRANSPOSITIONS are always safe ("loigc"->"logic", "palying"->"playing").
    * MID-WORD insertions/deletions are safe ("strngth"->"strength",
      "traning"->"training", "favrite"->"favorite").
    * ANY single SUBSTITUTION is rejected: two real words one substitution
      apart are almost always DIFFERENT words ("rises"/"rides"), not typos.
    * Insertions/deletions at the END of the word are rejected: appending or
      stripping a final letter turns a word into an inflection/derivation
      ("compute"->"computer", "computers"->"computer"), not a typo.
    """
    t = token.lower()
    if len(t) < 5 or t in _QUERY_VOCAB or t in _known_words():
        return t
    best: Optional[str] = None
    for cand in _QUERY_VOCAB:
        if len(cand) < 5 or cand[0] != t[0] or abs(len(cand) - len(t)) > 1:
            continue
        if _damerau_levenshtein(t, cand, cap=1) != 1:
            continue
        kind, idx = _single_edit_kind(t, cand) or ("", -1)
        if kind == "sub":
            continue          # one substitution apart = two different real words
        if kind == "ins" and idx == len(t):
            continue          # appended final letter = suffix ("compute"->"computer")
        if kind == "del" and idx == len(t) - 1:
            continue          # stripped final letter = suffix ("computers"->"computer")
        if best is None or len(cand) > len(best):
            best = cand
    return best or t


# Scope-shifting words. If one of these appears in a LOOKUP, the question is
# about a DIFFERENT topic even when it contains a concept word ("what is my goal
# weight?", "what is my plan for today?"). Concept expansion is disabled then.
# NOTE v3.2: "phone" removed -- possession.phone intent routing now handles
# phone questions precisely, and the concept path must be able to rescue
# paraphrases like "remind me which phone i use".
SCOPE_WORDS = frozenset("""
today tonight tomorrow yesterday weekend week month year date time schedule
itinerary project task tasks meeting trip class homework weight height here
there this that these those recipe code bug error file document email password
address weather news price cost capital movie song book
""".split())
SCOPE_SHIFT = re.compile(r"\b(?:" + "|".join(sorted(SCOPE_WORDS)) + r")\b", re.I)

_TYPO_TOKEN_RE = re.compile(r"[A-Za-z]{5,}")


# British/US spelling variants. Applied to BOTH query and memory-side key
# derivation so "what's my favourite colour?" reaches "favorite color".
BRITISH_TO_US: Dict[str, str] = {
    "colour": "color", "colours": "colors", "favourite": "favorite",
    "favourites": "favorites", "flavour": "flavor", "flavours": "flavors",
    "behaviour": "behavior", "centre": "center", "organise": "organize",
    "organised": "organized", "realise": "realize", "recognise": "recognize",
    "analyse": "analyze", "travelling": "traveling", "travelled": "traveled",
    "cancelled": "canceled", "labour": "labor", "neighbour": "neighbor",
    "grey": "gray", "programme": "program", "defence": "defense",
    "licence": "license", "metre": "meter", "theatre": "theater",
}
_BRITISH_RE = re.compile(r"\b(?:" + "|".join(BRITISH_TO_US) + r")\b", re.I)


def us_spelling(text: str) -> str:
    """Normalise a small set of British spellings to their US equivalents."""
    return _BRITISH_RE.sub(lambda m: BRITISH_TO_US[m.group(0).lower()], text)


def normalize_query(text: str) -> str:
    """Spelling + contractions + closed-vocabulary typo fix."""
    q = us_spelling(expand_contractions(normalize_text(text)))
    return _TYPO_TOKEN_RE.sub(lambda m: correct_typo(m.group(0)), q)


# Harmless lead-in noise. Some TUIs/agents prefix prompts with a command word
# ("logic what do you know about me"); it must not break intent detection.
LEAD_IN = re.compile(
    # A word boundary + optional .,:; and/or spaces. The BOUNDARY is essential:
    # without it "logical"/"solve" would lose their first syllable. Allowing
    # ZERO spaces handles glued prefixes like "logic.what do i like".
    r"^\s*(?:(?:logic|hey|hi|hello|ok|okay|alright|so|please|btw|now|also|hmm|"
    r"well|and|just|actually|assistant|ai|system|query|prompt|look|come\s+on)\b"
    r"[.,:\s]*)+", re.I)


def prepare_query(query: str) -> str:
    """Normalize contractions/typos, then strip lead-in noise (retrieval only)."""
    q = normalize_query(query)
    prev = None
    while q != prev:
        prev = q
        nxt = LEAD_IN.sub("", q).strip()
        # A bare "hello" / "logic" is the WHOLE prompt, not a lead-in. Never
        # strip a query down to nothing.
        if not nxt:
            break
        q = nxt
    return q


# A "tell me everything about me" request has BOTH a user-as-topic phrase and
# a recall/request word. Requiring both keeps topic lookups
# ("what do you know about the protest?") OUT of the meta path.
META_USER_TOPIC = re.compile(
    r"\b(?:who\s+am\s+i|about\s+(?:me|myself)|"
    # v3.3: agent-facing profile references ("the user's profile") are meta
    # topics too, but ONLY as profile/info nouns -- "about the user's exam"
    # is a topic lookup and must stay out of the meta path.
    r"(?:the\s+)?users?(?:'s)?\s+(?:profile|memor(?:y|ies)|information|info|"
    r"details|background)|"
    r"my\s+(?:profile|memor(?:y|ies)|information|info|details|background)|"
    # "my life" is a meta topic ONLY when it is not a goal/plan lookup:
    # "what is my life goal?" must NOT dump the whole database.
    r"my\s+life\b(?!\s+(?:goal|goals|plan|plans|purpose|ambition|dream\w*|aim\w*|"
    r"career|motivation|aspiration|target))|"
    r"everything\s+about\s+me|all\s+about\s+me|me\s+so\s+far|myself)\b", re.I)
# NOTE: "preferences" is intentionally NOT a meta topic -- that question has a
# dedicated narrow intent (preference.*) and must not dump the whole database.
META_RECALL = re.compile(
    r"\b(?:know|remember|recall|memor(?:y|ies)|profile|information|info|told|said|"
    r"stored|saved|learned|learnt|tell|show|list|what|who|everything|all|"
    r"summari[sz]e|am\s+i|do\s+you|can\s+you|could\s+you|would\s+you|"
    r"remind|say|describe)\b", re.I)


# --- v3.5: user-scoped meta answers ----------------------------------------
# An identity query ("who am i?", "what do you know about me?") must answer
# with memories ABOUT THE USER -- never with the whole database. A general
# note ("pizza is great with friends", "the protest is on friday") or another
# person's action ("he is playing valheim", "my friend rafi loves cricket")
# is not part of who the user IS, so it stays out of meta answers. Such
# memories are still retrievable by topic, and /list still shows everything.
USER_SUBJECT_RE = re.compile(
    r"\b(?:i|my|me|mine|myself|we|our|ours|us|(?:the\s+)?users?'?s?)\b", re.I)

# Openings whose SUBJECT is someone/something else. Possessives WITHOUT an
# action verb ("my best friend is rafi", "my favorite movie is x") stay
# user-scoped: they describe the user's own circle and attributes. A relation
# DOING something ("my friend loves pizza") belongs to the friend, not the
# user.
OTHER_SUBJECT_START_RE = re.compile(
    r"^\s*(?:he|she|they|it|his|her|their|there|someone|somebody|everyone|everybody|no\s+one|nobody)\b"
    r"|^\s*my\s+(?:friend|friends|best\s+friend|brother|brothers|sister|sisters|"
    r"sibling|siblings|mom|mum|mother|dad|father|parent|parents|wife|husband|"
    r"partner|girlfriend|boyfriend|cousin|cousins|uncle|aunt|son|daughter|"
    r"kids?|children|child|grandma|grandpa|grandmother|grandfather|boss|"
    r"manager|colleague|coworker|co-worker|teammate|roommate|neighbor|"
    r"neighbour|teacher|dog|cat|pet|pets)\b[^,.!?;]{0,40}?\b"
    r"(?:loves?|liked?|loved?|hates?|hated?|plays?|played?|is\s+playing|"
    r"works?|worked?|lives?|lived?|went|goes|gone|said|told|wants?|wanted?|"
    r"has|had|was|were|will|is\s+going|studies|studied)\b", re.I)


def is_user_scoped_memory(text: str, state_key: str = "") -> bool:
    """
    v3.5: does this memory describe the USER (vs another person or a general
    topic)? Used by the meta gate so identity queries answer user-scoped.

    True when the text is first-person / user-subject ("i like pizza",
    "my meeting is on friday", "user lives in dhaka") and does not open with
    another subject. A record with an extracted state_key is user-scoped
    UNLESS its text opens with someone else (guards memories like "my brother
    is playing valheim now", which still carry activity.game). General notes
    with no user marker ("pizza is great with friends") are never user-scoped.
    """
    t = normalize_text(text or "")
    if not t:
        return False
    opens_other = bool(OTHER_SUBJECT_START_RE.match(t))
    if opens_other:
        return False
    if state_key:
        return True
    return bool(USER_SUBJECT_RE.search(t))


# "What do I want to do with my life?" is an ASPIRATION question, not a
# profile dump. The generic "my life" meta topic would otherwise swallow it.
ASPIRATION_LIFE_RE = re.compile(
    r"\bwhat\s+(?:do|should|can|could|will)\s+i\s+(?:want\s+to\s+)?(?:do|be|become)\b"
    r"[^?.!]{0,30}?\bmy\s+life\b"
    r"|\bwhat\s+am\s+i\s+(?:doing|going\s+to\s+do)\s+with\s+my\s+life\b"
    r"|\bwhat\'s\s+my\s+life\s+(?:goal|purpose|plan|ambition)\b", re.I)


def is_meta_query(query: str) -> bool:
    """
    Robustly detect a "tell me what you know about ME" request (v3.5: such
    queries are answered USER-SCOPED -- see is_user_scoped_memory).

    Handles lead-in words, paraphrases and polite filler:
      "logic what do you know about me", "what can you tell me about myself?",
      "hey, what do you remember about me?", "who am i", "my profile", ...
    It deliberately does NOT match topic lookups such as
    "what do you know about the protest?" -- those go through normal retrieval.
    "what do I want to do with my life?" is routed to aspiration instead.
    Bare one-word commands ("list", "memories") are NOT meta since v3.5:
    commands live behind slashes (/list), everything else is a normal query.
    """
    q = prepare_query(query)
    if not q:
        return False
    if ASPIRATION_LIFE_RE.search(q):
        return False
    if any(p.search(q) for p in META_QUERY_PATTERNS):
        return True
    return bool(META_USER_TOPIC.search(q) and META_RECALL.search(q))


def is_question(text: str) -> bool:
    t = normalize_text(text)
    return "?" in t or bool(QUESTION_START.search(t))


NOUN_ALIASES: Dict[str, str] = {
    "dog's name": "pet.dog_name", "dog name": "pet.dog_name",
    "cat's name": "pet.cat_name", "cat name": "pet.cat_name",
    "dog called": "pet.dog_name", "cat called": "pet.cat_name",
    "dog named": "pet.dog_name", "cat named": "pet.cat_name",
    "pet's name": "pet.name", "pet name": "pet.name",
    "partner's name": "relationship.partner", "wife's name": "relationship.partner",
    "husband's name": "relationship.partner",
    "mother's name": "family.mother", "mom's name": "family.mother",
    "mom name": "family.mother", "mother name": "family.mother",
    "father's name": "family.father", "dad's name": "family.father",
    "dad name": "family.father", "father name": "family.father",
    "brother's name": "family.brother", "sister's name": "family.sister",
    "grandma's name": "family.grandmother", "grandpa's name": "family.grandfather",
    "uncle's name": "family.uncle", "aunt's name": "family.aunt",
    "cousin's name": "family.cousin", "family": "family.general",
    "parents": "family.parents",
    "phone number": "possession.phone", "current game": "activity.game",
    "current job": "identity.profession", "current city": "identity.location",
    "favorite game": "preference.favorite_game",
    "favourite game": "preference.favorite_game",
    "education level": "education.level", "grade": "education.level",
    "class": "education.level", "exam": "education.level",
    "exams": "education.level", "board exam": "education.level",
    "admission goal": "education.admission_goal", "university goal": "education.admission_goal",
    "study goal": "education.admission_goal", "admission plan": "education.admission_goal",
    "dream university": "education.admission_goal", "target university": "education.admission_goal",
    "gym": "activity.training", "workout": "activity.training", "training": "activity.training",
    "strength training": "activity.training", "interests": "preference.likes",
    "hobbies": "preference.likes", "likes": "preference.likes",
    "goal": "education.admission_goal", "goals": "education.admission_goal",
    "ambition": "education.admission_goal", "dream": "education.admission_goal",
    "aspiration": "education.admission_goal", "motivation": "education.admission_goal",
    "career goal": "education.admission_goal", "purpose": "education.admission_goal",
}


def derive_noun_key(noun: str) -> Optional[str]:
    """Map 'my <noun>' onto a relation key used by the writer."""
    n = us_spelling(re.sub(r"\s+", " ", noun.lower().replace("'s", ""))).strip()
    if not n:
        return None
    if n.startswith(("favorite ", "favourite ")):
        rest = n.split(" ", 1)[1].strip()
        return "preference.favorite_" + _slug(rest) if rest else None
    if n in NOUN_ALIASES:
        return NOUN_ALIASES[n]
    if n in MY_NOUN_KEYS:
        return MY_NOUN_KEYS[n]
    if n.startswith("current "):
        return current_key(n[8:])
    # "my dog's name" -> "my dog name" -> map the leading noun. The leading
    # word resolves through BOTH tables, so "my motivation in life" routes to
    # the aspiration relation instead of an unknown state.* attribute
    # (which would wrongly look like an unresolved lookup). But a SCOPE word
    # in the tail ("my goal weight", "my plan for today") shifts the topic:
    # the whole thing stays an unknown attribute and returns nothing.
    words = n.split()
    if len(words) > 1:
        if any(w in SCOPE_WORDS for w in words[1:]):
            return "state." + _slug(n)
        if words[0] in MY_NOUN_KEYS:
            return MY_NOUN_KEYS[words[0]]
        if words[0] in NOUN_ALIASES:
            return NOUN_ALIASES[words[0]]
    return "state." + _slug(n)


def detect_intents(query: str) -> List[Tuple[str, str]]:
    """
    Return [(target_key, intent_label)] for strict self-attribute LOOKUPS.

    A bare statement ("my preferences are changing") is not a lookup: intents
    only fire for questions, explicit recall requests, or very short "my X"
    queries. That keeps first-person prompts from being treated as retrieval
    commands by accident.
    """
    q = normalize_text(query)
    if not q:
        return []
    is_lookup = (is_question(q) or bool(LOOKUP_PREFIX.search(q)) or
                 (len(q.split()) <= 4 and re.search(r"\bmy\b", q, re.I)))
    if not is_lookup:
        return []
    found: List[Tuple[str, str]] = []
    for pattern, keys, label in INTENT_RULES:
        if pattern.search(q):
            for k in keys:
                found.append((k, label))
    for m in WHAT_MY_RE.finditer(q):
        key = derive_noun_key(m.group("noun"))
        if key:
            found.append((key, "what_is_my:" + key))
    # General "what <noun> do i <verb>?" -> the same noun key the writer uses.
    # Covers "what car do i drive?", "what laptop do i use?", "what pet do i
    # have?" without one hand-written rule per object.
    for m in re.finditer(
            r"\bwhat\s+([a-z]+)\s+do\s+i\s+(?:drive|ride|use|wear|own|have)\b",
            q, re.I):
        key = derive_noun_key(m.group(1))
        if key:
            found.append((key, "what_do_i_verb:" + key))
    # "do you remember my X?", "tell me about my X", "is my X ...?"
    for m in RECALL_MY_RE.finditer(q):
        key = derive_noun_key(m.group("noun"))
        if key:
            found.append((key, "my_noun:" + key))

    # If a SPECIFIC relation was identified, drop broad category keys.
    # "what's my favorite movie?" must not dump every like/dislike memory.
    keys = list(dict.fromkeys(k for k, _ in found))
    categories = {k for k in keys if "." not in k}
    redundant = {c for c in categories
                 if any(k.startswith(c + ".") for k in keys)}
    if redundant:
        found = [(k, label) for k, label in found if k not in redundant]
    return list(dict.fromkeys(found))


def is_generation_task(query: str) -> bool:
    """Backward-compatible alias: True when the prompt is gated out."""
    return gate_reason(query) is not None


# ---------------------------------------------------------------------------
# ASPECT ROUTING  (closed-vocabulary, no LLM, no fuzzy global expansion)
#
# Some perfectly relevant questions share NO word with the memory text:
#   "what do i like"          vs "user is interested in computer science"
#   "when did i start gym?"   vs "user has been strength training since 2022"
#   "where i want to get admitted?" vs "user aims to study CSE at a public university"
# Each rule has a NARROW first-person query pattern and a NARROW memory-side
# cue. An aspect only fires for lookup questions containing a self marker, so
# "when did the gym open?" or "write a poem about gym" can never trigger it.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AspectRule:
    name: str
    query: re.Pattern
    memory: re.Pattern
    score: float = 0.72


# One shared vocabulary of training-related query words. Kept in ONE place so
# adding a new synonym ("calisthenics", "powerlifting", ...) immediately fixes
# every "when did I start..." phrasing instead of one hand-patched instance.
_TRAIN_Q = (r"(?:gym|gymnasium|train\w*|work\w*\s*out|lift\w*|strength|weights?|"
            r"exercis\w*|fitness|bodybuild\w*|powerlift\w*|calisthenics)")

ASPECT_RULES: List[AspectRule] = [
    AspectRule(
        "admission_goal",
        re.compile(
            r"\b(?:"
            r"where\s+(?:do|did|would|am)\s+i\s+(?:want|wish|plan|hope|aim|trying|going)\b"
            r"[^?]{0,60}?\b(?:study|admitted|admission|university|college|apply|get\s+in)\b"
            r"|where\s+i\s+want\s+to\s+(?:get\s+)?(?:admitted|study|go)\b"
            r"|which\s+(?:university|college)\s+(?:do|would)\s+i\s+(?:want|like|plan|hope)\b"
            r"|what\s+(?:university|college|subject|course)\s+(?:do|would)\s+i\s+(?:want|plan|hope|aim)\b"
            r"|what\s+are\s+my\s+(?:plans|goals|aims|dreams)\b[^?]{0,40}?\b(?:university|study|admission|college)\b"
            r"|(?:know|remember|recall)\b[^?]{0,70}?\bwhere\s+i\s+want\b"
            r"|what(?:'s|\s+is)\s+my\s+(?:admission|university|study)\s+(?:goal|plan|target|aim)\b"
            r")", re.I),
        re.compile(
            r"\b(?:aims?\s+to\s+study|want\w*\s+to\s+(?:study|attend|get\s+admitted)|"
            r"admission|get\s+admitted|public\s+university|private\s+university|"
            r"university\s+admission|apply\w*\s+to|hop\w*\s+to\s+study|"
            r"plans?\s+to\s+study|engineering\s+university|"
            r"prepared\s+for\s+[^.]{0,40}admission|"
            r"(?:study|studying|studied)\s+(?:at|in)\s+(?:a|an|the)\b)", re.I),
    ),
    AspectRule(
        "training_start",
        re.compile(
            r"\b(?:"
            + r"when\s+did\s+i\s+(?:start|started|begin|began|join|joined)\b[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r"|when\s+(?:did|was)\s+i\b[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r"|since\s+when\s+(?:have|had|do|am)\s+i\b[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r"|how\s+long\s+(?:have|had|did)\s+i\s+been\b[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r"|when\s+i\s+(?:start|started|began|begin)\b[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r"|(?:know|remember|recall)\b[^?]{0,70}?\bwhen\s+i\s+(?:start|started|began|begin|join|joined)\b"
            + r"[^?]{0,60}?\b" + _TRAIN_Q + r"\b"
            + r")", re.I),
        re.compile(
            r"\b(?:strength\s+train\w*|train\w*|gym|gymnasium|work\w*\s*out|lift\w*|"
            r"weights?|exercis\w*|fitness|bodybuild\w*|powerlift\w*|calisthenics)\b", re.I),
    ),
    AspectRule(
        "aspiration",
        re.compile(
            r"\b(?:"
            r"what\s+do\s+i\s+want\s+to\s+(?:do|be|become|study)\b"
            # Goal-noun questions must END on the noun. Otherwise "what is my
            # plan for today?", "what is my goal weight?" or "what is my aim in
            # this game?" would wrongly surface university-goal memories.
            r"|what\s+(?:are|is)\s+my\s+(?:goals?|ambition\w*|dream\w*|aspiration\w*|"
            r"motivation|purposes?|plans?|aims?|career\s+plans?|career\s+goals?|"
            r"life\s+goals?|life\s+purpose|future\s+plans?)\s*[?.!]*\s*$"
            r"|what(?:'s|\s+is)\s+my\s+(?:goal|goals|motivation|ambition|dream|purpose|"
            r"aspiration|aim|aims|plan|plans|career\s+plan|career\s+goal|"
            r"life\s+goal|life\s+purpose|future\s+plan)\s*[?.!]*\s*$"
            r"|what\s+am\s+i\s+aiming\s+for\s*[?.!]*\s*$"
            r"|what\s+(?:drives|motivates)\s+me\s*[?.!]*\s*$"
            r"|(?:know|remember|recall)\b[^?]{0,70}?\bwhat\s+i\s+want\s+to\s+do\b"
            r")", re.I),
        re.compile(
            r"\b(?:aims?\s+to|want\w*\s+to|goal|ambition|dream|aspiration|"
            r"motivat\w*|admission|plan\w*\s+to|hop\w*\s+to|intend\w*\s+to|"
            r"aspir\w*|prepared\s+for)\b", re.I),
    ),
    AspectRule(
        "preferences",
        re.compile(
            r"\b(?:what\s+(?:do|does)\s+(?:i|the\s+user|user)\s+prefer\b"
            r"|what\s+(?:are|is)\s+(?:my|the\s+user'?s?)\s+preferences\b|"
            r"what\s+do\s+i\s+favou?r\b|what\s+would\s+i\s+rather\b|"
            r"what\s+do\s+i\s+like\s+(?:more|better)\b|my\s+preferences?\b|"
            r"what\s+(?:am|are)\s+i\s+(?:keen\s+on|into)\b)", re.I),
        re.compile(
            r"\b(?:prefers?|preference|favorite|favourite|would\s+rather|"
            r"keen\s+on|fan\s+of|\bover\b[^.]{0,40}\bgames?\b|"
            r"likes?\s+[^.]{0,30}\bover\b)", re.I),
    ),
    AspectRule(
        "interests",
        re.compile(
            r"\b(?:"
            r"what\s+(?:do|does)\s+(?:i|the\s+user|user)\s+(?:like|love|enjoy)\b"
            r"|what\s+do\s+i\s+like\s+to\s+do\b"
            r"|what\s+(?:am|was)\s+i\s+interested\s+in\b"
            r"|what\s+(?:interests?|intersts?|intrests?|interestd)\s+me\b"
            r"|what\s+(?:are|is)\s+(?:my|the\s+user'?s?)\s+(?:interests?|hobbies|"
            r"likes|favorites|favourites)\b"
            # "what's (my | the user's | user) favourite thing?" -- a bare
            # superlative with no noun route. The INTERESTS memory side
            # (likes/loves/enjoys/prefers/favorite/...) is the right answer
            # set; routing it here keeps it far away from the meta dump.
            r"|what(?:'s|\s+is|\s+are)\s+(?:my|the\s+user'?s?|users?)\s+"
            r"favo(?:u)?rite\s+(?:things?|stuff)\b"
            r"|what\s+(?:do|would)\s+i\s+(?:enjoy|prefer)\s+doing\b"
            r"|what\s+i\s+(?:like|love|enjoy|prefer|am\s+interested\s+in)\b"
            r"|what\s+i'?m\s+into\b"
            r"|tell\s+me\s+(?:what|about)\s+(?:i|my)\s+(?:like|interests?|hobbies)\b"
            r"|(?:my|the\s+user'?s?)\s+(?:interests?|hobbies|likes)\b"
            r"|(?:know|remember|recall)\b[^?]{0,70}?\b(?:what|which)\s+i\s+(?:am\s+)?(?:interested|like|prefer|enjoy)\b"
            r")", re.I),
        re.compile(
            r"\b(?:interested\s+in|interests?\b|intersted|intrest\w*|likes?|loves?|"
            r"enjoys?|prefers?|favorite|favourite|hobbies|hobby|keen\s+on|"
            r"fan\s+of|passionate\s+about|fascinated\s+by|curious\s+about)\b", re.I),
    ),
]


def match_aspect(query: str) -> Optional[AspectRule]:
    """Return the FIRST (most specific) aspect a self-directed lookup asks for."""
    q = prepare_query(query)
    if not q:
        return None
    if not (SELF_MARKER.search(q) or _USER_REF.search(q)):   # no I/my/me/user -> not about the user
        return None
    if not (is_question(q) or RECALL_CUE.search(q) or LOOKUP_PREFIX.search(q)):
        return None                                    # statements are not lookups
    for rule in ASPECT_RULES:
        if rule.query.search(q):
            return rule
    return None


# ===========================================================================
# 2. Slot + state-fact extraction
# ===========================================================================

SLOT_CATEGORIES: Dict[str, List[Tuple[re.Pattern, str]]] = {
    "identity": [
        (re.compile(r"\b(?:my\s+name\s+is|call\s+me|people\s+call\s+me|i\s+go\s+by)\s+[A-Za-z]", re.I), "identity.name"),
        (re.compile(r"\b(?:born\s+on|my\s+(?:birthday|bday|dob)\s+is|i\s+was\s+born\s+on)\b", re.I), "identity.birthday"),
        (re.compile(r"\b(?:i\s+(?:live|am\s+living|moved\s+to|reside)\s+in|my\s+(?:location|city|hometown|address)\s+is)\b", re.I), "identity.location"),
        (re.compile(r"\bi(?:'m|\s+am)\s+\d{1,3}\s*(?:years?\s+old|yo)\b", re.I), "identity.age"),
        (re.compile(r"\b(?:i\s+work\s+as|my\s+(?:job|profession|career)\s+is)\b", re.I), "identity.profession"),
    ],
    "preference": [
        (re.compile(r"\b(?:i\s+(?:like|love|enjoy|prefer)|my\s+(?:favorite|favourite))\b", re.I), "preference.likes"),
        (re.compile(r"\b(?:i\s+(?:dislike|hate|don't\s+like|can't\s+stand))\b", re.I), "preference.dislikes"),
    ],
    "relationship": [
        (re.compile(r"\bmy\s+(?:wife|husband|partner|girlfriend|boyfriend|spouse|brother|sister|mother|father|friend)\b", re.I), "relationship.person"),
    ],
    "family": [
        (re.compile(r"\b(?:my|user'?s?|his|her|our)\s+(?:mother|mom|mum|father|dad|brother|sister|son|daughter|grandmother|grandma|grandfather|grandpa|uncle|aunt|cousin|parents|family)\b", re.I), "family.member"),
        (re.compile(r"\b(?:mother|mom|mum|father|dad)\b", re.I), "family.parent"),
        (re.compile(r"\b(?:brother|sister|sibling)\b", re.I), "family.sibling"),
    ],
    "habit": [
        (re.compile(r"\b(?:i\s+always|i\s+usually|every\s+day|every\s+morning|every\s+night)\b", re.I), "habit.routine"),
    ],
}


def detect_slots(sentence: str) -> List[str]:
    """Broad, non-exclusive tags. State keys are added separately."""
    matched: List[str] = []
    for cat, rules in SLOT_CATEGORIES.items():
        for pattern, slot_key in rules:
            if pattern.search(sentence):
                matched.append(slot_key)
                if cat not in matched:
                    matched.append(cat)
    return list(dict.fromkeys(matched))


@dataclass
class StateFact:
    """A single-valued, mutable (relation, value) assertion about the user."""
    key: str
    value: str
    value_norm: str
    kind: str
    verb: str
    cue: Optional[str] = None            # temporal | change | progressive | None
    negated: bool = False
    current: bool = False

    @property
    def eligible_for_update(self) -> bool:
        # family.* relations are multi-aspect (a mother has a job AND a health
        # state); auto-superseding one over the other would lose data. Family
        # facts are stored side by side instead.
        if self.key.startswith("family."):
            return False
        return bool(self.cue) and bool(self.key) and bool(self.value_norm)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "relation": self.key, "value": self.value, "value_normalized": self.value_norm,
            "kind": self.kind, "verb": self.verb, "cue": self.cue,
            "negated": self.negated, "current": self.current,
        }


VALUE = r"(?P<v>[^,.!?;\n]+)"


def clean_value(raw: str) -> Optional[str]:
    """Trim temporal tails, trailing 'with my friends', leading articles."""
    v = normalize_text(raw or "").strip().strip("\"'`").strip()
    tail = re.compile(
        r"[\s,;:-]*(?:right\s+now|at\s+the\s+moment|at\s+present|as\s+of\s+(?:now|today)|"
        r"these\s+days|lately|currently|presently|from\s+now\s+on|no\s+longer|any\s+more|"
        r"anymore|instead|now|still|actually|just|though)\b[.!?]*$", re.I)
    for _ in range(3):
        new = tail.sub("", v).strip()
        if new == v:
            break
        v = new
    v = re.sub(r"\s+with\s+(?:my|the|a|an|some|our)\b.*$", "", v, flags=re.I).strip()
    v = re.sub(r"^(?:a|an|the)\s+", "", v, flags=re.I).strip()
    v = v.strip(" ,.;:!?-")
    if not v or len(v) > 60:
        return None
    return v


def value_kind(v: str) -> str:
    if re.search(r"\d", v):
        return "numeric"
    words = re.findall(r"[A-Za-z0-9'-]+", v)
    if any(w[:1].isupper() for w in words):
        return "proper"
    if len(words) >= 2 and words[0].lower() not in {"a", "an", "the", "my", "some", "all"}:
        return "proper"
    return "common"


def normalize_value(v: str) -> str:
    return " ".join(stem(t) for t in tokenize(v) if t not in FILLER_WORDS).strip()


# -- relation table ---------------------------------------------------------

VERB_KEYS: Dict[str, str] = {
    "playing": "activity.game", "play": "activity.game",
    "started playing": "activity.game", "switched to playing": "activity.game",
    "reading": "activity.book", "read": "activity.book",
    "watching": "activity.show", "watch": "activity.show",
    "listening to": "activity.music", "listen to": "activity.music",
    # v3.2: exam prep and skill learning are SEPARATE relations, so
    # "I'm studying for the HSC exam" and "I'm learning Python" coexist.
    "studying for": "activity.study.exam", "study for": "activity.study.exam",
    "preparing for": "activity.study.exam", "revising for": "activity.study.exam",
    "studying": "activity.study", "study": "activity.study",
    "learning": "activity.study", "learn": "activity.study",
    "working on": "activity.project", "work on": "activity.project",
    "working at": "work.employer", "work at": "work.employer",
    "working for": "work.employer", "work for": "work.employer",
    "working as": "identity.profession", "work as": "identity.profession",
    "using": "tech.tool", "use": "tech.tool",
    "living in": "identity.location", "live in": "identity.location",
    "moved to": "identity.location", "based in": "identity.location",
    "reside in": "identity.location", "am based in": "identity.location",
    "going to": "education.school", "attend": "education.school",
    "attending": "education.school",
}


def _alt(keys: Iterable[str]) -> str:
    parts = []
    for k in sorted(keys, key=lambda s: (-len(s.split()), -len(s))):
        parts.append(r"\s+".join(re.escape(p) for p in k.split()))
    return "|".join(parts)


_VERB_ALT = _alt(VERB_KEYS.keys())

GENERIC_STATE_RE = re.compile(
    r"\b(?:"
    r"i(?:'m|\s+am)\s+(?P<neg>not\s+|no\s+longer\s+|don't\s+|do\s+not\s+)?"
    r"(?:currently\s+|right\s+now\s+|now\s+|still\s+)?(?P<verb>" + _VERB_ALT + r")\s+"
    r"|i\s+(?P<verb2>" + _VERB_ALT + r")\s+"
    r"|(?:currently\s+|right\s+now\s+|now\s+)i(?:'m|\s+am)\s+(?P<verb3>" + _VERB_ALT + r")\s+"
    r"|(?:currently\s+|right\s+now\s+|now\s+)(?P<verb4>" + _VERB_ALT + r")\s+"
    r")" + VALUE, re.I)

FAVORITE_RE = re.compile(
    r"\bmy\s+(?:current\s+)?(?:favorite|favourite)\s+(?P<k>[A-Za-z]+)\s+is\s+" + VALUE, re.I)
CURRENT_RE = re.compile(
    r"\bmy\s+current\s+(?P<k>[A-Za-z][A-Za-z ]{0,24}?)\s+is\s+" + VALUE, re.I)

MY_NOUN_KEYS: Dict[str, str] = {
    "name": "identity.name", "age": "identity.age",
    "birthday": "identity.birthday", "bday": "identity.birthday", "dob": "identity.birthday",
    "date of birth": "identity.birthday",
    "job": "identity.profession", "profession": "identity.profession",
    "career": "identity.profession", "occupation": "identity.profession",
    "company": "work.employer", "employer": "work.employer",
    "boss": "work.manager", "manager": "work.manager",
    "city": "identity.location", "location": "identity.location",
    "address": "identity.location", "hometown": "identity.location",
    "home": "identity.location", "country": "identity.location",
    "wife": "relationship.partner", "husband": "relationship.partner",
    "partner": "relationship.partner", "girlfriend": "relationship.partner",
    "boyfriend": "relationship.partner", "spouse": "relationship.partner",
    "dog": "pet.name", "cat": "pet.name", "pet": "pet.name",
    "phone": "possession.phone", "laptop": "possession.laptop", "car": "possession.car",
    # v3.2: family members get their own relation keys, so "what's my mother's
    # name?" / "how's my mom?" route precisely, and family facts never
    # auto-supersede each other (a mother has a job AND a health state).
    "mother": "family.mother", "mom": "family.mother", "mum": "family.mother",
    "mama": "family.mother", "father": "family.father", "dad": "family.father",
    "papa": "family.father", "brother": "family.brother", "sister": "family.sister",
    "son": "family.son", "daughter": "family.daughter",
    "grandmother": "family.grandmother", "grandma": "family.grandmother",
    "grandfather": "family.grandfather", "grandpa": "family.grandfather",
    "uncle": "family.uncle", "aunt": "family.aunt", "cousin": "family.cousin",
    "family": "family.general", "parents": "family.parents",
    "school": "education.school", "university": "education.school", "college": "education.school",
    "major": "education.major", "language": "tech.language", "editor": "tech.editor",
    "os": "tech.os", "operating system": "tech.os", "framework": "tech.framework",
    "project": "activity.project", "game": "activity.game",
    "favorite game": "preference.favorite_game", "favourite game": "preference.favorite_game",
}
MY_NOUN_RE = re.compile(
    r"\bmy\s+(?P<k>" + _alt(MY_NOUN_KEYS.keys()) + r")(?:\s*'s\s+name)?\s+is\s+" + VALUE, re.I)


def current_key(k: str) -> str:
    """'current game' -> activity.game, 'current phone' -> possession.phone."""
    n = us_spelling(re.sub(r"\s+", " ", k.lower())).strip()
    return MY_NOUN_KEYS.get(n, "state." + _slug(n))

NAME_RE = re.compile(r"\b(?:my\s+name\s+is|call\s+me|people\s+call\s+me|i\s+go\s+by)\s+" + VALUE, re.I)
PET_RE = re.compile(
    r"\b(?:my\s+(?:dog|cat|pet)\s+(?:is\s+)?(?:called|named)|"
    r"(?:i\s+have\s+(?:a|an)\s+)?(?:dog|cat|pet)\s+(?:called|named))\s+" + VALUE, re.I)
AGE_RE = re.compile(r"\bi(?:'m|\s+am)\s+(?P<v>\d{1,3})\s*(?:years?\s+old|yo)\b", re.I)
MY_AGE_RE = re.compile(r"\bmy\s+age\s+is\s+(?P<v>\d{1,3})\b", re.I)
BIRTHDAY_RE = re.compile(r"\b(?:i\s+was\s+born\s+on|my\s+(?:birthday|bday|dob)\s+is)\s+" + VALUE, re.I)

PROFESSION_WORDS = {
    "teacher", "doctor", "engineer", "developer", "designer", "student", "nurse", "lawyer",
    "chef", "artist", "writer", "manager", "analyst", "scientist", "accountant",
    "pharmacist", "driver", "farmer", "pilot", "soldier", "freelancer", "consultant",
    "researcher", "professor", "mechanic", "electrician", "plumber", "photographer",
    "musician", "actor", "architect", "dentist", "programmer", "entrepreneur", "intern",
}
I_AM_A_RE = re.compile(r"\bi(?:'m|\s+am)\s+(?:a|an)\s+" + VALUE, re.I)

# --- third-person / agent-written memories ---------------------------------
# An agent often distils facts as "user's name is X" / "user is playing X"
# instead of "my name is X". Those must be understood on SAVE (so they get a
# relation key and can be superseded later) and on RETRIEVAL (see
# text_relation_keys). Past events ("user was in a protest") are deliberately
# excluded: "was" is not in these patterns.
_U = r"(?:the\s+)?user'?s?"
USER_FAVORITE_RE = re.compile(
    r"\b" + _U + r"\s+(?:current\s+)?(?:favorite|favourite)\s+(?P<k>[A-Za-z]+)\s+is\s+" + VALUE, re.I)
USER_CURRENT_RE = re.compile(
    r"\b" + _U + r"\s+current\s+(?P<k>[A-Za-z][A-Za-z ]{0,24}?)\s+is\s+" + VALUE, re.I)
USER_NAME_RE = re.compile(r"\b" + _U + r"\s+name\s+is\s+" + VALUE, re.I)
USER_BDAY_RE = re.compile(
    r"\b" + _U + r"\s+(?:birthday|bday|dob)\s+is\s+(?:on\s+)?" + VALUE, re.I)
USER_NOUN_RE = re.compile(
    r"\b" + _U + r"\s+(?P<k>" + _alt(MY_NOUN_KEYS.keys()) + r")\s+is\s+" + VALUE, re.I)
USER_VERB_RE = re.compile(
    r"\b(?:the\s+)?user\s+(?:is|'s)\s+(?:currently\s+|right\s+now\s+|now\s+)?"
    r"(?P<uverb>" + _VERB_ALT + r")\s+" + VALUE, re.I)
USER_IS_A_RE = re.compile(r"\b(?:the\s+)?user\s+is\s+(?:a|an)\s+" + VALUE, re.I)

EDUCATION_PHRASES = {
    "hsc candidate", "hsc candiate", "hsc student", "college student",
    "university student", "high school student", "undergraduate",
    "graduate student", "student", "candidate", "candiate", "freshman",
    "sophomore", "pupil",
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", text.lower()).strip(".")


def _make_fact(sentence: str, raw_value: str, key: str, verb: str,
               negated: bool = False) -> Optional[StateFact]:
    value = clean_value(raw_value)
    if not value or not key:
        return None
    if key == "activity.game" and NON_GAME_OBJECTS.search(raw_value or ""):
        return None                                  # "playing guitar" is not a game
    if TIME_CUE.search(sentence):
        cue = "temporal"
    elif CHANGE_CUE.search(sentence):
        cue = "change"
    elif verb.endswith("ing") and re.search(r"\bi(?:'m|\s+am)\b|\bcurrently\b|\bright\s+now\b", sentence, re.I):
        cue = "progressive"
    else:
        cue = None
    return StateFact(
        key=key, value=value, value_norm=normalize_value(value), kind=value_kind(value),
        verb=verb, cue=cue, negated=negated,
        current=bool(cue) or not is_question(sentence),
    )


_DEVICE_RE = {
    "possession.phone": re.compile(r"\b(?:phones?|smartphones?|iphones?|iphone|androids?)\b", re.I),
    "possession.laptop": re.compile(r"\b(?:laptops?|macbooks?|notebooks?|thinkpads?)\b", re.I),
    "possession.car": re.compile(r"\b(?:cars?|vehicles?|motorcycles?|motorbikes?|bicycles?|bikes?)\b", re.I),
}
_SCHOOL_OBJECT_RE = re.compile(
    r"\b(?:school|schools|college|university|campus|academy|institute|"
    r"coaching|class|classes|madrasa|madrasah)\b", re.I)
_EXAM_OBJECT_RE = re.compile(
    r"\b(?:exams?|tests?|hsc|ssc|boards?|finals?|viva|quiz)\b", re.I)


def _specialize_relation(key: str, verb: str, sentence: str, raw_value: str) -> Optional[str]:
    """
    Refine a generic relation using what the sentence is actually about.

    * "use a Xiaomi phone"   -> possession.phone (not tech.tool)
    * "studying" + exam words -> activity.study.exam / otherwise .skill
    * "going to workout"      -> NO fact (future intent, not a school)
    """
    low = (sentence + " " + (raw_value or "")).lower()
    if key == "tech.tool":
        for dev_key, dev_re in _DEVICE_RE.items():
            if dev_re.search(low):
                return dev_key
        return key
    if key == "activity.study":
        return "activity.study.exam" if _EXAM_OBJECT_RE.search(low) else "activity.study.skill"
    if key == "education.school":
        # "i am going to workout now" / "going to bed" / "going to dhaka" are
        # future intents, not school facts. Only a real school object counts.
        return key if _SCHOOL_OBJECT_RE.search(low) else None
    return key


def extract_state_fact(sentence: str) -> Optional[StateFact]:
    """
    Extract ONE single-valued mutable fact, or None.

    Only present-tense, affirmative/negated assertions with a clear relation
    are considered -- in first person ("my name is X") OR agent third person
    ("user's name is X"). Questions, past events ("user was in a protest"),
    opinions and plain likes are NOT state facts and can never overwrite
    anything.
    """
    s = normalize_text(sentence)
    if not s or len(s) > 300 or is_question(s):
        return None
    has_first_person = bool(re.search(r"\b(?:i|my|me|mine|myself)\b", s, re.I))
    elliptical_current = bool(re.match(r"^\s*(?:currently|right\s+now|now)\b", s, re.I))
    has_user_subject = bool(re.search(r"\b(?:the\s+)?user'?s?\b", s, re.I))
    if not (has_first_person or elliptical_current or has_user_subject):
        return None

    m = FAVORITE_RE.search(s)
    if m:
        key = "preference.favorite_" + _slug(m.group("k"))
        return _make_fact(s, m.group("v"), key, "my favorite " + m.group("k") + " is")

    m = CURRENT_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), current_key(m.group("k")), "my current " + m.group("k") + " is")

    m = MY_NOUN_RE.search(s)
    if m:
        key = MY_NOUN_KEYS.get(m.group("k").lower())
        if key:
            return _make_fact(s, m.group("v"), key, "my " + m.group("k") + " is")

    m = NAME_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "identity.name", "my name is")

    m = PET_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "pet.name", "named")

    m = AGE_RE.search(s) or MY_AGE_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "identity.age", "age")

    m = BIRTHDAY_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "identity.birthday", "born on")

    m = GENERIC_STATE_RE.search(s)
    if m:
        verb_raw = next((g for g in (m.group("verb"), m.group("verb2"),
                                     m.group("verb3"), m.group("verb4")) if g), "")
        verb = re.sub(r"\s+", " ", verb_raw.lower()).strip()
        key = VERB_KEYS.get(verb)
        neg = bool(m.group("neg")) or bool(re.search(r"\b(?:anymore|any\s+more|no\s+longer|not\s+anymore)\b", s, re.I))
        if key:
            key = _specialize_relation(key, verb, s, m.group("v"))
            if key:      # None = e.g. "going to workout" is not a school fact
                return _make_fact(s, m.group("v"), key, verb, negated=neg)

    m = I_AM_A_RE.search(s)
    if m:
        raw = clean_value(m.group("v")) or ""
        if raw and any(w in PROFESSION_WORDS for w in tokenize(raw)):
            return _make_fact(s, m.group("v"), "identity.profession", "i am a")

    # ---- agent-written, third-person facts --------------------------------
    m = USER_FAVORITE_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"),
                          "preference.favorite_" + _slug(m.group("k")), "user's favorite")
    m = USER_CURRENT_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), current_key(m.group("k")), "user's current")
    m = USER_NAME_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "identity.name", "user's name is")
    m = USER_BDAY_RE.search(s)
    if m:
        return _make_fact(s, m.group("v"), "identity.birthday", "user's birthday is")
    m = USER_NOUN_RE.search(s)
    if m:
        key = MY_NOUN_KEYS.get(m.group("k").lower())
        if key:
            return _make_fact(s, m.group("v"), key, "user's " + m.group("k") + " is")
    m = USER_VERB_RE.search(s)
    if m:
        verb = re.sub(r"\s+", " ", m.group("uverb").lower()).strip()
        key = VERB_KEYS.get(verb)
        if key:
            key = _specialize_relation(key, verb, s, m.group("v"))
            if key:
                return _make_fact(s, m.group("v"), key, verb)
    m = USER_IS_A_RE.search(s)
    if m:
        raw = clean_value(m.group("v")) or ""
        low = raw.lower()
        if any(p in low for p in EDUCATION_PHRASES):
            return _make_fact(s, m.group("v"), "education.level", "user is a")
        if raw and any(w in PROFESSION_WORDS for w in tokenize(raw)):
            return _make_fact(s, m.group("v"), "identity.profession", "user is a")
    return None


# ---------------------------------------------------------------------------
# Relation cues read straight from the memory TEXT.
#
# Why: an agent may write "user's name is alice" (no first person), so no
# slot / state_key is produced at save time. Intent questions such as
# "what's my name?" would then match nothing. These phrase cues make retrieval
# independent of grammatical person -- and they are precise enough that
# "what's my name?" does not drag in unrelated memories.
# ---------------------------------------------------------------------------
RELATION_KEYWORDS: Dict[str, List[str]] = {
    "identity.name": ["name is", "name's", "my name", "user's name", "users name",
                      "call me", "i go by", "people call"],
    "identity.birthday": ["birthday is", "birthday's", "my birthday", "user's birthday",
                          "users birthday", "bday", "born on", "date of birth", "dob is"],
    "identity.age": ["years old", "my age", "user's age", "years of age"],
    "identity.location": ["live in", "lives in", "living in", "located in", "based in",
                          "moved to", "reside in", "my city", "user's city",
                          "my hometown", "my address", "my location"],
    "identity.profession": ["work as", "works as", "working as", "my job", "user's job",
                            "my profession", "user's profession", "occupation",
                            "my career", "user's career"],
    "work.employer": ["work at", "works at", "work for", "works for", "my company",
                      "user's company", "my employer", "user's employer"],
    "activity.game": ["playing", "current game", "favorite game", "favourite game", "main game"],
    "activity.book": ["reading", "reads ", "read ", "current book", "novels"],
    "activity.show": ["watching", "watches ", "watch ", "current show", "binge",
                      "documentaries"],
    "activity.music": ["listening to", "listens to", "listen to", "lo-fi"],
    # v3.2: split study relations so exam prep and skill learning coexist.
    "activity.study.exam": ["studying for", "study for", "revising for",
                            "preparing for", "exam preparation"],
    "activity.study.skill": ["learning", "learning to", "my major",
                             "user's major", "self-taught"],
    "activity.project": ["working on"],
    "tech.tool": ["i use", "user uses", "my editor", "my language", "using "],
    "possession.phone": ["my phone", "user's phone", "users phone", "phone is",
                         "use a phone", "uses a phone", "smartphone"],
    "pet.name": ["my dog", "my cat", "my pet", "dog named", "cat named", "pet named",
                 "dog called", "cat called", "named milo"],
    # Specific pet names: "what's my DOG's name?" must not also return the cat.
    "pet.dog_name": ["dog's name", "dog named", "dog called"],
    "pet.cat_name": ["cat's name", "cat named", "cat called"],
    "preference.likes": ["i like", "i love", "i enjoy", "user likes", "user loves",
                         "my favorite", "my favourite", "user's favorite", "user's favourite",
                         "interested in", "interest in", "interests are", "user enjoys",
                         "enjoys ", "prefers ", "hobbies", "keen on", "passionate about"],
    "preference.dislikes": ["i dislike", "i hate", "user dislikes", "user hates", "can't stand"],
    "education.admission_goal": ["aims to study", "admission", "get admitted",
                                 "wants to study", "plan to study", "public university",
                                 "university admission", "apply to", "hopes to study"],
    "activity.training": ["strength training", "training since", "gym", "workout",
                          "working out", "lifting", "weight training", "bodybuilding"],
    "relationship.partner": ["my wife", "my husband", "my partner", "my girlfriend",
                             "my boyfriend", "user's wife", "user's husband"],
    "education.level": ["hsc", "candidate", "candiate", "undergraduate",
                        "college student", "university student", "high school student"],
    "habit.routine": ["every day", "every morning", "i always", "i usually"],
    "possession.laptop": ["my laptop", "user's laptop", "laptop is"],
    "possession.car": ["my car", "user's car", "drive ", "drives ", "car is"],
    "relationship.friend": ["best friend", "my friend", "user's friend",
                            "close friend"],
    "identity.languages": ["speaks ", "speak ", "fluent in", "bilingual"],
    "travel.past": ["went to", "visited ", "travelled to", "traveled to",
                    "last year"],
    "exam_grievance": ["protest", "unfair", "objected", "complaint",
                       "treated unfairly", "raised concerns"],
    # v3.2: family relations read straight from the text, any grammatical
    # person. Word-boundary-safe cues only ("mom is", never bare "mom",
    # which substrings into "moment").
    "family.mother": ["my mother", "my mom", "my mum", "my mama",
                      "user's mother", "mother is", "mom is", "mum is",
                      "his mother", "her mother", "mom lives", "mother lives",
                      "mom's name", "mother's name"],
    "family.father": ["my father", "my dad", "my papa", "user's father",
                      "father is", "dad is", "his father", "her father",
                      "dad's name", "father's name"],
    "family.brother": ["my brother", "user's brother", "brother is",
                       "brother's name", "his brother", "her brother",
                       "brother named", "brother called", "have a brother"],
    "family.sister": ["my sister", "user's sister", "sister is",
                      "sister's name", "his sister", "her sister",
                      "sister named", "sister called", "have a sister",
                      "sister studying"],
    "family.son": ["my son", "user's son", "son is", "son's name",
                  "son named"],
    "family.daughter": ["my daughter", "user's daughter", "daughter is",
                        "daughter's name", "daughter named"],
    "family.grandmother": ["my grandmother", "my grandma", "user's grandmother",
                          "grandmother is", "grandma is", "grandma's name"],
    "family.grandfather": ["my grandfather", "my grandpa", "user's grandfather",
                          "grandfather is", "grandpa is", "grandpa's name"],
    "family.uncle": ["my uncle", "user's uncle", "uncle is", "uncle's name"],
    "family.aunt": ["my aunt", "user's aunt", "aunt is", "aunt's name"],
    "family.cousin": ["my cousin", "user's cousin", "cousin is",
                     "cousin's name"],
    "family.parents": ["my parents", "user's parents", "parents are",
                       "parents live"],
    "family.general": ["my family", "user's family", "family is",
                       "family lives", "family lives in"],
}
_PROFESSION_CUE = re.compile(
    r"\bis\s+an?\s+(?:\w+\s+)?(?:" + "|".join(sorted(PROFESSION_WORDS)) + r")\b", re.I)
_FAVORITE_CUE = re.compile(r"\b(?:favorite|favourite)\s+([a-z]+)\s+is\b", re.I)
# "user's mother is a teacher" is about the MOTHER, not the user. Likewise
# "user's dog's name is Milo" is a pet name, not the user's own name.
_OTHER_PERSON = re.compile(
    r"\b(?:mother|father|mom|dad|parent|parents|friend|friends|brother|sister|"
    r"wife|husband|partner|spouse|son|daughter|child|children|kid|kids|boss|"
    r"colleague|neighbour|neighbor|uncle|aunt|cousin|grandfather|grandmother)\b", re.I)
_POSSESSOR_NAME = re.compile(
    r"\b(?:dog|cat|pet|puppy|kitten|mother|father|mom|dad|parent|friend|"
    r"brother|sister|wife|husband|partner|spouse|son|daughter|child|kid|"
    r"boss|teacher|colleague|company|school)'s\s+name\s+is\b", re.I)
_PET_POSSESSOR_NAME = re.compile(
    r"\b(?:dog|cat|pet|puppy|kitten)'s\s+name\s+is\b", re.I)


def text_relation_keys(text: str) -> Set[str]:
    """Infer relation keys from raw memory text, in ANY grammatical person."""
    low = " " + us_spelling(normalize_text(text).lower()) + " "
    keys: Set[str] = set()
    for key, phrases in RELATION_KEYWORDS.items():
        if any(phrase in low for phrase in phrases):
            keys.add(key)
    if _PROFESSION_CUE.search(low) and not _OTHER_PERSON.search(low):
        keys.add("identity.profession")
    # A possessor's name must not become the user's identity.name.
    if _POSSESSOR_NAME.search(low):
        keys.discard("identity.name")
        keys.discard("identity.profession")
        keys.discard("work.employer")
        if _PET_POSSESSOR_NAME.search(low):
            keys.add("pet.name")
            if re.search(r"\bdog's\s+name", low):
                keys.add("pet.dog_name")
            if re.search(r"\bcat's\s+name", low):
                keys.add("pet.cat_name")
    if _OTHER_PERSON.search(low):
        keys.discard("identity.profession")
        keys.discard("work.employer")
    m = _FAVORITE_CUE.search(low)
    if m:
        keys.add("preference.favorite_" + _slug(m.group(1)))
    fact = extract_state_fact(text)
    if fact:
        keys.add(fact.key)
        keys.add(fact.key.split(".")[0])
    return keys


# ===========================================================================
# 3. Records
# ===========================================================================

@dataclass
class MemoryRecord:
    id: int
    text: str
    slots: List[str]
    state_key: str
    state_value: str
    state_value_norm: str
    state_kind: str
    is_current: int
    created_at: str
    updated_at: str
    source: str
    content_tokens: List[str] = field(default_factory=list, repr=False)
    stems: frozenset = frozenset()
    all_stems: frozenset = frozenset()      # includes function words, for word-deletion
    bigrams: frozenset = frozenset()
    text_keys: frozenset = frozenset()      # relations inferred from raw text
    concepts: Dict[str, float] = field(default_factory=dict, repr=False)
    # concept_profile() of the text, computed once per record (memory side)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "text": self.text, "slots": list(self.slots),
            "state_key": self.state_key, "state_value": self.state_value,
            "state_kind": self.state_kind, "current": bool(self.is_current),
            "created_at": self.created_at, "updated_at": self.updated_at,
            "source": self.source,
        }


@dataclass
class SearchHit:
    sentence_id: int
    text: str
    slots: List[str]
    state_key: str
    score: float
    reasons: List[str]

    @property
    def id(self) -> int:
        return self.sentence_id

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.sentence_id, "text": self.text, "slots": list(self.slots),
            "state_key": self.state_key, "score": self.score,
            "reasons": list(self.reasons),
        }


def format_hits(hits: Sequence[SearchHit]) -> str:
    """Human-readable rendering -- ALWAYS includes the memory ID."""
    if not hits:
        return ""
    n = len(hits)
    lines = [f"{n} relevant memor{'y' if n == 1 else 'ies'}:"]
    for h in hits:
        line = f"  [id={h.sentence_id}] {h.text}  (score={h.score:.2f})"
        if h.slots:
            line += f"  slots={','.join(h.slots)}"
        if h.reasons:
            line += f"  why={','.join(h.reasons)}"
        lines.append(line)
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _Index:
    """Cheap in-memory inverted view, rebuilt lazily after any write."""
    def __init__(self, records: List[MemoryRecord]):
        self.records = records
        self.n = len(records)
        df: Counter = Counter()
        for r in records:
            df.update(r.stems)
        denom = math.log((self.n + 1) / 1.5) or 1.0
        self.idf: Dict[str, float] = {}
        for term, d in df.items():
            self.idf[term] = min(1.0, max(0.0, math.log((self.n + 1) / (d + 0.5)) / denom))
        self.df = df


# ===========================================================================
# 4. MemoryStore
# ===========================================================================

MIN_SCORE = 0.40          # absolute relevance floor
ANCHOR_MIN_IDF = 0.15     # exclude only words that appear in ~every memory
WEAK_OVERLAP_MIN = 3      # shared terms needed when there is no anchor
# NOTE: the real anchor guard is GENERIC_STEMS (relation/category words can
# never be anchors), not rarity alone. A popular entity like "pizza" that
# appears in many memories must STILL be retrievable by name.


def _concept_strength(qw: float, rw: float) -> float:
    """
    Match strength between query-side and memory-side concept weights.

    A strong (unambiguous) query word matches ANY form of the same concept:
    "workout" reaches "training since 2022". A weak-but-valid query form
    (context-gated in query_concept_profile) also matches any form, scoring
    lower. Ranking only -- inclusion is decided by the shared key.
    """
    if qw >= 1.0 and rw >= 1.0:
        return 1.0
    if qw >= 1.0:
        return 0.85            # strong query word over weak memory form
    if rw >= 1.0:
        return 0.75            # weak query form over strong memory word
    return 0.60                # weak form on both sides: still the same topic


class MemoryStore:
    """
    SQLite-backed memory store.

    Public API (all results are plain JSON-serialisable dicts unless noted):
      save(text, ...)                 -> {"ok", "saved", "updated", ...}
      retrieve(prompt, limit=None)   -> List[SearchHit] (ALL relevant hits;
                                        never re-sends an already-sent memory;
                                        include_sent=True bypasses the tracker)
      reset_sent() / sent_ids()      -> clear / inspect the sent-dedup tracker
      explain(prompt)                -> why each memory was/wasn't returned
      list_all() / get(id) / find(word)  -> manual retrieval, always with IDs
      update(id|match, text)         -> manual overwrite
      delete(target)                 -> by ID or by WORD MATCHING
      update_from_prompt(prompt)     -> deterministic supersession
      process_prompt(prompt)         -> update + retrieve in ONE agent call
      history() / undo_last() / clear()
    """

    # v3.4 sent-dedup master switch (class-level default; any instance may
    # override). Set False (per instance or on the class) to restore classic
    # always-repeats behavior -- the built-in regression harness does exactly
    # that because it re-queries the same memories across sections.
    sent_dedup_enabled: bool = True

    SCHEMA_COLUMNS = {
        "slots": "TEXT NOT NULL DEFAULT ''",
        "state_key": "TEXT NOT NULL DEFAULT ''",
        "state_value": "TEXT NOT NULL DEFAULT ''",
        "state_kind": "TEXT NOT NULL DEFAULT ''",
        "is_current": "INTEGER NOT NULL DEFAULT 0",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT",
        "source": "TEXT NOT NULL DEFAULT ''",
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("MEMORY_DB_PATH", DEFAULT_DB)
        # v3.6: the default DB lives in <script dir>/memory/ -- create it.
        parent = os.path.dirname(os.path.abspath(str(self.db_path)))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._index_cache: Optional[_Index] = None
        # v3.4 sent-dedup: IDs already returned to the agent by an automatic
        # retrieval. In-memory on purpose: a new session starts clean.
        self._sent_ids: Set[int] = set()
        self._setup()

    # -- schema -------------------------------------------------------------

    def _setup(self):
        cur = self._conn.cursor()
        cur.execute("PRAGMA journal_mode = DELETE;")
        cur.execute("PRAGMA synchronous = NORMAL;")

        # Older versions used FTS triggers with no UPDATE trigger (edits went
        # stale). We no longer keep a mirrored FTS index: retrieval is done by
        # an exact, rebuildable in-memory IDF index built from these rows.
        for obj in ("sentences_ai", "sentences_ad", "sentences_au"):
            cur.execute(f"DROP TRIGGER IF EXISTS {obj};")
        for tbl in ("sentences_fts", "sentences_fts_data", "sentences_fts_idx",
                    "sentences_fts_docsize", "sentences_fts_config",
                    "term_index", "df_index"):
            cur.execute(f"DROP TABLE IF EXISTS {tbl};")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sentences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                slots TEXT NOT NULL DEFAULT '',
                state_key TEXT NOT NULL DEFAULT '',
                state_value TEXT NOT NULL DEFAULT '',
                state_kind TEXT NOT NULL DEFAULT '',
                is_current INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                source TEXT NOT NULL DEFAULT ''
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                reason TEXT NOT NULL DEFAULT '',
                at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migrate DBs written by older versions (add missing columns).
        cols = {r[1] for r in cur.execute("PRAGMA table_info(sentences)")}
        for name, decl in self.SCHEMA_COLUMNS.items():
            if name not in cols:
                cur.execute(f"ALTER TABLE sentences ADD COLUMN {name} {decl};")

        # Backfill state metadata for legacy rows (text is left untouched).
        for row in cur.execute("SELECT id, text, slots, state_key FROM sentences").fetchall():
            if row["state_key"]:
                continue
            fact = extract_state_fact(row["text"])
            if fact:
                slots = [s for s in (row["slots"] or "").split(",") if s]
                if fact.key not in slots:
                    slots.append(fact.key)
                cur.execute(
                    "UPDATE sentences SET slots=?, state_key=?, state_value=?, state_kind=?, is_current=? WHERE id=?",
                    (",".join(slots), fact.key, fact.value, fact.kind, int(fact.current), row["id"]))
        self._conn.commit()
        self._index_cache = None

    def _invalidate(self):
        self._index_cache = None

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        slots = [s for s in (row["slots"] or "").split(",") if s]
        state_key = row["state_key"] or ""
        value = row["state_value"] or ""
        kind = row["state_kind"] or ""
        all_tokens = tokenize(row["text"])
        ct = [t for t in all_tokens if t not in FILLER_WORDS]
        ct_stems = [stem(t) for t in ct]
        st = frozenset(ct_stems)
        return MemoryRecord(
            id=row["id"], text=row["text"], slots=slots, state_key=state_key,
            state_value=value, state_value_norm=normalize_value(value),
            state_kind=kind, is_current=int(row["is_current"] or 0),
            created_at=row["created_at"] or "", updated_at=row["updated_at"] or "",
            source=row["source"] or "", content_tokens=ct_stems,
            stems=st, all_stems=frozenset(stem(t) for t in all_tokens),
            bigrams=frozenset(_bigrams(ct_stems)),
            text_keys=frozenset(text_relation_keys(row["text"])),
            concepts=concept_profile(row["text"]),
        )

    def _index(self) -> _Index:
        if self._index_cache is None:
            rows = self._conn.execute("SELECT * FROM sentences ORDER BY id ASC").fetchall()
            self._index_cache = _Index([self._record_from_row(r) for r in rows])
        return self._index_cache

    def _row(self, memory_id: int) -> Optional[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM sentences WHERE id = ?", (memory_id,)).fetchone()

    def _snapshot(self, memory_id: int) -> Optional[Dict[str, Any]]:
        r = self._row(memory_id)
        return self._record_from_row(r).to_dict() if r else None

    def _log(self, memory_id: int, action: str, before: Optional[Dict[str, Any]],
             after: Optional[Dict[str, Any]], reason: str = ""):
        self._conn.execute(
            "INSERT INTO history (memory_id, action, before_json, after_json, reason, at) VALUES (?,?,?,?,?,?)",
            (memory_id, action, json.dumps(before) if before is not None else None,
             json.dumps(after) if after is not None else None, reason, _now()))

    # -- key matching -------------------------------------------------------

    @staticmethod
    def _key_matches(rec: MemoryRecord, key: str) -> bool:
        # Slots/state_key come from structured extraction at save time;
        # text_keys are inferred from the raw sentence (works for third-person
        # or legacy memories that were saved with no slots at all).
        candidates = list(rec.slots) + list(rec.text_keys)
        if rec.state_key:
            candidates.append(rec.state_key)
        for s in candidates:
            if s and (s == key or s.startswith(key + ".")):
                return True
        return False

    # -- READ: automatic retrieval -----------------------------------------

    def _score_prompt(self, prompt: str) -> Tuple[List[SearchHit], Dict[str, Any]]:
        idx = self._index()
        diag: Dict[str, Any] = {
            "prompt": prompt, "prepared_prompt": "", "total_memories": idx.n, "gate": "open",
            "query_content": [], "known_terms": [], "intents": [],
            "min_score": MIN_SCORE, "candidates": [], "hits": [],
        }
        q = prepare_query(prompt)
        diag["prepared_prompt"] = q
        if not q:
            diag["gate"] = "empty_prompt"
            return [], diag
        if idx.n == 0:
            diag["gate"] = "empty_memory"
            return [], diag

        if is_meta_query(q):
            diag["gate"] = "meta_query"
            # v3.5: answer with the USER's memories only. General notes and
            # other people's actions are NOT part of "who am i".
            user_recs = [r for r in idx.records
                         if is_user_scoped_memory(r.text, r.state_key)]
            diag["meta_total_memories"] = idx.n
            diag["meta_user_scoped"] = len(user_recs)
            hits = [SearchHit(r.id, r.text, list(r.slots), r.state_key, 1.0,
                              ["meta_query"]) for r in user_recs]
            diag["hits"] = [h.as_dict() for h in hits]
            return hits, diag

        gated = gate_reason(q)
        if gated:
            diag["gate"] = gated
            return [], diag

        # v3.3: "user"/"users" are stripped like stopwords (see
        # SELF_REF_QUERY_TOKENS) -- the subject of every third-person memory
        # must never open a topic.
        content = [t for t in tokenize(q)
                   if t not in FILLER_WORDS and t not in SELF_REF_QUERY_TOKENS]
        q_stems = list(dict.fromkeys(stem(t) for t in content))
        q_known = {s for s in q_stems if s in idx.idf}
        intents = detect_intents(q)
        aspect = match_aspect(q)
        aspect_hits: Dict[int, str] = (
            {r.id: aspect.name for r in idx.records if aspect.memory.search(r.text)}
            if aspect else {})
        diag["query_content"] = content
        diag["known_terms"] = sorted(q_known)
        diag["intents"] = sorted({label for _, label in intents})
        diag["aspect"] = aspect.name if aspect else None
        # A third-person factual question ("when did the gym open?") must not
        # fire on ONE coincidental word. It needs 2+ distinctive anchors or an
        # exact phrase, unless it explicitly names the user or a recall cue.
        third_person_question = bool(
            is_question(q) and not SELF_MARKER.search(q) and not RECALL_CUE.search(q)
            and not re.search(r"\b(?:the\s+)?user\b", q, re.I))
        diag["third_person_question"] = third_person_question

        # --- SELECTIVE concept expansion (v3.2) -----------------------------
        # Used as a FALLBACK when no exact anchor / aspect / intent answers
        # the prompt. v3.2 ALSO admits plain first-person STATEMENTS ("i am
        # going to workout now") because the retriever sits between the user
        # and an AI agent: whatever the user TALKS about should surface the
        # related memories. Precision is maintained by query_concept_profile()
        # (strong/weak form gating, noun-usage guard, strong dominance) and by
        # the same no-anchor suppression that always applied to lookups.
        user_directed = bool(SELF_MARKER.search(q) or
                             re.search(r"\b(?:the\s+)?user\b", q, re.I))
        is_lookup = bool(is_question(q) or RECALL_CUE.search(q) or LOOKUP_PREFIX.search(q))
        # Casual texting drops the subject entirely ("gonna hit the gym",
        # "movie night tonight"): no pronoun and no other person in sight means
        # the statement is about the user. Any second/third-person subject
        # keeps it out -- including an article-headed one ("the sun rises in
        # the east" is a world fact, not the user talking about themselves).
        if not user_directed and not is_lookup and not third_person_question:
            if (not re.match(r"\s*(?:the|a|an|this|that|these|those|it)\b", q, re.I)
                    and not re.search(r"\b(?:you|your|yours|he|him|his|she|her|hers|they|them|"
                                      r"their|it|its|we|us|our|ours|someone|anybody|"
                                      r"everybody|everyone)\b", q, re.I)
                    and not _OTHER_PERSON.search(q)):
                user_directed = True
        # An intent that ACTUALLY matches a memory already answers the query;
        # concept expansion must not pile unrelated memories on top of it. But
        # a bogus "my_noun:<unknown>" intent must not suppress a valid concept
        # match ("tell me about my fitness journey").
        intent_hits = {r.id for r in idx.records
                       if any(self._key_matches(r, k) for k, _ in intents)}
        diag["intent_hits"] = sorted(intent_hits)
        # "what is my height?" has a genuine attribute intent that NO memory
        # satisfies -- do not let a coincidental concept answer it.
        unresolved_attribute_lookup = bool(
            not intent_hits
            and any(label.startswith(("what_is_my:", "my_noun:"))
                    for _, label in intents)
            and re.search(r"\bwhat(?:'s|\s+is)\s+my\b", q, re.I))
        # Scope-shift words only block LOOKUPS ("what is my plan for today?");
        # a first-person statement about a topic ("i'm going to the gym
        # today") is exactly the conversational case that must match.
        scope_blocks_lookup = bool(SCOPE_SHIFT.search(q)) and is_lookup
        allow_concepts = bool(user_directed and not third_person_question
                              and not aspect_hits and not intent_hits
                              and not unresolved_attribute_lookup
                              and not scope_blocks_lookup)
        concept_hits: Dict[int, Tuple[List[str], float]] = {}
        if allow_concepts:
            q_profile = query_concept_profile(q)
            if q_profile:
                for rec in idx.records:
                    r_profile = rec.concepts
                    shared = [c for c in q_profile if c in r_profile]
                    if not shared:
                        continue
                    strength = max(_concept_strength(q_profile[c], r_profile[c])
                                   for c in shared)
                    prev = concept_hits.get(rec.id)
                    if prev is None or strength > prev[1]:
                        concept_hits[rec.id] = (sorted(shared), strength)
        exact_anchor_hits = any(
            any(idx.idf[s] >= ANCHOR_MIN_IDF and s not in GENERIC_STEMS
                for s in (q_known & set(r.stems)))
            for r in idx.records)
        use_concepts = bool(concept_hits and not exact_anchor_hits)
        diag["concepts"] = sorted({n for names, _ in concept_hits.values() for n in names})
        diag["concept_fallback"] = use_concepts

        # An aspect/concept hit is itself a valid gate (the query may share NO
        # word with the memory, e.g. "gym" vs "strength training").
        if not q_known and not intents and not aspect_hits and not concept_hits:
            diag["gate"] = "no_known_terms_or_intent"
            return [], diag

        # (unresolved_attribute_lookup was computed above, before the
        # concept gate: it also suppresses concept expansion.)
        diag["unresolved_attribute_lookup"] = unresolved_attribute_lookup

        q_weight = sum(idx.idf[s] for s in q_known)
        q_bi = frozenset(_bigrams(q_stems))
        hits: List[SearchHit] = []

        for rec in idx.records:
            matched = q_known & set(rec.stems)
            w_match = sum(idx.idf[s] for s in matched)
            w_doc = sum(idx.idf[s] for s in rec.stems)
            q_cov = w_match / q_weight if q_weight else 0.0
            d_cov = w_match / w_doc if w_doc else 0.0
            has_phrase = bool(q_bi & rec.bigrams)
            phrase = 0.08 if has_phrase else 0.0
            score = min(1.0, 0.70 * q_cov + 0.30 * d_cov + phrase)

            anchors = sorted(s for s in matched
                             if idx.idf[s] >= ANCHOR_MIN_IDF and s not in GENERIC_STEMS)
            strong_anchor = any(idx.idf[s] >= 0.60 and s not in GENERIC_STEMS
                                for s in matched)
            intent_keys = [k for k, _ in intents if self._key_matches(rec, k)]
            a_hit = aspect_hits.get(rec.id)
            reasons: List[str] = []
            decision = "reject"

            c_hit = concept_hits.get(rec.id) if use_concepts else None
            if a_hit:
                score = max(score, aspect.score if aspect else 0.72)
                reasons.append("aspect:" + a_hit)
                decision = "include" if score >= MIN_SCORE else "below_floor"
            elif c_hit:
                score = max(score, 0.60 + 0.10 * c_hit[1])
                reasons.append("concept:" + ",".join(c_hit[0]))
                decision = "include" if score >= MIN_SCORE else "below_floor"
            elif use_concepts:
                reasons.append("not_about_asked_concept")
            elif (third_person_question and len(anchors) < 2 and not has_phrase
                  and not intent_keys):
                # An explicit self/attribute intent beats the third-person
                # guard ("what unfair thing happened?" -> the protest memory).
                reasons.append("third_person_single_word_overlap")
            elif aspect_hits and not strong_anchor:
                # The user asked about ONE aspect; don't leak other memories
                # that merely share a generic word with the question.
                reasons.append("not_about_asked_aspect")
            elif intent_keys:
                score = max(score, 0.62)
                reasons.append("intent:" + intent_keys[0])
                decision = "include" if score >= MIN_SCORE else "below_floor"
            elif anchors and not unresolved_attribute_lookup:
                reasons.append("anchor:" + ",".join(anchors[:3]))
                decision = "include" if score >= MIN_SCORE else "below_floor"
            elif (not anchors and 1 <= len(matched) <= 2 and q_cov >= 0.75
                  and score >= 0.70 and is_lookup and user_directed
                  and not third_person_question and not aspect_hits
                  and not intent_hits and not SCOPE_SHIFT.search(q)
                  and not GENERATION_TASK.search(q) and not TASK_REQUEST.search(q)
                  # "what is my height?" must NOT match "afraid of heights":
                  # an attribute lookup is answered by intent routing ONLY.
                  and not re.search(r"\bwhat(?:'s|\s+is)\s+my\b", q, re.I)):
                # "what food do i like?", "what do i watch?", "what laptop do
                # i use?" -- the only shared word is a noun that the generic-
                # word guard refuses to use as an anchor. For a first-person
                # lookup with near-total query coverage, that noun IS the
                # topic, so it is safe to match on it alone.
                reasons.append("topic_anchor:" + ",".join(sorted(matched)))
                decision = "include"
            elif len(matched) >= WEAK_OVERLAP_MIN and q_cov >= 0.75 and score >= 0.55:
                reasons.append("broad_overlap")
                decision = "include"
            else:
                if not matched:
                    reasons.append("no_shared_terms")
                elif not anchors:
                    reasons.append("only_generic_overlap")
                else:
                    reasons.append("too_few_shared_terms")

            diag["candidates"].append({
                "id": rec.id, "text": rec.text, "matched": sorted(matched),
                "anchors": anchors, "aspect_hit": a_hit,
                "concept_hit": c_hit[0] if c_hit else None,
                "q_coverage": round(q_cov, 3), "d_coverage": round(d_cov, 3),
                "score": round(score, 3), "decision": decision,
                "reason": ",".join(reasons),
            })

            if decision == "include":
                hits.append(SearchHit(rec.id, rec.text, list(rec.slots),
                                     rec.state_key, round(score, 4), reasons))

        hits.sort(key=lambda h: (-h.score, h.sentence_id))
        diag["hits"] = [h.as_dict() for h in hits]
        return hits, diag

    def retrieve_hits(self, prompt: str, limit: Optional[int] = None, *,
                      include_sent: bool = False,
                      mark_sent: bool = True) -> Tuple[List[SearchHit], int]:
        """
        Core of retrieve(): returns (hits, n_hidden_by_sent_dedup).

        v3.6: NO exemptions -- meta answers are dedup'd like everything else,
        and the dedup is all-or-nothing: one already-shown memory anywhere in
        the answer suppresses the ENTIRE answer (fresh memories stay
        un-marked, so reset_sent() makes them sendable).
        """
        hits, diag = self._score_prompt(prompt)
        # v3.6: strict, ALL-OR-NOTHING dedup -- meta answers included. If the
        # answer would repeat even ONE already-shown memory, the WHOLE answer
        # is suppressed and nothing is marked sent (the fresh memories stay
        # sendable until reset_sent()). With include_sent=True the tracker is
        # bypassed entirely; mark_sent=False reads without tracking.
        if (self.sent_dedup_enabled and not include_sent and self._sent_ids
                and any(h.sentence_id in self._sent_ids for h in hits)):
            return [], len(hits)
        if limit is not None:
            hits = hits[:limit]
        if mark_sent:
            self._sent_ids.update(h.sentence_id for h in hits)
        return list(hits), 0

    def retrieve(self, prompt: str, limit: Optional[int] = None, *,
                 include_sent: bool = False, mark_sent: bool = True) -> List[SearchHit]:
        """
        Return ALL memories genuinely relevant to `prompt`, with IDs.

        `limit=None` (default) means no top-K truncation. Pass an int only if
        you explicitly want to cap the output. Unrelated prompts -> [].

        v3.4/v3.6 sent-dedup: every hit is remembered as "already sent", and
        dedup is ALL-OR-NOTHING (v3.6): if an answer would repeat even one
        already-shown memory, NOTHING is returned ([]) and nothing new is
        marked sent. include_sent=True bypasses the tracker; reset_sent()
        clears it. Meta queries ("what do you know about me?") are dedup'd
        like every other answer and respond with the USER-scoped memories
        only (v3.5). Superseding or deleting a memory un-marks it, so an
        UPDATED fact is sent again.
        """
        hits, _ = self.retrieve_hits(prompt, limit, include_sent=include_sent,
                                     mark_sent=mark_sent)
        return hits

    def explain(self, prompt: str) -> Dict[str, Any]:
        """Diagnostics: why every memory was included or rejected. For /why."""
        _, diag = self._score_prompt(prompt)
        diag["ok"] = True
        return diag

    # -- READ: manual retrieval (always with IDs) ---------------------------

    def list_all(self) -> List[Dict[str, Any]]:
        """Manual retrieval: every stored memory with its ID."""
        return [r.to_dict() for r in self._index().records]

    def get(self, memory_id: int) -> Optional[Dict[str, Any]]:
        """Manual retrieval of one memory by ID."""
        row = self._row(int(memory_id))
        return self._record_from_row(row).to_dict() if row else None

    def find(self, word: str, mode: str = "word") -> List[Dict[str, Any]]:
        """
        Manual retrieval by word. `mode='word'` = whole-word (stemmed) match,
        `mode='contains'` = raw substring match, `mode='id'` = exact ID.
        """
        w = normalize_text(word)
        if not w:
            return []
        if mode == "id" or w.isdigit():
            row = self.get(int(w)) if w.isdigit() else None
            return [row] if row else []
        if mode == "contains":
            needle = w.lower()
            return [r.to_dict() for r in self._index().records if needle in r.text.lower()]
        targets = {stem(t) for t in tokenize(w)} or {stem(w.lower())}
        # Single word: match it. Multi-word phrase: require ALL of its words,
        # so deleting "climate protest" doesn't wipe unrelated climate notes.
        if len(targets) == 1:
            return [r.to_dict() for r in self._index().records
                    if targets & set(r.all_stems)]
        return [r.to_dict() for r in self._index().records
                if targets.issubset(set(r.all_stems))]

    # -- WRITE --------------------------------------------------------------

    def _insert(self, sentence: str, slots: Optional[List[str]], source: str,
                reason: str = "created") -> Dict[str, Any]:
        fact = extract_state_fact(sentence)
        slot_list = list(slots) if slots is not None else detect_slots(sentence)
        if fact:
            for extra in (fact.key, fact.key.split(".")[0]):
                if extra not in slot_list:
                    slot_list.append(extra)
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO sentences (text, slots, state_key, state_value, state_kind, is_current, "
            "created_at, updated_at, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (sentence, ",".join(slot_list), fact.key if fact else "",
             fact.value if fact else "", fact.kind if fact else "",
             int(fact.current) if fact else 0, now, now, source))
        mid = int(cur.lastrowid)
        after = self._snapshot(mid)
        self._log(mid, "create", None, after, reason)
        self._invalidate()
        return after or {}

    def _find_overlaps(self, fact: StateFact) -> List[MemoryRecord]:
        """Same relation + same-ish object type; never multi-valued notes."""
        out = []
        for rec in self._index().records:
            if rec.state_key != fact.key or not rec.state_value_norm:
                continue
            if fact.negated:
                if rec.state_value_norm == fact.value_norm:
                    out.append(rec)
            elif rec.state_value_norm != fact.value_norm:
                out.append(rec)
        return sorted(out, key=lambda r: r.id, reverse=True)

    def _write_sentence(self, sentence: str, *, insert_if_no_overlap: bool,
                        auto_update: bool, source: str,
                        slots: Optional[List[str]] = None) -> Dict[str, Any]:
        """One sentence -> insert OR deterministic supersession."""
        fact = extract_state_fact(sentence) if auto_update else None
        if fact and fact.eligible_for_update:
            overlaps = self._find_overlaps(fact)
            if len(overlaps) > 1:
                return {
                    "ok": True, "action": "ambiguous", "changed": False,
                    "reason": "more than one memory shares this relation; update manually by id",
                    "relation": fact.key,
                    "candidates": [{"id": r.id, "text": r.text} for r in overlaps],
                    "fact": fact.as_dict(),
                }
            if len(overlaps) == 1:
                target = overlaps[0]
                if fact.negated:
                    removed = self._delete_by_id(target.id, reason="negated_by_prompt:" + fact.key)
                    return {"ok": True, "action": "removed", "changed": True,
                            "id": target.id, "old_text": target.text,
                            "reason": "explicit negation of a current fact",
                            "fact": fact.as_dict(), "removed_record": removed}
                updated = self.update(target.id, sentence, slots=slots,
                                      reason="superseded_by_prompt:" + fact.key)
                # Flat summary fields make this trivially consumable by an agent.
                first = (updated.get("updated") or [{}])[0]
                updated.update({
                    "ok": True, "action": "superseded", "changed": True,
                    "id": first.get("id", target.id),
                    "old_text": (first.get("before") or {}).get("text", target.text),
                    "new_text": (first.get("after") or {}).get("text", sentence),
                    "relation": fact.key, "fact": fact.as_dict(),
                })
                return updated
            # No overlap.
            if not insert_if_no_overlap:
                return {"ok": True, "action": "none", "changed": False,
                        "reason": "no overlapping memory for this relation",
                        "fact": fact.as_dict()}

        if not insert_if_no_overlap:
            return {"ok": True, "action": "none", "changed": False,
                    "reason": "prompt is not an eligible state change"}

        rec = self._insert(sentence, slots, source)
        return {"ok": True, "action": "created", "changed": True,
                "id": rec["id"], "text": rec["text"], "slots": rec["slots"],
                "state_key": rec["state_key"]}

    def save(self, text: Any, *, slots: Optional[List[str]] = None,
             source: str = "", auto_update: bool = True) -> Dict[str, Any]:
        """
        SAVE -- designed to be called directly by an AI agent's tool call.

        `text` may be a string (split into sentences), or a list of strings.
        Returns a JSON-serialisable dict with the ID of every memory written.

        If a sentence is a CURRENT single-valued fact whose relation already
        exists with a different value, the old memory is OVERWRITTEN in place
        (same ID) instead of creating a contradiction. Set auto_update=False
        to always append.
        """
        if isinstance(text, (list, tuple)):
            chunks: List[str] = []
            for item in text:
                chunks.extend(split_into_sentences(str(item)))
        else:
            chunks = split_into_sentences(str(text or ""))
        if not chunks:
            return {"ok": False, "action": "empty", "error": "nothing to save (empty text)",
                    "saved": [], "updated": [], "count": 0}

        saved: List[Dict[str, Any]] = []
        updated: List[Dict[str, Any]] = []
        notes: List[Dict[str, Any]] = []
        one_slot_batch = slots if len(chunks) == 1 else None
        for sentence in chunks:
            res = self._write_sentence(sentence, insert_if_no_overlap=True,
                                       auto_update=auto_update, source=source,
                                       slots=one_slot_batch)
            if res["action"] == "created":
                saved.append(res)
            elif res.get("changed"):
                updated.append(res)
            else:
                notes.append(res)
        self._conn.commit()
        return {
            "ok": True, "action": "save", "saved": saved, "updated": updated,
            "notes": notes, "count": len(saved),
            "updated_count": len(updated),
            "ids": [r["id"] for r in saved],
            "message": f"created {len(saved)}, superseded {len(updated)}",
        }

    def update_from_prompt(self, prompt: str, *, source: str = "prompt") -> Dict[str, Any]:
        """
        Read a RAW USER PROMPT and overwrite overlapping memories.

        Example: after "I'm playing Valheim now", the prompt
        "I'm playing Crimson Desert right now" supersedes the Valheim memory
        in place. Does NOT save anything new (use `save` for that).
        """
        chunks = split_into_sentences(prompt)
        results = []
        for sentence in chunks:
            results.append(self._write_sentence(
                sentence, insert_if_no_overlap=False, auto_update=True,
                source=source))
        self._conn.commit()
        changed = [r for r in results if r.get("changed")]
        return {
            "ok": True, "action": "update_from_prompt", "changed": bool(changed),
            "results": results, "updated_count": len(changed),
            "message": ("superseded " + ", ".join(str(r.get("id", "?")) for r in changed)
                        if changed else "no overlapping memory updated"),
        }

    def process_prompt(self, prompt: str, *, auto_update: bool = True,
                       include_sent: bool = False) -> Dict[str, Any]:
        """
        THE main entry point for a user prompt:
          1. optionally supersede a stale overlapping fact,
          2. return ALL relevant memories (with IDs) -- or NOTHING when the
             answer would repeat an already-sent memory (v3.6 strict
             all-or-nothing dedup; include_sent=True bypasses).
        One call, JSON out, no LLM. This is what an agent should call per turn.
        """
        update = (self.update_from_prompt(prompt) if auto_update else
                  {"ok": True, "action": "update_from_prompt", "changed": False,
                   "updated_count": 0, "results": [], "message": "auto_update disabled"})
        hits, sent_hidden = self.retrieve_hits(prompt, None,
                                               include_sent=include_sent, mark_sent=True)
        mems = [h.as_dict() for h in hits]
        return {
            "ok": True, "action": "process_prompt", "prompt": prompt,
            "update": update, "count": len(mems), "memories": mems,
            "sent_hidden": sent_hidden,
            "rendered": format_hits(hits),
        }

    # -- WRITE: manual update ----------------------------------------------

    def update(self, memory_id: Optional[int] = None, text: str = "", *,
               match: Optional[str] = None, slots: Optional[List[str]] = None,
               all_matches: bool = False, reason: str = "manual_update") -> Dict[str, Any]:
        """
        Manual overwrite. Give `memory_id`, or `match` (a word/phrase) to
        target memories by word matching. Returns before/after WITH IDs.
        """
        if not normalize_text(text):
            return {"ok": False, "error": "text is required"}
        targets: List[int] = []
        if memory_id is not None:
            targets = [int(memory_id)]
        elif match:
            found = self.find(match)
            if not all_matches:
                found = found[:1]
            targets = [int(r["id"]) for r in found]
        else:
            return {"ok": False, "error": "provide memory_id or match"}
        if not targets:
            return {"ok": True, "action": "update", "changed": False,
                    "updated": [], "message": "no memory matched"}

        updated = []
        for mid in targets:
            before = self._snapshot(mid)
            if before is None:
                continue
            fact = extract_state_fact(text)
            new_slots = list(slots) if slots is not None else detect_slots(text)
            if fact:
                for extra in (fact.key, fact.key.split(".")[0]):
                    if extra not in new_slots:
                        new_slots.append(extra)
            self._conn.execute(
                "UPDATE sentences SET text=?, slots=?, state_key=?, state_value=?, state_kind=?, "
                "is_current=?, updated_at=? WHERE id=?",
                (normalize_text(text), ",".join(new_slots), fact.key if fact else "",
                 fact.value if fact else "", fact.kind if fact else "",
                 int(fact.current) if fact else 0, _now(), mid))
            after = self._snapshot(mid)
            self._log(mid, "update", before, after, reason)
            # v3.4: the fact CHANGED -> the agent must see it again.
            self._sent_ids.discard(mid)
            updated.append({"id": mid, "before": before, "after": after})
        self._invalidate()
        self._conn.commit()
        return {"ok": True, "action": "update", "changed": bool(updated),
                "updated": updated, "updated_count": len(updated), "reason": reason}

    # -- WRITE: deletion by word -------------------------------------------

    def _delete_by_id(self, memory_id: int, reason: str = "deleted") -> Dict[str, Any]:
        before = self._snapshot(memory_id)
        if before is None:
            return {"ok": True, "action": "delete", "removed": [], "count": 0}
        self._conn.execute("DELETE FROM sentences WHERE id = ?", (memory_id,))
        self._log(memory_id, "delete", before, None, reason)
        self._sent_ids.discard(memory_id)
        self._invalidate()
        return {"ok": True, "action": "delete",
                "removed": [before], "count": 1, "reason": reason}

    def delete(self, target: Any, *, mode: str = "auto") -> Dict[str, Any]:
        """
        DELETE by WORD MATCHING (or by ID).

        memory_delete({"target": "protest"}) removes every memory containing
        the whole word 'protest' (case-insensitive, stemmed). Digits / ints
        delete by exact ID. mode='contains' uses a raw substring match.
        Returns the removed records WITH their IDs.
        """
        if target is None:
            return {"ok": False, "error": "target is required"}
        raw = normalize_text(str(target))
        if not raw:
            return {"ok": False, "error": "target is empty"}

        if mode == "id" or (mode == "auto" and raw.isdigit()):
            res = self._delete_by_id(int(raw), "delete_by_id")
        else:
            if mode == "contains":
                matches = self.find(raw, mode="contains")
                reason = "delete_by_contains:" + raw
            else:
                matches = self.find(raw, mode="word")
                reason = "delete_by_word:" + raw
            removed = []
            for m in matches:
                removed.extend(self._delete_by_id(int(m["id"]), reason)["removed"])
            res = {"ok": True, "action": "delete", "removed": removed,
                   "count": len(removed), "reason": reason}
        self._conn.commit()
        return res

    # aliases that read nicely for agents / humans
    delete_by_word = delete
    delete_matching = delete

    # -- history / undo / clear --------------------------------------------

    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [{"history_id": r["id"], "memory_id": r["memory_id"], "action": r["action"],
                 "before": json.loads(r["before_json"]) if r["before_json"] else None,
                 "after": json.loads(r["after_json"]) if r["after_json"] else None,
                 "reason": r["reason"], "at": r["at"]} for r in rows]

    def _restore(self, snap: Dict[str, Any]):
        mid = int(snap["id"])
        exists = self._row(mid) is not None
        vals = (snap["text"], ",".join(snap.get("slots", [])), snap.get("state_key", ""),
                snap.get("state_value", ""), snap.get("state_kind", ""),
                int(bool(snap.get("current"))), snap.get("source", ""), _now())
        if exists:
            self._conn.execute(
                "UPDATE sentences SET text=?, slots=?, state_key=?, state_value=?, state_kind=?, "
                "is_current=?, source=?, updated_at=? WHERE id=?", vals + (mid,))
        else:
            self._conn.execute(
                "INSERT INTO sentences (id, text, slots, state_key, state_value, state_kind, "
                "is_current, source, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid,) + vals[:7] + (snap.get("created_at") or _now(), _now()))

    def undo_last(self) -> Dict[str, Any]:
        """Undo the most recent create/update/delete. Repeat to step back."""
        row = self._conn.execute("SELECT * FROM history ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return {"ok": True, "action": "undo", "changed": False, "message": "nothing to undo"}
        before = json.loads(row["before_json"]) if row["before_json"] else None
        action = row["action"]
        mid = int(row["memory_id"])
        if action == "create":
            self._conn.execute("DELETE FROM sentences WHERE id = ?", (mid,))
            description = f"removed memory {mid} created by the last save"
        elif action in ("update", "delete") and before:
            self._restore(before)
            description = f"restored memory {mid} to its previous text"
        else:
            description = "nothing restorable"
        self._conn.execute("DELETE FROM history WHERE id = ?", (row["id"],))
        # Conservative: an undo can revert any action; keep the dedup tracker
        # consistent by forgetting it entirely (cheap, and undo is rare).
        self._sent_ids.clear()
        self._invalidate()
        self._conn.commit()
        return {"ok": True, "action": "undo", "changed": True,
                "undone_action": action, "memory_id": mid, "message": description}

    def clear(self, keep_history: bool = False) -> Dict[str, Any]:
        n = len(self.list_all())
        self._conn.execute("DELETE FROM sentences")
        if not keep_history:
            self._conn.execute("DELETE FROM history")
        self._sent_ids.clear()
        self._invalidate()
        self._conn.commit()
        return {"ok": True, "action": "clear", "removed": n,
                "message": f"removed {n} memories"}

    # -- sent-tracking (v3.4 dedup) -----------------------------------------

    def reset_sent(self) -> Dict[str, Any]:
        """
        Forget the "already sent" tracker: after this call the next automatic
        retrieval can return ANY relevant memory again. Deletes nothing from
        the database -- it only clears the in-memory set of sent IDs.
        """
        n = len(self._sent_ids)
        self._sent_ids.clear()
        return {"ok": True, "action": "reset_sent", "cleared": n,
                "message": f"cleared {n} sent markers; memories can be sent again"}

    def sent_ids(self) -> List[int]:
        """IDs currently marked as already-sent (for debugging / persistence)."""
        return sorted(self._sent_ids)

    def mark_sent(self, memory_ids: Iterable[int]) -> Dict[str, Any]:
        """
        Manually mark IDs as already-sent, e.g. after reviewing memories via
        memory_list and injecting them yourself. Accepts a single int too.
        """
        if isinstance(memory_ids, (int, str)):
            memory_ids = [memory_ids]
        marked = sorted({int(i) for i in memory_ids})
        self._sent_ids.update(marked)
        return {"ok": True, "action": "mark_sent", "marked": marked, "count": len(marked)}

    def close(self):
        try:
            self._conn.commit()
            self._conn.close()
        except Exception:
            pass


# ===========================================================================
# 5. Agent tool layer (JSON in / JSON out)
# ===========================================================================

def _spec(name: str, description: str, properties: Dict[str, Any],
          required: List[str]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_S = {"type": "string"}
_B = {"type": "boolean"}
_I = {"type": "integer"}

TOOL_SPECS: List[Dict[str, Any]] = [
    _spec("memory_save",
          "Save one or more memories. Call this with the distilled facts you want to keep. "
          "Returns the ID of every stored memory. If a saved fact is a CURRENT single-valued "
          "state (e.g. 'I am playing X right now') that conflicts with an older one on the same "
          "relation, the old memory is overwritten in place instead of creating a contradiction.",
          {"text": {"type": ["string", "array"], "items": {"type": "string"},
                    "description": "Memory text, or a list of memory strings. Multi-sentence text is split."},
           "slots": {"type": "array", "items": {"type": "string"},
                     "description": "Optional explicit slot tags; normally omitted (auto-detected)."},
           "source": _S, "auto_update": _B},
          ["text"]),
    _spec("memory_retrieve",
          "Given a raw user prompt, return ALL memories genuinely relevant to it, each with its "
          "numeric id, score and reason. Unrelated prompts return an empty list. STRICT "
          "sent-dedup (v3.6): memories already returned are never repeated -- and if an "
          "answer would contain even one already-returned memory, NOTHING is returned "
          "('sent_hidden' counts the suppressed candidates). Pass include_sent=true to "
          "bypass the tracker, or call memory_reset_sent to clear it. No LLM involved.",
          {"prompt": _S, "limit": _I, "include_sent": _B, "mark_sent": _B}, ["prompt"]),
    _spec("memory_process_prompt",
          "Preferred per-turn call: process a user prompt by (1) superseding any stale overlapping "
          "memory and (2) returning ALL relevant memories with IDs, ready to inject into context.",
          {"prompt": _S, "auto_update": _B}, ["prompt"]),
    _spec("memory_update_from_prompt",
          "Read a raw user prompt and overwrite memories that overlap the new fact. ONLY fires for "
          "first-person present-tense state changes with a now/currently/started cue on the same "
          "relation, e.g. 'I am playing Crimson Desert right now' after 'I am playing Valheim now'. "
          "Does not save new memories.",
          {"prompt": _S}, ["prompt"]),
    _spec("memory_list",
          "Manual retrieval: list EVERY stored memory with its id, text, slots and state key.",
          {}, []),
    _spec("memory_get", "Manual retrieval of one memory by its numeric id.", {"id": _I}, ["id"]),
    _spec("memory_find",
          "Manual retrieval by word: all memories containing that word, with IDs.",
          {"word": _S, "mode": {"type": "string", "enum": ["word", "contains", "id"]}}, ["word"]),
    _spec("memory_explain",
          "Debug a retrieval: show the gate, query terms, intents and why each memory was "
          "included or rejected. Use this to verify there are no false triggers.",
          {"prompt": _S}, ["prompt"]),
    _spec("memory_update",
          "Manually overwrite a memory. Address it by numeric id, or by `match` (a word/phrase); "
          "set all_matches=true to update every word-matched memory. Returns before/after with IDs.",
          {"id": _I, "match": _S, "text": _S,
           "slots": {"type": "array", "items": {"type": "string"}},
           "all_matches": _B}, ["text"]),
    _spec("memory_delete",
          "Delete memories by WORD MATCHING: pass a word like 'protest' and every memory containing "
          "that whole word is removed. Pass an integer/id to delete exactly that memory. "
          "Returns the removed records with their IDs.",
          {"target": {"type": ["string", "integer"]},
           "mode": {"type": "string", "enum": ["auto", "word", "contains", "id"]}}, ["target"]),
    _spec("memory_history", "Show recent create/update/delete audit entries.", {"limit": _I}, []),
    _spec("memory_undo", "Undo the most recent write (create/update/delete).", {}, []),
    _spec("memory_clear", "Delete ALL memories. Requires confirm=true.", {"confirm": _B}, ["confirm"]),
    _spec("memory_reset_sent",
          "Reset the sent-memory tracker used by memory_retrieve / memory_process_prompt: after "
          "this call those tools may return memories they already sent before. Deletes nothing.",
          {}, []),
]


def tool_specs_json() -> str:
    return json.dumps(TOOL_SPECS, indent=2, ensure_ascii=False)


def _h_save(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.save(a.get("text", ""), slots=a.get("slots"),
                      source=a.get("source", ""), auto_update=bool(a.get("auto_update", True)))


def _h_retrieve(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    hits, sent_hidden = store.retrieve_hits(
        a.get("prompt", ""), a.get("limit"),
        include_sent=bool(a.get("include_sent", False)),
        mark_sent=bool(a.get("mark_sent", True)))
    return {"ok": True, "action": "retrieve", "count": len(hits),
            "sent_hidden": sent_hidden,
            "memories": [h.as_dict() for h in hits], "rendered": format_hits(hits)}


def _h_process(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.process_prompt(a.get("prompt", ""), auto_update=bool(a.get("auto_update", True)))


def _h_update_prompt(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.update_from_prompt(a.get("prompt", ""))


def _h_list(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    rows = store.list_all()
    return {"ok": True, "action": "list", "count": len(rows), "memories": rows}


def _h_get(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    row = store.get(a.get("id"))
    return {"ok": row is not None, "action": "get", "memory": row,
            **({} if row else {"error": "no memory with that id"})}


def _h_find(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    rows = store.find(a.get("word", ""), mode=a.get("mode", "word"))
    return {"ok": True, "action": "find", "count": len(rows), "memories": rows}


def _h_explain(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.explain(a.get("prompt", ""))


def _h_update(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.update(a.get("id"), a.get("text", ""), match=a.get("match"),
                        slots=a.get("slots"), all_matches=bool(a.get("all_matches", False)))


def _h_delete(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.delete(a.get("target"), mode=a.get("mode", "auto"))


def _h_history(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    rows = store.history(int(a.get("limit", 20)))
    return {"ok": True, "action": "history", "count": len(rows), "entries": rows}


def _h_undo(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.undo_last()


def _h_clear(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    if not a.get("confirm"):
        return {"ok": False, "error": "memory_clear requires {\"confirm\": true}"}
    return store.clear()


def _h_reset_sent(store: MemoryStore, a: Dict[str, Any]) -> Dict[str, Any]:
    return store.reset_sent()


TOOL_HANDLERS = {
    "memory_save": _h_save,
    "memory_retrieve": _h_retrieve,
    "memory_process_prompt": _h_process,
    "memory_update_from_prompt": _h_update_prompt,
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

_DEFAULT_STORE: Optional[MemoryStore] = None


def configure(db_path: str) -> MemoryStore:
    """Point the module-level tool-call store at a specific DB file."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE:
        _DEFAULT_STORE.close()
    _DEFAULT_STORE = MemoryStore(db_path)
    return _DEFAULT_STORE


def _default_store() -> MemoryStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MemoryStore()
    return _DEFAULT_STORE


def reset_sent() -> Dict[str, Any]:
    """Clear the default store's sent-tracker (see MemoryStore.reset_sent)."""
    return _default_store().reset_sent()


def sent_ids() -> List[int]:
    """IDs the default store has already sent (see MemoryStore.sent_ids)."""
    return _default_store().sent_ids()


def call_tool(name: str, arguments: Any = None, *, store: Optional[MemoryStore] = None,
              **kwargs: Any) -> Dict[str, Any]:
    """
    Single dispatcher for AI-agent tool calls.

        call_tool("memory_save", {"text": "I live in Rajshahi"})
        call_tool("memory_retrieve", '{"prompt": "where do I live?"}')
        call_tool("memory_delete", target="protest")

    Always returns a JSON-serialisable dict with "ok". Never raises.
    """
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
    except Exception as exc:                                  # never leak to the agent
        return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}


# ===========================================================================
# 6. Self-test + demo
# ===========================================================================

def _temp_store() -> Tuple[MemoryStore, str]:
    fd, path = tempfile.mkstemp(prefix="memtest_", suffix=".db")
    os.close(fd)
    os.unlink(path)
    return MemoryStore(path), path


def run_selftest(verbose: bool = True) -> Tuple[int, int, List[str]]:
    """Built-in regression suite on a throwaway DB. Returns (passed, failed, lines)."""
    # The harness deliberately re-queries the same memories across sections
    # and through several ad-hoc stores; disable dedup for ALL of them, then
    # restore. The v3.4 dedup block re-enables it on its own store.
    _prev_dedup = MemoryStore.sent_dedup_enabled
    MemoryStore.sent_dedup_enabled = False
    store, path = _temp_store()
    checks: List[Tuple[str, bool, str]] = []

    def check(name: str, cond: Any, detail: str = ""):
        checks.append((name, bool(cond), detail))

    try:
        # --- seed ----------------------------------------------------------
        seed = store.save(
            "My name is Arif. I live in Rajshahi. I'm playing Valheim now. "
            "I like pizza. I attended a climate protest last year. My favorite food is biryani.")
        check("save returns ids", len(seed["ids"]) == 6, str(seed["ids"]))
        all_rows = store.list_all()
        check("list_all gives numeric ids", all(isinstance(r["id"], int) for r in all_rows))
        game_row = next(r for r in all_rows if "Valheim" in r["text"])
        check("state relation extracted", game_row["state_key"] == "activity.game",
              game_row["state_key"])

        # --- relevant retrieval -------------------------------------------
        hits = store.retrieve("what game am I playing?")
        check("self-attribute intent finds game", any("Valheim" in h.text for h in hits),
              format_hits(hits))
        check("hits expose id", all(isinstance(h.sentence_id, int) for h in hits))
        check("protest anchor works",
              any("protest" in h.text.lower()
                  for h in store.retrieve("what do you remember about the protest?")))
        check("pizza anchor works",
              any("pizza" in h.text.lower() for h in store.retrieve("tell me about pizza")))
        check("name intent works",
              any("Arif" in h.text for h in store.retrieve("what's my name?")))
        check("location intent works",
              any("Rajshahi" in h.text for h in store.retrieve("where do I live?")))
        check("meta query returns ALL",
              len(store.retrieve("what do you know about me?")) == len(all_rows))

        # --- v3.3: "user"-phrased queries must not dump the library --------
        # "user" is the subject of every third-person memory; it must never
        # act as a topic anchor (regression: whole-library dump at 0.70).
        solo, solo_path = _temp_store()
        try:
            for m in ("user lives in Bangladesh.",
                      "user enjoys experimenting with new technologies.",
                      "user prefers exploration-focused games over competitive games.",
                      "user has been strength training since April 2022.",
                      "user participated in protests concerning unfair HSC 2026 conditions."):
                solo.save(m)
            d = solo.retrieve("what's user favourite thing")
            check("user-phrased lookup answered, not dumped",
                  0 < len(d) <= 3 and any("prefers" in h.text for h in d),
                  format_hits(d))
            check("user-variant reaches training concept",
                  any("strength training" in h.text
                      for h in solo.retrieve("when did the user start working out?")))
            check("'tell me about the user' is an intentional meta dump",
                  len(solo.retrieve("tell me about the user")) == 5)
            check("'about the user's exam' stays a topic lookup",
                  len(solo.retrieve("what do you know about the user's exam?")) <= 2)
            check("'what's my favourite thing?' answers like the user variant",
                  0 < len(store.retrieve("what's my favourite thing?")) <= 4)
        finally:
            solo.close()
            try:
                os.unlink(solo_path)
            except OSError:
                pass

        # --- v3.4/v3.6: sent-dedup (strict, all-or-nothing, meta included) ---
        dedup, dedup_path = _temp_store()
        dedup.sent_dedup_enabled = True          # exercise dedup explicitly
        try:
            dedup.save(["I like pizza", "I live in Rajshahi", "I'm playing Valheim now"])
            first = dedup.retrieve("tell me about the pizza thing")
            check("sent-dedup: first retrieval returns the memory", len(first) == 1,
                  format_hits(first))
            second = dedup.retrieve("tell me about the pizza thing")
            check("sent-dedup: identical prompt does NOT re-send", second == [],
                  format_hits(second))
            check("sent-dedup: include_sent=True bypasses the tracker",
                  len(dedup.retrieve("tell me about the pizza thing", include_sent=True)) == 1)
            check("dedup: overlap with a sent memory -> other prompts return NOTHING",
                  dedup.retrieve("what do I like?") == [])
            n_cleared = dedup.reset_sent()["cleared"]
            check("reset_sent() clears the tracker and re-sends",
                  n_cleared >= 1
                  and len(dedup.retrieve("tell me about the pizza thing")) == 1,
                  f"cleared={n_cleared}")
            dedup.update_from_prompt("I'm playing FIFA now")
            refetch = dedup.retrieve("what game am I playing?")
            check("superseded fact is un-marked and re-sent",
                  any("FIFA" in h.text for h in refetch), format_hits(refetch))
            check("meta answers are dedup'd too: overlap with sent -> NOTHING",
                  dedup.retrieve("what do you know about me?") == [])
            dedup.reset_sent()
            check("meta answers send fresh user memories after reset_sent",
                  len(dedup.retrieve("what do you know about me?")) == 3)
            dedup.save("Pizza is great with friends")     # fresh, same topic
            check("ALL-OR-NOTHING: answer containing a sent memory returns NOTHING",
                  dedup.retrieve("tell me about the pizza thing") == [])
            check("suppressed fresh memory was NOT marked sent (still sendable)",
                  any("great with friends" in h.text for h in dedup.retrieve(
                      "tell me about the pizza thing", include_sent=True)))
            check("sent_ids() reports the tracker",
                  dedup.sent_ids() and all(isinstance(i, int) for i in dedup.sent_ids()),
                  str(dedup.sent_ids()))
        finally:
            dedup.close()
            try:
                os.unlink(dedup_path)
            except OSError:
                pass

        # --- v3.5: meta answers are user-scoped -----------------------------
        us, uspath = _temp_store()
        try:
            us.save(["I like pizza",
                     "My meeting is on Friday at 3pm",
                     "user prefers exploration-focused games",
                     "Pizza is great with friends",
                     "My friend Rafi loves cricket",
                     "The protest is on Friday",
                     "He is playing Valheim now",
                     "My best friend is Rafi",
                     "My favorite movie is Interstellar"])
            who = us.retrieve("who am i")
            who_txt = " | ".join(h.text for h in who)
            check("meta: 'who am i' answers with ONLY the 5 user memories",
                  len(who) == 5, who_txt)
            check("meta: user memories present",
                  "I like pizza" in who_txt and "My meeting is on Friday" in who_txt
                  and "exploration-focused games" in who_txt
                  and "My best friend is Rafi" in who_txt
                  and "Interstellar" in who_txt, who_txt)
            check("meta: general note about pizza excluded",
                  "great with friends" not in who_txt)
            check("meta: other people's actions excluded",
                  "Rafi loves cricket" not in who_txt
                  and "He is playing Valheim" not in who_txt)
            check("meta: general topic note excluded",
                  "The protest is on Friday" not in who_txt)
            dump = us.retrieve("what do you know about me?")
            check("meta: 'what do you know about me?' is user-scoped too",
                  {h.sentence_id for h in dump} == {h.sentence_id for h in who})
            check("meta: 'list my memories' still a meta answer (user-scoped)",
                  {h.sentence_id for h in us.retrieve("list my memories")}
                  == {h.sentence_id for h in who})
            topic = us.retrieve("tell me about the pizza thing")
            check("general notes stay retrievable by topic",
                  any("great with friends" in h.text for h in topic),
                  format_hits(topic))
            check("manual listing still shows everything", len(us.list_all()) == 9)
        finally:
            us.close()
            try:
                os.unlink(uspath)
            except OSError:
                pass

        # --- v3.7: false-injection fixes (audited live-session failures) ----
        probe, probe_path = _temp_store()
        try:
            probe.save(["user raised concerns about rainfall during examinations.",
                        "user objected to the insufficient exam time.",
                        "user reported incorrect questions in the exam.",
                        "user is playing Valheim now.",
                        "user got a pay raise last month."])
            # (A) raise-family is generic: construction talk never anchors
            # the "raised concerns" memory (audit item 4: anchor:rais).
            # ("... in valheim" MAY still match the Valheim memory -- that
            # is a genuine anchor, not an injection.)
            valheim_q = probe.retrieve("raise earth wall in valheim with less resources")
            check("'raise earth wall' never pulls the rainfall memory",
                  not any("rainfall" in h.text or "exam" in h.text for h in valheim_q),
                  format_hits(valheim_q))
            check("'raised earth wall' statement returns NOTHING",
                  probe.retrieve("no but i did place a raised earth wall around my base") == [])
            # (B) rainfall memory is not a finance memory anymore; real money
            # text still is (audit item 1: concept:money_finance mis-tag).
            check("rainfall memory is no longer tagged money_finance",
                  "money_finance" not in
                  concept_profile("user raised concerns about rainfall during examinations."))
            check("real money text still tags money_finance",
                  "money_finance" in
                  concept_profile("user got a pay raise last month."))
            check("pay-raise memory stays reachable by a money query",
                  any("pay raise" in h.text
                      for h in probe.retrieve("did i get a pay raise?")))
            # (C) the typo corrector must never rewrite a real verb
            # (audit item 1: "sending" -> "spending" -> finance fallback).
            check("'sending' is never typo-corrected to 'spending'",
                  correct_typo("sending") == "sending")
            check("'try sending my inbox a msg' returns NOTHING",
                  probe.retrieve("try sending my inbox a msg. alice smith") == [])
            # (D) v3.8: NO harness magic -- the caller sanitises assembled
            # turns, so the retriever scores exactly the string it is given.
            # A bare tiny turn (what the caller passes after its own SYSTEM:/
            # YOU: handling) must stay clean.
            check("tiny dismiss turns ('ignore it') return NOTHING",
                  probe.retrieve("ignore it") == [])
            check("'nah no need' returns NOTHING",
                  probe.retrieve("nah no need") == [])
            check("retrieve() never rewrites the prompt it is given",
                  probe.retrieve("YOU: when did the user object to the exam time?") != [])
        finally:
            probe.close()
            try:
                os.unlink(probe_path)
            except OSError:
                pass

        # --- NO false triggers --------------------------------------------
        noise = [
            "what is the weather like today?", "write a poem about rain",
            "how do I bake a cake?", "who is the president?",
            "do you like coffee?", "explain quantum physics",
            "translate this to French", "hello there", "what is 2+2?",
            "give me a recipe for pasta", "tell me a joke",
            "summarize this article", "what time is it?",
        ]
        for q in noise:
            got = store.retrieve(q)
            check("NO trigger: " + q, got == [], format_hits(got))

        # a generic-only overlap must NOT fire even with a single memory
        solo, solo_path = _temp_store()
        try:
            solo.save("I like pizza")
            check("generic-only overlap rejected", solo.retrieve("do you like coffee?") == [])
            check("third-person question gated out",
                  solo.retrieve("do you like pizza?") == [])
            check("real entity overlap accepted (first person)",
                  len(solo.retrieve("do I like pizza?")) == 1)
            check("real entity overlap accepted (recall cue)",
                  len(solo.retrieve("tell me about pizza")) == 1)
        finally:
            solo.close()
            try:
                os.unlink(solo_path)
            except OSError:
                pass

        # --- ALL relevant memories are returned (no top-K cut) ------------
        store.save(["Pizza is great with friends.", "My friend loves pizza."])
        pizza_hits = store.retrieve("tell me everything about pizza")
        check("returns ALL relevant memories",
              sum("pizza" in h.text.lower() for h in pizza_hits) >= 3,
              f"{len(pizza_hits)} hits: " + format_hits(pizza_hits))

        # --- deterministic supersession ------------------------------------
        upd = store.update_from_prompt("I'm playing Crimson Desert right now")
        check("supersession fired", upd["changed"] is True, json.dumps(upd, default=str)[:400])
        rows = store.list_all()
        check("old game value gone", not any("Valheim" in r["text"] for r in rows))
        check("new game value stored", any("Crimson Desert" in r["text"] for r in rows))
        check("same id kept (overwrite)", any(r["id"] == game_row["id"] and
                                             "Crimson Desert" in r["text"] for r in rows))
        check("no duplicate game memory",
              sum(r["state_key"] == "activity.game" for r in rows) == 1)

        # repeated identical fact = no change
        dup = store.update_from_prompt("I'm playing Crimson Desert right now")
        check("identical fact is a no-op", not dup["changed"], dup["message"])

        # --- conservative: things that must NOT update ---------------------
        protected = [
            "Am I playing Valheim right now?",                 # question
            "My friend is playing Valheim right now",         # third person
            "He is playing Valheim now",                      # third person
            "I'm playing the guitar right now",               # not a game
            "I used to play Valheim",                         # past
            "I like Valheim",                                # preference, no state cue
            "what game am I playing?",                        # question
        ]
        before = store.list_all()
        for p in protected:
            res = store.update_from_prompt(p)
            check("NO update: " + p, not res["changed"], json.dumps(res, default=str)[:250])
        check("memory untouched by protected prompts", store.list_all() == before)

        # save() also supersedes instead of stacking contradictions
        res = store.save("I'm playing Stardew Valley right now")
        check("save() auto-supersedes", res["updated_count"] == 1 and res["count"] == 0,
              str(res))
        check("still exactly one game memory",
              sum(r["state_key"] == "activity.game" for r in store.list_all()) == 1)

        # other relations update too (generality)
        store.update_from_prompt("I moved to Dhaka now")
        check("location relation supersedes",
              any("Dhaka" in r["text"] for r in store.list_all()) and
              not any("Rajshahi" in r["text"] for r in store.list_all()))
        store.update_from_prompt("my favorite food is sushi now")
        check("favorite relation supersedes",
              any("sushi" in r["text"] for r in store.list_all()) and
              not any("biryani" in r["text"] for r in store.list_all()))

        # --- delete by word -------------------------------------------------
        d = store.delete("protest")
        check("delete by word removes memory", d["count"] >= 1, str(d))
        check("deleted memory really gone",
              not any("protest" in r["text"].lower() for r in store.list_all()))
        check("delete result includes ids", all("id" in r for r in d["removed"]))
        check("unmatched word deletes nothing", store.delete("xylophone")["count"] == 0)

        # --- manual get / find / update / delete by id ---------------------
        row = store.list_all()[0]
        check("get(id) works", (store.get(row["id"]) or {}).get("id") == row["id"])
        check("find(word) works with ids",
              all("id" in r for r in store.find("pizza")))
        u = store.update(row["id"], "My name is Arif Rahman")
        check("manual update by id", u["changed"] and
              "Rahman" in (store.get(row["id"]) or {}).get("text", ""), str(u)[:200])
        u2 = store.update(match="pizza", text="I like pizza with extra cheese",
                          all_matches=True)
        check("manual update by word", u2["updated_count"] >= 1)
        did = store.delete(str(row["id"]))
        check("delete by id string", did["count"] == 1)

        # --- history + undo -------------------------------------------------
        hist = store.history()
        check("history recorded", len(hist) >= 1 and hist[0]["action"] in
              {"create", "update", "delete"})
        pre_undo = len(store.list_all())
        undo = store.undo_last()
        check("undo works", undo["changed"] and len(store.list_all()) == pre_undo + 1,
              undo["message"])

        # --- tool-call layer -------------------------------------------------
        t1 = call_tool("memory_save", {"text": "I use Neovim every day"}, store=store)
        check("tool memory_save", t1["ok"] and len(t1["ids"]) == 1, str(t1)[:200])
        t2 = call_tool("memory_retrieve", {"prompt": "what editor do I use?"}, store=store)
        check("tool memory_retrieve", t2["ok"] and isinstance(t2["memories"], list))
        t3 = call_tool("memory_retrieve", '{"prompt": "do you like coffee?"}', store=store)
        check("tool false-trigger guard", t3["count"] == 0, str(t3)[:200])
        t4 = call_tool("memory_find", {"word": "Neovim"}, store=store)
        check("tool memory_find", t4["count"] >= 1 and "id" in t4["memories"][0])
        t5 = call_tool("memory_delete", {"target": "Neovim"}, store=store)
        check("tool memory_delete by word", t5["count"] >= 1)
        t6 = call_tool("memory_process_prompt", {"prompt": "what game am I playing?"}, store=store)
        check("tool process_prompt", t6["ok"] and "memories" in t6 and "rendered" in t6)
        t7 = call_tool("memory_explain", {"prompt": "write a poem about rain"}, store=store)
        check("tool explain gate", t7["gate"] == "content_generation_command", str(t7["gate"]))
        t8 = call_tool("no_such_tool", {}, store=store)
        check("unknown tool handled", not t8["ok"] and "available" in t8)
        t9 = call_tool("memory_clear", {}, store=store)
        check("clear needs confirm", not t9["ok"])

        # --- user-reported regression: third-person memories + meta ---------
        u, upath = _temp_store()
        try:
            u.save(["user's name is alice", "user's birthday is on 15 jan 2000",
                    "user was in a protest against unfair exam system",
                    "user is an HSC candiate"])
            check("third-person save extracts relation keys",
                  [r["state_key"] for r in u.list_all()] ==
                  ["identity.name", "identity.birthday", "", "education.level"],
                  str([r["state_key"] for r in u.list_all()]))
            for q in ["logic what do you know about me",
                      "what can you tell me about myself?",
                      "who am i", "hey, what do you remember about me?",
                      "logic: what do you know about me",
                      "show me everything you know about me",
                      "remind me what you know about me"]:
                hits = u.retrieve(q)
                check("meta paraphrase returns the user's 4 memories: " + q,
                      len(hits) == 4,
                      f"{len(hits)} :: {format_hits(hits)}")
            check("topic lookup is NOT a meta dump",
                  len(u.retrieve("what do you know about the protest?")) == 1)
            check("third-person 'what's my name?' works",
                  any("alice" in h.text for h in u.retrieve("what's my name?")))
            check("third-person 'when is my birthday?' works",
                  any("jan 2000" in h.text for h in u.retrieve("when is my birthday?")))
            check("do you remember my name works",
                  any("alice" in h.text for h in u.retrieve("do you remember my name?")))
            check("HSC status is retrievable",
                  any("HSC" in h.text for h in u.retrieve("am I an HSC candidate?")))
            check("past event is not a state fact",
                  u.get(3)["state_key"] == "")
            check("new relation with no overlap does not write",
                  u.update_from_prompt("I'm playing Valheim now")["changed"] is False)
            u.save("user's current game is Valheim")
            res = u.update_from_prompt("I'm playing Crimson Desert right now")
            check("agent third-person fact superseded by first-person prompt",
                  res["changed"] and any("Crimson Desert" in r["text"]
                                         for r in u.list_all()))
        finally:
            u.close()
            try:
                os.unlink(upath)
            except OSError:
                pass

        # --- precision / generality regression suite (fresh DB) -------------
        e, epath = _temp_store()
        try:
            e.save(["My name is Tanvir Hasan", "I live in Rajshahi, Bangladesh",
                    "I work as a software engineer", "I'm playing Valheim now",
                    "I like biryani and pizza", "My favorite movie is Interstellar",
                    "I have a dog named Milo", "I attended a climate protest last year",
                    "I'm learning Japanese right now", "My phone is a Pixel 7",
                    "I dislike noisy cafes", "My birthday is 14 March"])
            extra_noise = [
                "is pizza a vegetable?", "give me three ideas for a birthday gift",
                "suggest a recipe for dinner", "explain how photosynthesis works",
                "who won the world cup in 2014?", "compute the area of a circle",
                "draft an email to my boss about the meeting", "I think it will rain tomorrow",
                "summarize this article for me", "translate 'hello' to Spanish",
            ]
            for q in extra_noise:
                got = e.retrieve(q)
                check("NO trigger (extra): " + q, got == [], format_hits(got))

            check("specific key beats broad category",
                  len(e.retrieve("what's my favorite movie?")) == 1,
                  format_hits(e.retrieve("what's my favorite movie?")))
            check("dislike intent is narrow",
                  len(e.retrieve("what do I dislike?")) == 1)
            check("dynamic 'what's my phone' intent",
                  any("Pixel" in h.text for h in e.retrieve("what's my phone?")))
            check("possessive noun alias (dog's name)",
                  any("Milo" in h.text for h in e.retrieve("what's my dog's name?")))
            check("what am I learning intent",
                  any("Japanese" in h.text for h in e.retrieve("what am I learning?")))
            check("'list my memories' is a meta query",
                  len(e.retrieve("list my memories")) == len(e.list_all()))

            # A very common entity must STILL be retrievable by name.
            e.save(["Pizza is my comfort food", "I ordered pizza last night",
                    "pizza with extra cheese is best"])
            check("popular entity stays retrievable",
                  len(e.retrieve("tell me about pizza")) >= 3,
                  format_hits(e.retrieve("tell me about pizza")))
            check("phrase deletion needs ALL words",
                  e.find("climate protest") and not e.find("climate pizza"))

            # Generalisation of supersession beyond the example relation.
            for prompt, expected in [
                ("Currently playing Stardew Valley", "superseded"),
                ("I started playing Terraria", "superseded"),
                ("I'm playing Hades", "superseded"),
                ("my favorite movie is Dune now", "superseded"),
                ("I moved to Dhaka", "superseded"),
                ("My phone is an iPhone 15 right now", "superseded"),
                ("I'm learning Korean now", "superseded"),
                ("I'm not playing Hades anymore", "removed"),
            ]:
                r = e.update_from_prompt(prompt)
                check("general supersession: " + prompt,
                      r["results"][0]["action"] == expected,
                      r["results"][0]["action"])
            check("never stacks contradictory game memories",
                  sum(r["state_key"] == "activity.game" for r in e.list_all()) <= 1,
                  str([r["text"] for r in e.list_all() if r["state_key"] == "activity.game"]))

            # Stemmer must align inflected and base forms.
            for a, b in [("play", "playing"), ("like", "likes"), ("game", "games"),
                         ("protest", "protesting"), ("study", "studies"),
                         ("live", "living"), ("use", "using"), ("go", "going")]:
                check(f"stem aligns {a}/{b}", stem(a) == stem(b), f"{stem(a)} != {stem(b)}")
        finally:
            e.close()
            try:
                os.unlink(epath)
            except OSError:
                pass

        # --- user's real 20-memory corpus: aspect routing regression --------
        corpus = [
            "user lives in Bangladesh.",
            "user is interested in Computer Science and Engineering.",
            "user aims to study CSE at a public university.",
            "user completed SSC in 2024 with GPA 5.00.",
            "user completed HSC in 2026 with GPA 3.50.",
            "user studied Physics, Chemistry, Higher Mathematics, and Biology at HSC level.",
            "user prepared for engineering university admission through Udvash.",
            "user's usual Udvash branch merit position was approximately 200.",
            "user participated in protests concerning unfair HSC 2026 examination conditions.",
            "user objected to insufficient exam time and unusually difficult questions.",
            "user reported incorrect questions across multiple subjects.",
            "user raised concerns about rainfall during examinations.",
            "user believed the HSC 2026 batch was treated unfairly compared with previous batches.",
            "user is interested in computer science and software engineering.",
            "user is interested in artificial intelligence and autonomous AI agents.",
            "user is interested in open-source software.",
            "user enjoys experimenting with new technologies.",
            "user is interested in computer hardware and PC gaming.",
            "user prefers exploration-focused games over competitive multiplayer games.",
            "user has been strength training since April 2022.",
        ]
        r, rpath = _temp_store()
        try:
            r.save(corpus)
            interest_ids = {2, 14, 15, 16, 17, 18, 19}
            aspect_cases = {
                "hey logic.what do i like to do?": interest_ids,
                "what do i like": interest_ids,
                "what do i like to do?": interest_ids,
                "what intersts me": interest_ids,
                "what are my interests?": interest_ids,
                "what am i interested in?": interest_ids,
                "what do i prefer?": {19},
                "when did i started gym?": {20},
                "when did i started training": {20},
                "hmm you know where i want to get admitted?": {3, 7},
                "what do you know about the protest?": {9},
                "where do i live?": {1},
            }
            for q, expected in aspect_cases.items():
                got = {h.sentence_id for h in r.retrieve(q)}
                check("aspect retrieval: " + q, got == expected,
                      f"expected {sorted(expected)}, got {sorted(got)}")
            check("bare 'list' is no longer a command/dump", r.retrieve("list") == [])
            check("bare 'memories' is no longer a command/dump",
                  r.retrieve("memories") == [])
            check("who am i returns the user's own memories (all 20 are user-subject)",
                  len(r.retrieve("who am i")) == 20)
            check("'studied' shares a stem with 'study'", stem("studied") == stem("study"))

            clean_prompts = [
                "hey logic", "i am nice", "i like tea", "when did the gym open?",
                "explain quantum physics", "describe the weather",
                "write a poem about rain", "give me three ideas for a birthday gift",
                "is pizza a vegetable?", "do you like coffee?", "tell me a joke",
                "what time is it?", "compute the area of a circle",
                "who won the world cup in 2014?", "translate 'hello' to Spanish",
                "what is the capital of France?", "when did the protest start?",
            ]
            for q in clean_prompts:
                got = r.retrieve(q)
                check("NO false trigger (corpus): " + q, got == [], format_hits(got))

            # --- lexical generalization: similar words, goals, typos --------
            # "gym"/"workout" must reach the "strength training" memory, and
            # "goal"/"motivation"/"want to do" must reach the aspiration
            # memories -- WITHOUT dumping unrelated memories.
            lexical_cases = {
                "hey loigc. do you know when i started training?": {20},
                "hey loigc. do you know when i started gym?": {20},
                "hey loigc. do you know when i started to workout?": {20},
                "when did i begin lifting weights?": {20},
                "how long have i been exercising?": {20},
                "when did i start bodybuilding?": {20},
                "when did i start calisthenics?": {20},
                "do you remember when i took up weightlifting?": {20},
                "tell me about my fitness journey": {20},
                "what do you know about my fitness?": {20},
                "what do i want to do": {3, 7},
                "whats my goal": {3, 7},
                "whats my motivation": {3, 7},
                "what's my goal?": {3, 7},
                "whats my ambition?": {3, 7},
                "whats my career plan?": {3, 7},
                "whats my life goal?": {3, 7},
                "what are my future plans?": {3, 7},
                "what is my aim?": {3, 7},
                "what am i aiming for?": {3, 7},
                "what drives me?": {3, 7},
                "what do i intend to do?": {3, 7},
                "what do i hope to become?": {3, 7},
                "what are my hobbies?": interest_ids,
                "what am i passionate about?": interest_ids,
                "what did i complain about?": {9, 10, 11, 12, 13},
                "where am i based?": {1},
                "where do i reside?": {1},
                "what kind of games do i enjoy?": interest_ids,
            }
            for q, expected in lexical_cases.items():
                got = {h.sentence_id for h in r.retrieve(q)}
                check("lexical retrieval: " + q, got == expected,
                      f"expected {sorted(expected)}, got {sorted(got)}")

            lexical_clean = [
                "what is my plan for today?", "what are my plans for the weekend?",
                "what is my plan tonight?", "what is my aim in this game?",
                "what is my purpose here?", "what is my goal weight?",
                "what is my goal for this project?", "what is my favorite movie?",
                "whats my favourite food", "what is this based on?",
                "my answer is based on facts",
                "what is a good workout routine for beginners?",
                "what are the benefits of exercise?", "when does the gym open?",
                "what is the capital of France?", "tell me a joke",
                "recommend a movie", "how do I bake a cake?",
                "who invented the telephone?", "whats the weather like today?",
                "explain quantum physics", "describe the weather",
            ]
            for q in lexical_clean:
                got = r.retrieve(q)
                check("NO false trigger (lexical): " + q, got == [], format_hits(got))

            # Low-level helpers: controlled vocabulary, typo fallback, scope guard.
            check("compact bridges 'work out'/'workout'",
                  compact("work out") == compact("workout"))
            check("phrase_keys bridges 'work out'/'workout'",
                  bool(phrase_keys("work out") & phrase_keys("workout")))
            check("gym and strength training share a concept",
                  "physical_training" in concept_profile("gym")
                  and "physical_training" in concept_profile("strength training"))
            check("typo 'loigc' -> 'logic'", correct_typo("loigc") == "logic")
            check("typo 'traning' -> 'training'", correct_typo("traning") == "training")
            check("typo 'workkout' -> 'workout'", correct_typo("workkout") == "workout")
            check("typo 'strngth' -> 'strength' (word derived from a phrase)",
                  correct_typo("strngth") == "strength")
            check("typo 'intersts' -> 'interests'", correct_typo("intersts") == "interests")
            check("typo guard: 'rain' is NOT 'train'", correct_typo("rain") == "rain")
            check("typo guard: inflection 'drives' is NOT 'driven'",
                  correct_typo("drives") == "drives")
            check("contraction 'whats' expands",
                  "what is my goal" in normalize_query("whats my goal"))
            check("contraction \"what's\" expands",
                  "what is my goal" in normalize_query("what's my goal"))
            check("scope-shift guard active",
                  bool(SCOPE_SHIFT.search("what is my plan for today?"))
                  and not SCOPE_SHIFT.search("whats my goal"))
            add_concept("_selftest_concept", strong=["florbnix"], weak=[])
            check("add_concept extends the lexicon",
                  concept_profile("florbnix").get("_selftest_concept") == 1.0)
            remove_concept("_selftest_concept")
            check("remove_concept restores the lexicon",
                  "_selftest_concept" not in concept_profile("florbnix"))
        finally:
            r.close()
            try:
                os.unlink(rpath)
            except OSError:
                pass

        # --- Jarvis-scale stress regressions --------------------------------
        # Mixed first-person / agent-written profile. These are the bugs the
        # broad stress harness found; keeping them here makes the fixes stick
        # in the single-file deliverable too.
        j, jpath = _temp_store()
        try:
            J = [
                "My name is Rafi.",                                  # 1
                "I live in Dhaka.",                                  # 2
                "user works at a small software studio.",            # 3
                "user's mother is a teacher.",                       # 4
                "user's dog's name is Milo.",                        # 5
                "user has a cat named Luna.",                        # 6
                "user watches science documentaries.",              # 7
                "user reads sci-fi novels.",                         # 8
                "user is learning Japanese.",                        # 9
                "user listens to lo-fi music while coding.",         # 10
                "I drive a Toyota Corolla.",                         # 11
                "user was in a protest against unfair exam system.", # 12
                "user's favorite color is blue.",                    # 13
                "user bought a new keyboard in 2025.",               # 14
            ]
            j.save(J)

            def jids(q):
                return {h.sentence_id for h in j.retrieve(q)}

            check("spelling: favourite -> favorite", us_spelling("favourite") == "favorite")
            check("spelling: colour -> color", us_spelling("colour") == "color")
            check("unicode word stays one token",
                  "বাংলাদেশ" in tokenize("I live in বাংলাদেশ."))
            check("glued lead-in 'logic.what' is stripped",
                  prepare_query("logic.what do i like") == "what do i like")
            check("typo corrector never rewrites a real verb 'drive'",
                  correct_typo("drive") == "drive")
            check("general 'what car do i drive?' noun routing",
                  jids("what car do i drive?") == {11},
                  format_hits(j.retrieve("what car do i drive?")))
            check("topic anchor: 'what do i watch?'",
                  jids("what do i watch?") == {7})
            check("topic anchor: 'what do i read?'",
                  jids("what do i read?") == {8})
            check("activity verbs are not conflated",
                  jids("what am i learning?") == {9})
            check("British spelling lookup",
                  jids("what's my favourite colour?") == {13})
            check("dog name does not leak the cat",
                  jids("what's my dog's name?") == {5})
            check("cat name does not leak the dog",
                  jids("what is my cat called?") == {6})
            check("mother's job is not the user's job",
                  jids("where do i work?") == {3}
                  and jids("what's my name?") == {1})
            check("grievance intent with one content word",
                  jids("what unfair thing happened?") == {12})
            check("bare 'memories please list' is no longer a command/dump",
                  j.retrieve("memories please list") == [])
            check("'what is my height?' has no coincidental anchor",
                  j.retrieve("what is my height?") == [])
            check("'fix my code' is a generation command, not a lookup",
                  j.retrieve("fix my code") == []
                  and gate_reason("fix my code") == "content_generation_command")
            check("scope-shift noise still rejected",
                  j.retrieve("what is my plan for today?") == [])
            check("unresolved attribute lookup is diagnosed",
                  j.explain("what is my height?").get("unresolved_attribute_lookup") is True)
            check("concept fallback is off for unknown attribute questions",
                  not j.explain("what is my height?").get("concept_fallback"))
            j.save("user lives in বাংলাদেশ.")
            check("unicode memory is retrievable by its own word",
                  any("বাংলাদেশ" in h.text for h in j.retrieve("বাংলাদেশ")),
                  format_hits(j.retrieve("বাংলাদেশ")))
        finally:
            j.close()
            try:
                os.unlink(jpath)
            except OSError:
                pass

        # --- no LLM / no network (checked on the parsed import tree) --------
        import ast
        with open(__file__, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        banned = {"requests", "openai", "anthropic", "httpx", "urllib.request",
                  "http.client", "transformers", "torch", "numpy", "sklearn", "nltk"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        bad = sorted(b for b in imported if any(b == x or b.startswith(x + ".") for x in banned))
        check("no LLM/network/dependency imports (stdlib only)", not bad, str(bad))

    finally:
        store.close()
        MemoryStore.sent_dedup_enabled = _prev_dedup
        try:
            os.unlink(path)
        except OSError:
            pass

    lines = []
    for name, ok, detail in checks:
        lines.append(("PASS  " if ok else "FAIL  ") + name + (("  <- " + detail) if not ok and detail else ""))
    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    if verbose:
        for line in lines:
            print(line)
        print("-" * 68)
        print(f"{passed} passed, {failed} failed")
    return passed, failed, lines


def run_demo(verbose: bool = True) -> List[str]:
    """End-to-end Valheim -> Crimson Desert walkthrough on a throwaway DB."""
    store, path = _temp_store()
    out: List[str] = []
    def show(label: str, payload: Any):
        text = payload if isinstance(payload, str) else json.dumps(payload, indent=2, default=str)
        block = f"\n--- {label} ---\n{text}"
        out.append(block)
        if verbose:
            print(block)
    try:
        show("1. AI agent saves a memory with a tool call",
             call_tool("memory_save", {"text": "I'm playing Valheim now"}, store=store))
        show("2. A normal prompt retrieves ALL relevant memories (with IDs)",
             call_tool("memory_retrieve", {"prompt": "what game am I playing?"}, store=store)["rendered"])
        show("3. An unrelated prompt must trigger NOTHING",
             call_tool("memory_retrieve", {"prompt": "write me a poem about the rain"}, store=store)["rendered"])
        show("4. Months later: the user says he plays Crimson Desert right now",
             call_tool("memory_update_from_prompt",
                       {"prompt": "I'm playing Crimson Desert right now"}, store=store))
        show("5. The database now (old memory overwritten in place, same ID)",
             call_tool("memory_list", {}, store=store)["memories"])
        show("6. History / audit trail", call_tool("memory_history", {"limit": 5}, store=store)["entries"])
        show("7. Deleting by a single word ('protest')", "saving a memory that contains 'protest' first")
        call_tool("memory_save", {"text": "I joined a protest last year"}, store=store)
        show("   before", call_tool("memory_list", {}, store=store)["memories"])
        show("   memory_delete(target='protest')", call_tool("memory_delete", {"target": "protest"}, store=store))
        show("   after", call_tool("memory_list", {}, store=store)["memories"])
        show("8. process_prompt = update + retrieval in ONE call",
             call_tool("memory_process_prompt",
                       {"prompt": "what game am I playing these days?"}, store=store))
    finally:
        store.close()
        try:
            os.unlink(path)
        except OSError:
            pass
    return out


# ===========================================================================
# 7. Interactive REPL
# ===========================================================================

HELP = """
Type ANY prompt  -> retrieve ALL relevant memories (with IDs).
                    Current-state facts also supersede overlapping memories.
                    Memories sent once are NEVER repeated -- any answer that
                    would repeat one returns NOTHING (all-or-nothing, v3.6,
                    "who am i" included); /reset_sent re-sends.
                    Identity prompts ("who am i") answer with the user's own
                    memories only -- /list is the way to see everything.

  /save <text>           save memories (same path the AI agent's tool uses)
  /ask  <prompt>         retrieve only, no auto-update, ignores the sent-tracker
  /ingest <prompt>       run supersession + retrieval (process_prompt)
  /why   <prompt>        explain why each memory was / wasn't returned
  /sent                  show memory ids already sent (dedup tracker)
  /reset_sent            clear the sent-tracker (memories can be re-sent)

  /list                    MANUAL retrieval: every memory with IDs
                           (v3.5: bare "list"/"memories" are NOT commands --
                           slash commands are the only commands)
  /get   <id>            one memory by ID
  /find  <word>          memories containing a word, with IDs
  /update <id> <text>    manually overwrite a memory
  /replace <word> <text> manually overwrite memory matched by word
  /delete <word|id>      delete by WORD MATCHING or by ID
  /history               audit log        /undo   undo the last write
  /clear                 wipe all memories
  /reset_sent            clear the sent-dedup tracker

  /tools                 list agent tool calls + parameter schemas
  /call <name> <json>    invoke a tool EXACTLY as an AI agent would
                          e.g. /call memory_delete {"target":"protest"}
  /demo                  run the Valheim -> Crimson Desert walkthrough
  /selftest              run the built-in regression suite (temp DB)
  /toggle                turn plain-prompt auto-update on/off
  /help                  this help        /exit  quit

For agents (terminal, no REPL): run  python sentence_memory_retriever.py --help
"""

TOOL_HELP = """
Agent tool calls (also available in-process: call_tool(name, arguments)):
  memory_save                 {text, slots?, source?, auto_update?}
  memory_retrieve             {prompt, limit?, include_sent?, mark_sent?}
  memory_process_prompt       {prompt, auto_update?, include_sent?}
  memory_update_from_prompt   {prompt}
  memory_reset_sent           {}   (clear the sent-dedup tracker)
  memory_list / memory_undo   {}
  memory_get                  {id}
  memory_find                 {word, mode?}     mode: word|contains|id
  memory_explain              {prompt}
  memory_update               {id?|match?, text, slots?, all_matches?}
  memory_delete               {target, mode?}   target word or id
  memory_history              {limit?}
  memory_clear                {confirm: true}
Use `--tools` on the command line to print full JSON schemas.
"""


def _print_obj(obj: Any):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def run_repl(db_path: Optional[str] = None):
    store = MemoryStore(db_path)
    auto_update = True
    print("=" * 72)
    print(f" Sentence Memory Retriever v{VERSION}  |  no LLM, no network  |  DB: {store.db_path}")
    print("=" * 72)
    print(HELP)

    while True:
        try:
            line = input("memory> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break
        if not line:
            continue
        low = line.lower()

        # v3.5: NO bare-word command aliases. Slash commands are the only
        # commands; a bare word typed here is just a normal query.
        if low in ("/exit", "/quit"):
            print("Exiting.")
            break
        if low == "/help":
            print(HELP)
            continue
        if low == "/tools":
            print(TOOL_HELP)
            continue
        if low == "/toggle":
            auto_update = not auto_update
            print(f"Plain-prompt auto-update is now {'ON' if auto_update else 'OFF'}.")
            continue
        if low == "/demo":
            run_demo()
            continue
        if low == "/selftest":
            run_selftest()
            continue
        if low == "/clear":
            _print_obj(store.clear())
            continue
        if low == "/sent":
            ids = store.sent_ids()
            print(f"{len(ids)} already-sent id(s): {ids if ids else '(none)'}")
            continue
        if low == "/reset_sent":
            _print_obj(store.reset_sent())
            continue
        if low == "/list":
            rows = store.list_all()
            if not rows:
                print("No memories saved.")
            else:
                print(f"{len(rows)} memor{'y' if len(rows) == 1 else 'ies'} (manual retrieval):")
                for r in rows:
                    extra = f"  slots={','.join(r['slots'])}" if r["slots"] else ""
                    print(f"  [id={r['id']}] {r['text']}{extra}")
            continue
        if low == "/history":
            _print_obj(store.history(20))
            continue
        if low == "/undo":
            _print_obj(store.undo_last())
            continue
        if line.startswith("/get "):
            _print_obj(store.get(line[5:].strip()) or {"ok": False, "error": "no such id"})
            continue
        if line.startswith("/find "):
            rows = store.find(line[6:].strip())
            print(f"{len(rows)} match(es):")
            for r in rows:
                print(f"  [id={r['id']}] {r['text']}")
            continue
        if line.startswith("/save ") or line.startswith("/remember "):
            text = line.split(" ", 1)[1].strip()
            _print_obj(call_tool("memory_save", {"text": text}, store=store))
            continue
        if line.startswith("/ask "):
            # Diagnostic command: show the raw answer, ignore the sent-tracker.
            hits = store.retrieve(line[5:].strip(), include_sent=True, mark_sent=False)
            print(format_hits(hits))
            continue
        if line.startswith("/ingest "):
            _print_obj(store.process_prompt(line[8:].strip()))
            continue
        if line.startswith("/why "):
            diag = store.explain(line[5:].strip())
            print(f"gate={diag['gate']}  prepared_prompt={diag.get('prepared_prompt')!r}")
            print(f"known_terms={diag.get('known_terms')}  intents={diag.get('intents')}  "
                  f"aspect={diag.get('aspect')}  concepts={diag.get('concepts')}  "
                  f"concept_fallback={diag.get('concept_fallback')}")
            print(f"query_content={diag.get('query_content')}  total_memories={diag['total_memories']}")
            for c in diag["candidates"]:
                print(f"  [id={c['id']}] {c['decision']:<12} score={c['score']:.3f} "
                      f"matched={c['matched']} anchors={c['anchors']} :: {c['text']}")
            print(f"-> {len(diag['hits'])} returned")
            continue
        if line.startswith("/update "):
            parts = line[8:].strip().split(" ", 1)
            if len(parts) < 2 or not parts[0].isdigit():
                print("Usage: /update <id> <new text>")
            else:
                _print_obj(store.update(int(parts[0]), parts[1].strip()))
            continue
        if line.startswith("/replace "):
            parts = line[9:].strip().split(" ", 1)
            if len(parts) < 2:
                print("Usage: /replace <word> <new text>")
            else:
                _print_obj(store.update(match=parts[0], text=parts[1].strip()))
            continue
        if line.startswith("/delete "):
            _print_obj(store.delete(line[8:].strip()))
            continue
        if line.startswith("/call "):
            rest = line[6:].strip()
            name, _, arg_text = rest.partition(" ")
            _print_obj(call_tool(name.strip(), arg_text.strip() or {}, store=store))
            continue

        # Default: a raw user prompt -> update (if warranted) + ALL relevant memories.
        result = store.process_prompt(line, auto_update=auto_update)
        if result["update"]["changed"]:
            for r in result["update"]["results"]:
                if not r.get("changed"):
                    continue
                if r["action"] == "removed":
                    print(f"[auto-update] removed id={r.get('id', '?')}: {r.get('old_text', '?')}")
                else:
                    print(f"[auto-update] {r['action']} id={r.get('id', '?')}: "
                          f"{r.get('old_text', '?')}  ->  {r.get('new_text', '?')}")
        print(result["rendered"])
        if result.get("sent_hidden"):
            n = result["sent_hidden"]
            print(f"({n} already-sent memor{'y' if n == 1 else 'ies'} hidden; "
                  f"/reset_sent to send again)")

    store.close()


# ===========================================================================
# 8. CLI  --  four terminal verbs for agents, REPL for humans
#
# An agent should NOT have to read this file. It runs one command, gets a few
# plain lines back, and exits. No imports, no schemas, no JSON to memorise.
# ===========================================================================

SHORT_HELP = """\
Sentence Memory Retriever  (no LLM, no network)  --  terminal commands

  python sentence_memory_retriever.py                          interactive REPL (for testing)
  python sentence_memory_retriever.py add "text"               save memory(s); stale facts auto-overwrite
  python sentence_memory_retriever.py remove <word|id>         delete memories with that word, or by id
  python sentence_memory_retriever.py update <id|word> "text"  overwrite one memory
  python sentence_memory_retriever.py find "prompt"            ALL relevant memories, with IDs

  --db PATH   use another database (default memory/Memory.db next to this
              script; the folder is created on demand); may go anywhere
  find "who am i"  answers with the user's own memories (user-scoped since
              v3.5); use  memory_list  / the REPL  /list  for every memory.

Output: one line per memory,  [id=N] text    (or "none" when empty)
"""


def _take_db(argv: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Pull --db PATH / --db=PATH out of argv, wherever it sits."""
    for i, a in enumerate(list(argv)):
        if a.startswith("--db="):
            v = a.split("=", 1)[1].strip()
            argv.pop(i)
            return (v or None), None
        if a == "--db":
            if i + 1 >= len(argv):
                return None, "--db needs a path"
            v = argv[i + 1]
            del argv[i:i + 2]
            return v, None
    return None, None


def _emit(lines: List[str]) -> int:
    print("\n".join(lines) if lines else "none")
    return 0


def _simple_cli(cmd: str, rest: List[str], db: Optional[str]) -> int:
    """The four verbs. Plain text out, one line per memory."""
    usage = {"add": 'add "text"', "remove": "remove <word|id>",
             "update": 'update <id|word> "new text"', "find": 'find "prompt"'}
    if len(rest) < (2 if cmd == "update" else 1):
        print("usage: " + usage[cmd])
        return 1
    store = MemoryStore(db)
    try:
        if cmd == "add":
            r = store.save(" ".join(rest))
            if not r.get("ok"):
                print("error: " + str(r.get("error", "save failed")))
                return 1
            out = [f"added [id={s['id']}] {s.get('text', '')}" for s in r["saved"]]
            for u in r["updated"]:
                if u.get("action") == "removed":
                    out.append(f"removed [id={u.get('id', '?')}] {u.get('old_text', '')}")
                else:
                    out.append(f"updated [id={u.get('id', '?')}] {u.get('new_text', '')}")
            return _emit(out)

        if cmd == "remove":
            r = store.delete(" ".join(rest))
            return _emit([f"removed [id={m['id']}] {m.get('text', '')}"
                          for m in r["removed"]])

        if cmd == "update":
            target, text = rest[0], " ".join(rest[1:])
            r = (store.update(int(target), text) if target.isdigit()
                 else store.update(match=target, text=text))
            if not r.get("ok"):
                print("error: " + str(r.get("error", "update failed")))
                return 1
            return _emit([f"updated [id={u['id']}] {u['after']['text']}"
                          for u in r["updated"]])

        if cmd == "find":
            return _emit([f"[id={h.sentence_id}] {h.text}"
                          for h in store.retrieve(" ".join(rest))])
    finally:
        store.close()
    return 1


def _advanced_cli(argv: List[str], db: Optional[str]) -> int:
    """Older switches, kept working but hidden from the short help."""
    p = argparse.ArgumentParser(add_help=False)
    for flag in ("--selftest", "--demo", "--tools", "--list", "--version"):
        p.add_argument(flag, action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--db", help=argparse.SUPPRESS)
    p.add_argument("--tool", help=argparse.SUPPRESS)
    p.add_argument("--args", default="{}", help=argparse.SUPPRESS)
    a, unknown = p.parse_known_args(argv)
    if unknown:
        print("error: unknown option " + " ".join(unknown))
        return 1
    if a.version:
        print(VERSION)
        return 0
    if a.tools:
        print(tool_specs_json())
        return 0
    if a.selftest:
        _, failed, _ = run_selftest()
        return 1 if failed else 0
    if a.demo:
        run_demo()
        return 0
    if db:
        configure(db)
    if a.list:
        _print_obj(call_tool("memory_list"))
        return 0
    if a.tool:
        _print_obj(call_tool(a.tool, a.args))
        return 0
    print(SHORT_HELP)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    db, err = _take_db(argv)
    if err:
        print("error: " + err)
        return 1
    if not argv:                      # no args -> interactive testing
        run_repl(db)
        return 0
    if argv[0] in ("-h", "--help", "help"):
        print(SHORT_HELP)
        return 0
    if argv[0].startswith("-"):
        return _advanced_cli(argv, db)
    cmd = argv[0].lower()
    if cmd in ("add", "remove", "update", "find"):
        return _simple_cli(cmd, argv[1:], db)
    print("error: unknown command " + repr(cmd) + "\n" + SHORT_HELP)
    return 1


if __name__ == "__main__":
    sys.exit(main())
