"""Project 06: standardize household entries and find likely duplicates (fuzzy matching).

Standardization, in order:
  1. Unicode NFKC, trim, collapse repeated spaces.
  2. Arabic letter forms: remove diacritics (harakat) and tatweel; alef variants
     (أ إ آ ٱ) become ا; taa marbuta ة becomes ه; alef maqsura ى becomes ي;
     hamza seats ؤ ئ become و ي.
  3. Case-fold Latin text, drop punctuation, and drop a hyphenated "al-" / "el-" prefix.
  4. A simple Arabic-to-Latin transliteration (one Latin form per letter).
  5. A consonant skeleton per word: vowels (a e i o u y w) removed, q read as k,
     doubled letters collapsed, a word-final h dropped. "Mohammad", "Muhammad"
     and "محمد" all give "mhmd"; "Fatima", "Fatmeh" and "فاطمة" all give "ftm".

Score (0 to 100), documented in the README:
  name 50 x similarity (word by word, see name_similarity), birth year 20 (same 1,
  one year apart 0.5, missing 0.5), gender 10 (same 1, missing 0.5), area 10
  (same 1), household size 10 (same 1, one apart 0.5). Pairs with a score of at
  least THRESHOLD and a name similarity of at least NAME_GATE are flagged.

Blocking keeps it fast: only pairs whose birth years are within one year, or whose
family-name skeletons are equal, are scored.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Any

from rapidfuzz import fuzz

THRESHOLD = 80.0  # minimum total score for a potential duplicate
NAME_GATE = 0.85  # and the names must be at least this similar
WEIGHTS = {"name": 50.0, "birth_year": 20.0, "gender": 10.0, "area": 10.0, "size": 10.0}

# Fields shown side by side, in display order, with labels.
FIELDS: list[tuple[str, str]] = [
    ("head_name", "Head of household (as entered)"),
    ("name_script", "Name script"),
    ("head_birth_year", "Birth year of head"),
    ("head_gender", "Gender of head"),
    ("household_size", "Household size (people)"),
    ("area_id", "Area"),
    ("vulnerability", "Vulnerability flags"),
    ("registered_at", "Registered at"),
    ("registered_by", "Registered by"),
    ("status", "Record status"),
]
# Fields staff choose a value for when the two records disagree. name_script follows head_name.
CHOOSABLE_FIELDS = ["head_name", "head_birth_year", "head_gender", "household_size", "area_id", "vulnerability"]

_ARABIC_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ARABIC_LETTER = re.compile("[ء-ي]")
_SPACES = re.compile(r"\s+")
_LATIN_PUNCT = re.compile(r"[^\w\s؀-ۿ-]")
_PREFIX = re.compile(r"^(al|el)-")

_ARABIC_TO_LATIN = {
    "ا": "a", "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh", "د": "d",
    "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t",
    "ظ": "z", "ع": "", "غ": "gh", "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m",
    "ن": "n", "ه": "h", "و": "w", "ي": "y", "ء": "",
}  # fmt: skip
_VOWELS = set("aeiouyw")


def collapse_spaces(text: str | None) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def normalize_arabic(text: str | None) -> str:
    """Remove diacritics and tatweel and unify alef, taa marbuta, alef maqsura and hamza seats."""
    value = _ARABIC_DIACRITICS.sub("", text or "")
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"), ("ة", "ه"), ("ى", "ي"), ("ؤ", "و"), ("ئ", "ي")):
        value = value.replace(src, dst)
    return value


def clean_name(name: str | None) -> str:
    """Storage form written by a merge: trimmed, single spaces, Latin words capitalized,
    Arabic without diacritics or tatweel (letters themselves are kept as entered)."""
    value = _ARABIC_DIACRITICS.sub("", collapse_spaces(name))
    words = []
    for word in value.split(" "):
        if _ARABIC_LETTER.search(word):
            words.append(word)
        else:
            words.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
    return " ".join(words)


def match_form(name: str | None) -> str:
    """Normalized text used for matching (case-folded, Arabic letters unified, prefixes dropped)."""
    value = normalize_arabic(collapse_spaces(name)).casefold()
    value = _LATIN_PUNCT.sub(" ", value)
    words = [_PREFIX.sub("", w).replace("-", "") for w in value.split()]
    return " ".join(w for w in words if w)


def _transliterate_word(word: str) -> str:
    out = []
    for i, ch in enumerate(word):
        last = i == len(word) - 1
        if ch == "و":
            out.append("w" if i == 0 else "u")
        elif ch == "ي":
            out.append("y" if i == 0 else "i")
        elif ch == "ه" and last and i > 0:
            out.append("a")  # taa marbuta (already turned into ه) or a final -eh
        else:
            out.append(_ARABIC_TO_LATIN.get(ch, ch))
    return "".join(out)


def transliterate(name: str | None) -> str:
    """Simple Arabic-to-Latin transliteration of the match form. Latin text passes through."""
    return " ".join(_transliterate_word(w) for w in match_form(name).split())


def _skeleton_word(word: str) -> str:
    letters = [("k" if c == "q" else c) for c in word if c.isalpha() and c.isascii()]
    consonants = [c for c in letters if c not in _VOWELS]
    collapsed: list[str] = []
    for c in consonants:
        if not collapsed or collapsed[-1] != c:
            collapsed.append(c)
    if len(collapsed) > 1 and collapsed[-1] == "h":
        collapsed.pop()
    return "".join(collapsed) or (letters[0] if letters else "")


def skeleton(name: str | None) -> str:
    """Consonant skeleton of every word, after transliteration."""
    return " ".join(_skeleton_word(w) for w in transliterate(name).split())


def standardize(name: str | None) -> dict[str, str]:
    """All standardized forms of one entry, for display next to the original."""
    return {
        "original": name or "",
        "cleaned": clean_name(name),
        "match_form": match_form(name),
        "transliteration": transliterate(name),
        "skeleton": skeleton(name),
    }


def _word_similarity(skel_a: str, skel_b: str, lat_a: str, lat_b: str) -> float:
    return max(fuzz.ratio(skel_a, skel_b), fuzz.ratio(lat_a, lat_b))


def name_similarity(name_a: str | None, name_b: str | None) -> float:
    """0..1. Names with the same number of words are compared word by word (first name with
    first name, family with family) and the weakest word counts, so a shared family name
    alone does not make two people look alike. Each word scores the better of rapidfuzz
    ratio on its consonant skeleton and on its transliteration. Names with a different
    number of words fall back to token_sort_ratio on the whole skeleton."""
    skel_a, skel_b = skeleton(name_a).split(), skeleton(name_b).split()
    lat_a, lat_b = transliterate(name_a).split(), transliterate(name_b).split()
    if not skel_a or not skel_b:
        return 0.0
    if len(skel_a) == len(skel_b):
        words = [_word_similarity(sa, sb, la, lb) for sa, sb, la, lb in zip(skel_a, skel_b, lat_a, lat_b, strict=True)]
        return round(min(words) / 100.0, 3)
    whole = max(fuzz.token_sort_ratio(" ".join(skel_a), " ".join(skel_b)), fuzz.token_sort_ratio(" ".join(lat_a), " ".join(lat_b)))
    return round(whole / 100.0, 3)


def _near(a: Any, b: Any, step: int = 1) -> float:
    if a is None or b is None:
        return 0.5
    diff = abs(int(a) - int(b))
    return 1.0 if diff == 0 else 0.5 if diff <= step else 0.0


def score_pair(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Score two household rows. Returns the total (0..100), each part, and plain-language reasons."""
    parts = {
        "name": name_similarity(a["head_name"], b["head_name"]),
        "birth_year": _near(a["head_birth_year"], b["head_birth_year"]),
        "gender": 0.5 if not a["head_gender"] or not b["head_gender"] else float(a["head_gender"] == b["head_gender"]),
        "area": float(a["area_id"] == b["area_id"]),
        "size": _near(a["household_size"], b["household_size"]),
    }
    total = round(sum(WEIGHTS[k] * v for k, v in parts.items()), 1)
    reasons = [f"name {parts['name'] * 100:.0f}% similar"]
    reasons.append(
        "same birth year" if parts["birth_year"] == 1 else "birth year 1 apart" if parts["birth_year"] == 0.5 else "birth year differs"
    )
    if parts["gender"] < 1:
        reasons.append("gender differs")
    reasons.append("same area" if parts["area"] else "different area")
    reasons.append("same size" if parts["size"] == 1 else "size 1 apart" if parts["size"] == 0.5 else "size differs")
    return {"score": total, "parts": parts, "reasons": ", ".join(reasons)}


