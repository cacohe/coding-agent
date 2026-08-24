"""按 API Key 推断默认模型。"""

from __future__ import annotations

import pytest
from agents.coding.provider import ModelInferenceError, infer_model_id


def test_infer_deepseek_only() -> None:
    assert infer_model_id({"DEEPSEEK_API_KEY": "sk-test"}) == "deepseek/deepseek-chat"


def test_infer_openai_only() -> None:
    assert infer_model_id({"OPENAI_API_KEY": "sk-test"}) == "openai/gpt-4o"


def test_infer_gemini_aliases_are_one_provider() -> None:
    env = {"GEMINI_API_KEY": "a", "GOOGLE_API_KEY": "b"}
    assert infer_model_id(env) == "gemini/gemini-2.0-flash"


def test_infer_none_raises() -> None:
    with pytest.raises(ModelInferenceError, match="No provider API key"):
        infer_model_id({})


def test_infer_multiple_raises() -> None:
    env = {"DEEPSEEK_API_KEY": "a", "OPENAI_API_KEY": "b"}
    with pytest.raises(ModelInferenceError, match="Multiple provider"):
        infer_model_id(env)
