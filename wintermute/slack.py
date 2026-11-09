"""Slack front end (Bolt + Socket Mode). Replaces the retired RTM API / python-rtmbot plugins.

Requires a Slack app with Socket Mode enabled, the `im:history`, `chat:write` and `app_mentions:read`
scopes, and event subscriptions for `message.im` and `app_mention`.
  SLACK_BOT_TOKEN  xoxb-... bot token
  SLACK_APP_TOKEN  xapp-... app-level token with connections:write
"""

from __future__ import annotations

import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from wintermute.agent import Agent

log = logging.getLogger(__name__)

CHANNEL_REPLY = "Until @rbates8 gets general channel contexts working, if you want to chat DM me!"


def build_app(agent: Agent, bot_token: str) -> App:
    app = App(token=bot_token)

    @app.event("message")
    def on_message(event, say):
        # Direct messages only; ignore edits, bot messages and other subtypes.
        if event.get("channel_type") != "im" or event.get("subtype") or event.get("bot_id"):
            return
        say(agent.respond(event.get("text", ""), event["channel"]))

    @app.event("app_mention")
    def on_mention(event, say):
        say(CHANNEL_REPLY)

    return app


def run(agent: Agent) -> None:
    bot_token = os.environ.get("SLACK_BOT_TOKEN")
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not bot_token or not app_token:
        raise SystemExit("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must be set to run the Slack bot.")
    SocketModeHandler(build_app(agent, bot_token), app_token).start()
