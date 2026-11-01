"""Make the module importable when tests run from this folder."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
