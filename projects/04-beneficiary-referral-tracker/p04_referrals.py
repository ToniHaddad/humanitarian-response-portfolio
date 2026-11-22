"""Project 04: find a service for a family's need, explain the match, track the referral.

Technique: rule-based scoring (weights below, documented in the README).
Every function takes a connection so tests can call it directly.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from shared import clock, confirm, db
from shared.call_notes import CATEGORIES, CATEGORY_LABELS
from shared.geo import distance_km

CHANNEL = "app:04"
APPROVER_ROLES = ("caseworker", "coordinator")

# Score weights (sum to 1.0). Documented in the README.
W_CATEGORY = 0.40
W_KEYWORDS = 0.25
W_DISTANCE = 0.20
W_CAPACITY = 0.15
KEYWORD_HITS_FOR_FULL_SCORE = 2  # two distinct keyword hits give the full keyword score
DISTANCE_CUTOFF_KM = 40.0  # at or beyond this distance the distance score is 0

STATUSES = ("proposed", "approved", "accepted", "declined", "support_received", "support_not_received")
NEXT_STEP = {
    "proposed": "Approve the referral (staff confirmation)",
    "approved": "Record the provider's response",
    "accepted": "Confirm whether the family received support (staff confirmation)",
    "declined": "Ended: provider declined. Find another service.",
    "support_received": "Closed: family received support.",
    "support_not_received": "Closed: family did not receive support. Consider a new referral.",
}


class InvalidTransition(ValueError):
    """Raised when a referral step is attempted out of order."""


# -- text handling --------------------------------------------------------
_TASHKEEL = re.compile("[ً-ٰـ]")
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_ARABIC_FOLD = (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه"))
_AL = "ال"  # the Arabic article


def normalise(text: str) -> str:
    """Lower-case, strip Arabic diacritics and unify alef/ya/ta-marbuta forms."""
    text = _TASHKEEL.sub("", (text or "").lower())
    for src, dst in _ARABIC_FOLD:
        text = text.replace(src, dst)
    return text


def tokenise(text: str) -> list[str]:
    return _TOKEN.findall(normalise(text))


def _variants(token: str) -> set[str]:
    """Simple forms of a token: English plural 's', Arabic article and the prefixes waw, ba, lam."""
    forms = {token}
    if token.endswith("s") and len(token) > 3:
        forms.add(token[:-1])
    for form in list(forms):
        if form.startswith(_AL) and len(form) > 4:
            forms.add(form[2:])
        if len(form) > 3 and form[0] in "وبل":
            forms.add(form[1:])
            if form[1:].startswith(_AL):
                forms.add(form[3:])
    return forms


def keyword_hits(need_text: str, keywords: str) -> list[str]:
    """Distinct keywords (as written in the directory) found in the need text."""
    tokens = tokenise(need_text)
    forms: set[str] = set()
    for tok in tokens:
        forms |= _variants(tok)
    joined = " " + " ".join(tokens) + " "
    hits: list[str] = []
    for kw in [k.strip() for k in (keywords or "").split(",") if k.strip()]:
        nkw = normalise(kw)
        found = (" " + nkw + " ") in joined if " " in nkw else nkw in forms
        if found and kw not in hits:
            hits.append(kw)
    return hits


# -- data access ----------------------------------------------------------
def household_area(conn: sqlite3.Connection, household_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT h.household_id, h.household_size, h.area_id, a.name_en, a.name_ar, a.x_km, a.y_km
           FROM households h JOIN areas a ON a.area_id = h.area_id WHERE h.household_id = ?""",
        (household_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Unknown household {household_id}")
    return dict(row)


def open_requests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Service requests that are not closed and have a household (starting points for a referral)."""
    rows = conn.execute(
        """SELECT request_id, household_id, notes, category, status FROM service_requests
           WHERE status != 'closed' AND household_id IS NOT NULL ORDER BY request_id"""
    ).fetchall()
    return [dict(r) for r in rows]


def infer_category(conn: sqlite3.Connection, need_text: str) -> str | None:
    """Category whose services' keywords match the need most often (None when nothing matches)."""
    counts = dict.fromkeys(CATEGORIES, 0)
    for row in conn.execute("SELECT service_category, keywords FROM provider_services"):
        if row["service_category"] in counts:
            counts[row["service_category"]] += len(keyword_hits(need_text, row["keywords"]))
    best = max(CATEGORIES, key=lambda c: counts[c])
    return best if counts[best] > 0 else None


# -- scoring --------------------------------------------------------------
@dataclass
class Match:
    provider_id: str
    provider_name: str
    provider_name_ar: str
    service_category: str
    service_name: str
    score: float
    components: dict[str, float]
    keywords_found: list[str]
    distance_km: float
    remaining: int
    weekly_capacity: int
    eligibility: str
    explanation: str
    excluded_reason: str | None = None


def category_component(service_category: str, wanted: str | None) -> float:
    return 1.0 if wanted and service_category == wanted else 0.0


def keyword_component(hits: int) -> float:
    return min(1.0, hits / KEYWORD_HITS_FOR_FULL_SCORE)


def distance_component(km: float) -> float:
    return max(0.0, 1.0 - km / DISTANCE_CUTOFF_KM)


def capacity_component(remaining: int, weekly_capacity: int) -> float:
    if weekly_capacity <= 0:
        return 0.0
    return max(0.0, min(1.0, remaining / weekly_capacity))


def combine(category: float, keywords: float, distance: float, capacity: float) -> float:
    return round(W_CATEGORY * category + W_KEYWORDS * keywords + W_DISTANCE * distance + W_CAPACITY * capacity, 3)


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category).lower()


