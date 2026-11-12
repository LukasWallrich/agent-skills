# /// script
# dependencies = ["pymupdf>=1.24", "pillow>=10", "pytest"]
# ///
"""Checks for the parsing and location rules that caused wrong, missing or overconfident
highlights. Run: uv run test_pdf_evidence.py"""
import importlib.util, os, shutil
import pytest
spec = importlib.util.spec_from_file_location("pe", os.path.join(os.path.dirname(__file__), "pdf_evidence.py"))
pe = importlib.util.module_from_spec(spec); spec.loader.exec_module(pe)


def test_num_key_variants():
    assert pe.num_key("−.24∗∗") == "-.24"      # minus sign and asterisk operator
    assert pe.num_key("0,14c") == ".14"        # decimal comma, footnote letter
    assert pe.num_key("1,940") == "1940"       # thousands separator
    assert pe.num_key("0,123") == ".123"       # but a leading zero makes it a decimal comma
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
    assert pe.simple("α") != pe.simple("β")


# ---------- location on synthetic word lists ----------
def w(t, x, y, width=None, h=10):
    return {"t": t, "u": [x, y, x + (width or max(8, len(t) * 5)), y + h]}


def value(words, quote=".35", row="Alpha", col="N", **kw):
    return pe.locate_value(pe.lines_of(words), {"quote": quote, "row": row, "col": col}, **kw)


def test_value_in_next_row_is_not_a_cell():
    r = value([w("N", 200, 50), w("Alpha", 20, 100), w(".90", 200, 100), w("Beta", 20, 114), w(".35", 200, 114)])
    assert r["status"] != "cell"


def test_value_in_next_column_is_not_a_cell():
    r = value([w("Target", 200, 50, 35), w("Other", 245, 50), w("Alpha", 20, 100), w(".90", 200, 100), w(".35", 245, 100)], col="Target")
    assert r["status"] == "ambiguous"


def test_greek_headers_stay_distinct():
    r = value([w("α", 200, 50), w("β", 250, 50), w("Alpha", 20, 100), w(".35", 200, 100), w(".90", 250, 100)], col="β")
    assert r["status"] != "cell"
    r = value([w("α", 200, 50), w("β", 250, 50), w("Alpha", 20, 100), w(".35", 200, 100), w(".90", 250, 100)], col="α")
    assert r["status"] == "cell" and r["boxes"][0][0] == 200


def test_row_qualifier_is_respected():
    ws = [w("N", 200, 50), w("Contact", 20, 100), w("(Time", 60, 100), w("1)", 87, 100), w(".35", 200, 100),
          w("Contact", 20, 150), w("(Time", 60, 150), w("2)", 87, 150), w(".90", 200, 150)]
    assert value(ws, row="Contact (Time 2)")["status"] == "not_found"
    assert value(ws, quote=".90", row="Contact (Time 2)")["status"] == "cell"


def test_repeated_row_holding_the_value_twice_is_ambiguous():
    r = value([w("N", 200, 50), w("Alpha", 20, 100), w(".35", 200, 100), w("Alpha", 20, 150), w(".35", 200, 150)])
    assert r["status"] == "ambiguous" and len(r["cands"]) == 2


def test_lost_sign_or_punctuation_is_flagged_for_verification():
    assert value([w("N", 200, 50), w("Alpha", 20, 100), w(".35", 200, 100)], quote="-.35")["verify"]
    assert value([w("N", 200, 50), w("Alpha", 20, 100), w("35", 200, 100)], ocr=True)["verify"]
    assert not value([w("N", 200, 50), w("Alpha", 20, 100), w(".35", 200, 100)])["verify"]


def passage(text, quote, twice=False):
    ws = [w(t, 20 + i * 60, 100) for i, t in enumerate(text.split())]
    if twice:
        ws += [w(t, 20 + i * 60, 150) for i, t in enumerate(text.split())]
    return pe.locate_text(pe.lines_of(ws), quote)


def test_passages_differing_in_numbers_or_negation_are_not_matches():
    assert passage("The effect was significant.", "The effect was not significant.")["status"] == "unverified"
    assert passage("The sample included 180 participants.", "The sample included 1800 participants.")["status"] == "unverified"
    assert passage("The sample included 180 participants.", "The sample included 180 participants.")["status"] == "text"


def test_repeated_passage_is_ambiguous_with_candidates():
    r = passage("Strongly agree", "Strongly agree", twice=True)
    assert r["status"] == "ambiguous" and len(r["cands"]) == 2


# ---------- end to end on generated PDFs ----------
def make_pdf(path, pages, prose=True):
    d = pe.fitz.open()
    for entries in pages:
        p = d.new_page(width=400, height=500)
        if prose:
            p.insert_textbox(pe.fitz.Rect(20, 300, 380, 480), "Unrelated explanatory prose. " * 15, fontsize=10)
        for text, x, y in entries:
            p.insert_text((x, y), text, fontsize=10)
    d.save(path)
    d.close()


