"""Tests for india_ids."""

import pytest

import india_ids
from india_ids import (
    KINDS,
    GSTIN_STATE_CODES,
    PAN_HOLDER_TYPES,
    PIN_REGIONS,
    UPI_KNOWN_PSPS,
    VEHICLE_STATE_CODES,
    Result,
    ValidationError,
    aadhaar_check_digit,
    gstin_check_char,
    is_valid,
    mask,
    normalize,
    validate,
)

# Real, published sample identifiers used in GST and UPI documentation.
GSTIN_MAHARASHTRA = "27AAPFU0939F1ZV"
GSTIN_KARNATAKA = "29AAGCB7383J1Z4"


def aadhaar(prefix: str = "23456789012") -> str:
    return prefix + aadhaar_check_digit(prefix)


# --------------------------------------------------------------------------
# dispatch, Result and error plumbing
# --------------------------------------------------------------------------


def test_kinds_lists_every_supported_identifier():
    assert KINDS == ("aadhaar", "pan", "gstin", "ifsc", "vpa", "mobile", "pincode", "vehicle")


@pytest.mark.parametrize("kind", KINDS)
def test_validate_returns_a_result_for_every_kind(kind):
    result = validate(kind, "nonsense")
    assert isinstance(result, Result)
    assert result.kind == kind
    assert result.ok is False
    assert result.value is None
    assert result.reason


@pytest.mark.parametrize(
    "alias,kind",
    [
        ("aadhar", "aadhaar"),
        ("AADHAAR", "aadhaar"),
        ("gst", "gstin"),
        ("upi", "vpa"),
        ("upi-id", "vpa"),
        ("phone", "mobile"),
        ("pin", "pincode"),
        ("pin-code", "pincode"),
        ("postal-code", "pincode"),
        ("rc", "vehicle"),
        ("registration", "vehicle"),
        ("number-plate", "vehicle"),
        (" PAN ", "pan"),
    ],
)
def test_aliases_resolve_to_the_same_validator(alias, kind):
    result = validate(alias, "nonsense")
    assert result.kind == kind


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError, match="unknown identifier kind"):
        validate("passport", "X1234567")


def test_kind_must_be_a_string():
    with pytest.raises(TypeError):
        validate(None, "ABC")


def test_result_is_truthy_only_when_valid():
    assert bool(validate("pincode", "110001")) is True
    assert bool(validate("pincode", "010001")) is False


def test_result_unwrap_returns_value_or_raises():
    assert validate("pincode", "110001").unwrap() == "110001"
    with pytest.raises(ValidationError):
        validate("pincode", "11000").unwrap()


def test_rejections_echo_the_original_input():
    assert validate("pincode", "abc").details["input"] == "abc"
    assert validate("pincode", 123).details["input"] == "123"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", [None, True, False, [], {}, 3.5, object()])
def test_non_string_inputs_are_rejected_not_raised(kind, value):
    result = validate(kind, value)
    assert result.ok is False
    assert "must be a string" in result.reason


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_blank_input_is_rejected(kind, value):
    result = validate(kind, value)
    assert result.ok is False
    assert "empty" in result.reason


def test_integers_are_accepted_as_text():
    assert validate("pincode", 110001).value == "110001"
    assert validate("mobile", 9876543210).value == "+919876543210"
    assert validate("aadhaar", int(aadhaar())).ok is True


def test_validation_error_carries_kind_value_and_reason():
    with pytest.raises(ValidationError) as excinfo:
        normalize("pan", "ABCD1234")
    error = excinfo.value
    assert error.kind == "pan"
    assert error.value == "ABCD1234"
    assert "10 characters" in error.reason
    assert str(error).startswith("pan: ")


def test_normalize_returns_the_canonical_value():
    assert normalize("pan", "abcpa1234f") == "ABCPA1234F"
    assert normalize("gstin", " 27aapfu0939f1zv ") == GSTIN_MAHARASHTRA
    assert normalize("vpa", "Krish.Sharma@OkHDFCBank") == "krish.sharma@okhdfcbank"


