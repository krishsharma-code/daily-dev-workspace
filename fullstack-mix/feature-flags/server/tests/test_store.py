"""Tests for flag storage and evaluation (``server/store.py``)."""

import pytest

from store import (
    BUCKETS,
    REASON_ALLOWLIST,
    REASON_DISABLED,
    REASON_OUTSIDE_ROLLOUT,
    REASON_ROLLOUT,
    Flag,
    FlagStore,
    ValidationError,
    bucket_for,
    normalise_allowlist,
    normalise_key,
    normalise_rollout,
)


@pytest.fixture()
def store():
    s = FlagStore(":memory:")
    yield s
    s.close()


class TestKeys:
    def test_key_is_lowercased_and_trimmed(self):
        assert normalise_key("  New-Checkout  ") == "new-checkout"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "-leading-dash", "has space", "emoji-🙂", "a" * 65, None, 7]
    )
    def test_bad_keys_are_rejected(self, bad):
        with pytest.raises(ValidationError):
            normalise_key(bad)

    def test_dotted_key_is_allowed(self):
        assert normalise_key("checkout.v2") == "checkout.v2"


class TestRolloutValidation:
    @pytest.mark.parametrize("value", [0, 1, 50, 100])
    def test_valid_rollouts(self, value):
        assert normalise_rollout(value) == value

    @pytest.mark.parametrize("value", [-1, 101, 12.5, "50", True, None])
    def test_invalid_rollouts(self, value):
        with pytest.raises(ValidationError):
            normalise_rollout(value)


class TestAllowlist:
    def test_list_is_deduped_and_order_kept(self):
        assert normalise_allowlist(["bob", "alice", "bob", " "]) == ("bob", "alice")

    def test_comma_separated_string_is_accepted(self):
        assert normalise_allowlist("bob, alice\ncarol") == ("bob", "alice", "carol")

    def test_none_becomes_empty(self):
        assert normalise_allowlist(None) == ()

    def test_non_string_entry_is_rejected(self):
        with pytest.raises(ValidationError):
            normalise_allowlist(["bob", 3])


class TestBucketing:
    def test_bucket_is_stable(self):
        first = bucket_for("checkout", "user-1")
        assert first == bucket_for("checkout", "user-1")
        assert 0 <= first < BUCKETS

    def test_bucket_depends_on_flag_and_user(self):
        assert bucket_for("checkout", "user-1") != bucket_for("checkout", "user-2")
        assert bucket_for("checkout", "user-1") != bucket_for("search", "user-1")

    def test_buckets_spread_across_users(self):
        buckets = {bucket_for("checkout", f"user-{i}") for i in range(500)}
        # 500 users over 10,000 buckets should not collapse into a handful.
        assert len(buckets) > 400


class TestFlagEvaluation:
    def test_disabled_flag_is_off_even_for_allowlisted_user(self):
        flag = Flag(key="checkout", enabled=False, rollout=100, allowlist=("bob",))
        decision = flag.evaluate("bob")
        assert decision["enabled"] is False
        assert decision["reason"] == REASON_DISABLED

    def test_allowlist_wins_over_zero_rollout(self):
        flag = Flag(key="checkout", enabled=True, rollout=0, allowlist=("bob",))
        decision = flag.evaluate("bob")
        assert decision["enabled"] is True
        assert decision["reason"] == REASON_ALLOWLIST

    def test_full_rollout_includes_everyone(self):
        flag = Flag(key="checkout", enabled=True, rollout=100)
        for user in ("a", "b", "c", "d"):
            assert flag.evaluate(user)["reason"] == REASON_ROLLOUT

    def test_zero_rollout_excludes_everyone(self):
        flag = Flag(key="checkout", enabled=True, rollout=0)
        for user in ("a", "b", "c", "d"):
            assert flag.evaluate(user)["reason"] == REASON_OUTSIDE_ROLLOUT

    def test_decision_reports_bucket_and_rollout(self):
        flag = Flag(key="checkout", enabled=True, rollout=25)
        decision = flag.evaluate("bob")
        assert decision["bucket"] == bucket_for("checkout", "bob")
        assert decision["rollout"] == 25
        assert decision["key"] == "checkout"
        assert decision["user_id"] == "bob"


class TestStore:
    def test_put_flag_creates_with_defaults(self, store):
        flag, created = store.put_flag({"key": "checkout"})
        assert created is True
        assert flag.enabled is False
        assert flag.rollout == 0
        assert flag.allowlist == ()
        assert flag.created_at and flag.updated_at

    def test_put_flag_replaces_and_reports_update(self, store):
        store.put_flag({"key": "checkout", "description": "old"})
        flag, created = store.put_flag(
            {"key": "checkout", "description": "new", "enabled": True, "rollout": 40}
        )
        assert created is False
        assert flag.description == "new"
        assert flag.enabled is True
        assert flag.rollout == 40
        assert store.count_flags() == 1

    def test_put_flag_keeps_unsent_fields(self, store):
        store.put_flag({"key": "checkout", "description": "keep me", "rollout": 30})
        flag, _ = store.put_flag({"key": "checkout", "enabled": True})
        assert flag.description == "keep me"
        assert flag.rollout == 30

    def test_update_flag_patches_only_given_fields(self, store):
        store.put_flag(
            {"key": "checkout", "description": "desc", "rollout": 30, "enabled": True}
        )
        flag = store.update_flag("checkout", {"rollout": 5})
        assert flag.rollout == 5
        assert flag.description == "desc"
        assert flag.enabled is True

    def test_update_flag_rejects_empty_changes(self, store):
        store.put_flag({"key": "checkout"})
        with pytest.raises(ValidationError):
            store.update_flag("checkout", {"unknown": 1})

    def test_update_flag_raises_for_unknown(self, store):
        with pytest.raises(KeyError):
            store.update_flag("nope", {"rollout": 10})

    def test_list_flags_is_sorted_by_key(self, store):
        for key in ("zeta", "alpha", "mid"):
            store.put_flag({"key": key})
        assert [flag.key for flag in store.list_flags()] == ["alpha", "mid", "zeta"]

    def test_delete_flag(self, store):
        store.put_flag({"key": "checkout"})
        assert store.delete_flag("checkout") is True
        assert store.delete_flag("checkout") is False
        assert store.get_flag("checkout") is None

    def test_evaluate_unknown_flag_raises_key_error(self, store):
        with pytest.raises(KeyError):
            store.evaluate("missing", "bob")

    def test_evaluate_rejects_empty_user_id(self, store):
        store.put_flag({"key": "checkout", "enabled": True})
        with pytest.raises(ValidationError):
            store.evaluate("checkout", "   ")

    def test_evaluate_all_covers_every_flag(self, store):
        store.put_flag({"key": "a", "enabled": True, "rollout": 100})
        store.put_flag({"key": "b", "enabled": False})
        decisions = store.evaluate_all("bob")
        assert [d["key"] for d in decisions] == ["a", "b"]
        assert [d["enabled"] for d in decisions] == [True, False]

    def test_flags_persist_in_a_file(self, tmp_path):
        path = str(tmp_path / "flags.db")
        with FlagStore(path) as first:
            first.put_flag({"key": "checkout", "enabled": True, "rollout": 20})
        with FlagStore(path) as second:
            flag = second.get_flag("checkout")
            assert flag is not None
            assert flag.rollout == 20
