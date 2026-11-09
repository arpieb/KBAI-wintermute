# Project 3: Conversational Agent (Wintermute)

CS 7637, FA 2016, OMSCS  
Robert Bates  

> **2026 update:** the original Python 2.7 / Heroku / Neo4j / CLiPS Pattern / python-rtmbot
> implementation has been ported to Python 3 with spaCy, an embedded
> [LadybugDB](https://ladybugdb.com/) graph database, and Slack Bolt. The chatbot core no longer
> depends on Slack and can be run in a terminal. The rest of this README is the original 2016
> project write-up.

## Running

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run wintermute init     # one-time: builds data/wintermute.lbdb from FrameNet + course data (~20s)
uv run wintermute          # chat in the terminal
uv run wintermute slack    # run as a Slack bot (needs SLACK_BOT_TOKEN and SLACK_APP_TOKEN)
uv run wintermute ontologies                       # list the loaded ontology packs
uv run wintermute ontologies explain "CS 7637" --is CreativeWork   # explain a type, property or stored thing
uv run pytest              # tests
```

Unknown topics are looked up on DBpedia (SPARQL). Optional environment: `WINTERMUTE_REMOTE_LOOKUPS=0` disables remote lookups entirely; `WINTERMUTE_DB` overrides
the database location.

Knowledge is typed with Schema.org plus pluggable ontology packs (the DBpedia ontology by default,
and others such as Bioschemas through a manifest). See [docs/ontology-packs.md](docs/ontology-packs.md).

## Project Goal

>Conversational agents must be able to discuss for 5 minutes or more 
>topics related to KBAI (may include a limited subset of class
>discussion, topics, methods, grading, ebook, videos, student
>experience, etc) and/or topics particularly relevant and useful to
>Georgia Tech students.

[Alternative Project 3 Guidelines](https://piazza.com/class/irtr0zr0jqh156?cid=748)

## Milestones

### 3.1 / Nov 26, 2016
 
#### Original goals

* Ability to parse sentences into POS and identify topical subjects, possible sentiment analysis.
* Knowledge base populated with general AI/ML topics from sources such as WikiData, Google KnowledgeBase, with the ability to augment on demand when a proper noun is encountered and unknown.
* Interface with Piazza and/or Google Docs to have access to course information to augment knowledge base.
* Framing system for conversation populated from inputs, and transformation pipeline stubbed for responses.
* Basic web-accessible UI for conversation. 

#### Stretch goals

* Populated domain knowledge base with AI/ML topics (WikiData?)
* Populated domain knowledge base with basic GT information (GT web?)
* Ability to ad hoc query for information on topics not found in
  archival storage (Google KB/Search?)
* Ability to parse received conversational inputs into frames (limited
  frames at first)
* Ability to manage conversational contexts (frames, topics, partner
  "backstory" if any, etc)
* Ability to assemble a crude response for testing purposes.
* Graphs storage mechanism (archival as well as in-memory
  conversational) configured and working
* Basic UI to interact with system

### 3.2 / Dec 05, 2016

#### Original goals

* More complex interactions, i.e. ability to "carry" a conversation by prompting based on current topic(s) versus simply ask/answer interactions.
* Ability to modify the context based on direction/sentiment of the conversation.
* Storage of conversational background for extended conversational context with a specific user as well as a source of "hearsay" supplemental knowledge.

#### Stretch goals

* Broader ability to frame inputs and process synsets (more frames)
* Broader response abilities (more frames, improved sentence forming)
* Performance inprovements/optimizations
* Improvement is knowledge base traversal and topic resolution
* Ability to tie in "backstory" for current conversation partner or
  other conversations, added dynamically to "casual" knowledge base

## Tools

**Wintermute** will be built using Python 2.7, and the following services/APIs:

### Original proposed tooling

* NTLK or SyntaxNet for NLP
* WordNet for semantic network of English words
* FrameNet? (not sure if this is publicly available) for creating/analyzing frames from sentences.
* WikiData API - archival-type data
* Google Knowledge Base API - ad hoc data
* Piazza API (options on GH: https://goo.gl/NBjPaO) - course-specific current content
* Graph backend and in-memory storage (options for Python: https://goo.gl/YHO3FZ)
* Some web-accessible UI library/API (direct-connect chat, 'bot, etc) 

### Revised tooling

* [CLiPS Pattern](http://www.clips.ua.ac.be/pattern/) for NLP parsing and POS tagging
* [WordNet](https://wordnet.princeton.edu/) via Pattern.en for synsets
* [FrameNet](https://framenet.icsi.berkeley.edu/fndrupal/) to
  provide reference frames for processing and assembling conversational content
* [Schema.org](http://schema.org/) for hierarchical knowledge
  representation / ontology of domain information
* SlackHQ Team @ [FA2016 KBAI: Wintermute](https://fa16-kbai-wintermute.slack.com)
* SlackHQ official RTM bot codebase: [slackhq/python-rtmbot](https://github.com/slackhq/python-rtmbot)
* [OMSCS Course Surveys](https://omscentral.com/about/faq), JSON API interface (2026: replaced by [OMSCentral](https://www.omscentral.com) review data)
* [Google Knowledge Graph Search API](https://developers.google.com/knowledge-graph/) for on-demand lookup, results returned in Schema.org structures
* Hosted on [Heroku](https://www.heroku.com/):
    * Hobby-level Python 2.7 dyno
    * GrapheneDB (Neo4j) - persistent graph database
    * PaperTrail for logging

## Approach

High level:

1. Using Pattern to parse out the POS of phrases
2. Query FrameNet for each major POS to get a list of applicable frames
3. Find the intersection of all resulting sets of frames, which should help disambiguate frames, traversing up the hierarchy if necessary in an attempt to find common ground
4. If all else fails and I can't confidently disambiguate the concept, do what a human does: punt and ask a question from what I can discern, or ask for clarification based on the frames in question

## FrameNet 1.7
https://framenet.icsi.berkeley.edu/fndrupal

Cheat sheet for frames, LU markup, and relations taken from "The Book."

### Top level frames
Frame | Chldren
------|--------
Event|646
Relation|67
State|134
Entity|118
Locale|22
Process|11

### Other "tree tops" (top 90%)
Frame | Children | Cumulative %
------|----------|----
Attributes|202|12.7
Gradable_attributes|199|25.3
Information|123|33.1
Mention|103|39.6
Experiencer_focus|63|43.5
Being_included|50|46.7
Mental_activity|49|49.8
Part_whole|43|52.5
Social_behavior_evaluation|42|55.1
Being_necessary|41|57.7
Stimulus_focus|41|60.3
Reciprocality|40|62.8
Required_event|38|65.2
Attempt|26|66.9
Observable_body_parts|26|68.5
Being_obligated|25|70.1
Obligation_scenario|22|71.5
Likelihood|21|72.8
Possession|21|74.1
Satisfying|20|75.4
Morality_evaluation|19|76.6
Measurable_attributes|19|77.8
Similarity|18|78.9
Means|17|80
Opinion|15|80.9
Possibility|14|81.8
Image_schema|14|82.7
Success_or_failure|11|83.4
Position_on_a_scale|9|84
Measures|9|84.5
Visiting_scenario_stay|8|85
Cogitation|7|85.5
Instance|7|85.9
Seeking|7|86.4
Importance|7|86.8
Abundance|6|87.2
Leadership|6|87.6
Location_in_time|6|87.9
Alternatives|6|88.3
Getting_scenario|5|88.6
Cognitive_connection|5|89
Lose_possession_scenario|5|89.3
Being_named|5|89.6
Social_interaction_evaluation|5|89.9
Run_risk|5|90.2

### Singletons (24 total)
* Living_conditions, ID#2564
* Commemorative, ID#261
* Planned_trajectory, ID#2566
* Proper_reference, ID#1582
* Version_sequence, ID#1960
* Domain, ID#430
* Using_resource, ID#2480
* Political_actions, ID#2482
* Medical_intervention, ID#2741
* Biological_mechanisms, ID#2750
* Contrary_circumstances, ID#2500
* Cognitive_impact, ID#2502
* Reserving, ID#2679
* Pattern, ID#2133
* Graph_shape, ID#2520
* Serving_in_capacity, ID#2521
* Function, ID#2523
* Being_pregnant, ID#2921
* Specific_individual, ID#2668
* Notability, ID#2542
* Labor_product, ID#2164
* Increment, ID#1001
* Ranked_expectation, ID#1533
* Optical_image, ID#2261

### Phrase Types (PT) - Ch 4, "The Book"

* Noun Phrase Types
    * Possessive Noun Phrase (Poss)
    * Non-maximal Nominal (N)
    * Standard Noun Phrase (NP)
* Prepositional Phrase Types
    * Prepositional Phrases (PP)
    * PPing (Preposition with gerund object)
    * PPinterrog (Preposition governing a wh-interrogative clause)
    * PPadjP (Preposition governing an adjective phrase)
* Verb Phrase Types
    * Finite Verb Phrase (VPn)
    * Nonfinite Verb Phrase
        * VPbrst (Bare Stem Verb Phrase)
        * VPto (To-Marked Innitive Verb Phrase)
        * VPed (Participial Verb Phrase)
        * VPing (Gerundive Verb Phrase)
* Clause Types
    * Finite Clause
        * Sfin (Finite Clause (with or without that))
        * Sinterrog- (Wh-Clause)
        * Swhether (Whether/if -Clause)
    * Nonfinite Clause
        * Sing (Gerundive Clause)
        * Sto (To-marked Clause)
        * Sforto (For-to-marked Clause)
        * Sbrst (Bare Stem Clause)
* Subordinate Clause (Sub) with subordinating conjunction
* Adjective Phrase Types
    * Non-maximal Adjective (A)
    * Standard Adjective Phrase (AJP)
* Adverb Phrase (AVP)
* Quantifier Phrases (QUANT)
* Quote (QUO)

### Grammatical Functions (GF) - Ch 5, "The Book"

* Grammatical Functions assigned by Target Verbs
    * External Argument (Ext)
    * Object (Obj)
    * Dependent (Dep)
* Grammatical Functions assigned by Target Adjectives
    * External Argument (Ext)
    * Head noun modied by attributive adjective (Head)
    * Dependent (Dep)
* Grammatical Functions assigned by Target Prepositions
    * External Argument (Ext)
    * Dependent (Dep)
    * Object (Obj)
* Grammatical Functions assigned by Target Nouns
    * External Argument (Ext)
    * Genitive determiner (Gen)
    * Dependent (Dep)
    * Appositive (Appos)

### Semantic Relations - Ch 6, "The Book"

Relation | Super | Sub
-------- |-------|----
Inheritance | Parent | Child
Using | Parent | Child
Perspectiv_on | Neutral | Perspectivized
Subframe | Complex | Component
Precedes | Earlier | Later
Inchoative_of | Inchoative | Stative
Causative_of | Causative | Inchoative/Stative
Metaphor | Source | Target
See_also | MainEntry | ReferringEntry