def test_is_valid_matches_validate():
    assert is_valid("ifsc", "HDFC0000001") is True
    assert is_valid("ifsc", "HDFC1000001") is False


# --------------------------------------------------------------------------
# Aadhaar
# --------------------------------------------------------------------------


def test_aadhaar_accepts_a_generated_number():
    number = aadhaar()
    result = validate("aadhaar", number)
    assert result.ok is True
    assert result.value == number
    assert result.details["last4"] == number[-4:]


@pytest.mark.parametrize(
    "formatted",
    [
        "2345 6789 0124",
        "2345-6789-0124",
        "  234567890124  ",
        "2345 6789-0124",
    ],
)
def test_aadhaar_separators_are_stripped(formatted):
    assert validate("aadhaar", formatted).value == "234567890124"


@pytest.mark.parametrize("prefix", ["23456789012", "98765432109", "45678901234", "20000000000"])
def test_aadhaar_check_digit_produces_a_valid_number(prefix):
    number = prefix + aadhaar_check_digit(prefix)
    assert validate("aadhaar", number).ok is True


def test_every_single_digit_typo_breaks_the_checksum():
    number = aadhaar()
    for position in range(12):
        for replacement in "0123456789":
            if replacement == number[position]:
                continue
            typo = number[:position] + replacement + number[position + 1 :]
            assert validate("aadhaar", typo).ok is False, typo


def test_adjacent_transposition_is_caught_when_digits_differ():
    number = aadhaar()
    swapped = number[:4] + number[5] + number[4] + number[6:]
    if swapped != number:
        assert validate("aadhaar", swapped).ok is False


@pytest.mark.parametrize("number", ["034567890124", "134567890124"])
def test_aadhaar_cannot_start_with_0_or_1(number):
    result = validate("aadhaar", number)
    assert result.ok is False
    assert "cannot start with 0 or 1" in result.reason


def test_aadhaar_length_is_enforced():
    assert "12 digits, got 11" in validate("aadhaar", "23456789012").reason
    assert "12 digits, got 13" in validate("aadhaar", "2345678901245").reason


def test_aadhaar_rejects_letters():
    result = validate("aadhaar", "23456789012A")
    assert result.ok is False
    assert "only digits" in result.reason


def test_aadhaar_check_digit_rejects_bad_prefixes():
    with pytest.raises(ValueError):
        aadhaar_check_digit("2345678901")
    with pytest.raises(ValueError):
        aadhaar_check_digit("2345678901X")
    with pytest.raises(ValueError):
        aadhaar_check_digit("03456789012")
    with pytest.raises(ValueError):
        aadhaar_check_digit(23456789012)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# PAN
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pan,holder",
    [
        ("ABCPD1234E", "Individual"),
        ("AAACR5055K", "Company"),
        ("AAFFR1234M", "Firm / limited liability partnership"),
        ("AAAHG1234L", "Hindu undivided family (HUF)"),
        ("AAATG1234J", "Trust"),
        ("AAALG1234K", "Local authority"),
        ("AAAJG1234M", "Artificial juridical person"),
        ("AAABG1234N", "Body of individuals (BOI)"),
        ("AAAGG1234P", "Government agency"),
        ("AAAAG1234Q", "Association of persons (AOP)"),
    ],
)
def test_every_pan_holder_type_is_accepted(pan, holder):
    result = validate("pan", pan)
    assert result.ok is True
    assert result.details["holder_type"] == holder


def test_pan_holder_type_table_matches_the_documented_codes():
    assert set(PAN_HOLDER_TYPES) == set("ABCFGHJLPT")


def test_pan_details_include_the_surname_initial():
    result = validate("pan", "abcpd1234e")
    assert result.value == "ABCPD1234E"
    assert result.details["holder_code"] == "P"
    assert result.details["surname_initial"] == "D"


