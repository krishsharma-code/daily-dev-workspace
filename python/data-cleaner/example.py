"""Runnable tour of the datacleaner toolkit.

Run it directly::

    python example.py
"""

from datacleaner import (
    clean_records,
    dedupe_records,
    missing_required,
    normalize_email,
    normalize_phone,
    to_number,
)

# A deliberately messy batch, as you might get from a CSV export.
raw = [
    {"name": "  Ada   Lovelace ", "email": "ADA@Example.COM ", "phone": "98765 43210", "amount": "1,200"},
    {"name": "N/A", "email": "n/a", "phone": "", "amount": ""},
    {"name": "Grace Hopper", "email": "grace@navy.mil", "phone": "09876543211", "amount": "\u20b9900.50"},
    {"name": "Grace Hopper", "email": "grace@navy.mil", "phone": "09876543211", "amount": "\u20b9900.50"},
]

clean = clean_records(
    raw,
    transformers={
        "email": normalize_email,
        "phone": normalize_phone,
        "amount": to_number,
    },
)
clean = dedupe_records(clean, key_fields=["email"])

for record in clean:
    print(record)

print("missing required in first record:", missing_required(clean[0], ["name", "email", "phone"]))
