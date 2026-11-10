"""Tests for the SQLite layer (``server/store.py``)."""

import sqlite3

import pytest

from splitter import ValidationError
from store import ExpenseStore, slugify


@pytest.fixture()
def store():
    instance = ExpenseStore(":memory:")
    yield instance
    instance.close()


@pytest.fixture()
def goa(store):
    return store.create_group("Goa Trip", ["asha", "rahul", "meera"])


class TestSlugify:
    @pytest.mark.parametrize(
        "name,slug",
        [
            ("Goa Trip", "goa-trip"),
            ("  Office   Lunch  ", "office-lunch"),
            ("Flat 3B / Nov", "flat-3b-nov"),
            ("Caf\u00e9 Society", "caf-society"),
        ],
    )
    def test_slugs(self, name, slug):
        assert slugify(name) == slug

    def test_requires_a_letter_or_digit(self):
        with pytest.raises(ValidationError):
            slugify("!!!")


class TestGroups:
    def test_create_returns_the_group(self, store):
        group = store.create_group("Goa Trip", ["asha", "rahul"])
        assert group["id"] == "goa-trip"
        assert group["name"] == "Goa Trip"
        assert group["members"] == ["asha", "rahul"]
        assert group["created_at"].endswith("+00:00")

    def test_members_are_trimmed_and_ordered(self, store):
        group = store.create_group("Flat", [" asha ", "rahul"])
        assert group["members"] == ["asha", "rahul"]

    def test_duplicate_name_rejected(self, store):
        store.create_group("Goa Trip", ["a", "b"])
        with pytest.raises(ValidationError):
            store.create_group("goa trip", ["c", "d"])

    @pytest.mark.parametrize(
        "name,members",
        [
            ("", ["a", "b"]),
            ("   ", ["a", "b"]),
            (None, ["a", "b"]),
            ("Trip", ["solo"]),
            ("Trip", []),
            ("Trip", ["a", "a"]),
            ("Trip", "asha"),
            ("Trip", ["a", " "]),
            ("x" * 81, ["a", "b"]),
        ],
    )
    def test_bad_groups_rejected(self, store, name, members):
        with pytest.raises(ValidationError):
            store.create_group(name, members)

    def test_list_and_get(self, store):
        store.create_group("First", ["a", "b"])
        store.create_group("Second", ["a", "b"])
        assert [g["id"] for g in store.list_groups()] == ["first", "second"]
        assert store.get_group("second")["name"] == "Second"
        assert store.get_group("nope") is None

    def test_delete_group_removes_its_expenses(self, store, goa):
        store.add_expense("goa-trip", "Dinner", "asha", "300")
        assert store.delete_group("goa-trip") is True
        assert store.get_group("goa-trip") is None
        assert store.list_expenses("goa-trip") == []
        assert store.delete_group("goa-trip") is False