@pytest.mark.parametrize("pan", ["ABCDX1234E", "ABCDZ1234E", "ABCDI1234E"])
def test_pan_rejects_unknown_holder_letters(pan):
    result = validate("pan", pan)
    assert result.ok is False
    assert "holder type" in result.reason


@pytest.mark.parametrize(
    "pan",
    [
        "ABCPD123E",  # 9 characters
        "ABCPD12345E",  # 11 characters
        "ABCP1234E",  # only 4 letters up front
        "ABCDE1234",  # no trailing letter
        "ABCDE12345",  # digit where a letter belongs
        "1BCPD1234E",  # digit where a letter belongs
        "ABCDE 1234E",  # internal space
    ],
)
def test_pan_shape_is_enforced(pan):
    assert validate("pan", pan).ok is False


def test_pan_reports_length_before_shape():
    assert "10 characters" in validate("pan", "ABCD").reason


# --------------------------------------------------------------------------
# GSTIN
# --------------------------------------------------------------------------


@pytest.mark.parametrize("gstin", [GSTIN_MAHARASHTRA, GSTIN_KARNATAKA])
def test_real_gstins_validate(gstin):
    result = validate("gstin", gstin)
    assert result.ok is True
    assert result.value == gstin


def test_gstin_is_normalised_from_lowercase_and_spaces():
    assert normalize("gstin", " 27aapfu0939f1zv\n") == GSTIN_MAHARASHTRA


def test_gstin_details_expose_state_pan_and_entity():
    result = validate("gstin", GSTIN_MAHARASHTRA)
    assert result.details == {
        "state_code": "27",
        "state": "Maharashtra",
        "pan": "AAPFU0939F",
        "entity_number": "1",
    }


def test_gstin_embedded_pan_is_itself_a_valid_pan():
    pan = validate("gstin", GSTIN_KARNATAKA).details["pan"]
    assert is_valid("pan", pan)


def test_gstin_check_char_round_trips():
    for state, pan, entity in [
        ("07", "AAACR5055K", "1"),
        ("24", "AAACC1206D", "2"),
        ("33", "AABCT1332L", "1"),
        ("09", "AACCG0527D", "3"),
    ]:
        body = f"{state}{pan}{entity}Z"
        gstin = body + gstin_check_char(body)
        assert validate("gstin", gstin).ok is True, gstin


def test_gstin_check_char_rejects_bad_input():
    with pytest.raises(ValueError):
        gstin_check_char("27AAPFU0939F1")
    with pytest.raises(ValueError):
        gstin_check_char("27AAPFU0939F1!")
    with pytest.raises(ValueError):
        gstin_check_char(None)  # type: ignore[arg-type]


def test_gstin_wrong_check_character_is_named_in_the_reason():
    wrong = GSTIN_MAHARASHTRA[:14] + "A"
    result = validate("gstin", wrong)
    assert result.ok is False
    assert "check character should be 'V'" in result.reason


def test_gstin_length_is_enforced():
    assert "15 characters" in validate("gstin", GSTIN_MAHARASHTRA[:14]).reason


def test_gstin_rejects_unknown_state_codes():
    body = "98" + GSTIN_MAHARASHTRA[2:14]
    result = validate("gstin", body + gstin_check_char(body))
    assert result.ok is False
    assert "'98' is not a GST state code" in result.reason


def test_gstin_requires_z_in_position_fourteen():
    body = GSTIN_MAHARASHTRA[:13] + "Y"
    result = validate("gstin", body + gstin_check_char(body))
    assert result.ok is False
    assert "must be 'Z'" in result.reason


def test_gstin_entity_number_cannot_be_zero():
    body = GSTIN_MAHARASHTRA[:12] + "0Z"
    result = validate("gstin", body + gstin_check_char(body))
    assert result.ok is False
    assert "entity number" in result.reason


