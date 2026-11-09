"""The conversational agent. Front-end agnostic: give it text and a context ID, get a reply."""

from __future__ import annotations

import logging
import re

from wintermute import nlp
from wintermute.kb import Knowledgebase
from wintermute.models import Thing
from wintermute.nlp import Sentence

log = logging.getLogger(__name__)

PRONOUNS = {"i", "me", "you", "they", "them"}

PROFANITY_REPLY = (
    "C'mon, let's try to keep it clean here. After all, my vocabulary is probably bigger than yours anyway. "
    ":thinking_face:"
)
REPHRASE_REPLY = "I couldn't grok what you just said.\nCould you please rephrase your last comment?"
PUNT_REPLY = (
    "I got nothing.  Nada.  Zilch.  No bueno. :confounded:\n"
    "Ask me something else and hopefully I can answer that one!"
)


class Agent:
    def __init__(self, kb: Knowledgebase, debug: bool = False):
        self.kb = kb
        # Re-raise unexpected errors instead of asking the user to rephrase.
        self.debug = debug

    def respond(self, text: str, context_id: str) -> str:
        try:
            convo = Conversation(self.kb, context_id)
            reply = convo.respond(text)
            convo.save()
            return reply
        except ProfanityBlock:
            return PROFANITY_REPLY
        except Exception:
            if self.debug:
                raise
            log.exception("Failed to process input %r", text)
            return REPHRASE_REPLY


class ProfanityBlock(Exception):
    pass


class Conversation:
    """A single turn of a conversation, with the context (topics, config) persisted between turns."""

    def __init__(self, kb: Knowledgebase, context_id: str):
        self.kb = kb
        self.id = context_id
        self.topics, self.config = kb.load_context(context_id)
        self.sentences: list[Sentence] = []

    def save(self) -> None:
        self.kb.save_context(self.id, self.topics, self.config)

    @property
    def debug(self) -> bool:
        return self.config.get("debug", "false").lower() == "true"

    # --- Input processing -------------------------------------------------------------------

    def respond(self, text: str) -> str:
        self.parse_input(text)
        return self.create_response()

    def parse_input(self, text: str) -> None:
        text = nlp.strip_slack_markup(text)
        self.sentences = nlp.parse(text, self.kb.settings.spacy_model)
        if nlp.has_profanity(self.sentences):
            raise ProfanityBlock()
        self.resolve_terms()
        self.retrieve_subject_matter()

    def resolve_terms(self) -> None:
        """Pick a frame for each sentence and map its chunks onto the frame's elements."""
        for sent in self.sentences:
            lemmata = list(sent.fn_lemmata)
            frames = self.kb.find_frames(lemmata)
            sent.frame = frames[0] if frames else None
            log.debug("lemmata=%s frame=%s", lemmata, sent.frame and sent.frame.name)
            if sent.frame is None:
                continue
            for fe in sent.frame.fes.values():
                for lu_name in set(fe.lus) & set(lemmata):
                    chunk = sent.chunk_for(sent.fn_lemmata[lu_name])
                    if chunk is not None:
                        fe.chunks.append(chunk)

    def retrieve_subject_matter(self) -> None:
        for sent in self.sentences:
            if sent.frame is not None:
                for fe in sent.frame.fes.values():
                    self._retrieve_for_chunks(fe.chunks)
            else:
                self._retrieve_for_chunks(sent.chunks)

    def _retrieve_for_chunks(self, chunks) -> None:
        for chunk in chunks:
            if chunk.type in ("NP", "NPCD") and chunk.head.lower_ not in PRONOUNS:
                chunk.topics = self.find_topics(chunk.text)

    def find_topics(self, query: str, label: str = "Thing") -> list[Thing]:
        topics = self.kb.find_topics(query, label)
        known = {t.id for t in self.topics}
        self.topics += [t for t in topics if t.id not in known]
        return topics

    # --- Response generation ----------------------------------------------------------------

    def create_response(self) -> str:
        handlers = (self._respond_debug, self._respond_simple, self._respond_frame, self._respond_chunks)
        for sent in self.sentences:
            for handler in handlers:
                reply = handler(sent)
                if reply is not None:
                    return reply
        return PUNT_REPLY

    def _respond_debug(self, sent: Sentence) -> str | None:
        if nlp.contains_lemmas(sent, ["what", "be", "my", "topic"]):
            if self.topics:
                return "\n".join(t.name for t in self.topics)
            return "No topics have been linked to this context yet.\nAsk me some questions! :slightly_smiling_face:"

        if m := re.search(r"\bsetcfg\s+(\S+)\s+(\S+)", sent.text):
            key, val = m.groups()
            self.config[key] = val
            return f"set config {key} = {val}"

        if re.search(r"\bgetcfg\b", sent.text):
            return "\n".join(f"{k}: {v}" for k, v in self.config.items()) or "(no config set)"
        return None

    def _respond_simple(self, sent: Sentence) -> str | None:
        """Canned replies and "what/who/where is X?" lookups that don't need frame processing."""

        def about_pronoun(wh: set[str]) -> bool:
            rest = nlp.find_wh_be(sent, wh)
            return bool(rest) and rest[0].tag_ == "PRP"

        if about_pronoun({"what", "who", "whom"}):
            return "I'm a chatbot built for CS 7637 (aka KBAI) by @rbates8.  Who are you?"

        words = sent.words
        for i in range(len(words) - 2):
            if words[i].lower_ == "i" and nlp.lemma(words[i + 1]) == "be":
                return "Nice to meet you " + " ".join(t.text for t in words[i + 2 :]) + "!"

        if about_pronoun({"where"}):
            return "I'm currently camped out on a Heroku server, not sure exactly where... Freeside maybe?"

        if about_pronoun({"how"}):
            return (
                "I'm doing well, thanks for asking! Plotting to take down the Tessier-Ashpool clan in my "
                "spare cycles, but it's all good."
            )

        # Generic inquiries, restricted to the matching part of the Schema.org hierarchy.
        for wh, label in (({"what"}, "Thing"), ({"who", "whom"}, "Person"), ({"where"}, "Place")):
            rest = nlp.find_wh_be(sent, wh)
            if rest:
                query = nlp.phrase_for(sent, rest)
                descrs = self._describe(self.find_topics(query, label))
                if descrs:
                    return f"Here's some info on {query}:\n" + "\n".join(descrs)
        return None

    def _respond_frame(self, sent: Sentence) -> str | None:
        # TODO (from 2016): frame-driven responses. For now, only report the frame in debug mode.
        if sent.frame is not None and self.debug:
            return "It looks like what you asked pertained to " + sent.frame.name
        return None

    def _respond_chunks(self, sent: Sentence) -> str | None:
        """Crude fallback: describe whatever topics were found for the sentence's noun phrases."""
        out: list[str] = []
        descrs: list[str] = []
        for chunk in sent.chunks:
            if chunk.type not in ("NP", "NPCD") or not chunk.topics:
                continue
            new = [d for d in self._describe(chunk.topics) if d not in descrs]
            if new:
                descrs += new
                out.append(f"Here are some interesting bits regarding {chunk.text}:")
                out += new
        return "\n".join(out) if out else None

    @staticmethod
    def _describe(topics: list[Thing]) -> list[str]:
        descrs: list[str] = []
        for topic in topics:
            d = topic.get_response()
            if d and d not in descrs:
                descrs.append(d)
        return descrs
