# /// script
# dependencies = ["pymupdf>=1.24", "pillow>=10", "pytest"]
# ///
"""Unit checks for the token and label parsing that caused wrong or missing highlights."""
import importlib.util, os
spec = importlib.util.spec_from_file_location("pe", os.path.join(os.path.dirname(__file__), "pdf_evidence.py"))
pe = importlib.util.module_from_spec(spec); spec.loader.exec_module(pe)


def test_num_key_variants():
    assert pe.num_key("−.24∗∗") == "-.24"      # minus sign and asterisk operator
    assert pe.num_key("0,14c") == ".14"        # decimal comma, footnote letter
    assert pe.num_key("1,940") == "1940"       # thousands separator
    assert pe.num_key("(0.07)***") == ".07"
    assert pe.num_key("−.48ᵇ") == "-.48"
    assert pe.num_key(".12**.34") is None      # two numbers glued together
    assert pe.num_key("Contact") is None


def test_first_number():
    assert pe.first_number("F(2, 303) = 36.68***") == "36.68"
    assert pe.first_number("n = 80") == "80"
    assert pe.first_number("2.86 (1.13)") == "2.86"


def test_labels():
    assert pe.enum_number("2. Social policies") == "2"
    assert "Intergroup contact" in pe.label_variants("Within-neighborhoods level: Intergroup contact")
    assert "Quality of contact" in pe.label_variants("2. Quality of contact (Time 2, below diagonal)")
    assert pe.is_numeric_quote("−.35*") and not pe.is_numeric_quote("N varies between 1,346 and 1,493.")


if __name__ == "__main__":
    import pytest, sys
    sys.exit(pytest.main([__file__, "-q"]))