def test_gstin_rejects_non_base36_characters():
    result = validate("gstin", "27AAPFU0939F1Z*")
    assert result.ok is False
    assert "character 15 must be a digit or upper-case letter" in result.reason


def test_gstin_state_block_must_be_numeric():
    result = validate("gstin", "A7AAPFU0939F1ZV")
    assert result.ok is False
    assert "characters 1-2 must be the numeric state code" in result.reason


def test_gstin_pan_block_shape_is_reported():
    result = validate("gstin", "27AAPFU93A9F1ZV")
    assert result.ok is False
    assert "characters 8-12" in result.reason


def test_gstin_state_table_has_the_expected_coverage():
    assert GSTIN_STATE_CODES["27"] == "Maharashtra"
    assert GSTIN_STATE_CODES["38"] == "Ladakh"
    assert GSTIN_STATE_CODES["99"] == "Centre Jurisdiction"
    assert len(GSTIN_STATE_CODES) == 40


# --------------------------------------------------------------------------
# IFSC
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ifsc",
    ["HDFC0000001", "ICIC0000123", "SBIN0RRMGB1", "UTIB0004567", "KKBK0000958"],
)
def test_ifsc_accepts_real_looking_codes(ifsc):
    assert validate("ifsc", ifsc).ok is True


def test_ifsc_is_normalised_to_upper_case():
    result = validate("ifsc", "hdfc0000001")
    assert result.value == "HDFC0000001"
    assert result.details == {"bank_code": "HDFC", "branch_code": "000001"}


def test_ifsc_fifth_character_must_be_zero():
    result = validate("ifsc", "HDFC1000001")
    assert result.ok is False
    assert "'0'" in result.reason


@pytest.mark.parametrize("ifsc", ["HDFC000001", "HDFC00000011", "HDF0000001", "HDFC00000-1"])
def test_ifsc_shape_is_enforced(ifsc):
    assert validate("ifsc", ifsc).ok is False


def test_ifsc_length_is_reported():
    assert "11 characters" in validate("ifsc", "HDFC0001").reason


# --------------------------------------------------------------------------
# UPI VPA
# --------------------------------------------------------------------------


def test_vpa_normalises_to_lower_case():
    result = validate("vpa", "Krish.Sharma@OkHDFCBank")
    assert result.value == "krish.sharma@okhdfcbank"
    assert result.details["handle"] == "krish.sharma"
    assert result.details["psp"] == "okhdfcbank"


@pytest.mark.parametrize("vpa", ["krish@okhdfcbank", "9876543210@ybl", "a_b-c.d@paytm"])
def test_vpa_accepts_common_handles(vpa):
    assert validate("vpa", vpa).ok is True


def test_vpa_recognises_known_psps():
    result = validate("vpa", "9876543210@ybl")
    assert result.details["known_psp"] is True
    assert result.details["provider"] == "PhonePe (Yes Bank)"


def test_vpa_accepts_an_unknown_but_well_formed_psp():
    result = validate("vpa", "krish@some-new-bank")
    assert result.ok is True
    assert result.details["known_psp"] is False
    assert result.details["provider"] is None


def test_known_psp_table_is_lower_case():
    assert all(key == key.lower() for key in UPI_KNOWN_PSPS)
    assert UPI_KNOWN_PSPS["okhdfcbank"] == "HDFC Bank"


@pytest.mark.parametrize("vpa", ["krish", "krish@", "@okhdfcbank", "a@b@c", "krish@ okhdfcbank"])
def test_vpa_shape_is_enforced(vpa):
    assert validate("vpa", vpa).ok is False


def test_vpa_rejects_short_handles():
    result = validate("vpa", "ab@okhdfcbank")
    assert result.ok is False
    assert "3-255" in result.reason


def test_vpa_psp_must_start_with_a_letter():
    result = validate("vpa", "krish@1bank")
    assert result.ok is False
    assert "PSP handle" in result.reason


