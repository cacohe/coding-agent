"""编码 Agent CLI：交互 REPL。"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv

from agents.coding.factory import create_coding_harness
from agents.coding.provider import ModelInferenceError, infer_model_id


def build_parser() -> argparse.ArgumentParser:
    """
    接收命令行输入参数
    """
    p = argparse.ArgumentParser(description="Coding agent")
    p.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="project workspace (default: cwd)",
    )
    p.add_argument(
        "--model",
        default=None,
        help=(
            "LiteLLM model id, e.g. deepseek/deepseek-chat, openai/gpt-4o. "
            "If omitted, inferred from a single *_API_KEY in the environment."
        ),
    )
    p.add_argument(
        "--yes",
        "--auto-approve",
        dest="auto_approve",
        action="store_true",
        help="auto-approve write/exec tools (trusted workspaces only)",
    )
    p.add_argument(
        "--sandbox",
        default="auto",
        choices=["auto", "direct", "bubblewrap", "docker"],
    )
    p.add_argument("--quiet", action="store_true", help="hide per-tool verbose logs")
    p.add_argument(
        "--api-base",
        default=None,
        help="optional custom API base (official providers do not need this)",
    )
    return p


async def run_repl(
    workspace: Path,
    *,
    model_id: str,
    auto_approve: bool,
    verbose: bool,
    sandbox: str,
    api_base: str | None,
) -> None:
    """交互 REPL。"""
    harness = create_coding_harness(
        workspace,
        model_id=model_id,
        auto_approve=auto_approve,
        verbose=verbose,
        sandbox=sandbox,
        api_base=api_base,
    )
    session = harness.session(workspace=workspace)
    print(
        f"coding-agent ready · workspace={workspace}\n"
        f"model={model_id} · auto_approve={auto_approve}\n"
        "Type a coding task. Commands: /quit"
    )
    try:
        while True:
            try:
                query = input("\ncoding >> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not query:
                continue
            if query.lower() in {"/quit", "/exit", "q", "quit", "exit"}:
                break
            result = await session.run(query)
            print()
            print(result.final_text)
    finally:
        await harness.aclose()


def main() -> None:
    load_dotenv(override=True)
    args = build_parser().parse_args()
    workspace = args.workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        model_id = args.model or infer_model_id()
    except ModelInferenceError as exc:
        raise SystemExit(str(exc)) from exc

    asyncio.run(
        run_repl(
            workspace=workspace,
            model_id=model_id,
            auto_approve=args.auto_approve,
            verbose=not args.quiet,
            sandbox=args.sandbox,
            api_base=args.api_base,
        )
    )


if __name__ == "__main__":
    main()