def active_households(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM households WHERE status = 'active' ORDER BY household_id")]


def decided_pairs(conn: sqlite3.Connection) -> set[frozenset[str]]:
    """Pairs already decided in merge_history (merged or not a duplicate)."""
    rows = conn.execute("SELECT kept_household_id, other_household_id FROM merge_history").fetchall()
    return {frozenset((r[0], r[1])) for r in rows}


def candidate_pairs(conn: sqlite3.Connection, threshold: float = THRESHOLD) -> list[dict[str, Any]]:
    """Potential duplicate pairs among active households, best first. Never returns a decided pair."""
    households = active_households(conn)
    decided = decided_pairs(conn)
    by_year: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for h in households:
        by_year[h["head_birth_year"]].append(h)
        family = skeleton(h["head_name"]).split(" ")[-1]
        by_family[family].append(h)

    pairs: set[tuple[str, str]] = set()
    rows = {h["household_id"]: h for h in households}

    def add(group: list[dict[str, Any]], other: list[dict[str, Any]]) -> None:
        for x in group:
            for y in other:
                if x["household_id"] != y["household_id"]:
                    pairs.add(tuple(sorted((x["household_id"], y["household_id"]))))  # type: ignore[arg-type]

    unknown_year = by_year.get(None, [])
    for year, group in by_year.items():
        if year is None:
            add(group, households)
            continue
        add(group, group + by_year.get(year + 1, []) + unknown_year)
    for group in by_family.values():
        add(group, group)

    found = []
    for a_id, b_id in pairs:
        if frozenset((a_id, b_id)) in decided:
            continue
        a, b = rows[a_id], rows[b_id]
        result = score_pair(a, b)
        if result["score"] >= threshold and result["parts"]["name"] >= NAME_GATE:
            found.append(
                {
                    "household_a": a_id,
                    "household_b": b_id,
                    "name_a": a["head_name"],
                    "name_b": b["head_name"],
                    "skeleton_a": skeleton(a["head_name"]),
                    "skeleton_b": skeleton(b["head_name"]),
                    "score": result["score"],
                    "name_similarity": result["parts"]["name"],
                    "reasons": result["reasons"],
                }
            )
    found.sort(key=lambda p: (-p["score"], p["household_a"], p["household_b"]))
    return found


def _display(field: str, value: Any) -> str:
    if value is None or value == "":
        return "(blank)"
    return str(value)


def compare_fields(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Every field side by side. status: 'same', 'same after standardization' or 'conflict'."""
    out = []
    for field, label in FIELDS:
        va, vb = a.get(field), b.get(field)
        if field == "head_name":
            sa, sb = skeleton(va), skeleton(vb)
            if va == vb:
                status = "same"
            elif sa == sb:
                status = "same after standardization"
            else:
                status = "conflict"
            std_a, std_b = f"{clean_name(va)} → {sa}", f"{clean_name(vb)} → {sb}"
        elif field == "vulnerability":
            set_a = set(filter(None, (va or "").split(",")))
            set_b = set(filter(None, (vb or "").split(",")))
            status = "same" if set_a == set_b else "conflict"
            std_a, std_b = ", ".join(sorted(set_a)) or "(none)", ", ".join(sorted(set_b)) or "(none)"
        else:
            status = "same" if va == vb else "conflict"
            std_a, std_b = _display(field, va), _display(field, vb)
        out.append(
            {
                "field": field,
                "label": label,
                "value_a": _display(field, va),
                "value_b": _display(field, vb),
                "standardized_a": std_a,
                "standardized_b": std_b,
                "status": status,
                # Staff pick a value whenever the entries differ, including a name that is
                # the same after standardization (to keep the Arabic or the Latin spelling).
                "choosable": field in CHOOSABLE_FIELDS and status != "same",
            }
        )
    return out


def standardized_entries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Original and standardized name of every active household (for the standardization table)."""
    out = []
    for h in active_households(conn):
        forms = standardize(h["head_name"])
        out.append(
            {
                "household_id": h["household_id"],
                "original": forms["original"],
                "cleaned": forms["cleaned"],
                "transliteration": forms["transliteration"],
                "skeleton": forms["skeleton"],
                "changed_by_cleaning": forms["cleaned"] != forms["original"],
            }
        )
    return out
