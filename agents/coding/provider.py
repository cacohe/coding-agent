"""按环境变量里的厂商 API Key 推断默认 LiteLLM model id。"""

from __future__ import annotations

import os

# 只认「设了就能确定一家官方 API」的密钥。网关/Azure 仍需显式 --model。
_KEY_TO_MODEL: dict[str, str] = {
    "DEEPSEEK_API_KEY": "deepseek/deepseek-chat",
    "ANTHROPIC_API_KEY": "anthropic/claude-sonnet-4-5",
    "OPENAI_API_KEY": "openai/gpt-4o",
    "GEMINI_API_KEY": "gemini/gemini-2.0-flash",
    "GOOGLE_API_KEY": "gemini/gemini-2.0-flash",
}


class ModelInferenceError(ValueError):
    """无法从环境变量唯一确定模型。"""


def infer_model_id(environ: dict[str, str] | None = None) -> str:
    """根据已设置的 *_API_KEY 选出默认 model id。

    恰好一家厂商有密钥时返回对应模型；零家或多家则抛错，需显式 --model。
    """
    env = os.environ if environ is None else environ
    found: dict[str, str] = {}
    for key, model_id in _KEY_TO_MODEL.items():
        value = env.get(key, "").strip()
        if not value:
            continue
        found.setdefault(model_id, key)

    if len(found) == 1:
        return next(iter(found))
    if not found:
        keys = ", ".join(_KEY_TO_MODEL)
        raise ModelInferenceError(
            f"No provider API key found. Set one of {keys} in .env, or pass --model explicitly."
        )
    present = ", ".join(sorted(found.values()))
    raise ModelInferenceError(
        f"Multiple provider API keys are set ({present}). "
        "Pass --model to choose, e.g. --model deepseek/deepseek-chat."
    )
