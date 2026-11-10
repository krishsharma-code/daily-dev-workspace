"""Put ``server/`` on ``sys.path`` when pytest is run from this folder."""

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
