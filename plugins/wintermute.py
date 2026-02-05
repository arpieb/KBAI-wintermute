#from __future__ import print_function
from __future__ import unicode_literals
import re
import os
import json
import httplib
import urllib
import sys
import xml.etree.ElementTree as ET
import pprint
import numbers
from urlparse import urlparse
import hashlib

# Import the SlackHQ RTM bot framework.
# https://github.com/slackhq/python-rtmbot
from rtmbot.core import Plugin

# Imports from the Pattern NLP framework.
# http://www.clips.ua.ac.be/pages/pattern
from pattern.en import *
from pattern.en.wordlist import *
from pattern.search import *

# Import Google API Client Libraries for Python.
from apiclient.discovery import build

# Imports for Neo4j.
from neo4j.v1 import GraphDatabase, basic_auth

#
# Hashable dict class from:
# http://stackoverflow.com/a/1151705
#
class hashabledict(dict):
    def __hash__(self):
        return hash(tuple(sorted(self.items())))


#
# Exception classes.
#
class WintermuteProfanityBlock(Exception):
    def __init__(self):
        pass


#
# RtmBot plugin to process direct message messages.
#
class WintermuteDirectPlugin(Plugin):

    def __init__(self, name=None, slack_client=None, plugin_config=None):
        Plugin.__init__(self, name, slack_client, plugin_config)
        self.agent = WintermuteAgent()

    # Process message callback.
    def process_message(self, data):
        if data['channel'].startswith("D"):
            self.outputs.append(
                [
                    data['channel'],
                    self.agent.process_input(data['text'], data['channel'])
                ]
            )


#
# RtmBot plugin to process general channel messages.
#
class WintermuteGeneralPlugin(Plugin):

    # Process message callback.
    def process_message(self, data):
        if data['channel'].startswith("C"):
            self.outputs.append(
                [
                    data['channel'],
                    "Until @rbates8 gets general channel contexts working, if you want to chat DM me!"
                ]
            )


#
# Core processor for messages with a context.
#
class WintermuteAgent(object):

    # Initialize the object.
    def __init__(self):
        pass

    # Process input + context.
    def process_input(self, input, context_id):
        # Wrap the whole transformation in exception handling so we can recover (somewhat) gracefully.
        response = ''
        try:
            print 'process_utterance'
            context = WintermuteContext(context_id)

            # Transform the utterance into a response.
            context.parse_input(input)
            response = context.create_response()

            # Perform any final processing on the context and save.
            context.save()
        except WintermuteProfanityBlock:
            response = "C'mon, let's try to keep it clean here. After all, my vocabulary is probably bigger than yours anyway. :thinking_face:"
        except:
            # Try to request a rephrase from the other end as we apparently couldn't handle what we received.
            print "Unexpected error:", sys.exc_info()[0]
            response = "I couldn't grok what you just said.\nCould you please rephrase your last comment?"
            # Uncomment the following "raise" to trap exceptions in the debugger.
            #raise

        # Return our crafted response.
        print 'response:', response
        sys.stdout.flush()
        sys.stderr.flush()
        return response


