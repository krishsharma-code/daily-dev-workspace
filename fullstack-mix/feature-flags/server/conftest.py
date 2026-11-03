"""Make the server package importable when tests run from this folder.

pytest's default import mode adds the directory containing each test file to
``sys.path``; this conftest ensures the ``server/`` directory is on the path
too, so ``import store`` and ``import app`` work without an install step.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
