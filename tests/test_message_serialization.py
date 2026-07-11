from jianer import events, segments


def test_gen_message_serializes_text_and_at_segments():
    message = events.gen_message({
        "message": [
            {"type": "at", "data": {"qq": "10001"}},
            {"type": "text", "data": {"text": " hello"}},
        ]
    })

    assert isinstance(message[0], segments.At)
    assert isinstance(message[1], segments.Text)
    assert message.get_sync() == [
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "text", "data": {"text": " hello"}},
    ]
