from io import StringIO

from jianer.utils import screens


class _TTYStringIO(StringIO):
    def isatty(self):
        return True


class _PlainStringIO(StringIO):
    def isatty(self):
        return False


def test_clear_screen_writes_escape_sequence_for_tty(monkeypatch):
    stream = _TTYStringIO()
    monkeypatch.setattr(screens.sys, "stdout", stream)

    screens.clear_screen()

    assert stream.getvalue() == "\033[2J\033[H"


def test_clear_screen_skips_non_tty_output(monkeypatch):
    stream = _PlainStringIO()
    monkeypatch.setattr(screens.sys, "stdout", stream)

    screens.clear_screen()

    assert stream.getvalue() == ""