def run(tmp_path, item, written=None):
    out = tmp_path / "out"; out.mkdir(exist_ok=True)
    return pe.process({"pages_tolerance": 0, **item}, str(out), None, None, written)


def test_shared_img_name_never_overwritten_by_a_different_crop(tmp_path):
    pdf = tmp_path / "shared.pdf"
    make_pdf(pdf, [[("Alpha", 20, 100), (".35", 200, 100)], [("Beta", 20, 100), (".90", 200, 100)]])
    base = {"pdf": str(pdf), "page": 1, "clip": [0, 50, 350, 150], "img": "shared.jpg", "rotate": 0, "pages_tolerance": 1}
    written = {}
    a = run(tmp_path, {**base, "id": "a", "quote": ".35", "row": "Alpha"}, written)
    b = run(tmp_path, {**base, "id": "b", "quote": ".90", "row": "Beta"}, written)
    assert a["img"] == "shared.jpg" and b["img"] != "shared.jpg"


def test_partly_clipped_passage_is_outside_clip(tmp_path):
    pdf = tmp_path / "partial.pdf"
    make_pdf(pdf, [[("The sample included", 20, 100), ("180 participants.", 20, 120)]])
    r = run(tmp_path, {"id": "p", "pdf": str(pdf), "page": 1, "quote": "The sample included 180 participants.",
                       "clip": [0, 80, 350, 105]})
    assert r["status"] == "outside_clip"


def test_sparse_text_layer_is_used(tmp_path, monkeypatch):
    pdf = tmp_path / "sparse.pdf"
    make_pdf(pdf, [[("N", 200, 50), ("Alpha", 20, 100), (".35", 200, 100)]], prose=False)
    monkeypatch.setattr(pe.shutil, "which", lambda name: None)  # no Tesseract
    r = run(tmp_path, {"id": "s", "pdf": str(pdf), "page": 1, "quote": ".35", "row": "Alpha", "col": "N"})
    assert r["status"] == "cell" and not r["review"]


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs Tesseract")
def test_missing_minus_is_checked_by_ocr(tmp_path):
    pdf = tmp_path / "sign.pdf"
    make_pdf(pdf, [[("N", 200, 50), ("Alpha", 20, 100), (".35", 200, 100)]])
    r = run(tmp_path, {"id": "s", "pdf": str(pdf), "page": 1, "quote": "-.35", "row": "Alpha", "col": "N"})
    assert r["status"] == "unverified" and r["review"]


def test_choose_selects_a_candidate(tmp_path):
    pdf = tmp_path / "rep.pdf"
    make_pdf(pdf, [[("N", 200, 50), ("Alpha", 20, 100), (".35", 200, 100), ("Alpha", 20, 150), (".35", 200, 150)]])
    item = {"id": "c", "pdf": str(pdf), "page": 1, "quote": ".35", "row": "Alpha", "col": "N"}
    r = run(tmp_path, item)
    assert r["status"] == "ambiguous" and len(r["candidates"]) == 2
    r2 = run(tmp_path, {**item, "choose": 2})
    assert r2["status"] == "picked" and r2["boxes"][0] == r["candidates"][1][0]


def test_indented_neighbouring_row_is_foreign():
    r = value([w("N", 200, 50), w("Alpha", 20, 100), w(".90", 200, 100), w("Beta", 40, 114), w(".35", 200, 114)])
    assert r["status"] != "cell"


def test_header_qualifier_beats_a_nearer_stripped_match():
    ws = [w("Target", 200, 30, 30), w("(Time", 232, 30), w("2)", 258, 30), w("Target", 300, 50, 30), w("(Time", 332, 50), w("1)", 358, 50),
          w("Alpha", 20, 100), w(".35", 210, 100), w(".90", 310, 100)]
    r = value(ws, col="Target (Time 2)")
    assert r["status"] == "cell" and r["boxes"][0][0] == 210


def test_missing_label_qualifier_is_noted():
    ws = [w("N", 200, 50), w("Contact", 20, 100), w("(Time", 60, 100), w("1)", 87, 100), w(".35", 200, 100)]
    r = value(ws, row="Contact (Time 2)")
    assert any("qualifier" in n for n in r["notes"])


def test_truncated_window_does_not_evade_negation():
    assert passage("There was not a significant effect", "There was a significant effect")["status"] == "unverified"


def test_leading_decimal_point_counts():
    assert passage("The correlation was 35 overall", "The correlation was .35 overall")["status"] == "unverified"


def test_ambiguous_stated_page_is_kept(tmp_path):
    pdf = tmp_path / "amb.pdf"
    make_pdf(pdf, [[("Strongly agree", 20, 100), ("Strongly agree", 20, 150)], [("Strongly agree", 20, 100)]])
    r = run(tmp_path, {"id": "a", "pdf": str(pdf), "page": 1, "quote": "Strongly agree", "pages_tolerance": 1})
    assert r["status"] == "ambiguous" and r["page"] == 1


