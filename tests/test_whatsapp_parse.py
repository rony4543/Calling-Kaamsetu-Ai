"""WhatsApp payload parsing — app/webhooks/whatsapp.py (_parse_message / _extract_messages).

Pure functions: given the JSON Meta sends, produce normalized InboundMessages.
No network, no FastAPI app needed.
"""

from app.webhooks.whatsapp import _extract_messages, _parse_message


class TestParseMessage:
    def test_text(self):
        m = _parse_message({"from": "9111", "id": "wamid.1", "type": "text",
                            "text": {"body": "hello"}})
        assert m is not None
        assert m.wa_id == "9111" and m.message_id == "wamid.1"
        assert m.type == "text" and m.text == "hello"
        assert m.interactive_id is None

    def test_interactive_button_reply(self):
        m = _parse_message({
            "from": "9111", "id": "wamid.2", "type": "interactive",
            "interactive": {"type": "button_reply",
                            "button_reply": {"id": "role_candidate", "title": "Find work"}},
        })
        assert m.type == "interactive"
        assert m.interactive_id == "role_candidate"
        assert m.text == "Find work"

    def test_interactive_list_reply(self):
        m = _parse_message({
            "from": "9111", "id": "wamid.3", "type": "interactive",
            "interactive": {"type": "list_reply",
                            "list_reply": {"id": "opt_a", "title": "Option A"}},
        })
        assert m.interactive_id == "opt_a"
        assert m.text == "Option A"

    def test_template_button(self):
        m = _parse_message({"from": "9111", "id": "wamid.4", "type": "button",
                            "button": {"text": "Yes"}})
        assert m.type == "button" and m.text == "Yes"

    def test_voice_note_normalized_to_voice(self):
        m = _parse_message({"from": "9111", "id": "wamid.5", "type": "audio",
                            "audio": {"id": "media-1", "mime_type": "audio/ogg"}})
        assert m.type == "voice"  # both "audio" and "voice" collapse to "voice"
        assert m.media_id == "media-1"
        assert m.media_mime == "audio/ogg"

    def test_image_with_caption(self):
        m = _parse_message({"from": "9111", "id": "wamid.6", "type": "image",
                            "image": {"id": "media-2", "mime_type": "image/jpeg",
                                      "caption": "my resume"}})
        assert m.media_id == "media-2"
        assert m.text == "my resume"

    def test_document(self):
        m = _parse_message({"from": "9111", "id": "wamid.7", "type": "document",
                            "document": {"id": "media-3", "mime_type": "application/pdf"}})
        assert m.media_id == "media-3"
        assert m.media_mime == "application/pdf"

    def test_missing_from_or_id_returns_none(self):
        assert _parse_message({"id": "wamid.8", "type": "text", "text": {"body": "x"}}) is None
        assert _parse_message({"from": "9111", "type": "text", "text": {"body": "x"}}) is None


class TestExtractMessages:
    def _envelope(self, messages, extra_change=None):
        changes = [{"value": {"messages": messages}}]
        if extra_change is not None:
            changes.append(extra_change)
        return {"entry": [{"changes": changes}]}

    def test_yields_each_message(self):
        body = self._envelope([
            {"from": "9111", "id": "wamid.a", "type": "text", "text": {"body": "hi"}},
            {"from": "9222", "id": "wamid.b", "type": "text", "text": {"body": "hire"}},
        ])
        out = list(_extract_messages(body))
        assert [m.message_id for m in out] == ["wamid.a", "wamid.b"]
        assert [m.wa_id for m in out] == ["9111", "9222"]

    def test_status_callbacks_yield_nothing(self):
        # Delivery/read receipts have `statuses`, not `messages`.
        body = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
        assert list(_extract_messages(body)) == []

    def test_empty_body(self):
        assert list(_extract_messages({})) == []

    def test_skips_unparseable_but_keeps_valid(self):
        body = self._envelope([
            {"type": "text", "text": {"body": "no from/id"}},          # dropped
            {"from": "9333", "id": "wamid.c", "type": "text", "text": {"body": "ok"}},
        ])
        out = list(_extract_messages(body))
        assert [m.message_id for m in out] == ["wamid.c"]
