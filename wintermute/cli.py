"""Command line entry point: `wintermute [chat|init|slack]`."""

from __future__ import annotations

import argparse
import logging

from wintermute.agent import Agent
from wintermute.config import Settings
from wintermute.kb import Knowledgebase
from wintermute.kb.knowledgebase import KBFormatError


def chat(agent: Agent, context_id: str) -> None:
    print("Wintermute is listening. Ctrl-D or 'quit' to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.lower() in ("quit", "exit"):
            return
        if text:
            print("wintermute>", agent.respond(text, context_id).replace("\n", "\n            "))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="wintermute", description="KBAI conversational agent")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v for info logging, -vv for debug")
    parser.add_argument("--debug", action="store_true", help="raise errors instead of asking to rephrase")
    sub = parser.add_subparsers(dest="command")
    chat_p = sub.add_parser("chat", help="chat in the terminal (default)")
    chat_p.add_argument("--context", default="terminal", help="conversation context ID to load/save")
    sub.add_parser("init", help="build the knowledge base (FrameNet, courses) and exit")
    sub.add_parser("slack", help="run as a Slack bot (Socket Mode)")
    args = parser.parse_args(argv)

    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # The first run loads FrameNet, so always show progress for that.
    logging.getLogger("wintermute.kb.loaders").setLevel(min(level, logging.INFO))

    settings = Settings()
    try:
        kb = Knowledgebase(settings)
    except KBFormatError as e:
        raise SystemExit(str(e)) from None
    try:
        kb.initialize()
        if args.command == "init":
            print(f"Knowledge base ready at {settings.db_path}")
            return
        agent = Agent(kb, debug=args.debug)
        if args.command == "slack":
            from wintermute import slack

            slack.run(agent)
        else:
            chat(agent, getattr(args, "context", "terminal"))
    finally:
        kb.close()


if __name__ == "__main__":
    main()
