import json
from pathlib import Path

from nowhere import thanks


def test_default_password_and_signed_session(monkeypatch):
    monkeypatch.delenv("THANKS_PASSWORD", raising=False)
    monkeypatch.delenv("THANKS_SESSION_SECRET", raising=False)

    assert thanks.password_matches("20250226")
    assert not thanks.password_matches("mianmian-preview")
    assert thanks.verify_session_token(thanks.create_session_token())
    assert not thanks.verify_session_token("0.not-a-valid-signature")


def test_letter_content_accepts_optional_presentation_metadata(monkeypatch):
    configured = {
        "eyebrow": "A PRIVATE LETTER",
        "title": "Hi Joyce,",
        "paragraphs": ["First paragraph.", "Closing line.", "(A small aside.)"],
        "signoff": "",
        "signature": "Eira",
        "highlights": ["First paragraph."],
        "closing_start": 1,
        "aside_index": 2,
    }
    monkeypatch.setenv("THANKS_LETTER_JSON", json.dumps(configured))

    assert thanks.letter_content() == configured


def test_invalid_presentation_metadata_falls_back(monkeypatch):
    configured = {
        "eyebrow": "A PRIVATE LETTER",
        "title": "Hi Joyce,",
        "paragraphs": ["One paragraph."],
        "signoff": "",
        "signature": "Eira",
        "highlights": "not-a-list",
    }
    monkeypatch.setenv("THANKS_LETTER_JSON", json.dumps(configured))

    assert thanks.letter_content()["title"] == "写给你"


def test_gratitude_assets_keep_private_copy_out_of_source():
    asset_dir = Path(__file__).parents[1] / "nowhere" / "static" / "thanks"
    html = (asset_dir / "index.html").read_text(encoding="utf-8")
    script = (asset_dir / "app.js").read_text(encoding="utf-8")

    assert 'id="opening-transition"' in html
    assert 'class="letter-ticket"' in html
    assert "letter.highlights" in script
    assert "closing-aside" in script
    assert "谢谢你给予我的一切帮助和关心" not in html + script
