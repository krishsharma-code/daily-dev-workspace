"""Shared pytest fixtures for the Markdown Notes server tests."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from store import NoteStore  # noqa: E402


@pytest.fixture()
def store():
    """An empty, in-memory notes store."""
    notes = NoteStore(":memory:")
    yield notes
    notes.close()
