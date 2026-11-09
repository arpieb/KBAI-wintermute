"""spaCy-based parsing layer (replaces the CLiPS Pattern parsetree/chunk/match API)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache, lru_cache
from itertools import pairwise
from typing import TYPE_CHECKING

import spacy
from spacy.tokens import Span, Token

from wintermute.config import PACKAGE_DATA

if TYPE_CHECKING:
    from wintermute.models import FNFrame, Thing

# Penn Treebank tags mapped to the (undocumented) FrameNet LU POS suffixes, e.g. "learn.v".
# The FrameNet tags were extracted via: {lu.POS for lu in nltk.corpus.framenet.lus()}
FN_TO_PTB = {
    "a": {"JJ", "JJR", "JJS"},
    "adv": {"RB", "RBR", "RBS", "RP", "WRB"},
    "c": {"CC"},
    "art": {"DT"},
    "scon": {"IN"},
    "n": {"NN", "NNS", "NNP", "NNPS", "PRP", "WP", "WP$"},
    "v": {"VB", "VBZ", "VBP", "VBD", "VBN", "VBG"},
    "intj": {"UH"},
    "prep": {"PP"},
}
WH_TAGS = {"WDT", "WP", "WP$", "WRB"}
COURSE_DEPTS = {"cs", "cse"}


@cache
def get_nlp(model: str = "en_core_web_sm") -> spacy.language.Language:
    return spacy.load(model)


@lru_cache(maxsize=1)
def profanity() -> frozenset[str]:
    text = (PACKAGE_DATA / "profanity.txt").read_text(encoding="utf-8")
    return frozenset(w.strip().lower() for w in text.split(",") if w.strip())


@dataclass(eq=False)
class Chunk:
    """A noun phrase in a sentence. type is "NP", or "NPCD" for course codes / bare numbers."""

    span: Span
    type: str
    text: str
    topics: list[Thing] = field(default_factory=list)

    @property
    def head(self) -> Token:
        return self.span.root

    def __repr__(self) -> str:
        return f"Chunk({self.type}: {self.text!r})"


@dataclass(eq=False)
class Sentence:
    span: Span
    chunks: list[Chunk] = field(default_factory=list)
    # FrameNet-style lemma ("learn.v") -> token.
    fn_lemmata: dict[str, Token] = field(default_factory=dict)
    question: bool = False
    frame: FNFrame | None = None

    @property
    def text(self) -> str:
        return self.span.text

    @property
    def words(self) -> list[Token]:
        return [t for t in self.span if not t.is_punct and not t.is_space]

    def chunk_for(self, token: Token) -> Chunk | None:
        for chunk in self.chunks:
            if chunk.span.start <= token.i < chunk.span.end:
                return chunk
        return None


def lemma(token: Token) -> str:
    return token.lemma_.lower()


def first_sentence(text: str, model: str = "en_core_web_sm") -> str:
    if not text:
        return ""
    doc = get_nlp(model)(text)
    return next((s.text.strip() for s in doc.sents), "")


def has_profanity(sentences: list[Sentence]) -> bool:
    words = profanity()
    return any(t.lower_ in words for s in sentences for t in s.span)


def strip_slack_markup(text: str) -> str:
    """Remove :emoji: codes injected by Slack."""
    return re.sub(r":\S+:", "", text)


def parse(text: str, model: str = "en_core_web_sm") -> list[Sentence]:
    doc = get_nlp(model)(text)
    return [_build_sentence(sent) for sent in doc.sents]


def _build_sentence(span: Span) -> Sentence:
    sent = Sentence(span=span)
    sent.question = any(t.tag_ in WH_TAGS for t in span)
    doc = span.doc
    tokens = list(span)

    # "CS 7637"-style course codes become their own chunks.
    course_spans: list[Chunk] = []
    for a, b in pairwise(tokens):
        if a.lower_ in COURSE_DEPTS and b.like_num and b.text.isdigit():
            end, number = b.i + 1, b.text
            # Section suffixes written without spaces, e.g. "8803-O08".
            if end + 1 < span.end and doc[end - 1].whitespace_ == "" and doc[end].text == "-" and doc[end].whitespace_ == "" \
                    and doc[end + 1].text.isalnum():
                number = f"{number}-{doc[end + 1].text.upper()}"
                end += 2
            chunk_span = doc[a.i : end]
            course = Chunk(chunk_span, "NPCD", f"{a.text.upper()} {number}")
            course_spans.append(course)
            sent.fn_lemmata[course.text + ".n"] = b

    def overlaps_course(s: Span) -> bool:
        return any(c.span.start < s.end and s.start < c.span.end for c in course_spans)

    # Noun chunks, minus bare wh-words ("What", "Who") and anything overlapping a course code.
    for nc in span.noun_chunks:
        if overlaps_course(nc) or all(t.tag_ in WH_TAGS for t in nc):
            continue
        sent.chunks.append(Chunk(nc, "NP", nc.text))
    sent.chunks += course_spans

    # Unchunked numbers are treated as nouns to cover bare course numbers ("7641").
    for t in tokens:
        if t.tag_ == "CD" and t.text.isdigit() and sent.chunk_for(t) is None:
            sent.chunks.append(Chunk(doc[t.i : t.i + 1], "NPCD", t.text))
            sent.fn_lemmata[t.text + ".n"] = t
    sent.chunks.sort(key=lambda c: c.span.start)

    # Everything else that maps onto a FrameNet POS.
    for t in tokens:
        for fn_pos, ptb_tags in FN_TO_PTB.items():
            if t.tag_ in ptb_tags:
                sent.fn_lemmata[f"{lemma(t)}.{fn_pos}"] = t
    return sent


# --- Rule matching helpers (stand-ins for Pattern's match()) ---------------------------------


def find_wh_be(sent: Sentence, wh_words: set[str]) -> list[Token] | None:
    """Find "<wh-word> <form of be> ..." and return the tokens following "be" (minus punctuation)."""
    words = sent.words
    for i in range(len(words) - 1):
        if words[i].lower_ in wh_words and lemma(words[i + 1]) == "be":
            return words[i + 2 :]
    return None


def phrase_for(sent: Sentence, tokens: list[Token]) -> str:
    """Pick the best topic phrase from a run of tokens: the first chunk, minus determiners."""
    if not tokens:
        return ""
    start, end = tokens[0].i, tokens[-1].i + 1
    for chunk in sent.chunks:
        if start <= chunk.span.start and chunk.span.end <= end:
            if chunk.type == "NPCD":
                return chunk.text
            words = [t for t in chunk.span if t.dep_ not in ("det", "poss") and not t.is_punct]
            if words:
                return " ".join(t.text for t in words)
    return " ".join(t.text for t in tokens)


def contains_lemmas(sent: Sentence, sequence: list[str]) -> bool:
    lemmas = [lemma(t) for t in sent.words]
    n = len(sequence)
    return any(lemmas[i : i + n] == sequence for i in range(len(lemmas) - n + 1))
