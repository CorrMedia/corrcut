"""CLI entry: python -m corrcut.ai.worker (stdin JSON → stdout RESULT)."""

from corrcut.engine.hidden_process import install_hidden_subprocess

install_hidden_subprocess()

from corrcut.ai.worker import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
