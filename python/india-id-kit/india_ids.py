"""Validation, normalisation and masking for Indian identifiers.

Standard library only. Covers the identifiers an Indian product actually ends
up validating in a signup or KYC form:

===========  ==========================================================
kind         what is checked
===========  ==========================================================
``aadhaar``  12 digits, first digit 2-9, Verhoeff checksum over all 12
``pan``      10 chars ``AAAAA9999A``, holder-type letter in position 4
``gstin``    15 chars, real state code, embedded PAN, base-36 check char
``ifsc``     11 chars ``AAAA0XXXXXX``
``vpa``      UPI ``handle@psp`` syntax, with known-PSP detection
``mobile``   10 digits starting 6-9, ``+91`` / ``0`` prefixes stripped
``pincode``  6 digits, first digit 1-9, postal region and circle
``vehicle``  standard ``SS RR [SERIES] NNNN`` and BH-series plates
===========  ==========================================================

Every validator returns the same shape, so a form handler needs one branch:

    >>> from india_ids import validate
    >>> result = validate("gstin", "27aapfu0939f1zv")
    >>> result.ok, result.value
    (True, '27AAPFU0939F1ZV')
    >>> result.details["state"]
    'Maharashtra'

Bad input is a value, not an exception:

    >>> result = validate("pan", "ABCD1234")
    >>> result.ok, result.reason
    (False, 'PAN must be 10 characters, got 8')

Use :func:`normalize` when you want the canonical value or an exception, and
:func:`mask` when you have to show an identifier on screen (Aadhaar in
particular should never be displayed or logged in full).

No network calls, no data files, no dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = [
    "KINDS",
    "Result",
    "ValidationError",
    "is_valid",
    "mask",
    "normalize",
    "validate",
    "GSTIN_STATE_CODES",
    "PAN_HOLDER_TYPES",
    "PIN_REGIONS",
    "UPI_KNOWN_PSPS",
    "VEHICLE_STATE_CODES",
    "aadhaar_check_digit",
    "gstin_check_char",
]

# --------------------------------------------------------------------------
# shared plumbing
# --------------------------------------------------------------------------


class ValidationError(ValueError):
    """Raised by :func:`normalize` when an identifier does not validate."""

    def __init__(self, kind: str, value: Any, reason: str) -> None:
        self.kind = kind
        self.value = value
        self.reason = reason
        super().__init__(f"{kind}: {reason}")


@dataclass
class Result:
    """Outcome of a validation.

    ``ok`` is the only thing a caller has to look at. ``value`` is the
    canonical form (upper case, separators stripped) and is ``None`` when the
    input was rejected. ``reason`` is a short, user-showable explanation of the
    *first* problem found, and ``details`` carries whatever is useful to keep
    once the identifier is known good.
    """

    kind: str
    ok: bool
    value: str | None = None
    reason: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok

    def unwrap(self) -> str:
        """Return the canonical value, or raise :class:`ValidationError`."""
        if not self.ok:
            raise ValidationError(self.kind, self.value, self.reason or "invalid")
        return self.value  # type: ignore[return-value]


def _reject(kind: str, value: Any, reason: str) -> Result:
    shown = value if isinstance(value, str) else repr(value)
    return Result(kind=kind, ok=False, value=None, reason=reason, details={"input": shown})


def _as_text(kind: str, value: Any) -> tuple[str | None, Result | None]:
    """Coerce to stripped upper-case text, or return an error Result."""
    if isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (int,)) and not isinstance(value, bool):
        text = str(value)
    else:
        return None, _reject(kind, value, f"{kind} must be a string, got {type(value).__name__}")
    if not text:
        return None, _reject(kind, value, f"{kind} is empty")
    return text.upper(), None


# --------------------------------------------------------------------------
# Aadhaar — Verhoeff
# --------------------------------------------------------------------------

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

_VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _verhoeff_valid(digits: str) -> bool:
    checksum = 0
    for position, char in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[position % 8][int(char)]]
    return checksum == 0


def aadhaar_check_digit(first_eleven: str) -> str:
    """Return the Verhoeff check digit for an 11-digit Aadhaar prefix."""
    if not isinstance(first_eleven, str) or not first_eleven.isdigit() or len(first_eleven) != 11:
        raise ValueError("expected 11 digits")
    if first_eleven[0] in "01":
        raise ValueError("Aadhaar cannot start with 0 or 1")
    checksum = 0
    for position, char in enumerate(reversed(first_eleven)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[(position + 1) % 8][int(char)]]
    return str(_VERHOEFF_INV[checksum])


def _check_aadhaar(value: Any) -> Result:
    text, error = _as_text("aadhaar", value)
    if error is not None:
        return error
    assert text is not None
    digits = re.sub(r"[\s-]", "", text)
    if not digits.isdigit():
        return _reject("aadhaar", value, "Aadhaar must contain only digits")
    if len(digits) != 12:
        return _reject("aadhaar", value, f"Aadhaar must be 12 digits, got {len(digits)}")
    if digits[0] in "01":
        return _reject("aadhaar", value, "Aadhaar cannot start with 0 or 1")
    if not _verhoeff_valid(digits):
        return _reject("aadhaar", value, "Aadhaar checksum (Verhoeff) does not match")
    return Result(kind="aadhaar", ok=True, value=digits, details={"last4": digits[-4:]})


# --------------------------------------------------------------------------
# PAN
# --------------------------------------------------------------------------

PAN_HOLDER_TYPES: Mapping[str, str] = {
    "A": "Association of persons (AOP)",
    "B": "Body of individuals (BOI)",
    "C": "Company",
    "F": "Firm / limited liability partnership",
    "G": "Government agency",
    "H": "Hindu undivided family (HUF)",
    "J": "Artificial juridical person",
    "L": "Local authority",
    "P": "Individual",
    "T": "Trust",
}

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def _check_pan(value: Any) -> Result:
    text, error = _as_text("pan", value)
    if error is not None:
        return error
    assert text is not None
    if len(text) != 10:
        return _reject("pan", value, f"PAN must be 10 characters, got {len(text)}")
    if not _PAN_RE.match(text):
        return _reject("pan", value, "PAN must look like AAAAA9999A (5 letters, 4 digits, 1 letter)")
    holder_code = text[3]
    if holder_code not in PAN_HOLDER_TYPES:
        return _reject(
            "pan",
            value,
            f"PAN character 4 must be a holder type, got {holder_code!r}",
        )
    return Result(
        kind="pan",
        ok=True,
        value=text,
        details={
            "holder_type": PAN_HOLDER_TYPES[holder_code],
            "holder_code": holder_code,
            "surname_initial": text[4],
        },
    )


# --------------------------------------------------------------------------
# GSTIN
# --------------------------------------------------------------------------

GSTIN_STATE_CODES: Mapping[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu (pre-2020)",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (pre-2014)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_B36_INDEX = {char: index for index, char in enumerate(_B36)}

def gstin_check_char(first_fourteen: str) -> str:
    """Return the GSTIN check character for the first 14 characters.

    The scheme is Luhn-like over base 36: each character is multiplied by an
    alternating factor of 1 and 2, and each product is reduced by folding its
    base-36 digits together.
    """
    if not isinstance(first_fourteen, str) or len(first_fourteen) != 14:
        raise ValueError("expected 14 characters")
    total = 0
    for position, char in enumerate(first_fourteen.upper()):
        try:
            value = _B36_INDEX[char]
        except KeyError:
            raise ValueError(f"{char!r} is not a base-36 character") from None
        product = value * (1 if position % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36]


def _check_gstin(value: Any) -> Result:
    text, error = _as_text("gstin", value)
    if error is not None:
        return error
    assert text is not None
    if len(text) != 15:
        return _reject("gstin", value, f"GSTIN must be 15 characters, got {len(text)}")
    for position, char in enumerate(text, start=1):
        if char not in _B36_INDEX:
            return _reject(
                "gstin",
                value,
                f"GSTIN character {position} must be a digit or upper-case letter, got {char!r}",
            )
    if not text[:2].isdigit():
        return _reject("gstin", value, "GSTIN characters 1-2 must be the numeric state code")
    if not text[2:7].isalpha():
        return _reject("gstin", value, "GSTIN characters 3-7 must be letters (PAN block)")
    if not text[7:11].isdigit() or not text[11].isalpha():
        return _reject("gstin", value, "GSTIN characters 8-12 must be 4 digits then a letter (PAN)")
    if text[12] in "0Z":
        return _reject("gstin", value, "GSTIN character 13 is the entity number: 1-9 or A-Z, not 0")
    state = text[:2]
    if state not in GSTIN_STATE_CODES:
        return _reject("gstin", value, f"{state!r} is not a GST state code")
    pan = text[2:12]
    if text[13] != "Z":
        return _reject("gstin", value, "GSTIN character 14 must be 'Z'")
    expected = gstin_check_char(text[:14])
    if text[14] != expected:
        return _reject(
            "gstin",
            value,
            f"GSTIN check character should be {expected!r}, got {text[14]!r}",
        )
    return Result(
        kind="gstin",
        ok=True,
        value=text,
        details={
            "state_code": state,
            "state": GSTIN_STATE_CODES[state],
            "pan": pan,
            "entity_number": text[12],
        },
    )


# --------------------------------------------------------------------------
# IFSC
# --------------------------------------------------------------------------

_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def _check_ifsc(value: Any) -> Result:
    text, error = _as_text("ifsc", value)
    if error is not None:
        return error
    assert text is not None
    if len(text) != 11:
        return _reject("ifsc", value, f"IFSC must be 11 characters, got {len(text)}")
    if not _IFSC_RE.match(text):
        return _reject("ifsc", value, "IFSC must look like AAAA0XXXXXX (5th character is '0')")
    return Result(
        kind="ifsc",
        ok=True,
        value=text,
        details={"bank_code": text[:4], "branch_code": text[5:]},
    )


# --------------------------------------------------------------------------
# UPI VPA
# --------------------------------------------------------------------------

UPI_KNOWN_PSPS: Mapping[str, str] = {
    "okhdfcbank": "HDFC Bank",
    "okicici": "ICICI Bank",
    "oksbi": "State Bank of India",
    "okaxis": "Axis Bank",
    "ybl": "PhonePe (Yes Bank)",
    "ibl": "PhonePe (ICICI Bank)",
    "axl": "PhonePe (Axis Bank)",
    "paytm": "Paytm Payments Bank",
    "apl": "Amazon Pay (Axis Bank)",
    "yapl": "Amazon Pay (Yes Bank)",
    "rapl": "Amazon Pay (RBL Bank)",
    "upi": "BHIM / NPCI",
    "sbi": "State Bank of India",
    "hdfcbank": "HDFC Bank",
    "icici": "ICICI Bank",
    "axisbank": "Axis Bank",
    "kotak": "Kotak Mahindra Bank",
    "yesbank": "Yes Bank",
    "barodampay": "Bank of Baroda",
    "idfcbank": "IDFC First Bank",
    "airtel": "Airtel Payments Bank",
    "jupiteraxis": "Jupiter (Axis Bank)",
    "fam": "FamPay (Axis Bank)",
    "slice": "Slice (Axis Bank)",
    "naviaxis": "Navi (Axis Bank)",
    "timecosmos": "CRED (Axis Bank)",
}

_VPA_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]{3,255}$")
_VPA_PSP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9.-]{1,63}$")


def _check_vpa(value: Any) -> Result:
    if not isinstance(value, str):
        return _reject("vpa", value, f"vpa must be a string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        return _reject("vpa", value, "vpa is empty")
    if text.count("@") != 1:
        return _reject("vpa", value, "A UPI ID must contain exactly one '@'")
    handle, _, psp = text.partition("@")
    if not _VPA_HANDLE_RE.match(handle):
        return _reject(
            "vpa",
            value,
            "The part before '@' must be 3-255 letters, digits, dots, hyphens or underscores",
        )
    if not _VPA_PSP_RE.match(psp):
        return _reject("vpa", value, "The part after '@' must be a PSP handle such as 'okhdfcbank'")
    psp_key = psp.lower()
    return Result(
        kind="vpa",
        ok=True,
        value=f"{handle.lower()}@{psp_key}",
        details={
            "handle": handle.lower(),
            "psp": psp_key,
            "provider": UPI_KNOWN_PSPS.get(psp_key),
            "known_psp": psp_key in UPI_KNOWN_PSPS,
        },
    )


# --------------------------------------------------------------------------
# Mobile
# --------------------------------------------------------------------------

_MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")
_MOBILE_PREFIX_RE = re.compile(r"^(?:\+?91|0)")


def _check_mobile(value: Any) -> Result:
    text, error = _as_text("mobile", value)
    if error is not None:
        return error
    assert text is not None
    digits = re.sub(r"[\s\-()]", "", text)
    if digits.startswith("+"):
        digits = digits[1:]
    elif digits.startswith("00"):
        digits = digits[2:]
    digits = _MOBILE_PREFIX_RE.sub("", digits, count=1) if len(digits) > 10 else digits
    if not digits.isdigit():
        return _reject("mobile", value, "A mobile number must contain only digits")
    if len(digits) != 10:
        return _reject("mobile", value, f"An Indian mobile number is 10 digits, got {len(digits)}")
    if not _MOBILE_RE.match(digits):
        return _reject("mobile", value, "An Indian mobile number starts with 6, 7, 8 or 9")
    return Result(
        kind="mobile",
        ok=True,
        value=f"+91{digits}",
        details={"national": digits, "e164": f"+91{digits}", "last4": digits[-4:]},
    )


# --------------------------------------------------------------------------
# PIN code
# --------------------------------------------------------------------------

PIN_REGIONS: Mapping[str, str] = {
    "1": "Delhi, Haryana, Punjab, Himachal Pradesh, Jammu and Kashmir, Ladakh",
    "2": "Uttar Pradesh, Uttarakhand",
    "3": "Rajasthan, Gujarat, Daman and Diu, Dadra and Nagar Haveli",
    "4": "Maharashtra, Madhya Pradesh, Chhattisgarh",
    "5": "Telangana, Andhra Pradesh, Karnataka",
    "6": "Tamil Nadu, Kerala, Puducherry, Lakshadweep",
    "7": "West Bengal, Odisha, Assam and the north-eastern states",
    "8": "Bihar, Jharkhand",
    "9": "Army Postal Service (APO/FPO)",
}

_PIN_RE = re.compile(r"^[1-9][0-9]{5}$")


def _check_pincode(value: Any) -> Result:
    text, error = _as_text("pincode", value)
    if error is not None:
        return error
    assert text is not None
    if not text.isdigit():
        return _reject("pincode", value, "A PIN code must contain only digits")
    if len(text) != 6:
        return _reject("pincode", value, f"A PIN code is 6 digits, got {len(text)}")
    if not _PIN_RE.match(text):
        return _reject("pincode", value, "A PIN code cannot start with 0")
    return Result(
        kind="pincode",
        ok=True,
        value=text,
        details={"region": PIN_REGIONS[text[0]], "circle": text[:2]},
    )


# --------------------------------------------------------------------------
# Vehicle registration
# --------------------------------------------------------------------------

VEHICLE_STATE_CODES: Mapping[str, str] = {
    "AN": "Andaman and Nicobar Islands",
    "AP": "Andhra Pradesh",
    "AR": "Arunachal Pradesh",
    "AS": "Assam",
    "BR": "Bihar",
    "CG": "Chhattisgarh",
    "CH": "Chandigarh",
    "DD": "Daman and Diu (pre-2020)",
    "DL": "Delhi",
    "DN": "Dadra and Nagar Haveli (pre-2020)",
    "GA": "Goa",
    "GJ": "Gujarat",
    "HP": "Himachal Pradesh",
    "HR": "Haryana",
    "JH": "Jharkhand",
    "JK": "Jammu and Kashmir",
    "KA": "Karnataka",
    "KL": "Kerala",
    "LA": "Ladakh",
    "LD": "Lakshadweep",
    "MH": "Maharashtra",
    "ML": "Meghalaya",
    "MN": "Manipur",
    "MP": "Madhya Pradesh",
    "MZ": "Mizoram",
    "NL": "Nagaland",
    "OD": "Odisha",
    "OR": "Odisha (pre-2012)",
    "PB": "Punjab",
    "PY": "Puducherry",
    "RJ": "Rajasthan",
    "SK": "Sikkim",
    "TN": "Tamil Nadu",
    "TR": "Tripura",
    "TS": "Telangana",
    "UA": "Uttarakhand (pre-2007)",
    "UK": "Uttarakhand",
    "UP": "Uttar Pradesh",
    "WB": "West Bengal",
}

_PLATE_STANDARD_RE = re.compile(r"^([A-Z]{2})([0-9]{1,2})([A-Z]{0,3})([0-9]{4})$")
_PLATE_BHARAT_RE = re.compile(r"^([0-9]{2})BH([0-9]{4})([A-Z]{1,2})$")


def _check_vehicle(value: Any) -> Result:
    text, error = _as_text("vehicle", value)
    if error is not None:
        return error
    assert text is not None
    plate = re.sub(r"[\s-]", "", text)

    bharat = _PLATE_BHARAT_RE.match(plate)
    if bharat:
        year, number, series = bharat.groups()
        return Result(
            kind="vehicle",
            ok=True,
            value=plate,
            details={
                "format": "bharat",
                "year": f"20{year}",
                "series": series,
                "number": number,
            },
        )

    standard = _PLATE_STANDARD_RE.match(plate)
    if not standard:
        return _reject(
            "vehicle",
            value,
            "Registration must look like MH12AB1234 or 22BH1234AA",
        )
    state, rto, series, number = standard.groups()
    if state not in VEHICLE_STATE_CODES:
        return _reject("vehicle", value, f"{state!r} is not a state or UT registration code")
    return Result(
        kind="vehicle",
        ok=True,
        value=plate,
        details={
            "format": "standard",
            "state_code": state,
            "state": VEHICLE_STATE_CODES[state],
            "rto_code": rto.zfill(2),
            "series": series,
            "number": number,
        },
    )


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

_CHECKS: dict[str, Callable[[Any], Result]] = {
    "aadhaar": _check_aadhaar,
    "pan": _check_pan,
    "gstin": _check_gstin,
    "ifsc": _check_ifsc,
    "vpa": _check_vpa,
    "mobile": _check_mobile,
    "pincode": _check_pincode,
    "vehicle": _check_vehicle,
}

_ALIASES: dict[str, str] = {
    "aadhar": "aadhaar",
    "aadhaar-number": "aadhaar",
    "gst": "gstin",
    "gst-number": "gstin",
    "upi": "vpa",
    "upi-id": "vpa",
    "phone": "mobile",
    "mobile-number": "mobile",
    "pin": "pincode",
    "pin-code": "pincode",
    "postal-code": "pincode",
    "rc": "vehicle",
    "registration": "vehicle",
    "number-plate": "vehicle",
}

KINDS: tuple[str, ...] = tuple(_CHECKS)


def _resolve(kind: str) -> str:
    if not isinstance(kind, str):
        raise TypeError(f"kind must be a string, got {type(kind).__name__}")
    key = kind.strip().lower()
    key = _ALIASES.get(key, key)
    if key not in _CHECKS:
        raise ValueError(f"unknown identifier kind {kind!r}; expected one of {', '.join(KINDS)}")
    return key


def validate(kind: str, value: Any) -> Result:
    """Validate ``value`` as identifier ``kind`` and never raise on bad input.

    ``kind`` accepts the names in :data:`KINDS` plus the aliases ``aadhar``,
    ``gst``, ``upi``, ``phone``, ``pin``, ``rc`` and a few hyphenated variants.
    An unknown ``kind`` raises :class:`ValueError` — that is a programming
    error, not user input.
    """
    return _CHECKS[_resolve(kind)](value)


def is_valid(kind: str, value: Any) -> bool:
    """Boolean shorthand for :func:`validate`."""
    return validate(kind, value).ok


def normalize(kind: str, value: Any) -> str:
    """Return the canonical form of ``value`` or raise :class:`ValidationError`."""
    result = validate(kind, value)
    if not result.ok:
        raise ValidationError(result.kind, value, result.reason or "invalid")
    return result.value  # type: ignore[return-value]


_MASKS: dict[str, Callable[[str], str]] = {
    "aadhaar": lambda digits: f"XXXX XXXX {digits[-4:]}",
    "pan": lambda text: f"XXXXX{text[5:]}",
    "mobile": lambda text: f"XXXXXX{text[-4:]}",
    "gstin": lambda text: f"{text[:2]}{'*' * 11}{text[-2:]}",
    "ifsc": lambda text: text,
    "pincode": lambda text: text,
    "vpa": lambda text: f"{text.split('@')[0][:2]}***@{text.split('@')[1]}",
    "vehicle": lambda text: f"{text[:-4]}****",
}


def mask(kind: str, value: Any) -> str:
    """Return a display-safe form of an identifier.

    Raises :class:`ValidationError` if the identifier is invalid, so a bad
    value can never be half-masked into something that looks legitimate.
    """
    key = _resolve(kind)
    canonical = normalize(key, value)
    return _MASKS[key](canonical)
