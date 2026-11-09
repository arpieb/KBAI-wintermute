"""One-time bulk loaders that populate the knowledge base from FrameNet and course data."""

from __future__ import annotations

import csv
import logging
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from wintermute.models import Course, CourseSurvey, Specialty

if TYPE_CHECKING:
    from wintermute.kb.knowledgebase import Knowledgebase

log = logging.getLogger(__name__)

FN = "{http://framenet.icsi.berkeley.edu}"

COURSE_CATALOG_URL = "https://raw.githubusercontent.com/GatechVIP/Courses/master/app/json/GatechCourses.json"
# OMSCentral (https://www.omscentral.com, github.com/oms-fyi/reviews) replaces the 2016 gt-surveyor
# service. Its Sanity dataset is publicly readable but unofficial/undocumented, so we make a single
# cached (apicdn) request with server-side aggregation, once per KB build.
OMSCENTRAL_QUERY_URL = "https://3yw11hu2.apicdn.sanity.io/v2025-02-06/data/query/production"
OMSCENTRAL_SURVEY_QUERY = """
*[_type == "course"]{
  name, codes, isFoundational, isDeprecated,
  "programs": programs[]->acronym,
  "reviewCount": count(*[_type == "review" && references(^._id)]),
  "rating": math::avg(*[_type == "review" && references(^._id)].rating),
  "difficulty": math::avg(*[_type == "review" && references(^._id)].difficulty),
  "workload": math::avg(*[_type == "review" && references(^._id)].workload)
}
"""


# --- FrameNet ----------------------------------------------------------------------------------


def _write_csv(path: Path, header: list[str], rows) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def _lu_valences(path: Path):
    """Return (lexUnit attributes, valence units) without parsing the (large) annotation section."""
    attrs, valences = None, []
    for event, elem in ET.iterparse(path, events=("start", "end")):
        if event == "start" and elem.tag == FN + "lexUnit":
            attrs = dict(elem.attrib)
        elif event == "end" and elem.tag == FN + "valenceUnit":
            valences.append(dict(elem.attrib))
        elif event == "start" and elem.tag == FN + "subCorpus":
            break
    return attrs, valences


def load_framenet(kb: Knowledgebase, framenet_dir: Path) -> None:
    graph = kb.graph
    if graph.is_loaded("framenet"):
        return
    log.info("Loading FrameNet from %s (one-time, takes a minute or two)", framenet_dir)

    frames, fes, frame_fes = [], {}, []
    fe_by_frame_name: dict[tuple[int, str], int] = {}
    for path in sorted((framenet_dir / "frame").glob("*.xml")):
        root = ET.parse(path).getroot()
        fid = int(root.get("ID"))
        frames.append((fid, root.get("name")))
        for fe in root.findall(FN + "FE"):
            fe_id = int(fe.get("ID"))
            fes[fe_id] = (fe_id, fe.get("name"), fe.get("abbrev"))
            frame_fes.append((fid, fe_id, fe.get("coreType")))
            fe_by_frame_name[(fid, fe.get("name"))] = fe_id

    lus, valence_units = [], set()
    for path in sorted((framenet_dir / "lu").glob("*.xml")):
        attrs, valences = _lu_valences(path)
        if not attrs:
            continue
        lu_id, frame_id = int(attrs["ID"]), int(attrs["frameID"])
        lus.append((lu_id, attrs["name"], attrs.get("POS"), frame_id, attrs.get("frame"), attrs.get("status")))
        # LUs only tie to frames through FEs, via their valence units (as in the original design).
        for vu in valences:
            fe_id = fe_by_frame_name.get((frame_id, vu.get("FE")))
            if fe_id is not None:
                valence_units.add((lu_id, fe_id, vu.get("GF", ""), vu.get("PT", "")))

    relations = set()
    for rel_type in ET.parse(framenet_dir / "frRelation.xml").getroot():
        for rel in rel_type:
            relations.add((int(rel.get("supID")), int(rel.get("subID")), rel_type.get("name")))

    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        graph.copy_from("Frame", _write_csv(t / "frame.csv", ["ID", "name"], frames))
        graph.copy_from("FE", _write_csv(t / "fe.csv", ["ID", "name", "abbrev"], fes.values()))
        graph.copy_from("LU", _write_csv(t / "lu.csv", ["ID", "name", "POS", "frameID", "frame", "status"], lus))
        graph.copy_from("frame_element", _write_csv(t / "frame_element.csv", ["from", "to", "type"], frame_fes))
        graph.copy_from("valence_unit", _write_csv(t / "valence_unit.csv", ["from", "to", "GF", "PT"], valence_units))
        graph.copy_from("frame_relation", _write_csv(t / "frame_relation.csv", ["from", "to", "type"], relations))

    graph.mark_loaded("framenet")
    log.info("FrameNet loaded: %d frames, %d FEs, %d LUs, %d relations", len(frames), len(fes), len(lus), len(relations))


# --- Courses -----------------------------------------------------------------------------------

# Common nicknames for courses.
COURSE_ALIASES = {
    "CS 7637": ["KBAI"],
    "CS 6035": ["InfoSec"],
    "CSE 6220": ["HPC"],
    "CS 7110": ["PCA"],
    "CSE 6643": ["NLA"],
    "CS 7641": ["ML"],
    "CS 7545": ["MLT"],
    "CS 6460": ["edtech", "eductech"],
    "CS 6400": ["DB"],
    "CS 6476": ["CV"],
    "CS 6601": ["AI"],
    "CSE 6230": ["HPPC"],
    "CS 7495": ["CV"],
    "CS 7615": ["KA"],
    "CS 6290": ["HPCA"],
    "CS 7496": ["CA"],
    "CS 6310": ["SAD"],
    "CSE 6242": ["DVA"],
    "CS 7650": ["NL"],
    "CS 6300": ["SDP"],
    "CS 7210": ["DC"],
    "CS 7646": ["ML4T"],
    "CS 6210": ["advos"],
}