#
# Encapsulates the context of this conversation.
#
class WintermuteContext(object):
    # Constructor creates empty context, tries to overwrite if one exists.
    def __init__(self, id):
        # Attempt to load from persistent store.
        self.id = id
        self.kb = None
        self.config = {}
        self._load()

        # Initialize attributes for processing this utterance.
        self.rawInput = None
        self.parsedInput = None
        self.frames = []

    # Try to load context from persistent storage.
    def _load(self):
        print 'Loading context'
        self.__dict__.update(_kb.load_context(self.id))
        #print self.kb

    # Save context to persistent storage.
    def save(self):
        print 'Saving context'
        _kb.save_context(self)

    # Parse the input using our NLP tool of choice.
    def parse_input(self, input):
        # Initial processing of input.
        print 'parse_input'
        input = self.preprocess_input(input)
        self.rawInput = input
        self.parsedInput = parsetree(input, relations=True, lemmata=True)
        print self.parsedInput
        print [sent.chunks for sent in self.parsedInput]
        self.postprocess_input()

        # Process input into the context.
        self.extract_terms()
        self.resolve_terms()
        self.retrieve_subject_matter()

    # Preprocess input, stripping out Slack-added markup.
    def preprocess_input(self, input):
        # Remove :XXX: escaped emojis injected by Slack.
        input = re.sub(r':\S+:', '', input)

        return input

    # Quick post-processing, extra sanitizing of inputs parsed into POS.
    def postprocess_input(self):
        # Check for profanity; keep it clean!
        for sent in self.parsedInput:
            words = [w.string for w in sent.words]
            if len(set(words) & set(PROFANITY)) > 0:
                raise WintermuteProfanityBlock()

    # Extract all nouns and verbs from the input; very simplistic search for key concepts.
    def extract_terms(self):
        print 'extract_terms'
        for sentence in self.parsedInput:
            sentence.fn_lemmata = {}

            # Check for "W" words, see if a possible inquiry exists.
            sentence.question = len(self.match_pos_words('WDT|WP|WP$|WRB', sentence)) > 0

            # Split "CS/CSE xxxx" bigrams into their own chunks.
            course_chunks = [Chunk(sentence, words=[a, b], type='NPCD') for a, b in ngrams(sentence, n=2) if a.string.lower() in ['cs', 'cse'] and b.type in ['CD']]
            for chunk in course_chunks:
                sentence.fn_lemmata[chunk.string + '.n'] = chunk.words[1]
            sentence.chunks += course_chunks

            # Remove orphaned "CS/CSE" chunks.
            sentence.chunks = [chunk for chunk in sentence.chunks if not (len(chunk.words) == 1 and chunk.words[0].string.lower() in ['cs', 'cse'])]

            # Extract unchunked numbers as nouns to cover courses.
            for word in self.match_pos_words('CD', sentence):
                if word.string.isdigit() and word.chunk == None:
                    sentence.chunks.append(Chunk(sentence, words=[word], type='NPCD'))
                    sentence.fn_lemmata[word.lemma + '.n'] = word

            # Extract everything else we can map to a FrameNet POS.
            # FrameNet POS tags are not documented, so were extracted via:
            #   import nltk
            #   from nltk.corpus import framenet as fn
            #   {lu.POS for lu in fn.lus()}
            fn2ptb = {
                'a': 'JJ|JJR|JJS',
                'adv': 'RB|RBR|RBS|RP|WRB',
                'c': 'CC',
                'art': 'DT',
                'scon': 'IN',
                'n': 'NN|NNS|NNP|NNPS|PRP|WP|WP$',
                #'num': 'CD',
                'v': 'VB|VBZ|VBP|VBD|VBN|VBG',
                'intj': 'UH',
                'prep': 'PP',
            }
            for fn_pos, ptb_pos in fn2ptb.items():
                # Search for words matching Penn Tree Bank pattern.
                for word in self.match_pos_words(ptb_pos, sentence):
                    # If found, assign to lemmata using FrameNet POS tag.
                    sentence.fn_lemmata[word.lemma + '.' + fn_pos] = word

    # Locate words in the sentence with the requested tags.
    # Roll our own since Pattern's match() and search() don't appear to work for all POS.
    def match_pos_words(self, pattern, sent):
        tags = pattern.split('|')
        return [w for w in sent.words if w.type.split('-')[0] in tags]

    # Locate chunks in the sentence with the requested tags.
    # Roll our own since Pattern's match() and search() don't appear to work for all POS.
    def match_pos_chunks(self, pattern, sent):
        tags = pattern.split('|')
        return [c for c in sent.chunks if c.type.split('-')[0] in tags]

    # Disambiguate terms, attempt to resolve frame elements.
    def resolve_terms(self):
        print 'resolve_terms'
        for sent in self.parsedInput:
            # Find "most recent" common ancestor frame for extracted lemmata.
            lemmata = sent.fn_lemmata.keys()
            print lemmata
            frames = _kb.find_frames(lemmata)
            sent.frame = None if len(frames) == 0 else frames[0]

            if sent.frame != None:
                # Try to map chunks to frame elements.
                for fe_name, fe in sent.frame.fes.items():
                    print "\t", sent.frame.name, ":", fe_name
                    print "\t\tfe.lus.keys():", fe.lus.keys()
                    print "\t\tset(lemmata):", set(lemmata)
                    matched_lus = set(fe.lus.keys()) & set(lemmata)
                    print "\t\tmatched_lus:", matched_lus
                    for lemma in matched_lus:
                        print "\t\t\tsent.fn_lemmata[lemma].chunk:", sent.fn_lemmata[lemma].chunk
                        if sent.fn_lemmata[lemma].chunk != None:
                            fe.chunks.append(sent.fn_lemmata[lemma].chunk)

    # Load relevant subject matter based on terms, that isn't in our contextual KB.
    def retrieve_subject_matter(self):
        print 'retrieve_subject_matter'
        for sent in self.parsedInput:
            if sent.frame != None:
                for fe_name, fe in sent.frame.fes.items():
                    print "\t", sent.frame.name, ":", fe_name
                    print "\tfe.chunks:", fe.chunks
                    self._retrieve_subject_matter_for_chunks(fe.chunks)#, fe.name)
            else:
                # Try to make due with an unframed sentence.
                self._retrieve_subject_matter_for_chunks(sent.chunks)

    # Search for topics associated with provided list of chunks.
    def _retrieve_subject_matter_for_chunks(self, chunks, label='Thing'):
        if chunks == None or len(chunks) == 0:
            print "_retrieve_subject_matter_for_chunks: no chunks to process"
            return

        pronouns = [
            'i',
            'me',
            'you',
            'they', 'them',
        ]
        for chunk in [c for c in chunks if c.type in ['NP', 'NPCD'] and c.head.string.lower() not in pronouns]:
            query = chunk.string
            print "_retrieve_subject_matter_for_chunks", query
            chunk.topics = self.find_topics(query, label)

    # Context-aware topic search.  Updates local KB.
    def find_topics(self, query, label='Thing'):
        topics = _kb.find_topics(query, label)
        self.kb += topics
        return topics

    # Create a response from the context and most recent utterance.
    def create_response(self):
        print 'create_response'
        out = []

        # Run through sentences and respond to the first thing we can handle.
        for sent in self.parsedInput:
            # See if this is a debug request we can handle.
            resp = self._create_response_debug(sent)
            if resp is not None:
                return resp

            # See if this is a simpler request we can handle.
            resp = self._create_response_simple(sent)
            if resp is not None:
                return resp

            # Now see if we need to try frame-level processing.
            resp = self._create_response_frame(sent)
            if resp is not None:
                return resp

            # Fall-through to crude responses based purely on topics assigned to chunks.
            resp = self._create_response_chunks(sent)
            if resp is not None:
                return resp

        # Yep, we got this far we have no idea what we were asked.  Punt!
        return "I got nothing.  Nada.  Zilch.  No bueno. :confounded:\nAsk me something else and hopefully I can answer that one!"

    # Try to respond via frame processing.
    def _create_response_frame(self, sent):
        # Check for frame hits.
        if sent.frame != None:
            # TODO
            if self.config.get('debug', 'false').lower() == 'true':
                return "It looks like what you asked pertained to " + sent.frame.name

        return None

    # Try to craft a response based solely on chunk data + topics.
    def _create_response_chunks(self, sent):
        out = []

        # Extract noun phrases for this sentence.
        np_chunks = [chunk for chunk in sent.chunks if chunk.type in ['NP', 'NPCD']]

        descrs = set()
        print "examining np_chunks:", np_chunks
        for chunk in [c for c in np_chunks if hasattr(c, 'topics')]:
            intro = "Here are some interesting bits regarding " + chunk.string + ':'
            for topic in chunk.topics:
                # Try to determine what kind of topic this is (Schema.org class).
                # If we can, then we invoke output methods for customized response vs raw dump of descriptions.
                topic_labels = topic.get_labels()
                if 'Course' in topic_labels:
                    descr = WintermuteCourse.from_thing(topic).get_response()
                elif 'CourseSurvey' in topic_labels:
                    descr = WintermuteCourseSurvey.from_thing(topic).get_response()
                else:
                    descr = topic.get_response()

                # Make sure we have something to add.  Note we're using sets to dedupe topics.
                if descr != None and len(descr) > 0:
                    descrs.add(descr)

            # See if we have anything we can assemble.
            if len(descrs) > 0:
                out.append(intro)
                out += list(descrs)

        return "\n".join(out) if len(out) > 0 else None

    # Handle debugging requests.
    def _create_response_debug(self, sent):
        # We were asked "(what) be my topics?"  Reply!
        m = match('[what] be my topics', sent)
        if m != None:
            topics = [t.name for t in self.kb]
            if len(topics) > 0:
                return "\n".join(topics)
            return "No topics have been linked to this context yet.\nAsk me some questions! :slightly_smiling_face:"

        # Set dev config item.
        m = match('setcfg {*} {*}', sent)
        if m != None:
            key = m.group(1).string
            val = m.group(2).string
            self.config[key] = val
            return "set config " + key + " = " + val

        # Get dev config items.
        m = match('getcfg', sent)
        if m != None:
            resp = []
            for key, val in self.config.items():
                resp.append(str(key) + ': ' + str(val))
            return "\n".join(resp)

        # No hits.
        return None

    # Handle some anticipated common canned requests that don't require more complex frame processing.
    def _create_response_simple(self, sent):
        # We were asked "(what|who|whom) be (me|you|it)?"  Reply!
        m = match('[what|who|whom] be {PRP}', sent)
        if m != None:
            return "I'm a chatbot built for CS 7637 (aka KBAI) by @rbates8.  Who are you?"

        # We were told "I be (something|someone)."  Reply!
        m = match('i be {*}', sent)
        if m != None:
            return "Nice to meet you " + m.group(1).string + "!"

        # We were asked "(where) be (me|you|it)?"  Reply!
        m = match('[where] be {PRP}', sent)
        if m != None:
            return "I'm currently camped out on a Heroku server, not sure exactly where... Freeside maybe?"

        # We were asked "(how) be (me|you|it)?"  Reply!
        m = match('[how] be {PRP}', sent)
        if m != None:
            return "I'm doing well, thanks for asking! Plotting to take down the Tessier-Ashpool clan in my spare cycles, but it's all good."

        # Handle generic "what is...?" inquries.
        m = match('[what] be {*? NP|VP}', sent)
        if m != None:
            query = m.group(1).string
            descrs = set()

            # Unrestricted search across all Things.
            for topic in self.find_topics(query):
                descr = topic.get_response()
                if descr != None and len(descr) > 0:
                    descrs.add(descr)
            if len(descrs) > 0:
                return "Here's some info on " + query + ":\n" + "\n".join(descrs)

        # Handle generic "who is...?" inquries.
        m = match('[who|whom] be {*? NP|VP}', sent)
        if m != None:
            query = m.group(1).string
            descrs = set()

            # Restrict our topic query to the Person subtree.
            for topic in self.find_topics(query, 'Person'):
                descr = topic.get_response()
                if descr != None and len(descr) > 0:
                    descrs.add(descr)
            if len(descrs) > 0:
                return "Here's some info on " + query + ":\n" + "\n".join(descrs)

        # Handle generic "where is...?" inquries.
        m = match('[where] be {*? NP|VP}', sent)
        if m != None:
            query = m.group(1).string
            descrs = set()

            # Restrict our topic query to the Place subtree.
            for topic in self.find_topics(query, 'Place'):
                descr = topic.get_response()
                if descr != None and len(descr) > 0:
                    descrs.add(descr)
            if len(descrs) > 0:
                return "Here's some info on " + query + ":\n" + "\n".join(descrs)

        # No hits.
        return None

