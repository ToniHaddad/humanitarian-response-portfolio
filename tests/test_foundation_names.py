"""Seeded household names: three first-name and family-name combinations never appear."""

from __future__ import annotations

import sqlite3

from shared import seed
from shared import seed_content as sc


def _denied_names() -> set[str]:
    firsts = {f[0]: f for f in sc.FIRST_NAMES_M + sc.FIRST_NAMES_F}
    families = {f[0]: f for f in sc.FAMILY_NAMES}
    names: set[str] = set()
    for first, family in sc.DENIED_NAME_PAIRS:
        f, fam = firsts[first], families[family]
        names.add(f"{f[0]} {fam[0]}")
        names.add(f"{f[1]} {fam[1]}")
        for fv in [f[0], *f[2]]:
            for famv in [fam[0], *fam[2]]:
                names.add(f"{fv} {famv}")
    return names


def test_denied_names_absent_from_seeded_households(conn: sqlite3.Connection) -> None:
    denied = _denied_names()
    assert {"Fadi Karam", "Karim Bitar", "Ali Hamdan", "فادي كرم", "كريم بيطار", "علي حمدان"} <= denied
    names = {r[0] for r in conn.execute("SELECT head_name FROM households")}
    assert names, "no households seeded"
    assert not names & denied


def test_denied_pair_is_replaced_by_another_first_name_of_the_same_gender() -> None:
    class Forced:
        def __init__(self, first: tuple, family: tuple) -> None:
            self.values = [first, family]

        def choice(self, _seq: object) -> tuple:
            return self.values.pop(0)

    builder = seed.Seeder.__new__(seed.Seeder)
    for first_pool, first_name, family_name in (
        (sc.FIRST_NAMES_M, "Fadi", "Karam"),
        (sc.FIRST_NAMES_M, "Karim", "Bitar"),
        (sc.FIRST_NAMES_M, "Ali", "Hamdan"),
    ):
        first = next(f for f in first_pool if f[0] == first_name)
        family = next(f for f in sc.FAMILY_NAMES if f[0] == family_name)
        got_first, got_family = builder._name(Forced(first, family), "M")
        assert got_family == family
        assert got_first in first_pool and got_first != first
        assert (got_first[0], got_family[0]) not in sc.DENIED_NAME_PAIRS