def explain(m: Match, wanted: str | None, area_name: str) -> str:
    if m.components["category"]:
        cat = f"Category match: {_label(m.service_category)}."
    elif wanted:
        cat = f"Category differs: this is {_label(m.service_category)}, the need looks like {_label(wanted)}."
    else:
        cat = f"Category not matched: no category known for the need (service is {_label(m.service_category)})."
    kws = f"Keywords found: {', '.join(m.keywords_found)}." if m.keywords_found else "Keywords found: none."
    dist = f"{m.distance_km:.1f} km from {area_name} (the family's area centre)."
    cap = f"{m.remaining} of {m.weekly_capacity} places left this week."
    return f"{cat} {kws} {dist} {cap} Eligibility: {m.eligibility}"


def find_matches(conn: sqlite3.Connection, household_id: str, need_text: str, category: str | None = None) -> dict[str, Any]:
    """Score every provider service for the family.

    Returns the category used, the family's area, ranked matches (best first) and the
    relevant services excluded with a reason (not accepting, or no places left).
    """
    area = household_area(conn, household_id)
    wanted = category or infer_category(conn, need_text)
    origin = (area["x_km"], area["y_km"])
    ranked: list[Match] = []
    excluded: list[Match] = []
    rows = conn.execute(
        """SELECT s.*, p.name, p.name_ar, p.x_km, p.y_km
           FROM provider_services s JOIN service_providers p ON p.provider_id = s.provider_id
           ORDER BY s.provider_id, s.service_name"""
    ).fetchall()
    for r in rows:
        hits = keyword_hits(need_text, r["keywords"])
        km = distance_km(origin, (r["x_km"], r["y_km"]))
        remaining = r["weekly_capacity"] - r["current_load"]
        comps = {
            "category": category_component(r["service_category"], wanted),
            "keywords": keyword_component(len(hits)),
            "distance": distance_component(km),
            "capacity": capacity_component(remaining, r["weekly_capacity"]),
        }
        m = Match(
            provider_id=r["provider_id"],
            provider_name=r["name"],
            provider_name_ar=r["name_ar"],
            service_category=r["service_category"],
            service_name=r["service_name"],
            score=combine(**comps),
            components=comps,
            keywords_found=hits,
            distance_km=km,
            remaining=remaining,
            weekly_capacity=r["weekly_capacity"],
            eligibility=r["eligibility"],
            explanation="",
        )
        m.explanation = explain(m, wanted, area["name_en"])
        reasons = []
        if not r["accepts_new"]:
            reasons.append("not accepting new referrals")
        if remaining <= 0:
            reasons.append("no places left this week")
        if reasons:
            m.excluded_reason = "Excluded: " + " and ".join(reasons) + "."
            if comps["category"] or hits:  # list only services that would otherwise be relevant
                excluded.append(m)
        else:
            ranked.append(m)
    ranked.sort(key=lambda m: (-m.score, m.distance_km, m.provider_id, m.service_name))
    excluded.sort(key=lambda m: (-m.score, m.provider_id, m.service_name))
    return {"category": wanted, "area": area, "matches": ranked, "excluded": excluded}


# -- referral lifecycle ---------------------------------------------------
def _get(conn: sqlite3.Connection, referral_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM referrals WHERE referral_id = ?", (referral_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown referral {referral_id}")
    return dict(row)


def _require_staff(conn: sqlite3.Connection, staff_id: str) -> None:
    if conn.execute("SELECT 1 FROM staff WHERE staff_id = ? AND active = 1", (staff_id,)).fetchone() is None:
        raise ValueError(f"Unknown or inactive staff ID: {staff_id}")


