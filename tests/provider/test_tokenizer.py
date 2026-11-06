"""Golden counts pin the framework's tokenizer, independently of model routing."""
import pytest

from provider.tokenizer import count_text, count_value
from run.cards import CardMeter, estimate_tokens
from run.context import estimate_text_tokens


@pytest.mark.parametrize(("text", "tokens"), [
    ("", 0),
    ("hello world", 2),
    ("\u4f60\u597d\uff0c\u4e16\u754c\uff01", 4),
    ("def hello():\n    return 42\n", 8),
    ("\U0001f642\U0001f680", 4),
])
def test_pinned_deepseek_counts(text, tokens):
    assert count_text(text) == estimate_tokens(text) == estimate_text_tokens(text) == tokens


def test_model_switch_keeps_history_measurement_and_tokenizer_identity(tmp_path):
    from run.cards import CardStore

    first = CardMeter.for_model("deepseek", "deepseek-chat")
    second = CardMeter.for_model("other-gateway", "other-model")
    assert first.key == second.key
    assert first.calibration_scope != second.calibration_scope
    (tmp_path / "users" / "alice").mkdir(parents=True)
    store = CardStore(tmp_path, "alice", meter=first)
    store.ensure_session("web", "s1")
    store.append("web", "s1", "run", "user_text", {"text": "hello world"})
    before = store.snapshot("web", "s1")
    store.meter = second
    assert store.remeter("web", "s1") == before


def test_structured_count_is_canonical():
    assert count_value({"b": "hello", "a": 1}) == count_value({"a": 1, "b": "hello"})


def test_long_input_is_not_truncated_to_tokenizer_metadata_maximum():
    assert count_text("hello " * 20000) > 16384