#
# Class to abstract out the knowledgebase backend.
#
class WintermuteKnowledgebase(object):
    # FrameNet specific constants.
    FRAMENET_XML_NS = {
        'fn': 'http://framenet.icsi.berkeley.edu',
    }

    def __init__(self):
        # Initialize driver for Neo4j database.
        graphenedb_url = os.environ.get("GRAPHENEDB_BOLT_URL")
        graphenedb_user = os.environ.get("GRAPHENEDB_BOLT_USER")
        graphenedb_pass = os.environ.get("GRAPHENEDB_BOLT_PASSWORD")
        #print graphenedb_url, graphenedb_user, graphenedb_pass
        self.driver = GraphDatabase.driver(graphenedb_url, auth=basic_auth(graphenedb_user, graphenedb_pass))

        # Initialize our Schema.org hierarchy in working memory.
        self.schema = {}
        self._init_schema_org()

        # Init/update our local KB from static resources.
        self._init_framenet()
        self._init_course_catalog()
        self._init_course_surveys()
        self._init_specializations()

    # Get status of database, see if we need to load/update specific sets.
    def _get_status(self):
        # See if we have a status node on the graph.
        query = "MATCH (status:WMKBStatus) return status"
        session = self.driver.session()
        results = list(session.run(query))
        session.close()
        if len(results):
            return results[0][0].properties
        return {}

    # Update database status.
    def _update_status(self, prop_name, prop_value=True):
        params = {
            'props': {
                prop_name: prop_value,
            },
            'prop_name': prop_name,
            'prop_value': prop_value,
        }
        query = "MERGE (status:WMKBStatus) ON CREATE SET status = {props} ON MATCH SET status." + prop_name + " = {prop_value}"
        session = self.driver.session()
        result = session.run(query, params)
        session.close()

    # Load Schema.org hierachy into working memory.
    # JSON source loaded from http://schema.link.fish/downloads/all.json.
    def _init_schema_org(self):
        print "Loading Schema.org hierarchy definitions"
        conn = httplib.HTTPConnection("schema.link.fish")
        conn.request("GET", "/downloads/all.json")
        resp = conn.getresponse()
        if resp.status == 200:
            data = resp.read()
            schema = json.loads(data)
            for name, definition in schema['types'].items():
                self.schema[name] = definition

        # Add some custom extensions.
        exts = {
            'Course': {
                'ancestors': ['Thing', 'CreativeWork'],
            },
            'CourseSurvey': {
                'ancestors': ['Thing', 'CreativeWork'],
            }
        }
        for ext, definition in exts.items():
            self.schema[ext] = definition

    # Return the ancestry label list for a Schema.org entity type.
    def get_schema_labels(self, name):
        return self.schema[name]['ancestors'] if self.schema.has_key(name) else []

    # Load the FrameNet data into our graph database.
    def _init_framenet(self):
        print 'Initializing FrameNet'
        self._init_framenet_frames()
        self._init_framenet_lus()
        self._init_framenet_relations()

    # Load frames + frame elements (FE) from FrameNet.
    def _init_framenet_frames(self):
        # Check to see if we need to update.
        status = self._get_status()
        if status.has_key('framenet_frames'):
            return

        # Load frames.
        print 'Loading frameIndex'
        dir = './assets/framenet/frame'
        for fname in os.listdir(dir):
            if fname.endswith('.xml'):
                # Open backend session.
                session = self.driver.session()

                #print "\t", fname
                tree = ET.parse(os.path.join(dir, fname))
                frame = tree.getroot()

                # Store off frame.
                query = "CREATE (f:FrameNet:Frame {props})"
                result = session.run(query, {'props': frame.attrib})

                # Now store off frame elements.
                for fe in frame.findall('fn:FE', self.FRAMENET_XML_NS):
                    params = {
                        'fe_props': {
                            'ID': fe.get('ID'),
                            'name': fe.get('name')
                        },
                        'rel_props': {
                            'type': fe.get('coreType'),
                        },
                        'fid': frame.get('ID')
                    }
                    query = """
                        MATCH (f:FrameNet:Frame {ID: {fid}})
                        MERGE (fe:FrameNet:FE {ID: {fe_props}.ID})
                        ON CREATE set fe = {fe_props}
                        CREATE (f)-[:frame_element {rel_props}]->(fe)
                    """
                    result = session.run(query, params)

                # Close backend session.
                session.close()

        # Mark database as having loaded this chunk of data.
        self._update_status('framenet_frames')

    # Load lexical units (LU) from FrameNet, tie to FEs.
    def _init_framenet_lus(self):
        # Check to see if we need to update.
        status = self._get_status()
        if status.has_key('framenet_lus'):
            return

        # Load LUs.
        print 'Loading luIndex'
        dir = './assets/framenet/lu'
        for fname in os.listdir(dir):
            if fname.endswith('.xml'):
                # Open backend session.
                session = self.driver.session()

                #print "\t", fname
                tree = ET.parse(os.path.join(dir, fname))
                lu = tree.getroot()

                # Store off lexical unit.
                query = "CREATE (lu:FrameNet:LU {props})"
                result = session.run(query, {'props': lu.attrib})

                # Now store off LU:FE mappings, stored as valence units in the XML.
                # NOTE that the end result is we are going to ignore any LU that is not tied to a frame through an FE.
                # Good?  Bad?  Ugly?
                for vu in lu.findall('./fn:valences/fn:FERealization/fn:pattern/fn:valenceUnit', WintermuteKnowledgebase.FRAMENET_XML_NS):
                    params = {
                        'lu': lu.attrib,
                        'vu': vu.attrib,
                    }
                    query = """
                        MATCH (f:Frame {ID: {lu}.frameID})-[:frame_element]->(fe:FE {name: {vu}.FE}), (lu:LU {ID: {lu}.ID})
                        MERGE (lu)-[:valence_unit {GF: {vu}.GF, PT: {vu}.PT}]->(fe)
                    """
                    result = session.run(query, params)

                # Close backend session.
                session.close()

        # Mark database as having loaded this chunk of data.
        self._update_status('framenet_lus')

    # Create relationships between frames.
    def _init_framenet_relations(self):
        # Check to see if we need to update.
        status = self._get_status()
        if status.has_key('framenet_relations'):
            return

        # Load frame relations.
        print 'Loading frRelation'
        tree = ET.parse('./assets/framenet/frRelation.xml')
        rels = tree.getroot()
        for rel_type in rels:
            # Open backend session.
            session = self.driver.session()

            rel_type_label = rel_type.attrib['name']
            for frame_rel in rel_type:
                params = {
                    'supID': frame_rel.get('supID'),
                    'subID': frame_rel.get('subID'),
                }
                #print params
                query = "MATCH (sup:FrameNet:Frame {ID: {supID}}), (sub:FrameNet:Frame {ID: {subID}}) "\
                    "MERGE (sup)-[r:" + rel_type_label + "]->(sub)"
                result = session.run(query, params)

            # Close backend session.
            session.close()

        # Mark database as having loaded this chunk of data.
        self._update_status('framenet_relations')

    # Initialize courses from VIP catalog JSON.
    def _init_course_catalog(self):
        # Check to see if we need to update courses.
        status = self._get_status()
        if status.has_key('courses'):
            return

        # Additonal course aliases.
        aliases = {
            'CS 7637': {
                'KBAI',
            },
            'CS 6035': {
                'InfoSec',
            },
            'CSE 6220': {
                'HPC',
            },
            'CS 7110': {
                'PCA',
            },
            'CSE 6643': {
                'NLA',
            },
            'CS 7641': {
                'ML',
            },
            'CS 7545': {
                'MLT',
            },
            'CS 6460': {
                'edtech',
                'eductech',
            },
            'CS 6400': {
                'DB',
            },
            'CS 6476': {
                'CV',
            },
            'CS 6601': {
                'AI',
            },
            'CSE 6230': {
                'HPPC',
            },
            'CS 7495': {
                'CV',
            },
            'CS 7615': {
                'KA',
            },
            'CS 6290': {
                'HPCA',
            },
            'CS 7496': {
                'CA',
            },
            'CS 6310': {
                'SAD',
            },
            'CSE 6242': {
                'DVA',
            },
            'CS 7650': {
                'NL',
            },
            'CS 6300': {
                'SDP',
            },
            'CS 7210': {
                'DC',
            },
            'CS 7646': {
                'ML4T',
            },
            'CS 6210': {
                'advos',
            },
        }

        # Open connection to the VIP JSON course catalog.
        conn = httplib.HTTPSConnection("raw.githubusercontent.com")

        # Pull down course catalog JSON.
        print 'Loading course catalog'
        conn.request("GET", "/GatechVIP/Courses/master/app/json/GatechCourses.json")
        resp = conn.getresponse()
        if resp.status == 200:
            data = resp.read()
            courses = json.loads(data)
            for key, item in courses.items():
                # Break apart some course info from the KV pair.
                [(name, dept, courseno, altName)] = re.findall(r'((\w+)\s+(\w+))\s+(.*)', key)
                if dept in ['CS', 'CSE']:
                    # We're only interested in CS + CSE courses.
                    course = WintermuteCourse(name)
                    course.alternateName = altName.strip()
                    course.description = item.strip()
                    course.department = dept
                    course.courseCode = courseno

                    # Save course + alias to course code.
                    self.save_thing(course)
                    alias = WintermuteAlias(courseno)
                    self.save_thing(alias)
                    self.save_relationship(alias, course, 'alias')

                    # Add any extra aliases.
                    if aliases.has_key(course.name):
                        for alias_name in aliases[course.name]:
                            alias = WintermuteAlias(alias_name)
                            self.save_thing(alias)
                            self.save_relationship(alias, course, 'alias')

        # Mark database as having loaded course catalog.
        self._update_status('courses')

    # Initialize/update OMSCS course survey data in local KB.
    def _init_course_surveys(self):
        print 'Loading course surveys'

        # Open connection to the survey's JSON endpoints host.
        conn = httplib.HTTPSConnection("gt-surveyor.firebaseio.com")

        # Read in the aggregated ratings for the courses.
        ratings = {}
        print "\tLoading ratings"
        conn.request("GET", "/aggregations.json")
        resp = conn.getresponse()
        if resp.status == 200:
            data = resp.read()
            ratings = json.loads(data)

        # Read in the course listings from the survey's JSON endpoint.
        print "\tLoading courses"
        conn.request("GET", "/courses.json")
        resp = conn.getresponse()
        if resp.status == 200:
            data = resp.read()
            courses = json.loads(data)
            for key, item in courses.items():
                course = WintermuteCourseSurvey(item['department'] + ' ' + key)
                course.alternateName = item['name']
                course.courseCode = str(item['number'])
                course.department = item['department']

                # Flag as foundational where necessary.
                if item['foundational']:
                    course.position = 'foundational'

                # Attach the ratings to it.  Note we're not using Schema.org's
                # Ratings/Review/AggregateRating objects as they are overkill for this.
                course.difficulty = course.rating = course.workload = ''
                if ratings.has_key(key):
                    course.difficulty = ratings[key]['average']['difficulty']
                    course.rating = ratings[key]['average']['rating']
                    course.workload = ratings[key]['average']['workload']

                # Save out survey node.
                self.save_thing(course)

                # Now create relationship with course node, if it doesn't exist.
                self.save_relationship(
                    course,
                    WintermuteCourse(course.name),
                    'survey'
                )

                # Now alias to the survey as well.
                alias = WintermuteAlias(course.courseCode)
                self.save_thing(alias)
                self.save_relationship(alias, course, 'alias')

        # Clean up.
        conn.close()

    # Initialize specializations, creating relationships to courses.
    def _init_specializations(self):
        # Check to see if we need to update.
        status = self._get_status()
        if status.has_key('specializations'):
            return

        # Massive map of MSCS specializatiosn to courses.
        specializations = {
            'Computational Perception and Robotics': {
                'core': [
                    'CS 6505',
                    'CS 6520',
                    'CS 6550',
                    'CS 7520',
                    'CS 7530',
                    'CSE 6140',
                    'CS 6601',
                    'CS 7641',
                ],
                'elective': [
                    'CS 6475',
                    'CS 6476',
                    'CS 7499',
                    'CS 7636',
                    'CS 7650',
                    'CS 8803',
                    'CS 7630',
                    'CS 7631',
                    'CS 7633',
                    'CS 7649',
                    'CS 8803-O01',
                ],
            },
            'Computing Systems': {
                'core': [
                    'CS 6505',
                    'CS 6210',
                    'CS 6241',
                    'CS 6250',
                    'CS 6290',
                    'CS 6300',
                    'CS 6400',
                ],
                'elective': [
                    'CS 6035',
                    'CS 6235',
                    'CS 6238',
                    'CS 6260',
                    'CS 6262',
                    'CS 6310',
                    'CS 6340',
                    'CS 6365',
                    'CS 6422',
                    'CS 6550',
                    'CS 6675',
                    'CS 7210',
                    'CS 7260',
                    'CS 7270',
                    'CS 7290',
                    'CS 7292',
                    'CS 7560',
                    'CS 8803-FPL',
                ],
            },
            'Interactive Intelligence': {
                'core': [
                    'CS 6300',
                    'CS 6505',
                    'CS 6601',
                    'CS 7620',
                    'CS 7637',
                    'CS 7641',
                ],
                'elective': [
                    'CS 6440',
                    'CS 6460',
                    'CS 6465',
                    'CS 7634',
                    'CS 8803',
                    'CS 7632',
                    'CS 7650',
                    'CS 8803',
                    'CS 6795',
                    'CS 7610',
                    'CS 8803',
                ],
            },
            'Machine Learning': {
                'core': [
                    'CS 6505',
                    'CS 6520',
                    'CS 6550',
                    'CS 7510',
                    'CS 7520',
                    'CS 7530',
                    'CSE 6140',
                    'CS 7641',
                    'CSE 6740',
                ],
                'elective': [
                    'CS 7540',
                    'CS 7545',
                    'CS 7616',
                    'CS 7646',
                    'CS 7650',
                    'CS 8803',
                    'CSE 6240',
                    'CSE 6242',
                ],
            },
        }

        # Create/update specialization nodes.
        print 'Loading specializations'
        for name, ctypes in specializations.items():
            sp = WintermuteSpecialty(name)
            self.save_thing(sp)

            # Now create/update relationships with courses.
            for ctype, courses in ctypes.items():
                for course in courses:
                    self.save_relationship(sp, WintermuteCourse(course), ctype)

        # Mark database as having loaded this chunk of data.
        self._update_status('specializations')

    # Load context from the KB.
    def load_context(self, id):
        params = {
            'id': id,
        }
        query = "MATCH (ctx:WMContext {ID: {id}})-[r:context_uses]->(t) return ctx, t"
        session = self.driver.session()
        results = list(session.run(query, params))
        session.close()

        topics = []
        config = {}
        for record in results:
            tnode = record['t']
            if tnode != None:
                label = ':'.join(tnode.labels)
                topics.append(WintermuteThing(label=label, props=tnode.properties))

            ctx = record['ctx']
            if ctx != None:
                config = json.loads(ctx.properties['config']) if ctx.properties.has_key('config') else {}

        return {'kb': topics, 'config': config}

    # Save context into our KB.
    def save_context(self, context):
        # Start new session.
        session = self.driver.session()

        # Create/update context.
        params = {
            'ctx_id': context.id,
            'ctx_config': json.dumps(context.config),
        }

        # Create/update topic relatonships.
        #print context.kb
        for topic in context.kb:
            params['topic'] = topic.__dict__
            #print params
            query = """
                MATCH (t:Alias {name: lower({topic}.name)})
                MERGE (ctx:WMContext {ID: {ctx_id}})
                ON MATCH SET ctx.config = {ctx_config}
                ON CREATE SET ctx.ID = {ctx_id}, ctx.config = {ctx_config}
                MERGE (ctx)-[r:context_uses]->(t)
            """
            result = session.run(query, params)

        # Close session.
        session.close()

    # Save a WintermuteThing object to a node in the graph backend.
    def save_thing(self, thing):
        # Make sure this thing has an ID.
        if not hasattr(thing, 'ID'):
            setattr(thing, 'ID', hashlib.md5(thing.name).hexdigest())

        # Now extract property map and save.
        thing_props = thing.__dict__
        label = thing._type
        params = {
            'props': thing_props,
            }
        query = "MERGE (thing:" + label + " {name: '" + thing.name + "'}) ON CREATE SET thing = {props} ON MATCH SET thing = {props}"
        session = self.driver.session()
        result = session.run(query, params)
        session.close()

    # Save a relationship between two WintermuteThings.
    def save_relationship(self, from_thing, to_thing, rel_type):
        params = {
            'from_props': from_thing.__dict__,
            'to_props': to_thing.__dict__,
        }
        from_label = from_thing._type
        to_label = to_thing._type
        query = "MATCH (from:" + from_label + " {name: {from_props}.name}), (to:" + to_label + " {name: {to_props}.name})"\
            "MERGE (from)-[r:" + rel_type + "]->(to)"
        session = self.driver.session()
        result = session.run(query, params)
        session.close()

    # Pull related topic nodes from the knowledgebase.
    def find_topics(self, search_topic, label='Thing'):
        # Try to find a local copy in our local KB.
        topics = self._find_topics_local(search_topic, label)
        if len(topics) == 0:
            # Reach out to DBPedia.
            topics = self._find_topics_dbpedia(search_topic, label)
            if len(topics) == 0:
                # Final attempt against Google's Knowledge Graph.
                topics = self._find_topics_google(search_topic, label)

        # For future reference, link these topics to the requested topic as aliases.
        topic_thing = WintermuteAlias(search_topic)
        self.save_thing(topic_thing)
        for topic in topics:
            self.save_relationship(topic_thing, topic, 'alias')

        # Now try to pull again from  the local store so we're not using all the things found.
        topics = self._find_topics_local(search_topic, label)

        # Return whatever we found.
        return topics

    # Look up in our local datastore.
    def _find_topics_local(self, topic, label):
        topics = []
        params = {
            'name': topic,
            'label': label,
        }
        query = """
            optional match (t) where {label} in labels(t) and lower(t.name) = lower({name}) optional match (a:Alias)-[:alias]->(at) where {label} in labels(at) and lower(a.name) = lower({name}) return *
        """
        session = self.driver.session()
        results = list(session.run(query, params))
        session.close()

        #print "_find_topics_local", topic, label, results
        for record in results:
            tnode = record['at'] if record['at'] != None else record['t']
            if tnode != None:
                label = ':'.join(tnode.labels)
                topics.append(WintermuteThing(label=label, props=tnode.properties))

        #print topics
        return topics

    # Search google for a match.
    def _find_topics_google(self, topic, label):
        # Execute search against Google Knowledge Graph.
        api_key = os.environ.get('GOOGLE_KGSEARCH_API_KEY')
        kgsearch = build('kgsearch', 'v1', developerKey=api_key)
        request = kgsearch.entities().search(query=topic, types=label)
        response = request.execute()

        # Add results to knowledge base.
        topics = []
        for result in [entity['result'] for entity in response['itemListElement']]:
            # Extract properties from search result, filter out unwanted ones.
            props = {key: val for key, val in result.items() if key[0] != '@' and isinstance(val, (basestring, numbers.Number))}
            props['ID'] = result['@id']
            labels = ':'.join(self.get_schema_labels(result['@type']))

            # Construct KB data node for result.
            #print labels, props
            thing = WintermuteThing(props['name'], labels, props=props)

            # Save in KB, add to found list.
            self.save_thing(thing)
            topics.append(thing)

        # Return what we found, or an empty list.
        return topics

    # Search DBPedia for a match.
    def _find_topics_dbpedia(self, topic, label):
        # Open connection to the VIP JSON course catalog.
        topics = []
        conn = httplib.HTTPConnection('lookup.dbpedia.org')
        conn.request('GET', '/api/search/KeywordSearch?' + urllib.urlencode({'QueryString': topic}),
                     headers={'Accept': 'application/json'})
        resp = conn.getresponse()
        if resp.status == 200:
            data = resp.read()
            #print data
            res_wrapper = json.loads(data)
            for result in res_wrapper['results']:
                props = {key: val for key, val in result.items() if isinstance(val, (basestring, numbers.Number))}
                props['ID'] = result['uri']
                labels = set(['Thing'])

                # See if we have a Schema.org class.
                for res_class in result['classes']:
                    parsed_url = urlparse(res_class['uri'])
                    if parsed_url.netloc == 'schema.org':
                        schema_class = parsed_url.path[1:]
                        labels.add(schema_class)
                        labels |= set(self.get_schema_labels(schema_class))

                # Construct KB data node for result.
                #print labels, props
                thing = WintermuteThing(result['label'], ':'.join(labels), props=props)

                # Save in KB, add to found list.
                self.save_thing(thing)
                topics.append(thing)

        return topics

    # Find frames in the KB that are related to topics + actions.
    def find_frames(self, lemmata):
        # Open session.
        session = self.driver.session()

        # Find most recent common ancestor.
        # Look for Inheritance and Subframe relations as these will "walk up"
        # the hierarchy, whereas the other relations are more contextual or elaborative.
        # Query based on code from:
        # https://github.com/NicMcPhee/MICS-2014-GraphDB-EC/wiki/Cypher-query-sheet
        params = {
            'lemmata': lemmata,
        }
        query = """
            MATCH (n:Frame)-[:frame_element {type: 'Core'}]->(fe:FE)<-[vu:valence_unit]-(lu:LU) where lu.name in {lemmata}
            MATCH ps = (p)-[:Inheritance|Reframing_Mapping*]->(n)
            WITH collect(ps) as paths, max(length(ps)) as maxLength
            WITH filter(path in paths where length(path)=maxLength) as longest
            WITH reduce(s = nodes(longest[0]), pt in longest | filter(nd in nodes(pt) where nd in s)) as pre
            RETURN last(pre) as frames;
        """
        results = list(session.run(query, params))
        frames = [FNFrame(result['frames'].properties) for result in results if result['frames'] != None] if len(results) > 0 else []
        print "Number of frames found:", len(frames)
        print [frame.name for frame in frames]

        # Pull in FE and LU nodes for each frame.
        for frame in frames:
            params = {
                'fid': frame.ID,
            }
            query = """
                MATCH (f:Frame)-[:frame_element]->(fe:FE)<-[vu:valence_unit]-(lu:LU)
                WHERE f.ID = {fid}
                RETURN *
            """
            results = list(session.run(query, params))
            setattr(frame, 'fes', {})
            for result in results:
                # Add FE.
                fe_props = result['fe'].properties
                if not frame.has_fe(fe_props['name']):
                    frame.add_fe(FNFrameElement(fe_props))
                fe = frame.get_fe(fe_props['name'])

                # Add LU.
                lu_props = result['lu'].properties
                if not fe.has_lu(lu_props['name']):
                    fe.add_lu(FNLexicalUnit(lu_props))
                lu = fe.get_lu(lu_props['name'])
                lu.rels.append(result['vu'].properties)

        # Cleanup session.
        session.close()
        return frames

