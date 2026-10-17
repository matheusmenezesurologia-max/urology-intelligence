"""Command-line entrypoints.

Usage:
    python -m app.cli run                 # run the weekly pipeline once, now
    python -m app.cli ask "question..."   # ask the RAG knowledge base
    python -m app.cli init-db             # create tables directly (dev/bootstrap)
    python -m app.cli serve               # run the API + scheduler (uvicorn)
"""
from __future__ import annotations

import argparse
import json
import sys

from app.utils.logger import configure_logging, get_logger

log = get_logger("cli")


def cmd_run(_: argparse.Namespace) -> int:
    from app.workflows.weekly_pipeline import run_pipeline

    state = run_pipeline()
    print(json.dumps({"run_id": state.get("run_id"), "stats": state.get("stats"),
                      "errors": state.get("errors")}, ensure_ascii=False, indent=2))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    from app.rag.retriever import answer_question

    ans = answer_question(args.question, top_k=args.top_k)
    print(ans.answer)
    print("\nFontes:")
    for s in ans.sources:
        print(f"  [{s['n']}] {s['title']} ({s['source_type']}, score={s['score']})")
    return 0


def cmd_init_db(_: argparse.Namespace) -> int:
    from app.database.session import init_db

    init_db()
    log.info("init_db_complete")
    print("Database tables created.")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:  # pragma: no cover - long-running server
    import uvicorn

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="urology-intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="Run the weekly pipeline once").set_defaults(func=cmd_run)

    p_ask = sub.add_parser("ask", help="Query the RAG knowledge base")
    p_ask.add_argument("question")
    p_ask.add_argument("--top-k", type=int, default=8)
    p_ask.set_defaults(func=cmd_ask)

    sub.add_parser("init-db", help="Create database tables directly").set_defaults(func=cmd_init_db)

    p_serve = sub.add_parser("serve", help="Run the API + scheduler")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
