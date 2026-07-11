from jianer import events, segments


STANDARD_SEGMENTS = {
    "text": (segments.Text, {"text": "hello"}),
    "stream": (segments.StreamTest, {"text": "hello"}),
    "image": (segments.Image, {"file": "https://example.test/image.png"}),
    "at": (segments.At, {"qq": "10001"}),
    "reply": (segments.Reply, {"id": "42"}),
    "face": (segments.Faces, {"id": "14"}),
    "record": (segments.Record, {"file": "https://example.test/audio.mp3"}),
    "video": (segments.Video, {"file": "https://example.test/video.mp4"}),
    "poke": (segments.Poke, {"type": "1", "id": "2"}),
    "contact": (segments.Contact, {"type": "qq", "id": "10001"}),
    "forward": (segments.Forward, {"id": "forward-1"}),
    "node": (segments.Node, {"user_id": "10001", "nickname": "tester", "content": {}}),
    "longmsg": (segments.LongMessage, {"id": "long-1"}),
    "json": (segments.Json, {"data": {"key": "value"}}),
    "mface": (segments.MarketFace, {"face_id": "face", "tab_id": "tab", "key": "key"}),
    "dice": (segments.Dice, {}),
    "rps": (segments.Rps, {}),
    "music": (segments.Music, {"type": "qq"}),
}


def test_all_standard_segments_are_registered_and_deserialized():
    assert set(STANDARD_SEGMENTS) <= set(segments.message_types)
    assert all(segments.message_types[name]["type"] is segment_type
               for name, (segment_type, _) in STANDARD_SEGMENTS.items())

    message = events.gen_message({
        "message": [
            {"type": name, "data": data}
            for name, (_, data) in STANDARD_SEGMENTS.items()
        ]
    })

    assert [type(segment) for segment in message] == [
        segment_type for segment_type, _ in STANDARD_SEGMENTS.values()
    ]


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