#
# Base class for FrameNet objects.
#
class FNBase(object):
    def __init__(self, props={}):
        self.__dict__.update(props)


#
# Class for FrameNet frames.
#
class FNFrame(FNBase):
    def __init__(self, props={}):
        super(FNFrame, self).__init__(props)
        self.fes = {}

    def add_fe(self, fe):
        self.fes[fe.name] = fe

    def has_fe(self, name):
        return self.fes.has_key(name)

    def get_fe(self, name):
        return self.fes[name] if self.has_fe(name) else None

#
# Class for top-level FrameNet frame Event.
#
class FNFrameEvent(FNFrame):
    def __init__(self, props={}):
        super(FNFrameEvent, self).__init__(props)


#
# Class for top-level FrameNet frame Relation.
#
class FNFrameRelation(FNFrame):
    def __init__(self, props={}):
        super(FNFrameRelation, self).__init__(props)


#
# Class for top-level FrameNet frame State.
#
class FNFrameState(FNFrame):
    def __init__(self, props={}):
        super(FNFrameState, self).__init__(props)


#
# Class for top-level FrameNet frame Entity.
#
class FNFrameEntity(FNFrame):
    def __init__(self, props={}):
        super(FNFrameEntity, self).__init__(props)


#
# Class for top-level FrameNet frame Locale.
#
class FNFrameLocale(FNFrame):
    def __init__(self, props={}):
        super(FNFrameLocale, self).__init__(props)


