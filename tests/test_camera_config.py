from camera_engine.camera import focus_drive_label, match_choice, normalize_choice


def test_normalize_choice_strips_spaces() -> None:
    assert normalize_choice(" Far 1 ") == "far1"


def test_match_choice_prefers_exact() -> None:
    choices = ["Near 1", "Near 2", "Far 1", "Far 2"]
    assert match_choice(choices, "Far1") == "Far 1"
    assert match_choice(choices, "near 2") == "Near 2"


def test_match_choice_falls_back_to_partial() -> None:
    choices = ["Internal RAM", "Memory card"]
    assert match_choice(choices, "internal") == "Internal RAM"


def test_match_choice_returns_none_when_missing() -> None:
    assert match_choice(["ISO 100", "ISO 200"], "auto") is None


def test_focus_drive_label_clamps_size() -> None:
    assert focus_drive_label("near", 1) == "Near1"
    assert focus_drive_label("FAR", 3) == "Far3"
    assert focus_drive_label("near", 99) == "Near3"
    assert focus_drive_label("far", 0) == "Far1"
