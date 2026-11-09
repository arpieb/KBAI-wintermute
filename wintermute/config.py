"""Runtime settings, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DATA = Path(__file__).resolve().parent / "data"


def _env_path(name: str, default: Path | None) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


@dataclass
class Settings:
    # LadybugDB database file holding the knowledge base and conversation contexts.
    db_path: Path = field(default_factory=lambda: _env_path("WINTERMUTE_DB", REPO_ROOT / "data" / "wintermute.lbdb"))
    # Directory for downloaded/cached reference data (e.g. parsed ontology packs).
    cache_dir: Path = field(default_factory=lambda: _env_path("WINTERMUTE_CACHE", REPO_ROOT / "data" / "cache"))
    # FrameNet 1.7 XML release.
    framenet_dir: Path = field(default_factory=lambda: _env_path("WINTERMUTE_FRAMENET", REPO_ROOT / "assets" / "framenet"))
    # spaCy pipeline name.
    spacy_model: str = field(default_factory=lambda: os.environ.get("WINTERMUTE_SPACY_MODEL", "en_core_web_sm"))
    # Remote topic lookups (DBpedia, Google Knowledge Graph) when a topic isn't in the local KB.
    remote_lookups: bool = field(default_factory=lambda: os.environ.get("WINTERMUTE_REMOTE_LOOKUPS", "1") != "0")
    # Google Knowledge Graph lookups are on hold; set WINTERMUTE_GOOGLE_KG=1 (plus an API key) to enable.
    google_kg: bool = field(default_factory=lambda: os.environ.get("WINTERMUTE_GOOGLE_KG", "0") == "1")
    google_api_key: str | None = field(default_factory=lambda: os.environ.get("GOOGLE_KGSEARCH_API_KEY"))
    # Ontology packs to load, comma-separated in the env var. None defers to the manifest's `load`
    # list, then the built-in defaults (see wintermute/kb/ontology_packs.py).
    ontologies: list[str] | None = field(
        default_factory=lambda: [n.strip() for n in os.environ.get("WINTERMUTE_ONTOLOGIES", "").split(",") if n.strip()]
        or None
    )
    # TOML manifest declaring extra ontology packs (and optionally which packs to load).
    ontology_manifest: Path | None = field(
        default_factory=lambda: _env_path("WINTERMUTE_ONTOLOGY_MANIFEST", None)
    )
    http_timeout: float = 10.0
