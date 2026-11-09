"""Runnable demo for india_ids: validate, normalise, explain, mask.

    python example.py
"""

from india_ids import (
    ValidationError,
    aadhaar_check_digit,
    gstin_check_char,
    is_valid,
    mask,
    normalize,
    validate,
)

SAMPLES = [
    ("aadhaar", "2345 6789 0124"),
    ("pan", "abcpd1234e"),
    ("gstin", " 27aapfu0939f1zv "),
    ("ifsc", "hdfc0000001"),
    ("vpa", "Krish.Sharma@OkHDFCBank"),
    ("mobile", "+91 98765 43210"),
    ("pincode", "560001"),
    ("vehicle", "ka-05-mj-1234"),
]

REJECTS = [
    ("aadhaar", "2345 6789 0125"),
    ("pan", "ABCD1234"),
    ("gstin", "27AAPFU0939F1ZA"),
    ("ifsc", "HDFC1000001"),
    ("vpa", "krish@"),
    ("mobile", "5876543210"),
    ("pincode", "010001"),
    ("vehicle", "ZZ12AB1234"),
]


def section(title):
    print(title)
    print("-" * len(title))


def show_valid():
    section("validate: canonical value plus what can be kept")
    for kind, raw in SAMPLES:
        result = validate(kind, raw)
        print(f"  {kind:<8} {raw!r}")
        print(f"           -> {result.value!r}  ok={result.ok}")
        for key, value in result.details.items():
            print(f"              {key}: {value}")
    print()


def show_rejects():
    section("rejections: the reason is a value, never an exception")
    for kind, raw in REJECTS:
        result = validate(kind, raw)
        print(f"  {kind:<8} {raw!r} -> {result.reason}")
    print("  validate() returns a falsy Result; only normalize() raises:")
    try:
        normalize("pan", "ABCD1234")
    except ValidationError as error:
        print(f"    ValidationError: {error}")
    print()


def show_aadhaar():
    section("aadhaar: the 12th digit is a Verhoeff check digit")
    for prefix in ["23456789012", "98765432109", "45678901234"]:
        check = aadhaar_check_digit(prefix)
        print(f"  {prefix} + {check} -> valid={is_valid('aadhaar', prefix + check)}")
    typo = "23456789012" + aadhaar_check_digit("23456789012")
    broken = typo[:7] + ("0" if typo[7] != "0" else "1") + typo[8:]
    print(f"  {typo}  valid")
    print(f"  {broken}  one digit changed -> {validate('aadhaar', broken).reason}")
    print()


def show_gstin():
    section("gstin: state code, embedded PAN, entity number, check character")
    result = validate("gstin", "27AAPFU0939F1ZV")
    print(f"  27AAPFU0939F1ZV -> {result.ok}")
    for key, value in result.details.items():
        print(f"    {key}: {value}")
    body = "29AAGCB7383J1Z"
    print(f"  checksum for {body}: {gstin_check_char(body)}")
    print(f"  so {body}V is valid={is_valid('gstin', body + gstin_check_char(body))}")
    print()


def show_mask():
    section("mask: what to put in a log line or an on-screen confirmation")
    for kind, raw in SAMPLES:
        print(f"  {kind:<8} {mask(kind, raw)}")
    print("  Aadhaar is masked to the last four digits, as it should be.")
    print()


def show_dispatch():
    section("dispatch: one branch for every identifier kind")
    for kind in ("pin", "phone", "upi", "rc"):
        print(f"  alias {kind!r:<7} -> {validate(kind, '110001').kind}")
    print("  unknown kinds are a programming error, not user input:")
    try:
        validate("passport", "X1234567")
    except ValueError as error:
        print(f"    ValueError: {error}")
    print()


if __name__ == "__main__":
    show_valid()
    show_rejects()
    show_aadhaar()
    show_gstin()
    show_mask()
    show_dispatch()