def test_vpa_keeps_its_case_sensitivity_free_normalisation():
    assert normalize("vpa", "  ABC@OKAXIS ") == "abc@okaxis"


# --------------------------------------------------------------------------
# Mobile
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "9876543210",
        "+919876543210",
        "+91 98765 43210",
        "919876543210",
        "09876543210",
        "00919876543210",
        "(98765) 43210",
        "98765-43210",
        "  9876543210  ",
    ],
)
def test_mobile_accepts_common_spellings(number):
    result = validate("mobile", number)
    assert result.ok is True
    assert result.value == "+919876543210"


@pytest.mark.parametrize("leading", "6789")
def test_mobile_accepts_every_valid_leading_digit(leading):
    assert validate("mobile", leading + "876543210").ok is True


@pytest.mark.parametrize("number", ["5876543210", "4876543210", "3876543210", "2876543210"])
def test_mobile_rejects_invalid_leading_digits(number):
    result = validate("mobile", number)
    assert result.ok is False
    assert "starts with 6, 7, 8 or 9" in result.reason


def test_mobile_length_is_reported():
    assert "10 digits, got 9" in validate("mobile", "987654321").reason
    assert "10 digits, got 12" in validate("mobile", "91987654321000").reason


def test_mobile_rejects_letters():
    result = validate("mobile", "98765abcde")
    assert result.ok is False
    assert "only digits" in result.reason


def test_mobile_details_carry_national_and_e164_forms():
    details = validate("mobile", "+91 98765 43210").details
    assert details["national"] == "9876543210"
    assert details["e164"] == "+919876543210"
    assert details["last4"] == "3210"


# --------------------------------------------------------------------------
# PIN code
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pincode,region_start",
    [
        ("110001", "Delhi"),
        ("201301", "Uttar Pradesh"),
        ("302001", "Rajasthan"),
        ("400001", "Maharashtra"),
        ("500081", "Telangana"),
        ("600001", "Tamil Nadu"),
        ("700001", "West Bengal"),
        ("800001", "Bihar"),
        ("900001", "Army Postal Service"),
    ],
)
def test_pincode_region_is_derived_from_the_first_digit(pincode, region_start):
    result = validate("pincode", pincode)
    assert result.ok is True
    assert result.details["region"].startswith(region_start)
    assert result.details["circle"] == pincode[:2]


def test_pincode_region_table_covers_all_nine_regions():
    assert set(PIN_REGIONS) == set("123456789")


@pytest.mark.parametrize("pincode", ["010001", "000000", "12345", "1234567", "11000A"])
def test_pincode_shape_is_enforced(pincode):
    assert validate("pincode", pincode).ok is False


def test_pincode_leading_zero_has_its_own_message():
    assert "cannot start with 0" in validate("pincode", "010001").reason


def test_pincode_length_is_reported():
    assert "6 digits, got 5" in validate("pincode", "11000").reason


# --------------------------------------------------------------------------
# Vehicle registration
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "plate,expected",
    [
        ("MH12AB1234", "MH12AB1234"),
        ("MH 12 AB 1234", "MH12AB1234"),
        ("mh-12-ab-1234", "MH12AB1234"),
        ("DL3CAB1234", "DL3CAB1234"),
        ("KA05MJ1234", "KA05MJ1234"),
        ("TN10Z9999", "TN10Z9999"),
        ("KL01A1111", "KL01A1111"),
    ],
)
def test_standard_plates_are_normalised(plate, expected):
    result = validate("vehicle", plate)
    assert result.ok is True
    assert result.value == expected


def test_standard_plate_without_a_series():
    result = validate("vehicle", "DL 3 1234")
    assert result.ok is True
    assert result.details["series"] == ""


def test_standard_plate_details():
    result = validate("vehicle", "DL3CAB1234")
    assert result.details == {
        "format": "standard",
        "state_code": "DL",
        "state": "Delhi",
        "rto_code": "03",
        "series": "CAB",
        "number": "1234",
    }


