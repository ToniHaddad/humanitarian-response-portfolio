"""Seeded generator for the whole demo database, plus the reset command.

Usage:
    python -m shared.seed --reset                    # rebuild the database at PORTFOLIO_DB_PATH (or data/portfolio.db)
    python -m shared.seed --reset --anchor 2026-11-16T09:00:00   # pin the demo clock for exact reproduction
    python -m shared.seed --reset --db path/to/copy.db

Determinism: every value comes from random streams seeded with config.SEED and a
section name. Dates are offsets from an anchor time, which defaults to the moment
the seed runs so that "today", "overdue" and "next week" stay meaningful. With the
same anchor the database is identical byte for byte in content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared import call_notes, clock, config, db, food, security
from shared import seed_content as sc

TABLES_ALLOWED_EMPTY: frozenset[str] = frozenset({"sqlite_sequence"})


class Seeder:
    def __init__(self, conn: sqlite3.Connection, anchor: datetime, seed: int = config.SEED) -> None:
        self.c = conn
        self.A = anchor.replace(second=0, microsecond=0)
        self.D0 = self.A.date()
        self.seed = seed
        self.scenarios: dict[str, Any] = {}
        self.area = {a[0]: a for a in sc.AREAS}
        self.site_xy: dict[str, tuple[float, float]] = {}
        self.wh_xy: dict[str, tuple[float, float]] = {}
        self.audit_rows: list[dict[str, Any]] = []

    # -- helpers -----------------------------------------------------------
    def rng(self, section: str) -> random.Random:
        return random.Random(f"{self.seed}:{section}")

    def t(self, days: float = 0, hours: float = 0, minutes: float = 0) -> str:
        return clock.iso(self.A + timedelta(days=days, hours=hours, minutes=minutes))

    def at(self, day: date, hour: int, minute: int = 0) -> str:
        """A time on a given day, never later than the anchor."""
        value = datetime(day.year, day.month, day.day, hour, minute)
        return clock.iso(min(value, self.A))

    def d(self, days: int) -> str:
        return (self.D0 + timedelta(days=days)).isoformat()

    def ins(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        cols = list(rows[0].keys())
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        self.c.executemany(sql, [tuple(r[c] for c in cols) for r in rows])

    def audit(
        self,
        at: str,
        actor: str,
        action: str,
        table: str,
        entity: str,
        after: dict | None = None,
        before: dict | None = None,
        reason: str | None = None,
        channel: str = "seed",
    ) -> None:
        self.audit_rows.append(
            dict(
                at=at,
                actor_id=actor,
                actor_role=self.staff_role.get(actor, "system"),
                channel=channel,
                action=action,
                entity_table=table,
                entity_id=entity,
                before_json=json.dumps(before, ensure_ascii=False) if before else None,
                after_json=json.dumps(after, ensure_ascii=False) if after else None,
                reason=reason,
            )
        )

    # -- run ---------------------------------------------------------------
    def run(self) -> dict[str, Any]:
        self.reference()
        self.households()
        self.classifier_notes()
        self.call_centre()
        self.referrals()
        self.intake_and_sync()
        self.pipeline()
        self.volunteers()
        self.fleet()
        self.supply_chain()
        self.convoy_details()
        self.positions()
        self.security()
        self.briefings_and_sitreps()
        self.platform()
        self.merges()
        self.ins("audit_log", sorted(self.audit_rows, key=lambda r: r["at"]))
        self.c.execute(
            "INSERT INTO settings(key, value, description, updated_at, updated_by) VALUES (?,?,?,?,?)",
            (
                "demo.scenarios",
                json.dumps(self.scenarios, ensure_ascii=False, sort_keys=True),
                "IDs and periods the README walkthroughs use (written by the seed).",
                self.t(),
                None,
            ),
        )
        return self.scenarios

    # -- reference data ----------------------------------------------------
    def reference(self) -> None:
        self.ins(
            "areas",
            [
                dict(area_id=a[0], name_en=a[1], name_ar=a[2], district=a[3], x_km=a[4], y_km=a[5], population_estimate=a[6])
                for a in sc.AREAS
            ],
        )
        site_rows = []
        for s in sc.SITES:
            ax, ay = self.area[s[3]][4], self.area[s[3]][5]
            self.site_xy[s[0]] = (round(ax + s[5], 2), round(ay + s[6], 2))
            site_rows.append(
                dict(
                    site_id=s[0],
                    name=s[1],
                    name_ar=s[2],
                    area_id=s[3],
                    site_type=s[4],
                    x_km=self.site_xy[s[0]][0],
                    y_km=self.site_xy[s[0]][1],
                )
            )
        self.ins("sites", site_rows)
        wh_rows = []
        for w in sc.WAREHOUSES:
            ax, ay = self.area[w[2]][4], self.area[w[2]][5]
            self.wh_xy[w[0]] = (round(ax + w[3], 2), round(ay + w[4], 2))
            wh_rows.append(dict(warehouse_id=w[0], name=w[1], area_id=w[2], x_km=self.wh_xy[w[0]][0], y_km=self.wh_xy[w[0]][1]))
        self.ins("warehouses", wh_rows)
        self.ins(
            "items",
            [dict(item_id=i[0], name=i[1], category=i[2], unit=i[3], unit_weight_kg=i[4], person_days_per_unit=None) for i in sc.ITEMS],
        )
        self.item_weight = {i[0]: i[4] for i in sc.ITEMS}
        self.ins("staff", [dict(staff_id=s[0], display_name=s[1], role=s[2], active=1) for s in sc.STAFF])
        self.staff_role = {s[0]: s[2] for s in sc.STAFF}
        self.staff_by_role: dict[str, list[str]] = defaultdict(list)
        for s in sc.STAFF:
            self.staff_by_role[s[2]].append(s[0])
        settings = [
            ("callback_sla_hours", "48", "Hours after assignment within which a callback is due (01)."),
            ("stale_position_minutes", "15", "A vehicle position older than this many minutes is stale (14, 17, 20)."),
            ("classifier_confidence_threshold", "0.6", "Predictions below this confidence are left blank (01, 03)."),
            ("position_simulator_enabled", "1", "1 while the position simulator is running, 0 when stopped (14, 17, 20)."),
            ("simulator_interval_seconds", "60", "Seconds between simulated position reports."),
            ("board_refresh_seconds", "30", "Operations room board refresh interval (17)."),
            ("demo.anchor", clock.iso(self.A), "Time the demo data was generated around."),
        ]
        self.ins("settings", [dict(key=k, value=v, description=desc, updated_at=self.t(), updated_by=None) for k, v, desc in settings])
        assumptions = [
            dict(assumption_key=k, value=v[0], unit=v[1], description=v[2], updated_at=self.t(days=-30), updated_by="CRD-01")
            for k, v in food.DEFAULT_ASSUMPTIONS.items()
        ]
        self.ins("forecast_assumptions", assumptions)
        # The one source of person-days per parcel and the parcel-size rule (shared.food).
        self.assumptions = food.default_assumptions()

    # -- households --------------------------------------------------------
    def _name(self, r: random.Random, gender: str) -> tuple[tuple[str, str, list[str]], tuple[str, str, list[str]]]:
        first = r.choice(sc.FIRST_NAMES_F if gender == "F" else sc.FIRST_NAMES_M)
        family = r.choice(sc.FAMILY_NAMES)
        return first, family

    def households(self) -> None:
        r = self.rng("households")
        area_ids = [a[0] for a in sc.AREAS]
        weights = [a[6] * (1.6 if a[0] in ("AR-01", "AR-02", "AR-03") else 1.0) for a in sc.AREAS]
        sizes = [1, 2, 3, 4, 5, 6, 7, 8, 9]
        size_w = [4, 9, 14, 18, 18, 14, 10, 8, 5]
        vul = ["elderly_head", "disability", "female_headed", "chronic_illness"]
        rows: list[dict[str, Any]] = []
        self.hh_names: dict[str, tuple] = {}
        for i in range(1, 361):
            gender = "F" if r.random() < 0.35 else "M"
            first, family = self._name(r, gender)
            script = "ar" if r.random() < 0.25 else "latin"
            name = f"{first[1]} {family[1]}" if script == "ar" else f"{first[0]} {family[0]}"
            hid = f"HH-{i:05d}"
            self.hh_names[hid] = (first, family)
            flags = [v for v in vul if r.random() < 0.12]
            if gender == "F" and "female_headed" not in flags and r.random() < 0.5:
                flags.append("female_headed")
            rows.append(
                dict(
                    household_id=hid,
                    head_name=name,
                    name_script=script,
                    head_birth_year=r.randint(1948, 2003),
                    head_gender=gender,
                    household_size=r.choices(sizes, size_w)[0],
                    area_id=r.choices(area_ids, weights)[0],
                    registered_at=self.at(self.D0 - timedelta(days=r.randint(20, 420)), r.randint(8, 16), r.randint(0, 59)),
                    registered_by=r.choice(self.staff_by_role["caseworker"] + self.staff_by_role["field_worker"]),
                    vulnerability=",".join(flags) or None,
                    status="active",
                    merged_into=None,
                )
            )
        # Planted duplicates for Project 06: spelling variants, Arabic script, moved area.
        base_idx = [12, 27, 45, 63, 88, 104, 131, 157]
        pairs = []
        for n, bi in enumerate(base_idx):
            base = rows[bi - 1]
            first, family = self.hh_names[base["household_id"]]
            dup_id = f"HH-{361 + n:05d}"
            kind = [
                "latin_variant",
                "arabic_script",
                "latin_variant",
                "family_variant",
                "arabic_script",
                "moved_area",
                "latin_variant",
                "size_changed",
            ][n]
            if kind == "arabic_script":
                name, script = f"{first[1]} {family[1]}", "ar"
                if base["name_script"] == "ar":
                    name, script = f"{first[0]} {family[0]}", "latin"
            elif kind == "family_variant":
                name, script = f"{first[0]} {family[2][0]}", "latin"
            else:
                name, script = f"{first[2][0]} {family[2][0] if n % 2 else family[0]}", "latin"
            area = base["area_id"]
            if kind == "moved_area":
                area = area_ids[(area_ids.index(area) + 1) % len(area_ids)]
            size = base["household_size"] + (1 if kind == "size_changed" else 0)
            birth = base["head_birth_year"] + (1 if kind == "latin_variant" and n == 6 else 0)
            rows.append(
                dict(
                    base,
                    household_id=dup_id,
                    head_name=name,
                    name_script=script,
                    head_birth_year=birth,
                    household_size=size,
                    area_id=area,
                    registered_at=self.at(self.D0 - timedelta(days=r.randint(5, 60)), 11, 15),
                    registered_by=r.choice(self.staff_by_role["field_worker"]),
                )
            )
            self.hh_names[dup_id] = (first, family)
            pairs.append({"household_a": base["household_id"], "household_b": dup_id, "variation": kind})
        # HH-00369: already merged into HH-00200 (history exists). HH-00370: look-alike judged not a duplicate.
        b200 = rows[199]
        f200, fam200 = self.hh_names["HH-00200"]
        rows.append(
            dict(
                b200,
                household_id="HH-00369",
                head_name=f"{f200[2][0]} {fam200[0]}",
                name_script="latin",
                registered_at=self.at(self.D0 - timedelta(days=90), 10, 0),
                status="merged",
                merged_into="HH-00200",
            )
        )
        b210 = rows[209]
        f210, fam210 = self.hh_names["HH-00210"]
        rows.append(
            dict(
                b210,
                household_id="HH-00370",
                head_name=f"{f210[0]} {fam210[0]}",
                name_script="latin",
                head_birth_year=b210["head_birth_year"] - 23,
                household_size=max(1, b210["household_size"] - 1),
                area_id=area_ids[(area_ids.index(b210["area_id"]) + 4) % len(area_ids)],
                registered_at=self.at(self.D0 - timedelta(days=70), 10, 0),
            )
        )
        self.ins("households", rows)
        self.hh = {row["household_id"]: row for row in rows}
        self.active_hh = [row for row in rows if row["status"] == "active"]
        self.scenarios["p06"] = {
            "duplicate_pairs": pairs,
            "already_merged": {"kept": "HH-00200", "merged": "HH-00369"},
            "judged_not_duplicate": {"household_a": "HH-00210", "household_b": "HH-00370"},
        }

    # -- classifier notes --------------------------------------------------
    def classifier_notes(self) -> None:
        r = self.rng("classifier_notes")
        notes = call_notes.make_labelled_notes_split(r, per_category=100)
        self.ins(
            "classifier_notes",
            [dict(note_id=i + 1, text=t, language=lang, label=label, split=split) for i, (t, lang, label, split) in enumerate(notes)],
        )

    # -- call centre -------------------------------------------------------
    def _work_time(self, r: random.Random, max_age_days: float, min_age_days: float = 0.0) -> datetime:
        for _ in range(50):
            age = r.uniform(min_age_days, max_age_days)
            when = self.A - timedelta(days=age)
            when = when.replace(hour=r.randint(8, 17), minute=r.randint(0, 59), second=0)
            if when <= self.A:
                return when
        return self.A - timedelta(minutes=30)

    def call_centre(self) -> None:
        r = self.rng("call_centre")
        cats = list(call_notes.CATEGORIES)
        cat_w = [30, 18, 18, 16, 10, 8]
        operators = self.staff_by_role["operator"]
        caseworkers = self.staff_by_role["caseworker"]
        calls: list[dict[str, Any]] = []
        sla = 48
        # Service requests
        req_drafts = []
        for _ in range(130):
            created = self._work_time(r, 30)
            req_drafts.append(created)
        req_drafts.sort()
        requests, events = [], []
        for n, created in enumerate(req_drafts, start=1):
            rid = f"REQ-{n:05d}"
            hh = r.choice(self.active_hh)
            area = self.area[hh["area_id"]]
            lang = "ar" if r.random() < 0.6 else "en"
            cat = r.choices(cats, cat_w)[0]
            notes = call_notes.make_note(r, cat, lang, area[1], area[2], ambiguous=r.random() < 0.1)
            age_days = (self.A - created).total_seconds() / 86400
            roll = r.random()
            if age_days > 10:
                status = "closed" if roll < 0.85 else ("in_progress" if roll < 0.95 else "callback_scheduled")
            elif age_days > 3:
                status = "closed" if roll < 0.3 else "in_progress" if roll < 0.55 else "callback_scheduled" if roll < 0.85 else "assigned"
            else:
                status = "new" if roll < 0.4 else ("assigned" if roll < 0.65 else "callback_scheduled")
            op = r.choice(operators)
            row = dict(
                request_id=rid,
                created_at=clock.iso(created),
                household_id=hh["household_id"],
                area_id=hh["area_id"],
                notes=notes,
                language=lang,
                category=None if status == "new" else cat,
                suggested_category=None,
                suggested_confidence=None,
                status=status,
                priority=r.choices(["low", "normal", "high", "urgent"], [10, 60, 22, 8])[0],
                assigned_to=None,
                assigned_at=None,
                callback_due_at=None,
                callback_done_at=None,
                closed_at=None,
                closure_outcome=None,
                created_by=op,
            )
            events.append(dict(request_id=rid, at=row["created_at"], staff_id=op, event_type="created", detail="Logged from call"))
            if status != "new":
                cw = r.choice(caseworkers)
                assigned = min(created + timedelta(hours=r.uniform(0.5, 20)), self.A)
                row.update(assigned_to=cw, assigned_at=clock.iso(assigned))
                events.append(dict(request_id=rid, at=row["created_at"], staff_id=op, event_type="category_confirmed", detail=cat))
                events.append(dict(request_id=rid, at=row["assigned_at"], staff_id="CRD-01", event_type="assigned", detail=cw))
                if status in ("callback_scheduled", "in_progress", "closed"):
                    due = assigned + timedelta(hours=r.uniform(4, sla))
                    row["callback_due_at"] = clock.iso(due)
                    events.append(
                        dict(
                            request_id=rid,
                            at=row["assigned_at"],
                            staff_id=cw,
                            event_type="callback_scheduled",
                            detail=f"Callback due {row['callback_due_at']}",
                        )
                    )
                    if status in ("in_progress", "closed"):
                        done = min(due - timedelta(hours=r.uniform(-6, 3)), self.A)
                        done = max(done, assigned)
                        row["callback_done_at"] = clock.iso(done)
                        events.append(
                            dict(
                                request_id=rid,
                                at=row["callback_done_at"],
                                staff_id=cw,
                                event_type="callback_done",
                                detail="Spoke with the family",
                            )
                        )
                        if status == "closed":
                            closed = min(done + timedelta(days=r.uniform(0.5, 6)), self.A)
                            outcome = r.choices(["assisted", "referred", "not_eligible", "unreachable", "duplicate"], [55, 25, 8, 8, 4])[0]
                            row.update(closed_at=clock.iso(closed), closure_outcome=outcome)
                            events.append(dict(request_id=rid, at=row["closed_at"], staff_id=cw, event_type="closed", detail=outcome))
            requests.append(row)
            calls.append(
                dict(
                    received_at=clock.iso(created - timedelta(minutes=r.randint(2, 15))),
                    operator_id=op,
                    household_id=hh["household_id"],
                    area_id=hh["area_id"],
                    call_type="service_request",
                    language=lang,
                    notes=notes,
                    duration_sec=r.randint(120, 600),
                    complaint_id=None,
                    request_id=rid,
                    is_repeat=0,
                )
            )
            if r.random() < 0.2 and age_days > 1:
                follow = self._work_time(r, age_days - 0.2, 0)
                if follow > created:
                    calls.append(
                        dict(
                            received_at=clock.iso(follow),
                            operator_id=r.choice(operators),
                            household_id=hh["household_id"],
                            area_id=hh["area_id"],
                            call_type="follow_up",
                            language=lang,
                            notes=r.choice(sc.FOLLOW_UP_NOTES[lang]),
                            duration_sec=r.randint(60, 240),
                            complaint_id=None,
                            request_id=rid,
                            is_repeat=1,
                        )
                    )
        self.ins("service_requests", requests)
        self.ins("request_events", events)
        self.requests = requests

        # Complaints with repeat calls
        topics = list(sc.COMPLAINT_TOPICS.keys())
        topic_w = [30, 25, 15, 8, 10, 12]
        drafts = sorted(self._work_time(r, 30) for _ in range(28))
        complaints, followups = [], []
        for n, opened in enumerate(drafts, start=1):
            cid = f"CMP-{n:04d}"
            hh = r.choice(self.active_hh)
            lang = "ar" if r.random() < 0.6 else "en"
            topic = r.choices(topics, topic_w)[0]
            desc = r.choice(sc.COMPLAINT_TOPICS[topic][lang])
            age_days = (self.A - opened).total_seconds() / 86400
            roll = r.random()
            status = "closed" if age_days > 7 and roll < 0.7 else "in_progress" if roll < 0.85 and age_days > 1 else "open"
            op = r.choice(operators)
            owner = None if status == "open" and r.random() < 0.5 else r.choice(caseworkers + self.staff_by_role["coordinator"])
            row = dict(
                complaint_id=cid,
                opened_at=clock.iso(opened),
                household_id=hh["household_id"],
                area_id=hh["area_id"],
                topic=topic,
                description=desc,
                status=status,
                owner_id=owner,
                closed_at=None,
                resolution=None,
                created_by=op,
            )
            if owner:
                followups.append(
                    dict(
                        complaint_id=cid,
                        at=clock.iso(min(opened + timedelta(hours=3), self.A)),
                        staff_id="CRD-01",
                        action="owner_assigned",
                        note=f"Assigned to {owner}",
                    )
                )
            if status in ("in_progress", "closed") and owner:
                followups.append(
                    dict(
                        complaint_id=cid,
                        at=clock.iso(min(opened + timedelta(days=1), self.A)),
                        staff_id=owner,
                        action="contacted_family",
                        note="Called the family to understand the issue.",
                    )
                )
            if status == "closed" and owner:
                closed = min(opened + timedelta(days=r.uniform(2, 7)), self.A)
                row.update(closed_at=clock.iso(closed), resolution="Explained the decision and fixed the issue at the site.")
                followups.append(
                    dict(
                        complaint_id=cid,
                        at=row["closed_at"],
                        staff_id=owner,
                        action="closed",
                        note="Family informed of the outcome; complaint closed.",
                    )
                )
            complaints.append(row)
            calls.append(
                dict(
                    received_at=clock.iso(opened),
                    operator_id=op,
                    household_id=hh["household_id"],
                    area_id=hh["area_id"],
                    call_type="complaint",
                    language=lang,
                    notes=desc,
                    duration_sec=r.randint(180, 720),
                    complaint_id=cid,
                    request_id=None,
                    is_repeat=0,
                )
            )
            for _ in range(r.choices([0, 1, 2, 3], [45, 30, 15, 10])[0]):
                if age_days < 0.5:
                    break
                when = self._work_time(r, age_days - 0.1, 0)
                if when <= opened:
                    continue
                calls.append(
                    dict(
                        received_at=clock.iso(when),
                        operator_id=r.choice(operators),
                        household_id=hh["household_id"],
                        area_id=hh["area_id"],
                        call_type="complaint",
                        language=lang,
                        notes=r.choice(sc.REPEAT_CALL_NOTES[lang]),
                        duration_sec=r.randint(60, 300),
                        complaint_id=cid,
                        request_id=None,
                        is_repeat=1,
                    )
                )
        self.ins("complaints", complaints)
        self.ins("complaint_followups", followups)

        # Information calls
        for _ in range(90):
            when = self._work_time(r, 30)
            lang = "ar" if r.random() < 0.6 else "en"
            area = r.choice(sc.AREAS)
            hh = r.choice(self.active_hh) if r.random() < 0.5 else None
            calls.append(
                dict(
                    received_at=clock.iso(when),
                    operator_id=r.choice(operators),
                    household_id=hh["household_id"] if hh else None,
                    area_id=area[0],
                    call_type="information",
                    language=lang,
                    notes=r.choice(sc.INFO_CALL_NOTES[lang]).format(area=area[1] if lang == "en" else area[2]),
                    duration_sec=r.randint(45, 300),
                    complaint_id=None,
                    request_id=None,
                    is_repeat=0,
                )
            )
        calls.sort(key=lambda c: (c["received_at"], c["call_type"]))
        for n, call in enumerate(calls, start=1):
            call["call_id"] = f"CALL-{n:06d}"
        self.ins("calls", [{"call_id": c["call_id"], **{k: v for k, v in c.items() if k != "call_id"}} for c in calls])
        repeat = max(complaints, key=lambda c: sum(1 for k in calls if k["complaint_id"] == c["complaint_id"]))
        self.scenarios["p02"] = {
            "existing_open_complaint_id": next(c["complaint_id"] for c in reversed(complaints) if c["status"] != "closed"),
            "complaint_with_most_calls": repeat["complaint_id"],
        }
        self.scenarios["p01"] = {
            "unassigned_request_ids": [q["request_id"] for q in requests if q["status"] == "new"][:5],
        }

    # -- referrals ---------------------------------------------------------
    def referrals(self) -> None:
        r = self.rng("referrals")
        prov_rows, svc_rows = [], []
        services_by_cat: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for p in sc.PROVIDERS:
            ax, ay = self.area[p[3]][4], self.area[p[3]][5]
            prov_rows.append(
                dict(
                    provider_id=p[0],
                    name=p[1],
                    name_ar=p[2],
                    area_id=p[3],
                    x_km=round(ax + p[4], 2),
                    y_km=round(ay + p[5], 2),
                    open_days=p[6],
                    languages=p[7],
                )
            )
            for s in p[8]:
                svc_rows.append(
                    dict(
                        provider_id=p[0],
                        service_category=s[0],
                        service_name=s[1],
                        keywords=s[2],
                        weekly_capacity=s[3],
                        current_load=s[4],
                        eligibility=s[5],
                        accepts_new=s[6],
                    )
                )
                services_by_cat[s[0]].append((p[0], s[1]))
        self.ins("service_providers", prov_rows)
        self.ins("provider_services", svc_rows)
        candidates = [q for q in self.requests if q["status"] in ("in_progress", "closed") and q["category"]]
        statuses = (
            ["proposed"] * 4
            + ["approved"] * 6
            + ["accepted"] * 5
            + ["declined"] * 3
            + ["support_received"] * 9
            + ["support_not_received"] * 3
        )
        rows = []
        chosen = r.sample(candidates, len(statuses))
        chosen.sort(key=lambda q: q["created_at"])
        for n, (req, status) in enumerate(zip(chosen, statuses, strict=True), start=1):
            prov, svc = r.choice(services_by_cat[req["category"]])
            created = clock.parse(req["assigned_at"] or req["created_at"]) + timedelta(hours=2)
            created = min(created, self.A)
            cw = req["assigned_to"] or "CW-01"
            row = dict(
                referral_id=f"REF-{n:04d}",
                created_at=clock.iso(created),
                household_id=req["household_id"],
                request_id=req["request_id"],
                need_text=req["notes"],
                service_category=req["category"],
                provider_id=prov,
                service_name=svc,
                match_score=round(r.uniform(0.55, 0.95), 2),
                match_explanation=f"Category match ({req['category']}); provider has capacity; keywords found in the stated need.",
                status=status,
                caseworker_id=cw,
                approved_by=None,
                approved_at=None,
                provider_response=None,
                provider_response_note=None,
                provider_response_at=None,
                family_received=None,
                outcome_note=None,
                outcome_confirmed_at=None,
                outcome_confirmed_by=None,
            )
            if status != "proposed":
                appr = min(created + timedelta(hours=1), self.A)
                row.update(approved_by=cw, approved_at=clock.iso(appr))
                self.audit(row["approved_at"], cw, "approve", "referrals", row["referral_id"], after={"status": "approved"})
            if status in ("accepted", "declined", "support_received", "support_not_received"):
                resp = "declined" if status == "declined" else "accepted"
                row.update(
                    provider_response=resp,
                    provider_response_at=clock.iso(min(created + timedelta(days=1), self.A)),
                    provider_response_note="Capacity full this week." if resp == "declined" else "Appointment offered.",
                )
            if status in ("support_received", "support_not_received"):
                row.update(
                    family_received=1 if status == "support_received" else 0,
                    outcome_note="Family confirmed by phone."
                    if status == "support_received"
                    else "Family could not travel to the provider.",
                    outcome_confirmed_at=clock.iso(min(created + timedelta(days=4), self.A)),
                    outcome_confirmed_by=cw,
                )
            rows.append(row)
        self.ins("referrals", rows)

    # -- intake, drafts, offline sync --------------------------------------
    def intake_and_sync(self) -> None:
        r = self.rng("intake")
        records, versions = [], []
        needs = list(call_notes.CATEGORIES)
        for n in range(1, 37):
            rid = f"INT-{n:04d}"
            source = "data_entry" if n <= 12 else ("field_device" if n <= 32 else "server")
            hh = r.choice(self.active_hh)
            first, family = self.hh_names[hh["household_id"]]
            lang = "ar" if r.random() < 0.5 else "en"
            created = self._work_time(r, 25, 1)
            creator = r.choice(self.staff_by_role["operator"]) if source != "field_device" else r.choice(self.staff_by_role["field_worker"])
            need = r.choice(needs)
            area = self.area[hh["area_id"]]
            raw = None
            if source == "data_entry":
                raw = call_notes.make_note(r, need, lang, area[1], area[2])
                raw = (f"{first[1]} {family[1]} - " if lang == "ar" else f"{first[0]} {family[0]} - ") + raw
            data = dict(
                record_id=rid,
                source=source,
                household_id=hh["household_id"] if r.random() < 0.7 else None,
                head_name=hh["head_name"],
                area_id=hh["area_id"],
                household_size=hh["household_size"],
                primary_need=need,
                urgency=r.choice(["low", "normal", "high", "urgent"]),
                notes=f"Visit note: {call_notes.CATEGORY_LABELS[need].lower()} needed.",
                raw_note=raw,
                language=lang,
                version=1,
                created_at=clock.iso(created),
                created_by=creator,
                updated_at=clock.iso(created),
                updated_by=creator,
            )
            n_versions = 1 + (n % 3 if source != "data_entry" else 0)
            if rid == "INT-0021":
                n_versions = 2
            for v in range(1, n_versions + 1):
                when = created + timedelta(hours=6 * (v - 1))
                if v > 1:
                    data["household_size"] = data["household_size"] + (1 if v == 2 else 0)
                    data["urgency"] = "high" if v == 2 else data["urgency"]
                data["version"] = v
                data["updated_at"] = clock.iso(when)
                snapshot = {k: data[k] for k in ("head_name", "area_id", "household_size", "primary_need", "urgency", "notes")}
                versions.append(
                    dict(
                        record_id=rid,
                        version=v,
                        data_json=json.dumps(snapshot, ensure_ascii=False),
                        origin="data_entry" if source == "data_entry" else ("device" if v == 1 and source == "field_device" else "server"),
                        device_id="DEV-01" if source == "field_device" else None,
                        saved_at=clock.iso(when),
                        saved_by=creator,
                    )
                )
            records.append(dict(data))
        self.ins("intake_records", records)
        self.ins("record_versions", versions)

        drafts = []
        for n, rec in enumerate(records[:6], start=1):
            status = "saved" if n <= 4 else ("draft" if n == 5 else "discarded")
            extracted = {
                "head_name": rec["head_name"],
                "area_id": rec["area_id"],
                "household_size": rec["household_size"],
                "primary_need": rec["primary_need"],
            }
            flags = {"missing": ["household_size"] if n == 5 else [], "uncertain": ["area_id"] if n in (2, 5) else []}
            drafts.append(
                dict(
                    draft_id=f"DRF-{n:04d}",
                    created_at=rec["created_at"],
                    created_by=rec["created_by"],
                    raw_note=rec["raw_note"] or rec["notes"],
                    language=rec["language"],
                    extracted_json=json.dumps(extracted, ensure_ascii=False),
                    flags_json=json.dumps(flags),
                    status=status,
                    saved_record_id=rec["record_id"] if status == "saved" else None,
                )
            )
        self.ins("intake_drafts", drafts)

        self.ins(
            "sync_devices",
            [
                dict(device_id="DEV-01", assigned_to="FW-01", online=1, last_sync_at=self.t(minutes=-25)),
                dict(device_id="DEV-02", assigned_to="FW-02", online=1, last_sync_at=self.t(hours=-3)),
                dict(device_id="DEV-03", assigned_to="FW-03", online=0, last_sync_at=self.t(hours=-26)),
            ],
        )
        conflict_rec = next(x for x in records if x["record_id"] == "INT-0015")
        server_v = [v for v in versions if v["record_id"] == "INT-0015"]
        self.ins(
            "sync_conflicts",
            [
                dict(
                    conflict_id="CNF-0001",
                    record_id="INT-0015",
                    device_id="DEV-02",
                    base_version=1,
                    server_version=len(server_v),
                    device_data_json=json.dumps({"household_size": conflict_rec["household_size"] + 2, "urgency": "urgent"}),
                    server_data_json=server_v[-1]["data_json"],
                    detected_at=self.t(days=-3),
                    status="resolved",
                    resolution="keep_server",
                    resolved_data_json=server_v[-1]["data_json"],
                    resolved_by="CW-02",
                    resolved_at=self.t(days=-3, hours=2),
                    reason="Server copy was updated after a home visit; device edit was older.",
                )
            ],
        )
        self.audit(
            self.t(days=-3, hours=2),
            "CW-02",
            "resolve",
            "sync_conflicts",
            "CNF-0001",
            after={"resolution": "keep_server"},
            reason="Server copy was updated after a home visit; device edit was older.",
        )
        queue = []
        for rec in records:
            if rec["source"] == "field_device":
                queue.append(
                    dict(
                        device_id="DEV-01",
                        record_id=rec["record_id"],
                        operation="create",
                        payload_json=json.dumps({"head_name": rec["head_name"], "area_id": rec["area_id"]}, ensure_ascii=False),
                        base_version=None,
                        queued_at=rec["created_at"],
                        status="synced",
                        synced_at=clock.iso(clock.parse(rec["created_at"]) + timedelta(minutes=40)),
                        conflict_id=None,
                    )
                )
        queue.append(
            dict(
                device_id="DEV-02",
                record_id="INT-0015",
                operation="update",
                payload_json=json.dumps({"household_size": conflict_rec["household_size"] + 2, "urgency": "urgent"}),
                base_version=1,
                queued_at=self.t(days=-3, hours=-5),
                status="resolved",
                synced_at=self.t(days=-3, hours=2),
                conflict_id="CNF-0001",
            )
        )
        queue.append(
            dict(
                device_id="DEV-03",
                record_id="INT-DEV03-0001",  # device-scoped, so it cannot collide with the server's next INT- number
                operation="create",
                payload_json=json.dumps(
                    {"head_name": "Salma Rizk", "area_id": "AR-06", "household_size": 4, "primary_need": "shelter", "urgency": "high"},
                    ensure_ascii=False,
                ),
                base_version=None,
                queued_at=self.t(hours=-20),
                status="pending",
                synced_at=None,
                conflict_id=None,
            )
        )
        queue.append(
            dict(
                device_id="DEV-03",
                record_id="INT-0030",
                operation="update",
                payload_json=json.dumps({"urgency": "urgent", "notes": "Roof collapsed in the storm."}),
                base_version=next(x["version"] for x in records if x["record_id"] == "INT-0030"),
                queued_at=self.t(hours=-19),
                status="pending",
                synced_at=None,
                conflict_id=None,
            )
        )
        self.ins("sync_queue", queue)
        rec21 = next(x for x in records if x["record_id"] == "INT-0021")
        self.scenarios["p08"] = {
            "record_id": "INT-0021",
            "device_id": "DEV-01",
            "server_version": rec21["version"],
            "offline_device_with_pending_queue": "DEV-03",
        }

    # -- field data pipeline -----------------------------------------------
    def pipeline(self) -> None:
        r = self.rng("pipeline")
        sources = [
            ("TEAM-NORTH", "North District field team weekly export", "team_north_*.csv"),
            ("TEAM-COAST", "Coastal District field team weekly export", "team_coast_*.csv"),
            ("TEAM-CENTRAL", "Central District field team weekly export", "team_central_*.csv"),
        ]
        self.ins("pipeline_sources", [dict(source_id=s[0], description=s[1], file_pattern=s[2]) for s in sources])
        team_areas = {
            "TEAM-NORTH": ["AR-05", "AR-06", "AR-10"],
            "TEAM-COAST": ["AR-04", "AR-07", "AR-12"],
            "TEAM-CENTRAL": ["AR-08", "AR-09", "AR-11"],
        }
        rules = [
            ("missing_household_id", "household_id is empty"),
            ("unknown_area", "area code is not in the area list"),
            ("bad_date", "assessment_date is not a valid date"),
            ("household_size_out_of_range", "household_size must be 1-20"),
            ("unknown_need_category", "need_category is not one of the six categories"),
        ]
        plan = [
            (-13, "TEAM-NORTH", "success"),
            (-12, "TEAM-COAST", "partial"),
            (-10, "TEAM-CENTRAL", "success"),
            (-6, "TEAM-NORTH", "partial"),
            (-5, "TEAM-COAST", "failed"),
            (-4, "TEAM-NORTH", "duplicate"),
            (-2, "TEAM-CENTRAL", "partial"),
            (-1, "TEAM-COAST", "success"),
        ]
        runs, quarantine, assessments = [], [], []
        hashes: dict[str, str] = {}
        for n, (day, src, outcome) in enumerate(plan, start=1):
            run_id = f"RUN-{n:04d}"
            started = self.at(self.D0 + timedelta(days=day), 6, 0)
            fname = f"{src.lower().replace('-', '_')}_{(self.D0 + timedelta(days=day)).isoformat()}.csv"
            if outcome == "duplicate":
                prev = runs[3]
                fname, sha = prev["file_name"], prev["file_sha256"]
            else:
                sha = hashlib.sha256(f"{self.seed}:{fname}".encode()).hexdigest()
            hashes[fname] = sha
            row = dict(
                run_id=run_id,
                started_at=started,
                finished_at=clock.iso(clock.parse(started) + timedelta(seconds=r.randint(4, 30))),
                trigger="manual" if n in (4, 6) else "scheduled",
                source_id=src,
                file_name=fname,
                file_sha256=sha,
                status="success",
                rows_read=0,
                rows_loaded=0,
                rows_duplicate=0,
                rows_quarantined=0,
                error_message=None,
            )
            if outcome == "failed":
                row.update(status="failed", error_message="Missing required column 'household_id'; file rejected before loading.")
            elif outcome == "duplicate":
                prev = runs[3]
                row.update(
                    status="success",
                    rows_read=prev["rows_loaded"] + prev["rows_quarantined"],
                    rows_duplicate=prev["rows_loaded"],
                    rows_quarantined=prev["rows_quarantined"],
                )
                for q in [x for x in quarantine if x["run_id"] == prev["run_id"]]:
                    quarantine.append(dict(q, run_id=run_id))
            else:
                n_rows = r.randint(14, 22)
                bad = 0 if outcome == "success" else r.randint(2, 4)
                pool = [h for h in self.active_hh if h["area_id"] in team_areas[src]]
                for i in range(n_rows):
                    hh = r.choice(pool)
                    adate = (self.D0 + timedelta(days=day - r.randint(1, 5))).isoformat()
                    need = r.choice(call_notes.CATEGORIES)
                    raw = {
                        "household_id": hh["household_id"],
                        "area": hh["area_id"],
                        "assessment_date": adate,
                        "household_size": hh["household_size"],
                        "need_category": need,
                        "priority": r.choice(["low", "medium", "high"]),
                    }
                    if i < bad:
                        rule, reason = rules[(n + i) % len(rules)]
                        broken = dict(raw)
                        if rule == "missing_household_id":
                            broken["household_id"] = ""
                        elif rule == "unknown_area":
                            broken["area"] = "AR-99"
                        elif rule == "bad_date":
                            broken["assessment_date"] = "31/02/2026"
                        elif rule == "household_size_out_of_range":
                            broken["household_size"] = 0
                        else:
                            broken["need_category"] = "transport"
                        quarantine.append(
                            dict(run_id=run_id, source_id=src, row_number=i + 2, raw_row_json=json.dumps(broken), rule=rule, reason=reason)
                        )
                        continue
                    key = hashlib.sha256(f"{src}|{hh['household_id']}|{adate}|{need}".encode()).hexdigest()
                    if any(a["row_key"] == key for a in assessments):
                        row["rows_duplicate"] += 1
                        continue
                    assessments.append(
                        dict(
                            row_key=key,
                            source_id=src,
                            household_id=hh["household_id"],
                            area_id=hh["area_id"],
                            assessment_date=adate,
                            household_size=hh["household_size"],
                            need_category=need,
                            priority=raw["priority"],
                            loaded_run_id=run_id,
                            loaded_at=row["finished_at"],
                        )
                    )
                row["rows_read"] = n_rows
                row["rows_quarantined"] = bad
                row["rows_loaded"] = n_rows - bad - row["rows_duplicate"]
                if bad:
                    row["status"] = "partial"
            runs.append(row)
        self.ins("pipeline_runs", runs)
        self.ins("pipeline_quarantine", quarantine)
        self.ins("field_assessments", assessments)
        self.pipeline_runs = runs

    # -- volunteers --------------------------------------------------------
    def volunteers(self) -> None:
        r = self.rng("volunteers")
        trainings = ["driving", "packing", "registration", "distribution", "first_aid", "safeguarding"]
        vols, trs, avail = [], [], []
        self.drivers_vol: list[str] = []
        for n in range(1, 49):
            vid = f"VOL-{n:03d}"
            vols.append(
                dict(
                    volunteer_id=vid,
                    home_area_id=r.choice(sc.AREAS)[0],
                    languages=r.choice(["ar", "ar,en", "ar,en", "ar,fr", "ar,en,fr"]),
                    max_shifts_per_week=r.choice([2, 3, 3, 4]),
                    active=1,
                )
            )
            mine = r.sample(trainings, r.randint(1, 3))
            if n <= 10 and "driving" not in mine:
                mine.append("driving")
            if "driving" in mine:
                self.drivers_vol.append(vid)
            for t in mine:
                trs.append(dict(volunteer_id=vid, training=t, completed_on=self.d(-r.randint(20, 400))))
            for day in range(0, 14):
                if r.random() < 0.5:
                    start, end = r.choice([("08:00", "14:00"), ("12:00", "18:00"), ("08:00", "18:00")])
                    avail.append(dict(volunteer_id=vid, avail_date=self.d(day), start_time=start, end_time=end))
        # VOL-007 is free all day on day +3 so the planted overlap below is plausible.
        avail = [a for a in avail if not (a["volunteer_id"] == "VOL-007" and a["avail_date"] == self.d(3))]
        avail.append(dict(volunteer_id="VOL-007", avail_date=self.d(3), start_time="08:00", end_time="18:00"))
        if not any(t["volunteer_id"] == "VOL-007" and t["training"] == "packing" for t in trs):
            trs.append(dict(volunteer_id="VOL-007", training="packing", completed_on=self.d(-100)))
        self.ins("volunteers", vols)
        self.ins("volunteer_trainings", trs)
        self.ins("volunteer_availability", avail)
        shifts, roles, assigns = [], [], []
        plan = [
            (0, "SITE-01", "08:00", "14:00", "Monthly food distribution"),
            (1, "SITE-04", "09:00", "13:00", "Packing hygiene kits"),
            (3, "SITE-06", "08:00", "14:00", "Monthly food distribution"),
            (3, "SITE-04", "12:00", "16:00", "Packing food parcels"),
            (5, "SITE-10", "09:00", "15:00", "Blanket distribution"),
            (6, "SITE-09", "08:00", "14:00", "Monthly food distribution"),
        ]
        role_req = {
            "driver": (1, "driving"),
            "packing": (3, "packing"),
            "registration": (2, "registration"),
            "distribution": (4, "distribution"),
        }
        by_training: dict[str, list[str]] = defaultdict(list)
        for t in trs:
            by_training[t["training"]].append(t["volunteer_id"])
        for n, (day, site, start, end, purpose) in enumerate(plan, start=1):
            sid = f"SHF-{n:04d}"
            shifts.append(dict(shift_id=sid, site_id=site, shift_date=self.d(day), start_time=start, end_time=end, purpose=purpose))
            needed = ["packing"] if purpose.startswith("Packing") else ["driver", "registration", "distribution"]
            for role in needed:
                count, training = role_req[role]
                roles.append(dict(shift_id=sid, role=role, required_count=count, required_training=training))
                pool = sorted(by_training[training])
                fill = count if day == 0 else max(0, count - (1 if n % 2 else 2))
                picked = [v for v in pool if v != "VOL-007"][:fill] if day != 3 else [v for v in pool if v != "VOL-007"][1 : 1 + fill]
                for v in picked:
                    assigns.append(
                        dict(
                            shift_id=sid,
                            role=role,
                            volunteer_id=v,
                            status="confirmed" if day <= 1 else "suggested",
                            assigned_by="CRD-02",
                            assigned_at=self.t(days=-1),
                        )
                    )
        # Planted overlap: VOL-007 confirmed on two overlapping shifts on day +3.
        assigns.append(
            dict(
                shift_id="SHF-0003",
                role="distribution",
                volunteer_id="VOL-007",
                status="confirmed",
                assigned_by="CRD-02",
                assigned_at=self.t(days=-1),
            )
        )
        assigns.append(
            dict(
                shift_id="SHF-0004",
                role="packing",
                volunteer_id="VOL-007",
                status="confirmed",
                assigned_by="CRD-02",
                assigned_at=self.t(days=-1),
            )
        )
        if not any(t["volunteer_id"] == "VOL-007" and t["training"] == "distribution" for t in trs):
            self.ins("volunteer_trainings", [dict(volunteer_id="VOL-007", training="distribution", completed_on=self.d(-90))])
        self.ins("shifts", shifts)
        self.ins("shift_roles", roles)
        self.ins("shift_assignments", assigns)
        self.scenarios["p13"] = {"overlap_volunteer_id": "VOL-007", "overlap_date": self.d(3)}

    # -- fleet -------------------------------------------------------------
    def fleet(self) -> None:
        def v(
            vid: str,
            vtype: str,
            cap: float,
            odo: int,
            svc_days_ago: int,
            svc_odo: int,
            int_km: int,
            int_days: int,
            reg: int,
            ins: int,
            permit: int,
            wh: str,
            simulated: int = 1,
        ) -> dict[str, Any]:
            return dict(
                vehicle_id=vid,
                vehicle_type=vtype,
                load_capacity_kg=cap,
                odometer_km=odo,
                last_service_date=self.d(-svc_days_ago),
                last_service_odometer=svc_odo,
                service_interval_km=int_km,
                service_interval_days=int_days,
                registration_expiry=self.d(reg),
                insurance_expiry=self.d(ins),
                permit_expiry=self.d(permit),
                home_warehouse_id=wh,
                simulated=simulated,
            )

        vehicles = [
            v("VEH-01", "truck", 8000, 84210, 40, 80100, 10000, 180, 200, 150, 90, "WH-01"),
            v("VEH-02", "van", 1500, 51230, 60, 47000, 10000, 180, 300, 120, 60, "WH-01"),
            v("VEH-03", "truck", 10000, 120400, 30, 118000, 10000, 180, 250, 200, 120, "WH-02"),
            v("VEH-04", "truck", 8000, 97650, 200, 90100, 10000, 180, 180, 160, 100, "WH-03"),  # service overdue
            v("VEH-05", "van", 1200, 63900, 170, 60000, 10000, 180, 220, 90, 75, "WH-02"),  # due for service soon
            v("VEH-06", "pickup", 900, 45020, 50, 42000, 10000, 180, 260, 140, 80, "WH-03"),  # grounding defect
            v("VEH-07", "4x4", 700, 70110, 80, 66000, 10000, 180, 190, -3, 50, "WH-01", 0),  # insurance expired, tracker off
            v("VEH-08", "truck", 6000, 30500, 20, 29800, 10000, 180, 330, 210, 140, "WH-03"),
        ]
        self.ins("vehicles", vehicles)
        self.vehicles = {x["vehicle_id"]: x for x in vehicles}
        self.ins(
            "vehicle_defects",
            [
                dict(
                    defect_id="DEF-001",
                    vehicle_id="VEH-06",
                    reported_at=self.t(days=-2),
                    reported_by="FW-02",
                    description="Brake pedal goes to the floor; brakes unsafe.",
                    severity="grounding",
                    resolved_at=None,
                ),
                dict(
                    defect_id="DEF-002",
                    vehicle_id="VEH-08",
                    reported_at=self.t(days=-5),
                    reported_by="FW-01",
                    description="Rear cargo door latch is stiff.",
                    severity="minor",
                    resolved_at=None,
                ),
                dict(
                    defect_id="DEF-003",
                    vehicle_id="VEH-01",
                    reported_at=self.t(days=-20),
                    reported_by="FW-03",
                    description="Front tyre worn.",
                    severity="major",
                    resolved_at=self.t(days=-18),
                ),
                dict(
                    defect_id="DEF-004",
                    vehicle_id="VEH-05",
                    reported_at=self.t(days=-1),
                    reported_by="FW-02",
                    description="Air conditioning not working.",
                    severity="minor",
                    resolved_at=None,
                ),
            ],
        )
        drivers = []
        for n in range(1, 9):
            drivers.append(
                dict(
                    driver_id=f"DRV-{n:02d}",
                    volunteer_id=self.drivers_vol[n - 1] if n - 1 < len(self.drivers_vol) else None,
                    licence_class="C" if n <= 5 else "B",
                    licence_expiry=self.d(-10) if n == 6 else self.d(100 + n * 40),
                    home_area_id=sc.AREAS[(n * 3) % 12][0],
                    active=1,
                )
            )
        self.ins("drivers", drivers)
        self.ins(
            "convoys",
            [
                dict(
                    convoy_id="CNV-001",
                    name="North relief run",
                    planned_date=self.d(-6),
                    origin_warehouse_id="WH-02",
                    status="returned",
                    coordinator_id="CRD-01",
                    manual_order_json=json.dumps(["SITE-07", "SITE-05"]),
                    suggested_order_json=json.dumps(["SITE-05", "SITE-07"]),
                    manual_distance_km=None,
                    suggested_distance_km=None,
                    approved_by="CRD-01",
                    approved_at=self.t(days=-7),
                    departed_at=self.at(self.D0 - timedelta(days=6), 7, 0),
                    arrived_at=self.at(self.D0 - timedelta(days=6), 10, 30),
                    returned_at=self.at(self.D0 - timedelta(days=6), 16, 0),
                    notes="Hygiene, water and medical supplies.",
                ),
                dict(
                    convoy_id="CNV-002",
                    name="Riverbend distribution day",
                    planned_date=self.d(0),
                    origin_warehouse_id="WH-01",
                    status="departed",
                    coordinator_id="CRD-01",
                    manual_order_json=json.dumps(["SITE-01", "SITE-10"]),
                    suggested_order_json=json.dumps(["SITE-01", "SITE-10"]),
                    manual_distance_km=None,
                    suggested_distance_km=None,
                    approved_by="CRD-01",
                    approved_at=self.t(days=-1),
                    departed_at=self.t(minutes=-100),
                    arrived_at=None,
                    returned_at=None,
                    notes="Today's food round and blankets.",
                ),
                dict(
                    convoy_id="CNV-003",
                    name="Coastal resupply",
                    planned_date=self.d(1),
                    origin_warehouse_id="WH-03",
                    status="planned",
                    coordinator_id="CRD-02",
                    manual_order_json=json.dumps(["SITE-09", "SITE-02", "SITE-08"]),
                    suggested_order_json=None,
                    manual_distance_km=None,
                    suggested_distance_km=None,
                    approved_by=None,
                    approved_at=None,
                    departed_at=None,
                    arrived_at=None,
                    returned_at=None,
                    notes="Stop at SITE-09 is in an area under a restricted advisory.",
                ),
                dict(
                    convoy_id="CNV-004",
                    name="Highland winter kits",
                    planned_date=self.d(2),
                    origin_warehouse_id="WH-03",
                    status="draft",
                    coordinator_id="CRD-02",
                    manual_order_json=None,
                    suggested_order_json=None,
                    manual_distance_km=None,
                    suggested_distance_km=None,
                    approved_by=None,
                    approved_at=None,
                    departed_at=None,
                    arrived_at=None,
                    returned_at=None,
                    notes="Draft: no vehicles assigned yet.",
                ),
            ],
        )
        self.scenarios["p16"] = {
            "draft_convoy_id": "CNV-004",
            "overdue_vehicle_id": "VEH-04",
            "grounded_vehicle_id": "VEH-06",
            "insurance_expired_vehicle_id": "VEH-07",
            "expired_licence_driver_id": "DRV-06",
            "convoy_with_blocked_stop": "CNV-003",
            "blocked_site_id": "SITE-09",
            "blocked_area_id": "AR-07",
        }

    # -- supply chain: plans, dispatches, distributions, returns, receipts --
    def supply_chain(self) -> None:
        r = self.rng("supply")
        offsets = {"SITE-01": 0, "SITE-02": -1, "SITE-03": -9, "SITE-05": -15, "SITE-06": 3, "SITE-08": -4, "SITE-09": 6}
        rounds: list[tuple[date, str]] = []
        for site, off in offsets.items():
            for k in range(-3, 2):
                rd = self.D0 + timedelta(days=off + 28 * k)
                if self.D0 - timedelta(days=90) <= rd <= self.D0 + timedelta(days=28):
                    rounds.append((rd, site))
        rounds.sort()
        last_end: dict[str, date] = {}
        for h in self.active_hh:
            last_end[h["household_id"]] = self.D0 - timedelta(days=90 - r.randint(0, 45))
        by_area: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for h in self.active_hh:
            by_area[h["area_id"]].append(h)

        def parcel_for(size: int) -> tuple[str, int]:
            return food.parcel_plan(size, self.assumptions)

        def parcel_days(size: int, item: str, q: int) -> int:
            return food.coverage(size, item, q, 0, self.assumptions).parcel_days

        plans, dispatches, movements, dists, returns = [], [], [], [], []
        plan_n = disp_n = dist_n = ret_n = 0
        p12_period = (self.D0 - timedelta(days=12), self.D0 - timedelta(days=6))
        p12_round = self.D0 - timedelta(days=9)
        self.dispatch_log: list[tuple[str, str, date, int]] = []  # (warehouse, item, day, qty)

        def add_dispatch(
            wh: str, site: str, item: str, qty: int, when: str, plan_id: str | None, convoy: str | None, staff: str = "WHO-01"
        ) -> str:
            nonlocal disp_n
            disp_n += 1
            did = f"DSP-{disp_n:04d}"
            dispatches.append(
                dict(
                    dispatch_id=did,
                    plan_id=plan_id,
                    convoy_id=convoy,
                    warehouse_id=wh,
                    site_id=site,
                    item_id=item,
                    quantity=qty,
                    dispatched_at=when,
                    dispatched_by=staff,
                )
            )
            movements.append(
                dict(
                    warehouse_id=wh,
                    item_id=item,
                    moved_at=when,
                    movement_type="dispatched",
                    reason="dispatch",
                    quantity=qty,
                    dispatch_id=did,
                    reference=site,
                    recorded_by=staff,
                )
            )
            self.dispatch_log.append((wh, item, clock.parse(when).date(), qty))
            return did

        for rd, site in rounds:
            wh = sc.SITE_WAREHOUSE[site]
            catch = [h for a in sc.SITE_CATCHMENT[site] for h in by_area[a]]
            catch.sort(key=lambda h: (last_end[h["household_id"]], h["household_id"]))
            is_past = rd < self.D0
            is_today = rd == self.D0
            due = [h for h in catch if last_end[h["household_id"]] <= rd + timedelta(days=2)]
            served = list(due)
            forced = site == "SITE-03" and rd == p12_round
            if forced:
                std = [h for h in catch if h["household_size"] >= 3]
                chosen, total = [], 0
                for h in std:
                    q = parcel_for(h["household_size"])[1]
                    if total + q <= 94:
                        chosen.append(h)
                        total += q
                    if total == 94:
                        break
                if total != 94:
                    raise RuntimeError("Seed could not build the 100/96/3 reconciliation scenario")
                served = chosen + [h for h in due if h["household_size"] <= 2]
            # quantities per item
            lines: dict[str, list[tuple[dict[str, Any], int, int]]] = defaultdict(list)
            for h in served:
                item, q = parcel_for(h["household_size"])
                recent = clock.parse(h["registered_at"]).date() >= rd - timedelta(days=60)
                meals = h["household_size"] * 6 if (recent or r.random() < 0.15) else 0
                lines[item].append((h, q, meals))
            plan_ids: dict[str, str] = {}
            for item in ("ITM-FP-STD", "ITM-FP-SML"):
                est = sum(q for _, q, _ in lines[item])
                if est == 0:
                    continue
                planned = 100 if (forced and item == "ITM-FP-STD") else int(math.ceil((est + 2) / 10.0) * 10)
                plan_n += 1
                pid = f"PLN-{plan_n:04d}"
                plan_ids[item] = pid
                status = "completed" if is_past else ("dispatched" if is_today else "planned")
                plans.append(
                    dict(
                        plan_id=pid,
                        site_id=site,
                        warehouse_id=wh,
                        item_id=item,
                        planned_date=rd.isoformat(),
                        planned_quantity=planned,
                        planned_households=len(lines[item]),
                        status=status,
                        created_by="CRD-01",
                    )
                )
            if not (is_past or is_today):
                continue
            when = self.t(minutes=-100) if is_today else self.at(rd - timedelta(days=1), 15, 0)
            convoy = "CNV-002" if is_today else None
            disp_ids: dict[str, str] = {}
            for item, pid in plan_ids.items():
                qty = next(p["planned_quantity"] for p in plans if p["plan_id"] == pid)
                disp_ids[item] = add_dispatch(wh, site, item, qty, when, pid, convoy)
            meals_total = sum(m for item in lines for _, _, m in lines[item])
            if meals_total:
                disp_ids["ITM-RTE"] = add_dispatch(wh, site, "ITM-RTE", meals_total, when, None, convoy)
            # distributions
            rows_here: list[dict[str, Any]] = []
            for item in ("ITM-FP-STD", "ITM-FP-SML"):
                for h, q, meals in lines[item]:
                    cov = parcel_days(h["household_size"], item, q)
                    rows_here.append(dict(household=h, item=item, q=q, meals=meals, cov=cov))
            if forced:
                dup_h = rows_here[0]["household"]
                rows_here.append(
                    dict(
                        household=dup_h,
                        item="ITM-FP-STD",
                        q=1,
                        meals=0,
                        cov=parcel_days(dup_h["household_size"], "ITM-FP-STD", 1),
                        note="duplicate",
                    )
                )
                extra = next(h for h in catch if h["household_size"] >= 3 and h not in served)
                rows_here.append(
                    dict(
                        household=extra,
                        item="ITM-FP-STD",
                        q=1,
                        meals=0,
                        cov=parcel_days(extra["household_size"], "ITM-FP-STD", 1),
                        note="unmatched",
                    )
                )
                self.scenarios["p12"] = {
                    "site_id": site,
                    "item_id": "ITM-FP-STD",
                    "period_start": p12_period[0].isoformat(),
                    "period_end": p12_period[1].isoformat(),
                    "planned": 100,
                    "dispatched": 100,
                    "distributed": 96,
                    "returned": 3,
                    "unaccounted": 1,
                    "duplicate_household_id": dup_h["household_id"],
                    "unmatched_household_id": extra["household_id"],
                }
            if is_today:
                rows_here = rows_here[: max(1, int(len(rows_here) * 0.4))]
            for i, row in enumerate(rows_here):
                dist_n += 1
                h = row["household"]
                if is_today:
                    rec_at = self.t(minutes=-(len(rows_here) - i) * 3)
                else:
                    rec_at = self.at(rd, 9, 0)
                    rec_at = clock.iso(clock.parse(rec_at) + timedelta(minutes=3 * i))
                dist_id = f"DST-{dist_n:05d}"
                dispatch_link = None if row.get("note") == "unmatched" else disp_ids.get(row["item"])
                dists.append(
                    dict(
                        distribution_id=dist_id,
                        distribution_date=rd.isoformat(),
                        site_id=site,
                        household_id=h["household_id"],
                        household_size=h["household_size"],
                        parcel_item_id=row["item"],
                        parcel_quantity=row["q"],
                        rte_meal_quantity=row["meals"],
                        expected_coverage_days=row["cov"],
                        dispatch_id=dispatch_link,
                        request_id=None,
                        recorded_by=r.choice(self.staff_by_role["field_worker"]),
                        recorded_at=rec_at,
                    )
                )
                end = food.coverage_end_date(rd, row["cov"])
                last_end[h["household_id"]] = max(last_end[h["household_id"]], end)
            if is_past:
                for item, did in disp_ids.items():
                    if item == "ITM-RTE":
                        continue
                    dispatched = next(d["quantity"] for d in dispatches if d["dispatch_id"] == did)
                    distributed = sum(x["q"] for x in rows_here if x["item"] == item)
                    if forced and item == "ITM-FP-STD":
                        returned = 3
                    else:
                        gap = dispatched - distributed
                        unaccounted = 1 if gap >= 2 and r.random() < 0.15 else 0
                        returned = gap - unaccounted
                    if returned > 0:
                        ret_n += 1
                        when_ret = self.at(rd, 17, 30)
                        returns.append(
                            dict(
                                return_id=f"RET-{ret_n:04d}",
                                dispatch_id=did,
                                site_id=site,
                                item_id=item,
                                quantity=returned,
                                returned_at=when_ret,
                                recorded_by="WHO-01",
                            )
                        )
                        movements.append(
                            dict(
                                warehouse_id=wh,
                                item_id=item,
                                moved_at=when_ret,
                                movement_type="received",
                                reason="distribution_return",
                                quantity=returned,
                                dispatch_id=None,
                                reference=f"RET-{ret_n:04d}",
                                recorded_by="WHO-01",
                            )
                        )

        # Non-food dispatches: convoys CNV-001 and CNV-002 plus routine hygiene kits.
        cnv1_when = self.at(self.D0 - timedelta(days=6), 6, 30)
        self.convoy_dispatches = {
            "CNV-001": [("SITE-05", "ITM-HYG", 60), ("SITE-05", "ITM-WTR", 40), ("SITE-07", "ITM-MED", 30), ("SITE-07", "ITM-BLK", 50)],
            "CNV-002": [("SITE-10", "ITM-BLK", 80), ("SITE-10", "ITM-MAT", 20)],
        }
        for site, item, qty in self.convoy_dispatches["CNV-001"]:
            add_dispatch("WH-02", site, item, qty, cnv1_when, None, "CNV-001")
        for site, item, qty in self.convoy_dispatches["CNV-002"]:
            add_dispatch("WH-01", site, item, qty, self.t(minutes=-100), None, "CNV-002")
        for k in range(1, 6):
            day = self.D0 - timedelta(days=14 * k + 2)
            for wh, site in (("WH-01", "SITE-04"), ("WH-02", "SITE-06"), ("WH-03", "SITE-02")):
                add_dispatch(wh, site, "ITM-HYG", r.randint(2, 5) * 10, self.at(day, 10, 0), None, None, "WHO-02")

        # Receipts: each delivery covers dispatches until the next delivery; the last adds the target on hand.
        targets = {
            "WH-01": {
                "ITM-FP-STD": 150,
                "ITM-FP-SML": 40,
                "ITM-RTE": 600,
                "ITM-HYG": 220,
                "ITM-BLK": 300,
                "ITM-WTR": 180,
                "ITM-MAT": 60,
                "ITM-MED": 80,
            },
            "WH-02": {
                "ITM-FP-STD": 300,
                "ITM-FP-SML": 60,
                "ITM-RTE": 400,
                "ITM-HYG": 180,
                "ITM-BLK": 150,
                "ITM-WTR": 120,
                "ITM-MAT": 40,
                "ITM-MED": 50,
            },
            "WH-03": {
                "ITM-FP-STD": 260,
                "ITM-FP-SML": 50,
                "ITM-RTE": 500,
                "ITM-HYG": 30,
                "ITM-BLK": 200,
                "ITM-WTR": 90,
                "ITM-MAT": 30,
                "ITM-MED": 20,
            },
        }
        receipt_days = [-100, -79, -58, -37, -16]
        returned_by: dict[tuple[str, str], int] = defaultdict(int)
        for m in movements:
            if m["reason"] == "distribution_return":
                returned_by[(m["warehouse_id"], m["item_id"])] += m["quantity"]
        for wh, item_targets in targets.items():
            for item, target in item_targets.items():
                for i, day in enumerate(receipt_days):
                    start = self.D0 + timedelta(days=day)
                    end = self.D0 + timedelta(days=receipt_days[i + 1]) if i + 1 < len(receipt_days) else date.max
                    need = sum(q for (w, it, dday, q) in self.dispatch_log if w == wh and it == item and start <= dday < end)
                    if i == len(receipt_days) - 1:
                        need += target - returned_by[(wh, item)]
                    qty = int(math.ceil(max(need, 0) / 10.0) * 10)
                    if qty <= 0:
                        continue
                    movements.append(
                        dict(
                            warehouse_id=wh,
                            item_id=item,
                            moved_at=self.at(start, 8, 0),
                            movement_type="received",
                            reason="supplier_delivery",
                            quantity=qty,
                            dispatch_id=None,
                            reference=f"GRN-{wh}-{i + 1}",
                            recorded_by="WHO-01",
                        )
                    )
        self.ins("distribution_plans", plans)
        self.ins("dispatches", dispatches)
        movements.sort(key=lambda m: (m["moved_at"], m["movement_type"] == "dispatched"))
        self.ins("stock_movements", movements)
        self.ins("distributions", dists)
        self.ins("distribution_returns", returns)
        lead = {
            "ITM-FP-STD": (21, 120),
            "ITM-FP-SML": (21, 30),
            "ITM-RTE": (10, 300),
            "ITM-HYG": (14, 50),
            "ITM-BLK": (28, 60),
            "ITM-WTR": (7, 60),
            "ITM-MAT": (28, 20),
            "ITM-MED": (14, 25),
        }
        self.ins(
            "item_lead_times",
            [
                dict(warehouse_id=w[0], item_id=i, lead_time_days=lead[i][0], safety_days=7, reorder_threshold=lead[i][1])
                for w in sc.WAREHOUSES
                for i in lead
            ],
        )
        self.dists = dists
        self.plans = plans
        # Project 11 walkthrough household: coverage ends within the next 2-6 days and it is not merged.
        due_soon = sorted(
            (
                h
                for h in self.active_hh
                if self.D0 + timedelta(days=2) <= last_end[h["household_id"]] <= self.D0 + timedelta(days=6)
                and h["household_id"] <= "HH-00360"
            ),
            key=lambda h: (abs(h["household_size"] - 5), h["household_id"]),
        )
        if not due_soon:
            raise RuntimeError("Seed could not find a household for the Project 11 walkthrough")
        pick = due_soon[0]
        site_for = next((s for s, areas in sc.SITE_CATCHMENT.items() if pick["area_id"] in areas), "SITE-01")
        self.scenarios["p11"] = {
            "household_id": pick["household_id"],
            "household_size": pick["household_size"],
            "site_id": site_for,
            "current_coverage_end": last_end[pick["household_id"]].isoformat(),
        }
        # Reconciliation history: an approved report for SITE-01's previous round.
        prev = self.D0 - timedelta(days=28)
        p_start, p_end = (prev - timedelta(days=3)).isoformat(), (prev + timedelta(days=3)).isoformat()
        self.ins(
            "reconciliation_notes",
            [
                dict(
                    site_id="SITE-01",
                    period_start=p_start,
                    period_end=p_end,
                    discrepancy_key="unaccounted:ITM-FP-STD",
                    discrepancy_type="unaccounted",
                    note="Site team counted parcels again; difference is within the counted damage.",
                    resolved=1,
                    staff_id="CRD-01",
                    at=self.at(prev + timedelta(days=2), 11, 0),
                ),
                dict(
                    site_id="SITE-01",
                    period_start=p_start,
                    period_end=p_end,
                    discrepancy_key="other:queue",
                    discrepancy_type="other",
                    note="Distribution ran late; returns recorded the next morning.",
                    resolved=1,
                    staff_id="CRD-01",
                    at=self.at(prev + timedelta(days=2), 11, 5),
                ),
            ],
        )
        summary = {"site_id": "SITE-01", "item_id": "ITM-FP-STD", "note": "Approved with all discrepancies resolved."}
        self.ins(
            "reconciliation_reports",
            [
                dict(
                    report_id="RCN-0001",
                    site_id="SITE-01",
                    period_start=p_start,
                    period_end=p_end,
                    summary_json=json.dumps(summary),
                    markdown=f"# Reconciliation: SITE-01, {p_start} to {p_end}\n\nAll discrepancies resolved. Approved by CRD-01.\n",
                    approved_by="CRD-01",
                    approved_at=self.at(prev + timedelta(days=2), 12, 0),
                )
            ],
        )
        self.audit(
            self.at(prev + timedelta(days=2), 12, 0),
            "CRD-01",
            "approve",
            "reconciliation_reports",
            "RCN-0001",
            after={"status": "approved"},
        )

    # -- convoy details ----------------------------------------------------
    def convoy_details(self) -> None:
        self.ins(
            "convoy_vehicles",
            [
                dict(convoy_id="CNV-001", vehicle_id="VEH-03", driver_id="DRV-01"),
                dict(convoy_id="CNV-001", vehicle_id="VEH-05", driver_id="DRV-07"),
                dict(convoy_id="CNV-002", vehicle_id="VEH-01", driver_id="DRV-02"),
                dict(convoy_id="CNV-002", vehicle_id="VEH-02", driver_id="DRV-08"),
                dict(convoy_id="CNV-003", vehicle_id="VEH-03", driver_id="DRV-03"),
                dict(convoy_id="CNV-003", vehicle_id="VEH-05", driver_id="DRV-07"),
            ],
        )
        self.ins(
            "convoy_stops",
            [
                dict(convoy_id="CNV-001", site_id="SITE-07", vehicle_id="VEH-05", stop_seq=1),
                dict(convoy_id="CNV-001", site_id="SITE-05", vehicle_id="VEH-03", stop_seq=2),
                dict(convoy_id="CNV-002", site_id="SITE-01", vehicle_id="VEH-01", stop_seq=1),
                dict(convoy_id="CNV-002", site_id="SITE-10", vehicle_id="VEH-02", stop_seq=2),
                dict(convoy_id="CNV-003", site_id="SITE-09", vehicle_id="VEH-03", stop_seq=1),
                dict(convoy_id="CNV-003", site_id="SITE-02", vehicle_id="VEH-05", stop_seq=2),
                dict(convoy_id="CNV-003", site_id="SITE-08", vehicle_id="VEH-03", stop_seq=3),
            ],
        )
        loads = []
        vehicle_for = {("CNV-001", "SITE-05"): "VEH-03", ("CNV-001", "SITE-07"): "VEH-05", ("CNV-002", "SITE-10"): "VEH-02"}
        for convoy, lines in self.convoy_dispatches.items():
            for site, item, qty in lines:
                loads.append(
                    dict(
                        convoy_id=convoy,
                        vehicle_id=vehicle_for[(convoy, site)],
                        site_id=site,
                        item_id=item,
                        quantity=qty,
                        weight_kg=round(qty * self.item_weight[item], 1),
                    )
                )
        today_plans = [p for p in self.plans if p["planned_date"] == self.d(0) and p["site_id"] == "SITE-01"]
        for p in today_plans:
            loads.append(
                dict(
                    convoy_id="CNV-002",
                    vehicle_id="VEH-01",
                    site_id="SITE-01",
                    item_id=p["item_id"],
                    quantity=p["planned_quantity"],
                    weight_kg=round(p["planned_quantity"] * self.item_weight[p["item_id"]], 1),
                )
            )
        for site, item, qty, veh in (
            ("SITE-09", "ITM-FP-STD", 40, "VEH-03"),
            ("SITE-09", "ITM-HYG", 30, "VEH-03"),
            ("SITE-08", "ITM-FP-STD", 60, "VEH-03"),
            ("SITE-02", "ITM-WTR", 50, "VEH-05"),
        ):
            loads.append(
                dict(
                    convoy_id="CNV-003",
                    vehicle_id=veh,
                    site_id=site,
                    item_id=item,
                    quantity=qty,
                    weight_kg=round(qty * self.item_weight[item], 1),
                )
            )
        self.ins("convoy_loads", loads)
        checklist = [
            ("vehicle_inspection", "Vehicles inspected and ready"),
            ("fuel", "Fuel tanks full"),
            ("licences", "Driver licences checked"),
            ("load_secured", "Load secured and within capacity"),
            ("comms", "Radio and phone check done"),
            ("advisories", "Movement advisories checked for every stop"),
            ("manifest", "Load manifest printed"),
            ("first_aid", "First aid kit on board"),
        ]
        rows = []
        for convoy, done in (("CNV-001", 8), ("CNV-002", 8), ("CNV-003", 3), ("CNV-004", 0)):
            for i, (key, label) in enumerate(checklist):
                ok = i < done
                rows.append(
                    dict(
                        convoy_id=convoy,
                        item_key=key,
                        label=label,
                        checked=1 if ok else 0,
                        checked_by="CRD-01" if ok else None,
                        checked_at=self.t(days=-1) if ok and convoy != "CNV-001" else (self.t(days=-7) if ok else None),
                    )
                )
        self.ins("convoy_checklist", rows)
        d6 = self.D0 - timedelta(days=6)
        events = [
            ("CNV-001", self.at(d6 - timedelta(days=2), 10), "planned", "CRD-01", "Convoy planned"),
            ("CNV-001", self.at(d6 - timedelta(days=1), 15), "approved", "CRD-01", "Approved after checklist"),
            ("CNV-001", self.at(d6, 7), "departed", "CRD-01", None),
            ("CNV-001", self.at(d6, 10, 30), "arrived", "CRD-01", None),
            ("CNV-001", self.at(d6, 16), "returned", "CRD-01", None),
            ("CNV-002", self.t(days=-2), "planned", "CRD-01", "Convoy planned"),
            ("CNV-002", self.t(days=-1), "approved", "CRD-01", "Approved after checklist"),
            ("CNV-002", self.t(minutes=-100), "departed", "CRD-01", None),
            ("CNV-003", self.t(days=-1), "planned", "CRD-02", "Awaiting approval"),
            ("CNV-004", self.t(hours=-3), "draft", "CRD-02", "Draft created"),
        ]
        self.ins("convoy_events", [dict(convoy_id=c, at=a, status=s, staff_id=st, note=n) for c, a, s, st, n in events])
        self.audit(self.t(days=-1), "CRD-01", "approve", "convoys", "CNV-002", after={"status": "approved"})
        self.audit(self.t(days=-7), "CRD-01", "approve", "convoys", "CNV-001", after={"status": "approved"})

    # -- position reports --------------------------------------------------
    def positions(self) -> None:
        r = self.rng("positions")
        rows = []
        targets = {"VEH-01": self.site_xy["SITE-01"], "VEH-02": self.site_xy["SITE-10"]}
        for vid, veh in self.vehicles.items():
            home = self.wh_xy[veh["home_warehouse_id"]]
            if not veh["simulated"]:
                for k in range(6):
                    rows.append(
                        dict(
                            vehicle_id=vid,
                            convoy_id=None,
                            reported_at=self.t(hours=-5, minutes=-15 * (5 - k)),
                            x_km=round(home[0] + 0.2, 2),
                            y_km=round(home[1] - 0.1, 2),
                            speed_kmh=0.0,
                            source="seed",
                        )
                    )
                continue
            last_offset = r.randint(0, 3)
            for k in range(24):
                minutes_ago = last_offset + 5 * (23 - k)
                when = self.t(minutes=-minutes_ago)
                if vid in targets and minutes_ago <= 100:
                    frac = min(1.0, (100 - minutes_ago) / 90.0)
                    tx, ty = targets[vid]
                    x, y = home[0] + (tx - home[0]) * frac, home[1] + (ty - home[1]) * frac
                    rows.append(
                        dict(
                            vehicle_id=vid,
                            convoy_id="CNV-002",
                            reported_at=when,
                            x_km=round(x, 2),
                            y_km=round(y, 2),
                            speed_kmh=0.0 if frac >= 1.0 else round(r.uniform(25, 55), 1),
                            source="seed",
                        )
                    )
                else:
                    rows.append(
                        dict(
                            vehicle_id=vid,
                            convoy_id=None,
                            reported_at=when,
                            x_km=round(home[0] + r.uniform(-0.3, 0.3), 2),
                            y_km=round(home[1] + r.uniform(-0.3, 0.3), 2),
                            speed_kmh=0.0,
                            source="seed",
                        )
                    )
        d6 = self.D0 - timedelta(days=6)
        for vid, site in (("VEH-03", "SITE-05"), ("VEH-05", "SITE-07")):
            home, tgt = self.wh_xy["WH-02"], self.site_xy[site]
            for k in range(10):
                frac = k / 9
                rows.append(
                    dict(
                        vehicle_id=vid,
                        convoy_id="CNV-001",
                        reported_at=self.at(d6, 7 + k // 2, 30 * (k % 2)),
                        x_km=round(home[0] + (tgt[0] - home[0]) * frac, 2),
                        y_km=round(home[1] + (tgt[1] - home[1]) * frac, 2),
                        speed_kmh=round(r.uniform(20, 50), 1),
                        source="seed",
                    )
                )
        rows.sort(key=lambda x: (x["reported_at"], x["vehicle_id"]))
        self.ins("position_reports", rows)
        self.scenarios["p17"] = {
            "simulated_vehicle_ids": [v for v, x in self.vehicles.items() if x["simulated"]],
            "already_stale_vehicle_id": "VEH-07",
            "stale_limit_setting": "stale_position_minutes",
            "simulator_switch_setting": "position_simulator_enabled",
        }
        self.scenarios["p14"] = {"stale_vehicle_id": "VEH-07", "convoy_in_progress": "CNV-002"}

    # -- security ----------------------------------------------------------
    def security(self) -> None:
        r = self.rng("security")
        incidents: list[dict[str, Any]] = []

        def inc(area: str, hours_ago: float, itype: str, severity: int, status: str, source: str = "field_team") -> None:
            occurred = self.A - timedelta(hours=hours_ago)
            incidents.append(
                dict(
                    area_id=area,
                    occurred_at=clock.iso(occurred),
                    reported_at=clock.iso(min(occurred + timedelta(minutes=40), self.A)),
                    incident_type=itype,
                    severity=severity,
                    source=source,
                    verification_status=status,
                    verified_by="DO-01" if status != "unverified" else None,
                    verified_at=clock.iso(min(occurred + timedelta(hours=2), self.A)) if status != "unverified" else None,
                    description=r.choice(sc.INCIDENT_TYPES[itype]),
                    reported_by=r.choice(["DO-01", "DO-02", "FW-01", "FW-02"]),
                )
            )

        # Planted, recent: these decide the current advisories.
        inc("AR-07", 20, "armed_clash", 4, "verified")
        inc("AR-07", 30, "road_closure", 3, "verified")
        inc("AR-07", 5, "shelling", 5, "unverified", "media")
        inc("AR-11", 40, "checkpoint_delay", 3, "verified")
        inc("AR-04", 26, "checkpoint_delay", 3, "verified")
        inc("AR-04", 96, "road_closure", 2, "verified")
        inc("AR-02", 10, "unrest", 5, "unverified", "community_contact")
        inc("AR-02", 30, "armed_clash", 4, "unverified", "media")
        inc("AR-10", 72, "crime", 4, "discounted", "hotline")
        # Older history (outside the 7-day window).
        types = list(sc.INCIDENT_TYPES.keys())
        for _ in range(34):
            area = r.choice(sc.AREAS)[0]
            inc(
                area,
                r.uniform(8 * 24, 30 * 24),
                r.choice(types),
                r.randint(1, 4),
                r.choices(["verified", "unverified", "discounted"], [60, 30, 10])[0],
                r.choice(["field_team", "community_contact", "media", "hotline", "other_agency"]),
            )
        incidents.sort(key=lambda x: x["reported_at"])
        for n, row in enumerate(incidents, start=1):
            row["incident_id"] = f"INC-{n:04d}"
        self.ins("incidents", [{"incident_id": x["incident_id"], **{k: v for k, v in x.items() if k != "incident_id"}} for x in incidents])
        decided = self.A - timedelta(hours=2)
        advisories = []
        n = 0
        for area in [a[0] for a in sc.AREAS]:
            if area in ("AR-07", "AR-04"):
                n += 1
                advisories.append(
                    dict(
                        advisory_id=f"ADV-{n:04d}",
                        area_id=area,
                        suggested_level=0,
                        final_level=0,
                        is_override=0,
                        override_reason=None,
                        rule_basis_json=json.dumps({"rule_version": 1, "score": 0, "incidents": []}),
                        decided_by="DO-01",
                        decided_at=self.t(days=-9),
                        active=0,
                    )
                )
        for area in [a[0] for a in sc.AREAS]:
            rel = [x for x in incidents if x["area_id"] == area and clock.parse(x["occurred_at"]) <= decided]
            score = security.advisory_score(rel, decided)
            level = security.level_for_score(score)
            used = [
                x["incident_id"]
                for x in rel
                if x["verification_status"] == "verified" and security.incident_weight(clock.parse(x["occurred_at"]), decided) > 0
            ]
            final, override, reason = level, 0, None
            if area == "AR-04":
                final, override = 0, 1
                reason = "Checkpoint removed this morning; road confirmed open by the field team."
            n += 1
            adv_id = f"ADV-{n:04d}"
            advisories.append(
                dict(
                    advisory_id=adv_id,
                    area_id=area,
                    suggested_level=level,
                    final_level=final,
                    is_override=override,
                    override_reason=reason,
                    rule_basis_json=json.dumps({"rule_version": security.RULE_VERSION, "score": score, "incidents": used}),
                    decided_by="DO-01",
                    decided_at=clock.iso(decided),
                    active=1,
                )
            )
            if override:
                self.audit(
                    clock.iso(decided),
                    "DO-01",
                    "override",
                    "advisories",
                    adv_id,
                    before={"suggested_level": level},
                    after={"final_level": final},
                    reason=reason,
                )
        self.ins("advisories", advisories)
        self.scenarios["p15"] = {"restricted_area_id": "AR-07", "override_area_id": "AR-04", "unverified_only_area_id": "AR-02"}

    # -- briefings and situation reports -----------------------------------
    def briefings_and_sitreps(self) -> None:
        r = self.rng("briefings")
        yday = self.D0 - timedelta(days=1)
        site02_yday = {d["household_id"] for d in self.dists if d["site_id"] == "SITE-02" and d["distribution_date"] == yday.isoformat()}
        site01_today = {
            d["household_id"] for d in self.dists if d["site_id"] == "SITE-01" and d["distribution_date"] == self.D0.isoformat()
        }
        updates = [
            dict(
                reported_at=self.at(yday, 18, 10),
                area_id="AR-04",
                site_id="SITE-02",
                team="Coastal field team",
                text=f"Distribution at Harbor Flats finished. We reached {len(site02_yday) + 15} households today.",
                figure_key="households_reached",
                figure_value=float(len(site02_yday) + 15),
            ),
            dict(
                reported_at=self.t(minutes=-20),
                area_id="AR-08",
                site_id="SITE-01",
                team="Central field team",
                text=f"Riverbend distribution under way, {len(site01_today)} households served so far.",
                figure_key="households_reached",
                figure_value=float(len(site01_today)),
            ),
            dict(
                reported_at=self.at(yday, 9, 0),
                area_id="AR-07",
                site_id=None,
                team="Coastal field team",
                text="Road to Saltmarsh closed near the bridge; convoy movements on hold.",
                figure_key=None,
                figure_value=None,
            ),
            dict(
                reported_at=self.at(yday, 12, 30),
                area_id="AR-05",
                site_id="SITE-05",
                team="North field team",
                text="Hygiene kits from the last convoy fully distributed.",
                figure_key=None,
                figure_value=None,
            ),
            dict(
                reported_at=self.at(yday, 16, 0),
                area_id=None,
                site_id=None,
                team="Warehouse team",
                text="Coastal Depot hygiene kit stock is low; reorder requested.",
                figure_key=None,
                figure_value=None,
            ),
        ]
        for k in range(2, 6):
            day = self.D0 - timedelta(days=k)
            area = r.choice(sc.AREAS)
            updates.append(
                dict(
                    reported_at=self.at(day, 17, 0),
                    area_id=area[0],
                    site_id=None,
                    team="Field team",
                    text=f"Routine update from {area[1]}: no change in access; household visits continued.",
                    figure_key="household_visits",
                    figure_value=float(r.randint(8, 25)),
                )
            )
        updates.sort(key=lambda u: u["reported_at"])
        for n, u in enumerate(updates, start=1):
            u["update_id"] = f"FU-{n:04d}"
        self.ins("field_updates", [{"update_id": u["update_id"], **{k: v for k, v in u.items() if k != "update_id"}} for u in updates])
        self.scenarios["p18"] = {
            "conflict_site_id": "SITE-02",
            "conflict_date": yday.isoformat(),
            "field_update_households": len(site02_yday) + 15,
            "distribution_record_households": len(site02_yday),
        }
        bday = self.D0 - timedelta(days=2)
        calls_b = db.scalar("SELECT COUNT(*) FROM calls WHERE substr(received_at,1,10) = ?", (bday.isoformat(),), self.c)
        hh_b = len({d["household_id"] for d in self.dists if d["distribution_date"] == bday.isoformat()})
        content = (
            f"# Daily emergency briefing, {bday.isoformat()}\n\n## Calls\n- Calls received: {calls_b}\n\n"
            f"## Distributions\n- Households reached: {hh_b}\n\n## Notes\nNo conflicting totals.\n"
        )
        self.ins(
            "briefings",
            [
                dict(
                    briefing_id="BRF-0001",
                    briefing_date=bday.isoformat(),
                    status="approved",
                    content_md=content,
                    conflicts_json="[]",
                    created_at=self.at(bday, 18, 0),
                    created_by="CRD-02",
                    approved_by="CRD-01",
                    approved_at=self.at(bday, 19, 0),
                )
            ],
        )
        self.ins(
            "briefing_sources",
            [
                dict(
                    briefing_id="BRF-0001",
                    figure_key="calls_received",
                    figure_value=float(calls_b),
                    unit="calls",
                    source_table="calls",
                    source_record_ids=f"received_at on {bday.isoformat()}",
                ),
                dict(
                    briefing_id="BRF-0001",
                    figure_key="households_reached",
                    figure_value=float(hh_b),
                    unit="households",
                    source_table="distributions",
                    source_record_ids=f"distribution_date = {bday.isoformat()}",
                ),
            ],
        )
        self.audit(self.at(bday, 19, 0), "CRD-01", "approve", "briefings", "BRF-0001", after={"status": "approved"})

        rs = self.rng("sitreps")
        sitreps, passages = [], []
        areas = [a for a in sc.AREAS]
        for n in range(1, 17):
            title_t, sections = sc.SITREP_TEMPLATES[(n - 1) % len(sc.SITREP_TEMPLATES)]
            a1 = areas[(n * 5) % 12]
            a2 = areas[(n * 5 + 3) % 12]
            vals = dict(area=a1[1], area2=a2[1], n=rs.randint(40, 400), m=rs.randint(20, 300), k=rs.randint(10, 200))
            title = title_t.format(**vals)
            report_date = self.d(-(20 + (16 - n) * 33 + rs.randint(0, 10)))
            body = [f"# {title}", f"Report date: {report_date}"]
            sid = f"SITREP-{n:03d}"
            for seq, (heading, text) in enumerate(sections, start=1):
                filled = text.format(**vals)
                body.append(f"## {heading}\n{filled}")
                passages.append(dict(sitrep_id=sid, seq=seq, heading=heading, text=filled))
            sitreps.append(dict(sitrep_id=sid, title=title, report_date=report_date, area_id=a1[0], body_md="\n\n".join(body)))
        self.ins("sitreps", sitreps)
        self.ins("sitrep_passages", passages)

    # -- platform: alert rules, alerts, jobs, heartbeats -------------------
    def platform(self) -> None:
        self.ins(
            "alert_rules",
            [
                dict(
                    rule_id="RULE-STOCK",
                    name="Stock below threshold",
                    rule_type="stock_below",
                    threshold=50,
                    params_json=json.dumps({"comparison": "on_hand < threshold", "unit": "item units"}),
                    description="Fires for each warehouse and item whose on-hand quantity is below the threshold. At the threshold it does not fire.",
                    severity="warning",
                    enabled=1,
                ),
                dict(
                    rule_id="RULE-CALLBACK",
                    name="Overdue callbacks",
                    rule_type="overdue_callbacks",
                    threshold=5,
                    params_json=json.dumps({"comparison": "overdue_count >= threshold"}),
                    description="Fires when the number of callbacks past their due time is at or above the threshold.",
                    severity="warning",
                    enabled=1,
                ),
                dict(
                    rule_id="RULE-STALE",
                    name="Stale vehicle position",
                    rule_type="stale_position",
                    threshold=15,
                    params_json=json.dumps({"comparison": "minutes_since_last_report > threshold", "unit": "minutes"}),
                    description="Fires for each vehicle whose last position report is older than the threshold in minutes. Exactly at the threshold it does not fire.",
                    severity="critical",
                    enabled=1,
                ),
                dict(
                    rule_id="RULE-PIPELINE",
                    name="Failed pipeline run",
                    rule_type="pipeline_failed",
                    threshold=1,
                    params_json=json.dumps({"comparison": "failed_runs_last_24h >= threshold", "window_hours": 24}),
                    description="Fires when the number of failed pipeline runs in the last 24 hours is at or above the threshold.",
                    severity="critical",
                    enabled=1,
                ),
            ],
        )
        failed = next(x for x in self.pipeline_runs if x["status"] == "failed")
        self.ins(
            "alerts",
            [
                dict(
                    rule_id="RULE-PIPELINE",
                    fired_at=failed["finished_at"],
                    entity_ref=failed["run_id"],
                    observed_value=1,
                    threshold=1,
                    message=f"Pipeline run {failed['run_id']} failed: {failed['error_message']}",
                    status="resolved",
                    acknowledged_by="ADM-01",
                    acknowledged_at=clock.iso(clock.parse(failed["finished_at"]) + timedelta(hours=1)),
                    resolved_at=clock.iso(clock.parse(failed["finished_at"]) + timedelta(hours=26)),
                ),
                dict(
                    rule_id="RULE-CALLBACK",
                    fired_at=self.t(days=-1, hours=-2),
                    entity_ref="callbacks",
                    observed_value=6,
                    threshold=5,
                    message="6 callbacks are overdue.",
                    status="acknowledged",
                    acknowledged_by="CRD-01",
                    acknowledged_at=self.t(days=-1, hours=-1),
                    resolved_at=None,
                ),
                dict(
                    rule_id="RULE-STOCK",
                    fired_at=self.t(hours=-2),
                    entity_ref="WH-03/ITM-HYG",
                    observed_value=30,
                    threshold=50,
                    message="Coastal Depot: 30 Family hygiene kit on hand, below 50.",
                    status="open",
                    acknowledged_by=None,
                    acknowledged_at=None,
                    resolved_at=None,
                ),
                dict(
                    rule_id="RULE-STALE",
                    fired_at=self.t(hours=-4, minutes=-40),
                    entity_ref="VEH-07",
                    observed_value=20,
                    threshold=15,
                    message="VEH-07 has not reported a position for 20 minutes.",
                    status="open",
                    acknowledged_by=None,
                    acknowledged_at=None,
                    resolved_at=None,
                ),
            ],
        )
        for x in (("RULE-PIPELINE", "acknowledge"), ("RULE-CALLBACK", "acknowledge")):
            self.audit(self.t(days=-1), "CRD-01" if x[0] == "RULE-CALLBACK" else "ADM-01", x[1], "alerts", x[0])
        jobs = [
            ("pipeline_ingest", "Load new field team exports (Project 07)", 3600),
            ("alert_evaluation", "Evaluate alert rules (Project 20)", 300),
            ("position_simulator", "Send simulated vehicle position reports (Project 20)", 60),
            ("db_backup", "Back up the SQLite database (server mode)", 86400),
        ]
        self.ins("scheduled_jobs", [dict(job_name=j[0], description=j[1], interval_seconds=j[2], enabled=1) for j in jobs])
        runs = []
        for k in range(12, 0, -1):
            start = self.A - timedelta(minutes=5 * k)
            runs.append(
                dict(
                    job_name="alert_evaluation",
                    started_at=clock.iso(start),
                    finished_at=clock.iso(start + timedelta(seconds=2)),
                    status="success",
                    trigger="scheduled",
                    detail="4 rules evaluated",
                )
            )
        for k in range(5, 0, -1):
            start = self.A - timedelta(minutes=k)
            runs.append(
                dict(
                    job_name="position_simulator",
                    started_at=clock.iso(start),
                    finished_at=clock.iso(start + timedelta(seconds=1)),
                    status="success",
                    trigger="scheduled",
                    detail="7 vehicles reported",
                )
            )
        for pr in self.pipeline_runs:
            runs.append(
                dict(
                    job_name="pipeline_ingest",
                    started_at=pr["started_at"],
                    finished_at=pr["finished_at"],
                    status="failed" if pr["status"] == "failed" else "success",
                    trigger=pr["trigger"],
                    detail=f"{pr['run_id']}: {pr['file_name']}",
                )
            )
        for k in (2, 1):
            start = self.at(self.D0 - timedelta(days=k), 2, 0)
            runs.append(
                dict(
                    job_name="db_backup",
                    started_at=start,
                    finished_at=clock.iso(clock.parse(start) + timedelta(seconds=3)),
                    status="success",
                    trigger="scheduled",
                    detail="backup written",
                )
            )
        runs.sort(key=lambda x: x["started_at"])
        self.ins("job_runs", runs)
        self.ins(
            "service_heartbeats",
            [
                dict(service_name="homepage", last_seen_at=self.t(), status="ok", detail="Streamlit portfolio homepage"),
                dict(service_name="api", last_seen_at=self.t(), status="ok", detail="FastAPI operations API"),
                dict(service_name="scheduler", last_seen_at=self.t(), status="ok", detail="Pipeline, alerts, backups"),
                dict(service_name="position_simulator", last_seen_at=self.t(), status="ok", detail="Simulated vehicle positions"),
            ],
        )

    # -- merges ------------------------------------------------------------
    def merges(self) -> None:
        kept, merged = self.hh["HH-00200"], self.hh["HH-00369"]
        a, b = self.hh["HH-00210"], self.hh["HH-00370"]
        self.ins(
            "merge_history",
            [
                dict(
                    merge_id="MRG-0001",
                    kept_household_id="HH-00200",
                    other_household_id="HH-00369",
                    decision="merged",
                    match_score=0.93,
                    kept_snapshot_json=json.dumps(kept, ensure_ascii=False),
                    other_snapshot_json=json.dumps(dict(merged, status="active", merged_into=None), ensure_ascii=False),
                    field_choices_json=json.dumps({"head_name": "HH-00200"}),
                    moved_records_json=json.dumps({}),
                    reason="Same head of household, same birth year and area; spelling variant of the first name.",
                    decided_by="CW-01",
                    decided_at=self.at(self.D0 - timedelta(days=60), 11, 0),
                ),
                dict(
                    merge_id="MRG-0002",
                    kept_household_id="HH-00210",
                    other_household_id="HH-00370",
                    decision="not_duplicate",
                    match_score=0.71,
                    kept_snapshot_json=json.dumps(a, ensure_ascii=False),
                    other_snapshot_json=json.dumps(b, ensure_ascii=False),
                    field_choices_json=None,
                    moved_records_json=None,
                    reason="Same name but different birth year and area: confirmed by phone they are different families.",
                    decided_by="CW-02",
                    decided_at=self.at(self.D0 - timedelta(days=50), 14, 0),
                ),
            ],
        )
        self.audit(
            self.at(self.D0 - timedelta(days=60), 11, 0),
            "CW-01",
            "merge",
            "households",
            "HH-00369",
            after={"merged_into": "HH-00200"},
            reason="Same head of household; spelling variant.",
        )


def build(conn: sqlite3.Connection, anchor: datetime | None = None, seed: int = config.SEED) -> dict[str, Any]:
    """Fill an empty schema with the demo data. Returns the walkthrough scenarios."""
    anchor = anchor or clock.now()
    return Seeder(conn, anchor, seed).run()


def reset_database(path: Path | str | None = None, anchor: datetime | None = None) -> Path:
    """Delete and rebuild the database at path (default: PORTFOLIO_DB_PATH or data/portfolio.db)."""
    target = Path(path) if path else config.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".building")
    for p in (tmp, Path(str(tmp) + "-journal"), Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        if p.exists():
            p.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        db.init_schema(conn)
        build(conn, anchor)
        conn.commit()
        problems = conn.execute("PRAGMA foreign_key_check").fetchall()
        if problems:
            raise RuntimeError(f"Seed produced foreign key violations: {[tuple(p) for p in problems[:5]]}")
    finally:
        conn.close()
    if target.exists() and not db.database_exists(target):
        target.unlink()  # empty or not a portfolio database: replace it outright
    if target.exists():
        # Copy into the existing file with SQLite's backup API, so apps that have the
        # database open (Windows cannot delete open files) simply see the new content.
        src, dst = sqlite3.connect(tmp), sqlite3.connect(target, timeout=30)
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        tmp.unlink()
    else:
        tmp.replace(target)
    models = target.parent / "models"
    if models.exists():
        for f in models.glob("*.joblib"):
            f.unlink()
    return target


def ensure_database(path: Path | str | None = None) -> Path:
    """Create the database if it does not exist yet."""
    target = Path(path) if path else config.db_path()
    if not db.database_exists(target):
        reset_database(target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or reset the demo database.")
    parser.add_argument("--reset", action="store_true", help="delete and rebuild the database")
    parser.add_argument("--db", help="database path (default: PORTFOLIO_DB_PATH or data/portfolio.db)")
    parser.add_argument("--anchor", help="demo clock anchor, e.g. 2026-11-16T09:00:00 (default: now)")
    args = parser.parse_args(argv)
    anchor = datetime.fromisoformat(args.anchor) if args.anchor else None
    target = Path(args.db) if args.db else config.db_path()
    if args.reset or not db.database_exists(target):
        reset_database(target, anchor)
        print(f"Database rebuilt: {target}")
    else:
        print(f"Database already exists: {target} (use --reset to rebuild)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
