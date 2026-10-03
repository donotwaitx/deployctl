"""deployctl: Secure deployment CLI for AI Agents & Developers."""

__version__ = "0.2.0"

from deployctl.cli import app


def main():
    app()


__all__ = ["app", "main", "__version__"]
