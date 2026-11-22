"""Rule-based extraction of a draft intake form from a pasted Arabic or English field note.

Pure functions: no database access. Regular expressions and keyword lists in both
languages. Arabic text is normalised first (diacritics removed, alef/ya/ta-marbuta
variants merged, Arabic-Indic digits turned into ASCII digits) so keywords match
spelling variants.

Flags
  * missing   : a required field (head_name, area_id, household_size, primary_need) was not found.
  * uncertain : a value was found but is ambiguous (two areas, two needs, a fuzzy area match,
                a household ID not in the register, two different sizes, urgency guessed).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from shared import ui
from shared.call_notes import CATEGORIES

REQUIRED_FIELDS: tuple[str, ...] = ("head_name", "area_id", "household_size", "primary_need")
URGENCY_LEVELS: tuple[str, ...] = ("low", "normal", "high", "urgent")
FIELD_ORDER: tuple[str, ...] = ("head_name", "household_id", "area_id", "household_size", "primary_need", "urgency", "notes")

# -- normalisation ----------------------------------------------------------
_DIACRITICS = re.compile("[\u064b-\u065f\u0670\u0640]")
_MAP = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ئ": "ي",
        "ؤ": "و",
        **{chr(0x0660 + i): str(i) for i in range(10)},
        **{chr(0x06F0 + i): str(i) for i in range(10)},
    }
)
_AR = "\u0621-\u064a"
_AR_PREFIX = f"(?<![{_AR}])[وبلف]?(?:ال)?"


def clean_text(text: str) -> str:
    """Remove Arabic diacritics and tatweel (keeps letters as typed)."""
    return _DIACRITICS.sub("", text)


def normalize(text: str) -> str:
    """Search form: cleaned, letter variants merged, ASCII digits, lower case (one character in, one out)."""
    out = clean_text(text).translate(_MAP)
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in out)


# -- keyword lists ----------------------------------------------------------
NEED_KEYWORDS_EN: dict[str, tuple[str, ...]] = {
    "food": ("food", "hungry", "hunger", "meal", "rice", "flour", "bread", "grocer", "parcel", "formula"),
    "shelter": ("shelter", "roof", "leak", "evict", "displaced", "mattress", "blanket", "housing", "place to stay", "damaged"),
    "health": ("fever", "clinic", "medicine", "medication", "insulin", "doctor", "sick", "injur", "pregnan", "wound"),
    "cash": ("cash", "rent", "money", "debt", "income", "lost his job", "lost her job", "bills"),
    "water_hygiene": ("water", "hygiene", "soap", "sewage", "toilet", "diaper", "sanitary"),
    "protection": ("unsafe", "safety", "harass", "threat", "abuse", "violence", "afraid", "missing son"),
}
NEED_KEYWORDS_AR: dict[str, tuple[str, ...]] = {
    "food": ("طعام", "اكل", "جائع", "جوع", "خبز", "ارز", "طحين", "غذائي", "معلبات", "حليب"),
    "shelter": ("ايواء", "سقف", "يدلف", "نزح", "نزوح", "اخلاء", "فرش", "بطانيات", "سكن", "مأوى", "ترميم"),
    "health": ("حرارة", "مستوصف", "دواء", "ادوية", "انسولين", "طبيب", "مريض", "مرض", "جرح", "اصابة", "حامل", "عيادة"),
    "cash": ("نقدي", "مال", "ايجار", "دين", "ديون", "دخل", "فواتير"),
    "water_hygiene": ("مياه", "ماء", "صابون", "نظافة", "صرف", "مرحاض", "حفاضات", "فوط"),
    "protection": ("خائف", "امان", "تحرش", "تهديد", "عنف", "مفقود", "اساءة", "سلامتها"),
}

URGENCY_LOW_EN = ("not urgent", "no rush", "low priority", "when possible", "whenever possible")
URGENCY_LOW_AR = ("ليس عاجل", "ليست عاجلة", "غير عاجل", "غير مستعجل", "بدون استعجال", "لا يوجد استعجال", "عند الامكان")
URGENCY_URGENT_EN = ("urgent", "emergency", "immediately", "critical", "life-threatening", "asap", "right now")
URGENCY_URGENT_AR = ("عاجل", "طارئ", "فورا", "حرج", "في اسرع وقت")
URGENCY_HIGH_EN = ("as soon as", "soon", "serious", "severe", "badly", "high priority", "ran out", "run out")
URGENCY_HIGH_AR = ("بسرعة", "قريبا", "شديد", "خطير", "ضروري", "نفد", "نفذ", "اولوية")

NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_AR_WORDS_RAW: dict[str, int] = {
    "واحد": 1,
    "واحدة": 1,
    "اثنان": 2,
    "اثنين": 2,
    "ثلاثة": 3,
    "ثلاث": 3,
    "اربعة": 4,
    "اربع": 4,
    "خمسة": 5,
    "خمس": 5,
    "ستة": 6,
    "ست": 6,
    "سبعة": 7,
    "سبع": 7,
    "ثمانية": 8,
    "ثمان": 8,
    "تسعة": 9,
    "تسع": 9,
    "عشرة": 10,
    "عشر": 10,
    "احد عشر": 11,
    "اثنا عشر": 12,
}
NUMBER_WORDS_AR: dict[str, int] = {normalize(k): v for k, v in _AR_WORDS_RAW.items()}
_ALL_WORDS = {**NUMBER_WORDS, **NUMBER_WORDS_AR}
_NUM = r"(\d{1,3}|" + "|".join(re.escape(w) for w in sorted(_ALL_WORDS, key=len, reverse=True)) + r")"

_SIZE_PATTERNS = [
    re.compile(rf"(?<!\w)(?:family|household|group)\s+of\s+{_NUM}(?!\w)"),
    re.compile(rf"(?<!\w){_NUM}\s+(?:people|persons|members|individuals|household members)(?!\w)"),
    re.compile(rf"(?<!\w)size\s*(?:is|of|:)?\s*{_NUM}(?!\w)"),
    re.compile(rf"(?<!\w){_NUM}\s+of\s+us(?!\w)"),
    re.compile(rf"(?<![{_AR}])(?:عائله|اسره|عيله|اسرته|عائلته)\s+(?:مكونه\s+)?من\s+{_NUM}(?![{_AR}\d])"),
    re.compile(rf"(?<![{_AR}])عدد\s+(?:افراد\s+)?(?:الاسره|العائله|البيت)?\s*:?\s*{_NUM}(?![{_AR}\d])"),
    re.compile(rf"(?<![{_AR}\d]){_NUM}\s+(?:اشخاص|افراد|انفار|نفر)(?![{_AR}])"),
]

_NAME_WORD = r"[A-Z][a-z]+(?:[-'][A-Za-z]+)?"
_EN_NAME = rf"({_NAME_WORD}(?:\s+{_NAME_WORD}){{0,2}})"
_EN_NAME_TRIGGERS = [
    re.compile(r"(?i:head\s+of\s+(?:the\s+)?(?:household|family))\s*(?:is|:|-|,)?\s*(?:(?:Mr|Mrs|Ms|Mx)\.?\s+)?" + _EN_NAME),
    re.compile(r"(?i:\bname\s+is|\bnamed|\bcalled)\s*:?\s*(?:(?:Mr|Mrs|Ms|Mx)\.?\s+)?" + _EN_NAME),
    re.compile(r"(?i:\bname)\s*:\s*" + _EN_NAME),
    re.compile(r"\b(?:Mr|Mrs|Ms|Mx)\.?\s+" + _EN_NAME),
]
_EN_NAME_STOP = {"Family", "Household", "Lives", "Living", "The", "They", "He", "She", "Please", "Area", "From", "Age", "Has", "Is"}
_AR_NAME_TRIGGER = re.compile(
    rf"(?:رب|ربه)\s+(?:الاسره|العائله|البيت)|(?<![{_AR}])(?:الاسم|اسمه|اسمها)(?![{_AR}])|(?<![{_AR}])(?:السيد|السيده)(?![{_AR}])"
)
_AR_NAME_STOP = {
    normalize(w)
    for w in (
        "يسكن",
        "تسكن",
        "ويسكن",
        "وتسكن",
        "ساكن",
        "ساكنة",
        "في",
        "من",
        "عائلته",
        "عائلتها",
        "لديه",
        "لديها",
        "عدد",
        "يعيش",
        "تعيش",
        "بحاجة",
        "يحتاج",
        "تحتاج",
        "يطلب",
        "تطلب",
        "عمره",
        "عمرها",
        "هو",
        "هي",
        "الذي",
        "التي",
        "وعائلته",
        "وعائلتها",
        "ولديه",
        "ولديها",
        "الاسره",
        "العائله",
    )
}
_HH_RE = re.compile(r"(?<![A-Za-z0-9])hh-?(\d{5})(?!\d)")
_AREA_CODE_RE = re.compile(r"(?<![a-z0-9])ar-?(\d{1,2})(?!\d)")


@dataclass
class Extraction:
    """Draft intake form: field values (None when not found) and the flags with reasons."""

    language: str
    fields: dict[str, Any]
    missing: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)

    def flags(self) -> dict[str, Any]:
        return {"missing": list(self.missing), "uncertain": list(self.uncertain), "reasons": dict(self.reasons)}


def _flag_missing(ex: Extraction, name: str, reason: str) -> None:
    if name not in ex.missing:
        ex.missing.append(name)
    ex.reasons[name] = reason


def _flag_uncertain(ex: Extraction, name: str, reason: str) -> None:
    if name not in ex.uncertain:
        ex.uncertain.append(name)
    ex.reasons[name] = (ex.reasons[name] + " " if name in ex.reasons else "") + reason


# -- field extractors -------------------------------------------------------
def extract_head_name(clean: str, norm: str) -> str | None:
    """Head of household name from English or Arabic trigger phrases."""
    for pattern in _EN_NAME_TRIGGERS:
        m = pattern.search(clean)
        if m:
            kept: list[str] = []
            for w in m.group(1).split():
                if w in _EN_NAME_STOP:
                    break
                kept.append(w)
            if kept:
                return " ".join(kept)
    m = _AR_NAME_TRIGGER.search(norm)
    if m:
        end = m.end()
        lead = re.match(r"\s*[:\-]?\s*(?:(?:السيد|السيده|الحاج|الحاجه)\s+)?", norm[end:])
        start = end + (lead.end() if lead else 0)
        words: list[str] = []
        pos = start
        for wm in re.finditer(rf"[{_AR}]+", norm[start:]):
            gap = norm[pos : start + wm.start()]
            if words and gap.strip():
                break  # punctuation between words ends the name
            if wm.group() in _AR_NAME_STOP or len(words) == 3:
                break
            if not words and gap.strip():
                break
            words.append(clean[start + wm.start() : start + wm.end()])
            pos = start + wm.end()
        if words:
            return " ".join(words)
    return None


def extract_sizes(norm: str) -> list[int]:
    """All household sizes stated in size context, in order of appearance (digits or number words)."""
    found: list[tuple[int, int]] = []
    for pattern in _SIZE_PATTERNS:
        for m in pattern.finditer(norm):
            token = m.group(1)
            value = int(token) if token.isdigit() else _ALL_WORDS.get(token)
            if value is not None:
                found.append((m.start(1), value))
    found.sort()
    return [v for _, v in found]


def extract_household_id(norm: str) -> str | None:
    m = _HH_RE.search(norm)
    return f"HH-{m.group(1)}" if m else None


def find_areas(norm: str, areas: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    """(position, area_id, how) for each area mentioned, first mention first. how: code, name or fuzzy:<text>."""
    ids = {a["area_id"] for a in areas}
    hits: dict[str, tuple[int, str]] = {}

    def keep(area_id: str, pos: int, how: str) -> None:
        if area_id not in hits or pos < hits[area_id][0]:
            hits[area_id] = (pos, how)

    for m in _AREA_CODE_RE.finditer(norm):
        code = f"AR-{int(m.group(1)):02d}"
        if code in ids:
            keep(code, m.start(), "code")
    for a in areas:
        en = re.search(rf"(?<!\w){re.escape(normalize(a['name_en']))}(?!\w)", norm)
        if en:
            keep(a["area_id"], en.start(), "name")
        ar = re.search(rf"(?<![{_AR}])[وبلف]?{re.escape(normalize(a['name_ar']))}(?![{_AR}])", norm)
        if ar:
            keep(a["area_id"], ar.start(), "name")
    if not hits:
        for a in areas:
            for name, token_re in ((normalize(a["name_en"]), r"[a-z]+"), (normalize(a["name_ar"]), rf"[{_AR}]+")):
                n = len(name.split())
                tokens = list(re.finditer(token_re, norm))
                for i in range(len(tokens) - n + 1):
                    window = " ".join(t.group() for t in tokens[i : i + n])
                    if difflib.SequenceMatcher(None, window, name).ratio() >= 0.85:
                        keep(a["area_id"], tokens[i].start(), f"fuzzy:{window}")
    return sorted((pos, aid, how) for aid, (pos, how) in hits.items())


def _kw_regex(keyword: str, arabic: bool) -> re.Pattern[str]:
    k = re.escape(normalize(keyword))
    return re.compile(_AR_PREFIX + k) if arabic else re.compile(rf"(?<!\w){k}")


_NEED_RES: dict[str, list[re.Pattern[str]]] = {
    c: [_kw_regex(k, False) for k in NEED_KEYWORDS_EN[c]] + [_kw_regex(k, True) for k in NEED_KEYWORDS_AR[c]] for c in CATEGORIES
}


def score_needs(norm: str) -> dict[str, tuple[int, int]]:
    """Per category with at least one hit: (number of keywords that hit, position of the first hit)."""
    out: dict[str, tuple[int, int]] = {}
    for category, regexes in _NEED_RES.items():
        positions = [m.start() for r in regexes for m in [r.search(norm)] if m]
        if positions:
            out[category] = (len(positions), min(positions))
    return out


def _has(norm: str, phrases: tuple[str, ...], arabic: bool) -> bool:
    return any(_kw_regex(p, arabic).search(norm) for p in phrases)


def extract_urgency(norm: str) -> str | None:
    """low / urgent / high from cue words in either language; None when there is no cue."""
    if _has(norm, URGENCY_LOW_EN, False) or _has(norm, URGENCY_LOW_AR, True):
        return "low"
    if _has(norm, URGENCY_URGENT_EN, False) or _has(norm, URGENCY_URGENT_AR, True):
        return "urgent"
    if _has(norm, URGENCY_HIGH_EN, False) or _has(norm, URGENCY_HIGH_AR, True):
        return "high"
    return None


# -- the whole draft --------------------------------------------------------
def extract(note: str, areas: list[dict[str, Any]], known_household_ids: set[str] | frozenset[str] = frozenset()) -> Extraction:
    """Turn a pasted field note into a draft intake form with missing/uncertain flags."""
    text = note or ""
    clean = clean_text(text)
    norm = normalize(text)
    ex = Extraction(language="ar" if ui.has_arabic(text) else "en", fields=dict.fromkeys(FIELD_ORDER))

    name = extract_head_name(clean, norm)
    ex.fields["head_name"] = name
    if name is None:
        _flag_missing(
            ex, "head_name", "No head of household name found (looked for 'head of household', 'name is', 'Mr/Mrs', 'رب الأسرة', 'الاسم')."
        )
    elif len(name.split()) < 2:
        _flag_uncertain(ex, "head_name", "Only one name part found; check the full name.")

    hh = extract_household_id(norm)
    ex.fields["household_id"] = hh
    if hh and hh not in known_household_ids:
        _flag_uncertain(ex, "household_id", f"{hh} is not in the household register; correct it or clear it.")

    hits = find_areas(norm, areas)
    if hits:
        names = {a["area_id"]: a["name_en"] for a in areas}
        ex.fields["area_id"] = hits[0][1]
        if len(hits) > 1:
            listed = ", ".join(f"{names[h[1]]} ({h[1]})" for h in hits)
            _flag_uncertain(ex, "area_id", f"Several areas mentioned: {listed}. The first was chosen.")
        if hits[0][2].startswith("fuzzy:"):
            _flag_uncertain(ex, "area_id", f"Approximate match: '{hits[0][2][6:]}' looks like {names[hits[0][1]]}.")
    else:
        _flag_missing(ex, "area_id", "No area name or AR-NN code from the area list found.")

    sizes = extract_sizes(norm)
    if sizes:
        ex.fields["household_size"] = sizes[0]
        if len(set(sizes)) > 1:
            shown = ", ".join(str(s) for s in dict.fromkeys(sizes))
            _flag_uncertain(ex, "household_size", f"Different sizes stated ({shown}). The first was chosen.")
        elif sizes[0] > 30:
            _flag_uncertain(ex, "household_size", f"{sizes[0]} is unusually large for one household.")
    else:
        _flag_missing(ex, "household_size", "No household size found (looked for 'family of N', 'N people', 'عائلة من N', 'N أشخاص').")

    scores = score_needs(norm)
    if scores:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
        ex.fields["primary_need"] = ranked[0][0]
        if len(ranked) > 1:
            listed = ", ".join(c for c, _ in ranked)
            _flag_uncertain(
                ex, "primary_need", f"Several needs mentioned: {listed}. '{ranked[0][0]}' was chosen (most keywords, then first mentioned)."
            )
    else:
        _flag_missing(ex, "primary_need", "No need keyword found for food, shelter, health, cash, water and hygiene, or protection.")

    urgency = extract_urgency(norm)
    ex.fields["urgency"] = urgency or "normal"
    if urgency is None:
        _flag_uncertain(ex, "urgency", "No urgency cue in the note; 'normal' is a default guess.")

    ex.fields["notes"] = re.sub(r"\s+", " ", text).strip()[:300] or None
    return ex