def create_referral(
    conn: sqlite3.Connection,
    household_id: str,
    need_text: str,
    match: Match,
    caseworker_id: str,
    request_id: str | None = None,
) -> str:
    """Record a proposed referral from a chosen match (with its score and explanation)."""
    if not need_text.strip():
        raise ValueError("The stated need is empty.")
    if match.excluded_reason:
        raise ValueError(match.excluded_reason)
    household_area(conn, household_id)
    _require_staff(conn, caseworker_id)
    referral_id = db.next_id(conn, "referrals", "referral_id", "REF-", 4)
    conn.execute(
        """INSERT INTO referrals(referral_id, created_at, household_id, request_id, need_text, service_category,
                                 provider_id, service_name, match_score, match_explanation, status, caseworker_id)
           VALUES (?,?,?,?,?,?,?,?,?,?, 'proposed', ?)""",
        (
            referral_id,
            clock.iso(),
            household_id,
            request_id,
            need_text.strip(),
            match.service_category,
            match.provider_id,
            match.service_name,
            match.score,
            match.explanation,
            caseworker_id,
        ),
    )
    db.audit(
        conn,
        actor_id=caseworker_id,
        channel=CHANNEL,
        action="create",
        entity_table="referrals",
        entity_id=referral_id,
        after={"status": "proposed", "provider_id": match.provider_id, "service_name": match.service_name, "score": match.score},
    )
    return referral_id


def approve(conn: sqlite3.Connection, referral_id: str, confirmed_by: str | None) -> None:
    """Approve a proposed referral (consequential: needs staff confirmation)."""
    staff = confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    ref = _get(conn, referral_id)
    if ref["status"] != "proposed":
        raise InvalidTransition(f"{referral_id} is {ref['status']}; only a proposed referral can be approved.")
    conn.execute(
        "UPDATE referrals SET status='approved', approved_by=?, approved_at=? WHERE referral_id=?",
        (staff, clock.iso(), referral_id),
    )
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="approve",
        entity_table="referrals",
        entity_id=referral_id,
        before={"status": "proposed"},
        after={"status": "approved", "approved_by": staff},
    )


def record_provider_response(conn: sqlite3.Connection, referral_id: str, response: str, note: str, recorded_by: str) -> None:
    """Record the provider's answer (accepted or declined) on an approved referral."""
    if response not in ("accepted", "declined"):
        raise ValueError("Response must be 'accepted' or 'declined'.")
    _require_staff(conn, recorded_by)
    ref = _get(conn, referral_id)
    if ref["status"] != "approved":
        raise InvalidTransition(f"{referral_id} is {ref['status']}; a response can only be recorded on an approved referral.")
    conn.execute(
        "UPDATE referrals SET status=?, provider_response=?, provider_response_note=?, provider_response_at=? WHERE referral_id=?",
        (response, response, note.strip() or None, clock.iso(), referral_id),
    )
    db.audit(
        conn,
        actor_id=recorded_by,
        channel=CHANNEL,
        action="provider_response",
        entity_table="referrals",
        entity_id=referral_id,
        before={"status": "approved"},
        after={"status": response, "note": note.strip()},
    )


def confirm_outcome(conn: sqlite3.Connection, referral_id: str, family_received: bool, note: str, confirmed_by: str | None) -> None:
    """Confirm whether the family received support (consequential: needs staff confirmation)."""
    staff = confirm.require_confirmation(conn, confirmed_by, APPROVER_ROLES)
    ref = _get(conn, referral_id)
    if ref["status"] != "accepted":
        raise InvalidTransition(f"{referral_id} is {ref['status']}; the outcome can only be confirmed after the provider accepted.")
    status = "support_received" if family_received else "support_not_received"
    conn.execute(
        """UPDATE referrals SET status=?, family_received=?, outcome_note=?, outcome_confirmed_at=?, outcome_confirmed_by=?
           WHERE referral_id=?""",
        (status, 1 if family_received else 0, note.strip() or None, clock.iso(), staff, referral_id),
    )
    db.audit(
        conn,
        actor_id=staff,
        channel=CHANNEL,
        action="confirm_outcome",
        entity_table="referrals",
        entity_id=referral_id,
        before={"status": "accepted"},
        after={"status": status, "family_received": int(family_received), "note": note.strip()},
    )


def list_referrals(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    """Referrals with provider name and next step, newest first, optionally filtered by status."""
    sql = """SELECT r.referral_id, r.created_at, r.household_id, r.service_category, r.service_name, p.name AS provider_name,
                    r.match_score, r.status, r.caseworker_id, r.approved_by, r.provider_response_note, r.outcome_note,
                    r.need_text, r.match_explanation
             FROM referrals r JOIN service_providers p ON p.provider_id = r.provider_id"""
    params: tuple[Any, ...] = ()
    if status:
        sql += " WHERE r.status = ?"
        params = (status,)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY r.referral_id DESC", params)]
    for row in rows:
        row["next_step"] = NEXT_STEP[row["status"]]
    return rows
