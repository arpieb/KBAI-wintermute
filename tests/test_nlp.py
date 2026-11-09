from wintermute import nlp


def test_course_codes_become_single_chunks():
    [sent] = nlp.parse("Tell me about cs 7637 and CSE 6242.")
    assert [c.text for c in sent.chunks if c.type == "NPCD"] == ["CS 7637", "CSE 6242"]
    assert not any(c.text.lower() in ("cs", "cse") for c in sent.chunks)


def test_bare_numbers_become_chunks():
    [sent] = nlp.parse("What is 7641?")
    assert [(c.type, c.text) for c in sent.chunks] == [("NPCD", "7641")]


def test_wh_words_are_not_chunks_but_mark_questions():
    [sent] = nlp.parse("What is KBAI?")
    assert sent.question
    assert [c.text for c in sent.chunks] == ["KBAI"]


def test_framenet_lemmata():
    [sent] = nlp.parse("I want to learn about machine learning.")
    assert {"learn.v", "want.v", "machine.n"} <= set(sent.fn_lemmata)


def test_phrase_for_drops_determiners():
    [sent] = nlp.parse("What is the city of Atlanta?")
    assert nlp.phrase_for(sent, nlp.find_wh_be(sent, {"what"})) == "city"


def test_profanity():
    assert nlp.has_profanity(nlp.parse("well shit"))
    assert not nlp.has_profanity(nlp.parse("well shoot"))


def test_strip_slack_markup():
    assert nlp.strip_slack_markup("hi :wave: there").split() == ["hi", "there"]


def test_course_codes_keep_section_suffix():
    [sent] = nlp.parse("Tell me about cs 8803-o08 and CS 7637.")
    assert [c.text for c in sent.chunks if c.type == "NPCD"] == ["CS 8803-O08", "CS 7637"]
