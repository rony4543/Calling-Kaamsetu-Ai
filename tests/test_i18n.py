"""Language helpers + canned strings — app/utils/i18n.py."""

from app.utils import i18n


class TestDetectLanguage:
    def test_devanagari_is_hindi(self):
        assert i18n.detect_language("नमस्ते") == i18n.HI
        assert i18n.detect_language("मुझे काम चाहिए") == i18n.HI

    def test_latin_is_english(self):
        assert i18n.detect_language("hello there") == i18n.EN

    def test_empty_defaults_to_hindi(self):
        assert i18n.detect_language("") == i18n.HI
        assert i18n.detect_language(None) == i18n.HI

    def test_mixed_script_prefers_hindi(self):
        # Any Devanagari present => Hindi.
        assert i18n.detect_language("ok ठीक है") == i18n.HI


class TestCannedStrings:
    def test_known_key_per_language(self):
        assert i18n.t("role_candidate", i18n.EN) == "Find work"
        assert i18n.t("role_candidate", i18n.HI) == "काम चाहिए"

    def test_welcome_localized(self):
        assert i18n.t("welcome", i18n.EN).startswith("Hi! I'm Kaamsetu")
        assert "Kaamsetu" in i18n.t("welcome", i18n.HI)

    def test_missing_language_falls_back_to_hindi(self):
        # MWR has no explicit entry -> falls back to the HI string.
        assert i18n.t("welcome", i18n.MWR) == i18n.t("welcome", i18n.HI)

    def test_unknown_key_returns_empty_string(self):
        assert i18n.t("does_not_exist", i18n.EN) == ""

    def test_router_added_keys_exist(self):
        # Keys the router depends on must resolve in both languages.
        for key in ("profile_updated", "send_text_please", "fallback", "profile_live", "job_live"):
            assert i18n.t(key, i18n.EN)
            assert i18n.t(key, i18n.HI)


class TestLabel:
    def test_label_lookup_and_default(self):
        assert i18n.label(i18n.EN) == "English"
        assert i18n.label("zz") == "Hindi/Hinglish"  # unknown -> default