#
# Class for top-level FrameNet frame Process.
#
class FNFrameProcess(FNFrame):
    def __init__(self, props={}):
        super(FNFrameProcess, self).__init__(props)


#
# Class for FrameNet frame elements.
#
class FNFrameElement(FNBase):
    def __init__(self, props={}):
        super(FNFrameElement, self).__init__(props)
        self.lus = {}
        self.chunks = []

    def add_lu(self, lu):
        self.lus[lu.name] = lu

    def has_lu(self, name):
        return self.lus.has_key(name)

    def get_lu(self, name):
        return self.lus[name] if self.has_lu(name) else None


#
# Class for FrameNet lexical units.
#
class FNLexicalUnit(FNBase):
    def __init__(self, props={}):
        super(FNLexicalUnit, self).__init__(props)
        self.rels = []


#
# Encapsulates an Alias node for alternate names.
#
class WintermuteAlias(object):
    def __init__(self, name='', props={}):
        self._type = 'Alias'
        self.name = name
        self.__dict__.update(props)


#
# Encapsulates the minimum requirements for a Schema.org Thing.
# http://pending.schema.org/Thing
#
class WintermuteThing(object):
    def __init__(self, name='', label='Thing', props={}):
        self._type = label
        self.additionalType = ''
        self.alternateName = ''
        self.description = ''
        self.disambiguatingDescription = ''
        self.image = ''
        self.mainEntityOfPage = ''
        self.name = name
        self.sameAs = ''
        self.url = ''
        self.__dict__.update(props)

    def get_labels(self):
        return self._type.split(':')

    # Format a response for this object.
    def get_response(self):
        if hasattr(self, 'description'):
            parsed_desc = parsetree(self.description)
            if len(parsed_desc) > 0:
                return parsed_desc[0].string
        return ''

