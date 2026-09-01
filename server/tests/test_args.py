"""Tests for args.py: CLI argument parsing (parse, has, reset)."""

import pytest

from args import Args, parse, has, reset


@pytest.fixture(autouse=True)
def _clear_parse_cache():
    import args
    args._ARGS_PARSED = False
    args.ARGS = Args()
    yield
    import args
    args._ARGS_PARSED = False
    args.ARGS = Args()


class TestParseDefaults:
    def test_no_flags_defaults_all_false(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.no_speech is False
        assert a.no_forge is False
        assert a.test_mode is False
        assert a.auto_image is False
        assert a.voices is False
        assert a.persona is None
        assert a.raw == []


class TestParseFlags:
    def test_no_speech(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--no-speech"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.no_speech is True

    def test_no_forge(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--no-forge"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.no_forge is True

    def test_test(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--test"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.test_mode is True

    def test_auto_image(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--auto-image"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.auto_image is True

    def test_voices(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--voices"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.voices is True

    def test_persona_short(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--persona", "Alice"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.persona == "Alice"

    def test_persona_with_spaces(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--persona", "Victorian Lady"]
            a = parse()
        finally:
            sys.argv = _saved
        assert a.persona == "Victorian Lady"

    def test_unknown_flags_captured_in_raw(self):
        """--bogus is an unknown option; value and positional are consumed by the
        raw positional (nargs='*'), so only --bogus appears in ARGS.raw (which
        captures the unknown list from parse_known_args)."""
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--bogus", "value", "positional"]
            a = parse()
        finally:
            sys.argv = _saved
        # parse_known_args puts --bogus in unknown; value/positional
        # fill the raw positional; ARGS.raw = unknown = ['--bogus']
        assert "--bogus" in a.raw
        assert len(a.raw) == 1


class TestParseCaching:
    def test_parse_caches_result(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--no-speech"]
            first = parse()
            second = parse()
        finally:
            sys.argv = _saved
        assert first is second
        assert first.no_speech is True


class TestHas:
    def test_has_true_when_substring_present(self):
        import args as _a
        _a.ARGS = Args(_raw_argv=["alice.py", "--pytest-run", "test_file.py"])
        assert has("pytest") is True

    def test_has_false_when_no_match(self):
        import args as _a
        _a.ARGS = Args(_raw_argv=["alice.py", "--no-speech"])
        assert has("pytest") is False

    def test_has_multiple_needles_first_matches(self):
        import args as _a
        _a.ARGS = Args(_raw_argv=["alice.py", "--test-mode"])
        assert has("pytest", "test") is True

    def test_has_multiple_needles_none_match(self):
        import args as _a
        _a.ARGS = Args(_raw_argv=["alice.py", "--no-speech"])
        assert has("pytest", "test", "unittest") is False

    def test_has_searches_each_token(self):
        import args as _a
        _a.ARGS = Args(_raw_argv=["alice.py", "some-pytest-helper"])
        assert has("pytest") is True


class TestReset:
    def test_reset_clears_cached_args(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--no-speech"]
            parse()
            reset()
            import args
            assert args.ARGS.no_speech is False
            assert args._ARGS_PARSED is False
        finally:
            sys.argv = _saved

    def test_reset_allows_reparse_with_newargv(self):
        import sys
        _saved = sys.argv
        try:
            sys.argv = ["alice.py", "--no-speech"]
            parse()
            sys.argv = ["alice.py", "--no-forge"]
            reset()
            a = parse()
        finally:
            sys.argv = _saved
        assert a.no_speech is False
        assert a.no_forge is True
