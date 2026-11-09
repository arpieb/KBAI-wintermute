"""Domain objects: Schema.org-flavored knowledge "things" and FrameNet structures."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, ClassVar

from wintermute import nlp

# --- Knowledge base things ---------------------------------------------------------------------


@dataclass(eq=False)
class Thing:
    """A knowledge base node. labels is the Schema.org ancestry, e.g. ["Thing", "CreativeWork", "Course"]."""

    name: str
    labels: list[str] = field(default_factory=lambda: ["Thing"])
    props: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    # Registry of specialised subclasses keyed by their most specific Schema.org type.
    registry: ClassVar[dict[str, type[Thing]]] = {}
    type_labels: ClassVar[list[str]] = ["Thing"]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.registry[cls.type_labels[-1]] = cls

    def __post_init__(self):
        if not self.id:
            # Deterministic ID so the same named thing of the same type always maps to one node.
            key = ":".join(self.labels) + "|" + self.name
            self.id = hashlib.md5(key.encode("utf-8")).hexdigest()

    @classmethod
    def create(cls, name: str, **props) -> Thing:
        """Build a thing of this class's type with its canonical labels."""
        return cls(name=name, labels=list(cls.type_labels), props=props)

    @classmethod
    def from_record(cls, id: str, name: str, labels: list[str], props: dict[str, Any]) -> Thing:
        """Rehydrate a stored node as the most specific registered subclass for its labels."""
        for label in reversed(labels):
            if label in cls.registry:
                return cls.registry[label](name=name, labels=labels, props=props, id=id)
        return Thing(name=name, labels=labels, props=props, id=id)

    def get(self, key: str, default: Any = "") -> Any:
        return self.props.get(key, default)

    def get_response(self) -> str:
        return nlp.first_sentence(self.get("description"))


class Course(Thing):
    type_labels = ["Thing", "CreativeWork", "Course"]

    def get_response(self) -> str:
        descr = nlp.first_sentence(self.get("description"))
        if not descr:
            return ""
        return f"The course description for {self.get('alternateName')} ({self.name}) is: {descr}"


class CourseSurvey(Thing):
    type_labels = ["Thing", "CreativeWork", "CourseSurvey"]

    def get_response(self) -> str:
        return (
            f"The course survey for {self.get('alternateName')} ({self.name}) rates this course "
            f"{self.get('rating')}/5 with an average workload of {self.get('workload')} hours/week "
            f"and overall difficulty of {self.get('difficulty')}/5"
            + (f" (from {self.get('reviewCount')} OMSCentral reviews)." if self.get("reviewCount") else ".")
        )


class Specialty(Thing):
    type_labels = ["Thing", "Intangible", "Enumeration", "Specialty"]

    def get_response(self) -> str:
        return self.get("description")


# --- FrameNet ----------------------------------------------------------------------------------


@dataclass(eq=False)
class FNLexicalUnit:
    ID: int
    name: str
    rels: list[dict[str, str]] = field(default_factory=list)


@dataclass(eq=False)
class FNFrameElement:
    ID: int
    name: str
    lus: dict[str, FNLexicalUnit] = field(default_factory=dict)
    chunks: list[nlp.Chunk] = field(default_factory=list)


@dataclass(eq=False)
class FNFrame:
    ID: int
    name: str
    fes: dict[str, FNFrameElement] = field(default_factory=dict)