#
# Encapsulates the minimum requirements for a Schema.org Specialty.
# https://schema.org/Specialty
#
class WintermuteSpecialty(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteSpecialty, self).__init__(name, 'Thing:Intangible:Enumeration:Specialty', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)


#
# Encapsulates the minimum requirements for a Schema.org Course.
# https://pending.schema.org/Course
#
class WintermuteCourse(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteCourse, self).__init__(name, 'Thing:CreativeWork:Course', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        parsed_desc = parsetree(self.description)
        if len(parsed_desc) > 0:
            return "The course description for " + self.alternateName + " (" + self.name + ") is: " + parsed_desc[0].string
        return ''

#
# Encapsulates the minimum requirements for a custom Schema.org CourseSurvey.
#
class WintermuteCourseSurvey(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteCourseSurvey, self).__init__(name, 'Thing:CreativeWork:CourseSurvey', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        return "The course survey for " + self.alternateName + " (" + self.name + ") rates this course " \
               + str(self.rating) + "/5 with an average workload of " + str(self.workload) \
               + " hours/week and overall difficulty of " + str(self.difficulty) + "/5."


#
# Schema.org class for Book.
# http://schema.org/Book
#
class WintermuteBook(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteBook, self).__init__(name, 'Thing:CreativeWork:Book', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteCourseSurvey, self).get_response()


#
# Schema.org class for BookSeries.
# http://schema.org/BookSeries
#
class WintermuteBookSeries(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteBookSeries, self).__init__(name, 'Thing:CreativeWork:CreativeWorkSeries:BookSeries', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteBookSeries, self).get_response()


#
# Schema.org class for EducationalOrganization.
# http://schema.org/EducationalOrganization
#
class WintermuteEducationalOrganization(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteEducationalOrganization, self).__init__(name, 'Thing:Organization:EducationalOrganization', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteEducationalOrganization, self).get_response()


#
# Schema.org class for Event.
# http://schema.org/Event
#
class WintermuteEvent(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteEvent, self).__init__(name, 'Thing:Event', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteEvent, self).get_response()


#
# Schema.org class for GovernmentOrganization.
# http://schema.org/GovernmentOrganization
#
class WintermuteGovernmentOrganization(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteGovernmentOrganization, self).__init__(name, 'Thing:Organization:GovernmentOrganization', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteGovernmentOrganization, self).get_response()


#
# Schema.org class for LocalBusiness.
# http://schema.org/LocalBusiness
#
class WintermuteLocalBusiness(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteLocalBusiness, self).__init__(name, 'Thing:Organization:LocalBusiness:Place', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteLocalBusiness, self).get_response()


#
# Schema.org class for Movie.
# http://schema.org/Movie
#
class WintermuteMovie(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteMovie, self).__init__(name, 'Thing:CreativeWork:Movie', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteMovie, self).get_response()


#
# Schema.org class for MovieSeries.
# http://schema.org/MovieSeries
#
class WintermuteMovieSeries(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteMovieSeries, self).__init__(name, 'Thing:CreativeWork:CreativeWorkSeries:MovieSeries', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteMovieSeries, self).get_response()


#
# Schema.org class for MusicAlbum.
# http://schema.org/MusicAlbum
#
class WintermuteMusicAlbum(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteMusicAlbum, self).__init__(name, 'Thing:CreativeWork:MusicPlaylist:MusicAlbum', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteMusicAlbum, self).get_response()


#
# Schema.org class for MusicGroup.
# http://schema.org/MusicGroup
#
class WintermuteMusicGroup(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteMusicGroup, self).__init__(name, 'Thing:Organization:PerformingGroup:MusicGroup', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteMusicGroup, self).get_response()


#
# Schema.org class for MusicRecording.
# http://schema.org/MusicRecording
#
class WintermuteMusicRecording(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteMusicRecording, self).__init__(name, 'Thing:CreativeWork:MusicRecording', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteMusicRecording, self).get_response()


#
# Schema.org class for Organization.
# http://schema.org/Organization
#
class WintermuteOrganization(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteOrganization, self).__init__(name, 'Thing:Organization', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteOrganization, self).get_response()


#
# Schema.org class for Periodical.
# http://schema.org/Periodical
#
class WintermutePeriodical(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermutePeriodical, self).__init__(name, 'Thing:CreativeWork:CreativeWorkSeries:Periodical', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermutePeriodical, self).get_response()


#
# Schema.org class for Person.
# http://schema.org/Person
#
class WintermutePerson(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermutePerson, self).__init__(name, 'Thing:Person', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermutePerson, self).get_response()


#
# Schema.org class for Place.
# http://schema.org/Place
#
class WintermutePlace(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermutePlace, self).__init__(name, 'Thing:Place', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermutePlace, self).get_response()


#
# Schema.org class for SportsTeam.
# http://schema.org/SportsTeam
#
class WintermuteSportsTeam(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteSportsTeam, self).__init__(name, 'Thing:Organization:SportsOrganization:SportsTeam', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteSportsTeam, self).get_response()


#
# Schema.org class for TVEpisode.
# http://schema.org/TVEpisode
#
class WintermuteTVEpisode(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteTVEpisode, self).__init__(name, 'Thing:CreativeWork:Episode:TVEpisode', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteTVEpisode, self).get_response()


#
# Schema.org class for TVSeries.
# http://schema.org/TVSeries
#
class WintermuteTVSeries(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteTVSeries, self).__init__(name, 'Thing:CreativeWork:CreativeWorkSeries:TVSeries', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteTVSeries, self).get_response()


#
# Schema.org class for VideoGame.
# http://schema.org/VideoGame
#
class WintermuteVideoGame(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteVideoGame, self).__init__(name, 'Thing:CreativeWork:Game:VideoGame:SoftwareApplication', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteVideoGame, self).get_response()


#
# Schema.org class for VideoGameSeries.
# http://schema.org/VideoGameSeries
#
class WintermuteVideoGameSeries(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteVideoGameSeries, self).__init__(name, 'Thing:CreativeWork:CreativeWorkSeries:VideoGameSeries', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteVideoGameSeries, self).get_response()


#
# Schema.org class for WebSite.
# http://schema.org/WebSite
#
class WintermuteWebSite(WintermuteThing):
    def __init__(self, name, props={}):
        super(WintermuteWebSite, self).__init__(name, 'Thing:CreativeWork:WebSite', props=props)

    @classmethod
    def from_thing(cls, thing):
        return cls(thing.name, props=thing.__dict__)

    # Format a response for this object.
    def get_response(self):
        super(WintermuteWebSite, self).get_response()


#
# Init module globals.
#
_kb = WintermuteKnowledgebase()

