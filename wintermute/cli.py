"""Command line entry point: `wintermute [chat|init|slack|ontologies]`."""

from __future__ import annotations

import argparse
import logging

from wintermute.agent import Agent
from wintermute.config import Settings
from wintermute.kb import Knowledgebase
from wintermute.kb.knowledgebase import KBFormatError
from wintermute.kb.ontology import OntologyRegistry
from wintermute.kb.ontology_packs import ManifestError, select_packs


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
    onto_p = sub.add_parser("ontologies", help="list loaded ontology packs, or explain a type, property or thing")
    onto_sub = onto_p.add_subparsers(dest="onto_command")
    explain_p = onto_sub.add_parser("explain", help="explain a type, property, or stored thing")
    explain_p.add_argument("term", help='a type or property ("Place", "dbo:Mountain", an IRI) or a thing\'s name')
    explain_p.add_argument("--is", dest="is_type", metavar="TYPE", help="show why it is (or isn't) a TYPE")
    explain_p.add_argument("--properties", action="store_true", help="list the properties that apply to it")
    args = parser.parse_args(argv)

    level = [logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # The first run loads FrameNet, so always show progress for that.
    logging.getLogger("wintermute.kb.loaders").setLevel(min(level, logging.INFO))

    settings = Settings()
    if args.command == "ontologies":
        ontologies(args, settings)
        return
    try:
        kb = Knowledgebase(settings)
    except (KBFormatError, ManifestError) as e:
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


def ontologies(args: argparse.Namespace, settings: Settings) -> None:
    from wintermute import ontology_cli

    try:
        registry = OntologyRegistry(select_packs(settings), settings.cache_dir, settings.http_timeout)
    except (ManifestError, ValueError) as e:
        raise SystemExit(str(e)) from None
    if args.onto_command != "explain":
        print(ontology_cli.describe_packs(registry))
        return

    kb = None

    def find_things(name: str):
        # Only opened when the term isn't a type, and never looks anything up remotely.
        nonlocal kb
        if not settings.db_path.exists():
            return []
        try:
            kb = Knowledgebase(settings, ontology=registry)
        except KBFormatError as e:
            raise SystemExit(str(e)) from None
        return kb.find_topics(name, remote=False)

    try:
        print(ontology_cli.explain(registry, args.term, args.is_type, args.properties, find_things))
    finally:
        if kb is not None:
            kb.close()


if __name__ == "__main__":
    main()