class TestExpenses:
    def test_equal_split_defaults_to_every_member(self, store, goa):
        expense = store.add_expense("goa-trip", "Dinner", "asha", "300")
        assert expense["amount_paise"] == 30000
        assert expense["amount"] == "300.00"
        assert expense["split_type"] == "equal"
        assert expense["shares"] == {"asha": 10000, "rahul": 10000, "meera": 10000}

    def test_equal_split_carries_the_odd_paise(self, store, goa):
        expense = store.add_expense("goa-trip", "Auto", "rahul", "100")
        assert expense["shares"] == {"asha": 3334, "rahul": 3333, "meera": 3333}
        assert sum(expense["shares"].values()) == 10000

    def test_subset_of_participants(self, store, goa):
        expense = store.add_expense(
            "goa-trip", "Coffee", "asha", "100", participants=["asha", "meera"]
        )
        assert expense["shares"] == {"asha": 5000, "meera": 5000}

    def test_exact_split(self, store, goa):
        expense = store.add_expense(
            "goa-trip",
            "Cab",
            "rahul",
            "900",
            split_type="exact",
            shares={"asha": "300", "rahul": "600"},
        )
        assert expense["shares"] == {"asha": 30000, "rahul": 60000}

    def test_weighted_split(self, store, goa):
        expense = store.add_expense(
            "goa-trip",
            "Villa",
            "meera",
            "6000",
            split_type="weights",
            weights={"asha": 2, "rahul": 2, "meera": 1},
        )
        assert expense["split_type"] == "weights"
        assert expense["shares"] == {"asha": 240000, "rahul": 240000, "meera": 120000}

    def test_unknown_group(self, store):
        with pytest.raises(KeyError):
            store.add_expense("nope", "Dinner", "asha", "100")

    def test_payer_must_be_a_member(self, store, goa):
        with pytest.raises(ValidationError):
            store.add_expense("goa-trip", "Dinner", "stranger", "100")

    def test_participants_must_be_members(self, store, goa):
        with pytest.raises(ValidationError):
            store.add_expense(
                "goa-trip", "Dinner", "asha", "100", participants=["asha", "stranger"]
            )

    @pytest.mark.parametrize(
        "description,amount",
        [
            ("", "100"),
            ("   ", "100"),
            (None, "100"),
            ("x" * 201, "100"),
            ("Dinner", "0"),
            ("Dinner", "-5"),
            ("Dinner", "abc"),
            ("Dinner", 12.5),
        ],
    )
    def test_bad_expenses_rejected(self, store, goa, description, amount):
        with pytest.raises(ValidationError):
            store.add_expense("goa-trip", description, "asha", amount)

    def test_exact_without_shares_rejected(self, store, goa):
        with pytest.raises(ValidationError):
            store.add_expense("goa-trip", "Cab", "asha", "100", split_type="exact")

    def test_listing_and_deleting(self, store, goa):
        first = store.add_expense("goa-trip", "Dinner", "asha", "300")
        second = store.add_expense("goa-trip", "Cab", "rahul", "200")
        listed = store.list_expenses("goa-trip")
        assert [e["id"] for e in listed] == [first["id"], second["id"]]
        assert store.delete_expense("goa-trip", first["id"]) is True
        assert store.delete_expense("goa-trip", first["id"]) is False
        assert [e["description"] for e in store.list_expenses("goa-trip")] == ["Cab"]

    def test_expenses_do_not_leak_between_groups(self, store, goa):
        store.create_group("Office Lunch", ["asha", "rahul"])
        store.add_expense("goa-trip", "Dinner", "asha", "300")
        assert store.list_expenses("office-lunch") == []


class TestSettlement:
    def test_balances_and_plan(self, store, goa):
        store.add_expense("goa-trip", "Dinner", "asha", "300")
        state = store.settlement("goa-trip")
        assert state["total_paise"] == 30000
        assert state["total"] == "300.00"
        assert state["balances"]["asha"] == {"paise": 20000, "amount": "200.00"}
        assert state["balances"]["rahul"] == {"paise": -10000, "amount": "-100.00"}
        assert [dict(t) for t in state["settlements"]] == [
            {"from": "meera", "to": "asha", "amount": 10000, "amount_display": "100.00"},
            {"from": "rahul", "to": "asha", "amount": 10000, "amount_display": "100.00"},
        ]

    def test_empty_group_is_settled(self, store, goa):
        state = store.settlement("goa-trip")
        assert state["settlements"] == []
        assert all(entry["paise"] == 0 for entry in state["balances"].values())

    def test_balances_sum_to_zero(self, store, goa):
        store.add_expense("goa-trip", "Dinner", "asha", "333.33")
        store.add_expense("goa-trip", "Cab", "rahul", "100.01")
        store.add_expense("goa-trip", "Snacks", "meera", "50")
        balances = store.balances("goa-trip")
        assert sum(balances.values()) == 0

    def test_unknown_group(self, store):
        with pytest.raises(KeyError):
            store.settlement("nope")


class TestPersistence:
    def test_data_survives_reopening_the_file(self, tmp_path):
        path = str(tmp_path / "trips.db")
        first = ExpenseStore(path)
        first.create_group("Goa Trip", ["asha", "rahul"])
        first.add_expense("goa-trip", "Dinner", "asha", "300")
        first.close()

        second = ExpenseStore(path)
        try:
            assert second.get_group("goa-trip")["members"] == ["asha", "rahul"]
            assert second.balances("goa-trip") == {"asha": 15000, "rahul": -15000}
        finally:
            second.close()

    def test_schema_is_created_once(self, tmp_path):
        path = str(tmp_path / "trips.db")
        for _ in range(2):
            instance = ExpenseStore(path)
            instance.close()
        with sqlite3.connect(path) as db:
            tables = {
                row[0]
                for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert {"groups", "expenses"} <= tables