def test_single_digit_rto_is_zero_padded():
    assert validate("vehicle", "MH2AB1234").details["rto_code"] == "02"


@pytest.mark.parametrize("plate", ["22BH1234AA", "22BH1234A", "07BH0001AB"])
def test_bharat_series_plates(plate):
    result = validate("vehicle", plate)
    assert result.ok is True
    assert result.details["format"] == "bharat"


def test_bharat_series_details():
    result = validate("vehicle", "22 BH 1234 AA")
    assert result.value == "22BH1234AA"
    assert result.details["year"] == "2022"
    assert result.details["number"] == "1234"
    assert result.details["series"] == "AA"


@pytest.mark.parametrize("plate", ["ZZ12AB1234", "XX01A0001"])
def test_unknown_state_codes_are_rejected(plate):
    result = validate("vehicle", plate)
    assert result.ok is False
    assert "not a state or UT registration code" in result.reason


@pytest.mark.parametrize(
    "plate",
    [
        "MH12AB123",  # 3-digit number
        "MH12AB12345",  # 5-digit number
        "MH12ABCD1234",  # 4-letter series
        "M12AB1234",  # single-letter state
        "MH12AB12X4",  # letter inside the number
        "MH1234",  # no number
    ],
)
def test_malformed_plates_are_rejected(plate):
    result = validate("vehicle", plate)
    assert result.ok is False
    assert "MH12AB1234" in result.reason


def test_legacy_state_codes_still_validate():
    for plate in ["OR02A1234", "UA07B1234", "DD02C1234"]:
        assert validate("vehicle", plate).ok is True


def test_vehicle_state_table_includes_union_territories():
    assert VEHICLE_STATE_CODES["DL"] == "Delhi"
    assert VEHICLE_STATE_CODES["CH"] == "Chandigarh"
    assert VEHICLE_STATE_CODES["LA"] == "Ladakh"


# --------------------------------------------------------------------------
# masking
# --------------------------------------------------------------------------


def test_aadhaar_mask_shows_only_the_last_four_digits():
    assert mask("aadhaar", aadhaar()) == "XXXX XXXX 0124"


def test_pan_mask_hides_the_first_five_characters():
    assert mask("pan", "ABCPD1234E") == "XXXXX1234E"


def test_mobile_mask_keeps_the_last_four_digits():
    assert mask("mobile", "+91 98765 43210") == "XXXXXX3210"


def test_gstin_mask_keeps_the_state_and_check_character():
    assert mask("gstin", GSTIN_MAHARASHTRA) == "27***********ZV"


def test_vpa_mask_keeps_two_characters_of_the_handle():
    assert mask("vpa", "krish.sharma@okhdfcbank") == "kr***@okhdfcbank"


def test_vehicle_mask_hides_the_number():
    assert mask("vehicle", "MH12AB1234") == "MH12AB****"


def test_ifsc_and_pincode_are_not_masked():
    assert mask("ifsc", "HDFC0000001") == "HDFC0000001"
    assert mask("pincode", "110001") == "110001"


def test_mask_accepts_aliases():
    assert mask("phone", "9876543210") == "XXXXXX3210"


@pytest.mark.parametrize("kind", KINDS)
def test_mask_never_leaks_a_rejected_value(kind):
    with pytest.raises(ValidationError):
        mask(kind, "definitely not an identifier")


def test_mask_rejects_unknown_kinds():
    with pytest.raises(ValueError):
        mask("passport", "X1234567")


# --------------------------------------------------------------------------
# module surface
# --------------------------------------------------------------------------


def test_public_names_are_all_defined():
    for name in india_ids.__all__:
        assert hasattr(india_ids, name), name


def test_module_has_no_third_party_imports():
    import ast
    import pathlib

    source = pathlib.Path(india_ids.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"__future__", "re", "dataclasses", "typing"}