def load_course_catalog(kb: Knowledgebase, timeout: float) -> None:
    if kb.graph.is_loaded("courses"):
        return
    log.info("Loading course catalog")
    try:
        resp = requests.get(COURSE_CATALOG_URL, timeout=timeout)
        resp.raise_for_status()
        courses = resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("Could not load course catalog: %s", e)
        return

    for key, descr in courses.items():
        m = re.match(r"((\w+)\s+(\w+))\s+(.*)", key)
        if not m:
            continue
        name, dept, number, alt_name = m.groups()
        if dept not in ("CS", "CSE"):
            continue
        course = Course.create(name, alternateName=alt_name.strip(), description=descr.strip(), department=dept, courseCode=number)
        kb.save_thing(course)
        kb.add_alias(number, course)
        for alias in COURSE_ALIASES.get(name, []):
            kb.add_alias(alias, course)
    kb.graph.mark_loaded("courses")


def load_course_surveys(kb: Knowledgebase, timeout: float) -> None:
    """Load aggregated OMSCentral review ratings as CourseSurvey nodes, one per course code."""
    if kb.graph.is_loaded("course_surveys"):
        return
    log.info("Loading course surveys from OMSCentral")
    try:
        resp = requests.get(OMSCENTRAL_QUERY_URL, params={"query": OMSCENTRAL_SURVEY_QUERY}, timeout=max(timeout, 60))
        resp.raise_for_status()
        courses = resp.json()["result"]
    except (requests.RequestException, ValueError, KeyError) as e:
        log.warning("Could not load course surveys from OMSCentral: %s", e)
        return

    count = 0
    for item in courses:
        if not item.get("reviewCount"):
            continue
        for code in item.get("codes") or []:
            dept, _, number = code.partition("-")  # "CS-7637" -> "CS", "7637"; "CS-8803-O08" -> "CS", "8803-O08"
            survey = CourseSurvey.create(
                f"{dept} {number}",
                alternateName=item["name"],
                courseCode=number,
                department=dept,
                difficulty=round(item["difficulty"], 1),
                rating=round(item["rating"], 1),
                workload=round(item["workload"], 1),
                reviewCount=item["reviewCount"],
                programs=item.get("programs") or [],
                deprecated=bool(item.get("isDeprecated")),
                **({"position": "foundational"} if item.get("isFoundational") else {}),
            )
            kb.save_thing(survey)
            kb.save_relationship(survey, Course.create(survey.name), "survey")
            kb.add_alias(number, survey)
            for alias in COURSE_ALIASES.get(survey.name, []):
                kb.add_alias(alias, survey)
            count += 1
    kb.graph.mark_loaded("course_surveys")
    log.info("Loaded %d course surveys", count)


# MSCS specializations (as of 2016) and their core/elective courses.
SPECIALIZATIONS = {
    "Computational Perception and Robotics": {
        "core": ["CS 6505", "CS 6520", "CS 6550", "CS 7520", "CS 7530", "CSE 6140", "CS 6601", "CS 7641"],
        "elective": [
            "CS 6475", "CS 6476", "CS 7499", "CS 7636", "CS 7650", "CS 8803",
            "CS 7630", "CS 7631", "CS 7633", "CS 7649", "CS 8803-O01",
        ],
    },
    "Computing Systems": {
        "core": ["CS 6505", "CS 6210", "CS 6241", "CS 6250", "CS 6290", "CS 6300", "CS 6400"],
        "elective": [
            "CS 6035", "CS 6235", "CS 6238", "CS 6260", "CS 6262", "CS 6310", "CS 6340", "CS 6365", "CS 6422",
            "CS 6550", "CS 6675", "CS 7210", "CS 7260", "CS 7270", "CS 7290", "CS 7292", "CS 7560", "CS 8803-FPL",
        ],
    },
    "Interactive Intelligence": {
        "core": ["CS 6300", "CS 6505", "CS 6601", "CS 7620", "CS 7637", "CS 7641"],
        "elective": ["CS 6440", "CS 6460", "CS 6465", "CS 7634", "CS 8803", "CS 7632", "CS 7650", "CS 6795", "CS 7610"],
    },
    "Machine Learning": {
        "core": ["CS 6505", "CS 6520", "CS 6550", "CS 7510", "CS 7520", "CS 7530", "CSE 6140", "CS 7641", "CSE 6740"],
        "elective": ["CS 7540", "CS 7545", "CS 7616", "CS 7646", "CS 7650", "CS 8803", "CSE 6240", "CSE 6242"],
    },
}


def load_specializations(kb: Knowledgebase) -> None:
    if kb.graph.is_loaded("specializations"):
        return
    log.info("Loading specializations")
    for name, groups in SPECIALIZATIONS.items():
        description = (
            f"{name} is a Georgia Tech MSCS specialization with core courses {', '.join(groups['core'])} "
            f"and electives {', '.join(groups['elective'])}."
        )
        specialty = Specialty.create(name, description=description)
        kb.save_thing(specialty)
        for rel_type, courses in groups.items():
            for course in courses:
                kb.save_relationship(specialty, Course.create(course), rel_type)
    kb.graph.mark_loaded("specializations")