def test_box_crossing_the_clip_edge_is_outside_clip(tmp_path):
    pdf = tmp_path / "edge.pdf"
    make_pdf(pdf, [[("N", 200, 50), ("Alpha", 20, 100), (".35", 200, 100)]])
    r = run(tmp_path, {"id": "e", "pdf": str(pdf), "page": 1, "quote": ".35", "row": "Alpha", "col": "N", "clip": [0, 40, 205, 120]})
    assert r["status"] == "outside_clip"


def test_rerun_of_one_item_keeps_a_shared_image(tmp_path, monkeypatch):
    pdf = tmp_path / "shared.pdf"
    make_pdf(pdf, [[("Alpha", 20, 100), (".35", 200, 100)], [("Beta", 20, 100), (".90", 200, 100)]])
    base = {"pdf": str(pdf), "page": 1, "clip": [0, 50, 350, 150], "img": "shared.jpg", "rotate": 0}
    a, b = {**base, "id": "a", "quote": ".35", "row": "Alpha"}, {**base, "id": "b", "quote": ".90", "row": "Beta"}
    out = tmp_path / "out"
    import json, sys
    def main(items):
        (tmp_path / "items.json").write_text(json.dumps(items))
        monkeypatch.setattr(sys, "argv", ["pe", str(tmp_path / "items.json"), "--out", str(out), "--results", str(tmp_path / "r.json")])
        pe.main()
        return json.loads((tmp_path / "r.json").read_text())
    main([a, b])
    before = (out / "shared.jpg").read_bytes()
    assert main([b])[0]["img"] != "shared.jpg"
    assert (out / "shared.jpg").read_bytes() == before


def test_row_sharing_a_word_is_not_a_continuation():
    r = value([w("N", 200, 50), w("Contact", 20, 100), w("quality", 60, 100), w(".90", 200, 100),
               w("Contact", 20, 114), w("quantity", 60, 114), w(".35", 200, 114)], row="Contact quality")
    assert r["status"] != "cell"


def test_wrapped_label_continuation_is_not_foreign():
    r = value([w("N", 200, 50), w("Interactions", 20, 100), w("with", 85, 100), w("elderly", 110, 100),
               w("women", 20, 114), w(".35", 200, 114)], row="Interactions with elderly women")
    assert r["status"] == "cell"


def test_second_negated_near_miss_still_blocks():
    ws = [w(t, 20 + i * 60, 100) for i, t in enumerate("There was not a significant effect".split())]
    ws += [w(t, 20 + i * 60, 150) for i, t in enumerate("There was no significant effect".split())]
    assert pe.locate_text(pe.lines_of(ws), "There was a significant effect")["status"] != "text"


def test_exact_header_beats_a_nearer_fuzzy_one():
    ws = [w("Quality", 200, 30), w("(Time", 240, 30), w("2)", 266, 30), w("Quality", 300, 50), w("(Time", 340, 50), w("1)", 366, 50),
          w("Alpha", 20, 100), w(".35", 210, 100), w(".90", 310, 100)]
    r = value(ws, col="Quality (Time 2)")
    assert r["status"] == "cell" and r["boxes"][0][0] == 210


def test_colon_grouped_header_resolves_to_its_group():
    ws = [w("Low", 200, 20), w("group", 220, 20), w("High", 300, 20), w("group", 325, 20),
          w("M", 200, 40), w("SD", 240, 40), w("M", 300, 40), w("SD", 340, 40),
          w("Alpha", 20, 100), w(".10", 200, 100), w(".20", 240, 100), w(".30", 300, 100), w(".35", 340, 100)]
    r = value(ws, col="High group: SD")
    assert r["status"] == "cell" and r["boxes"][0][0] == 340


def test_alternate_image_name_is_protected_too(tmp_path):
    pdf = tmp_path / "s.pdf"
    make_pdf(pdf, [[("Alpha", 20, 100), (".35", 200, 100)]])
    base = {"pdf": str(pdf), "page": 1, "img": "x.jpg", "rotate": 0}
    written = {}
    run(tmp_path, {**base, "id": "a", "quote": ".35", "row": "Alpha", "clip": [0, 50, 350, 150]}, written)
    b = run(tmp_path, {**base, "id": "b", "quote": ".35", "row": "Alpha", "clip": [0, 60, 350, 150]}, written)
    c = run(tmp_path, {**base, "id": "b", "quote": ".35", "row": "Alpha", "clip": [0, 70, 350, 150]}, written)
    assert len({"x.jpg", b["img"], c["img"]}) == 3


@pytest.mark.skipif(not shutil.which("tesseract"), reason="needs Tesseract")
def test_choose_keeps_value_verification(tmp_path):
    pdf = tmp_path / "rep.pdf"
    make_pdf(pdf, [[("N", 200, 50), ("Alpha", 20, 100), (".35", 200, 100), ("Alpha", 20, 150), (".35", 200, 150)]])
    r = run(tmp_path, {"id": "c", "pdf": str(pdf), "page": 1, "quote": "-.35", "row": "Alpha", "col": "N", "choose": 1})
    assert r["status"] == "unverified"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
