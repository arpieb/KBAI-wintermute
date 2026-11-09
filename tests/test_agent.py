import uuid

import pytest

from wintermute.agent import PROFANITY_REPLY, PUNT_REPLY


@pytest.fixture
def ctx():
    return str(uuid.uuid4())


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Who are you?", "I'm a chatbot built for CS 7637"),
        ("I am Rob.", "Nice to meet you Rob!"),
        ("How are you?", "I'm doing well"),
        ("Where are you?", "camped out"),
    ],
)
def test_canned_replies(agent, ctx, text, expected):
    assert expected in agent.respond(text, ctx)


@pytest.mark.parametrize("text", ["What is KBAI?", "what is cs 7637", "What is 7637?"])
def test_course_lookup_by_name_alias_and_number(agent, ctx, text):
    assert "The course description for Knowledge-Based AI (CS 7637) is: Structured knowledge representation." in agent.respond(text, ctx)


def test_chunk_fallback(agent, ctx):
    reply = agent.respond("Tell me about CS 6601.", ctx)
    assert reply.startswith("Here are some interesting bits regarding CS 6601:")


def test_profanity_and_punt(agent, ctx):
    assert agent.respond("this is shit", ctx) == PROFANITY_REPLY
    assert agent.respond("asdf qwerty", ctx) == PUNT_REPLY


def test_context_topics_and_config_persist(agent, ctx):
    assert "No topics" in agent.respond("What are my topics?", ctx)
    agent.respond("What is ML?", ctx)
    agent.respond("setcfg debug true", ctx)
    assert agent.respond("What are my topics?", ctx) == "CS 7641"
    assert agent.respond("getcfg", ctx) == "debug: true"
    assert "No topics" in agent.respond("What are my topics?", ctx + "-other")


def test_frame_resolution(kb):
    [frame] = kb.find_frames(["learn.v", "want.v", "machine.n", "learning.n", "about.prep", "i.n"])
    assert frame.name == "Education_teaching"
    assert "Student" in frame.fes
    assert kb.find_frames(["zzzz.n"]) == []


def test_frame_reported_in_debug_mode(agent, ctx):
    agent.respond("setcfg debug true", ctx)
    assert agent.respond("I want to learn about machine learning.", ctx).endswith("Education_teaching")
