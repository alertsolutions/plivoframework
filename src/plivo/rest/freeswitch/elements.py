# -*- coding: utf-8 -*-
# Copyright (c) 2011 Plivo Team. See LICENSE for details.

import time
import os
import os.path
from datetime import datetime
import re
import uuid
try:
    import xml.etree.cElementTree as etree
except ImportError:
    from xml.etree.elementtree import ElementTree as etree

import gevent
from gevent import spawn_raw

from plivo.utils.files import mkdir_p, re_root
from plivo.rest.freeswitch.helpers import is_valid_url, is_sip_url, \
                                        file_exists, normalize_url_space, \
                                        get_resource, get_grammar_resource, \
                                        HTTPRequest, Stopwatch

from plivo.rest.freeswitch.beep_detector import *
from plivo.rest.freeswitch.playback_tool import PlaybackTool
from plivo.rest.freeswitch.exceptions import RESTFormatException, \
                                            RESTAttributeException, \
                                            RESTRedirectException, \
                                            RESTSIPTransferException, \
                                            RESTNoExecuteException, \
                                            RESTHangup

ELEMENTS_DEFAULT_PARAMS = {
        'Conference': {
                #'room': SET IN ELEMENT BODY
                'waitSound': '',
                'muted': 'false',
                'startConferenceOnEnter': 'true',
                'endConferenceOnExit': 'false',
                'stayAlone': 'true',
                'maxMembers': 200,
                'enterSound': '',
                'exitSound': '',
                'timeLimit': 0 ,
                'hangupOnStar': 'false',
                'recordFilePath': '',
                'recordFileFormat': 'mp3',
                'recordFileName': '',
                'action': '',
                'method': 'POST',
                'callbackUrl': '',
                'callbackMethod': 'POST',
                'digitsMatch': '',
                'floorEvent': 'false'
        },
        'Dial': {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST',
                'hangupOnStar': 'false',
                #callerId: DYNAMIC! MUST BE SET IN METHOD,
                #callerName: DYNAMIC! MUST BE SET IN METHOD,
                'timeLimit': 0,
                'confirmSound': '',
                'confirmKey': '',
                'dialMusic': '',
                'redirect': 'true',
                'callbackUrl': '',
                'callbackMethod': 'POST',
                'digitsMatch': ''
        },
        'BreakOnAnsweringMachine' : {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST'
        },
        'GetKeyPresses': {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST',
                'timeout': 5,
                'numDigits': 1,
                'validDigits': '0,1,2,3,4,5,6,7,8,9,*,#',
        },
        'GetDigits': {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST',
                'timeout': 5,
                'finishOnKey': '#',
                'numDigits': 99,
                'retries': 1,
                'playBeep': 'false',
                'validDigits': '0123456789*#',
                'invalidDigitsSound': ''
        },
        'AnsweringMachineDetect': {
                #'amdResultUrl': DYNAMIC! MUST BE SET IN METHOD,
                'preDetectPause': 500,
                'detectTime': 2000
        },
        'LeaveMessage': {
                'waitForBeep': 3,
                'detectType': 'avmd'
        },
        'Hangup': {
                'reason': '',
                'schedule': 0
        },
        'Number': {
                #'gateways': DYNAMIC! MUST BE SET IN METHOD,
                #'gatewayCodecs': DYNAMIC! MUST BE SET IN METHOD,
                #'gatewayTimeouts': DYNAMIC! MUST BE SET IN METHOD,
                #'gatewayRetries': DYNAMIC! MUST BE SET IN METHOD,
                #'extraDialString': DYNAMIC! MUST BE SET IN METHOD,
                'sendDigits': '',
        },
        'Wait': {
                'length': 1
        },
        'PlayMany' : {
        },
        'Play': {
                #url: SET IN ELEMENT BODY
                'loop': 1
        },
        'PreAnswer': {
        },
        'Record': {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST',
                'timeout': 15,
                'finishOnKey': '1234567890*#',
                'maxLength': 60,
                'playBeep': 'true',
                'filePath': '/usr/local/freeswitch/recordings/',
                'fileFormat': 'mp3',
                'fileName': '',
                'redirect': 'true',
                'bothLegs': 'false'
        },
        'SIPTransfer': {
                #url: SET IN ELEMENT BODY
        },
        'Redirect': {
                #url: SET IN ELEMENT BODY
                'method': 'POST'
        },
        'Notify': {
                #url: SET IN ELEMENT BODY
                'method': 'POST'
        },
        'Speak': {
                'voice': 'slt',
                'language': 'en',
                'loop': 1,
                'engine': 'flite',
                'method': '',
                'type': ''
        },
        'SayDigits': {
                'language': 'en',
                'loop': 1,
                'method': 'ITERATED',
                'type': 'NUMBER'
        },
        'GetSpeech': {
                #action: DYNAMIC! MUST BE SET IN METHOD,
                'method': 'POST',
                'timeout': 5,
                'playBeep': 'false',
                'engine': 'pocketsphinx',
                'grammar': '',
                'grammarPath': '/usr/local/freeswitch/grammar'
        }
    }

MAX_LOOPS = 10000

# DTO class
class Result:
    def __init__(self, **kwargs):
        self.__dict__ = kwargs

class Element(object):
    """Abstract Element Class to be inherited by all other elements"""

    def __init__(self):
        self.name = str(self.__class__.__name__)
        self.nestables = None
        self.attributes = {}
        self.text = ''
        self.children = []
        self.uri = None
        self._element = None

    def get_element(self):
        return self._element

    def parse_element(self, element, uri=None):
        self.uri = uri 
        self._element = element
        self.prepare_attributes(element)
        self.prepare_text(element)

    def run(self, outbound_socket):
        outbound_socket.log.info("[%s] %s %s" \
            % (self.name, self.text, self.attributes))
        execute = getattr(self, 'execute', None)
        if not execute:
            outbound_socket.log.error("[%s] Element cannot be executed !" % self.name)
            raise RESTNoExecuteException("Element %s cannot be executed !" % self.name)
        try:
            outbound_socket.current_element = self.name
            result = execute(outbound_socket)
            outbound_socket.current_element = None
        except RESTHangup:
            outbound_socket.log.info("[%s] Done (hangup)" % self.name)
            raise
        except RESTRedirectException:
            outbound_socket.log.info("[%s] Done (redirect)" % self.name)
            raise
        except RESTSIPTransferException:
            outbound_socket.log.info("[%s] Done (sip transfer)" % self.name)
            raise
        if not result:
            outbound_socket.log.info("[%s] Done" % self.name)
        else:
            outbound_socket.log.info("[%s] Done -- Result %s" % (self.name, result))

    def extract_attribute_value(self, item, default=None):
        try:
            item = self.attributes[item]
        except KeyError:
            item = default
        return item

    def prepare_attributes(self, element):
        element_dict = ELEMENTS_DEFAULT_PARAMS[self.name]
        if element.attrib and not element_dict:
            raise RESTFormatException("%s does not require any attributes!"
                                                                % self.name)
        self.attributes = dict(element_dict, **element.attrib)

    def prepare_text(self, element):
        text = element.text
        if not text:
            self.text = ''
        else:
            self.text = text.strip()

    def fetch_rest_xml(self, url, params={}, method='POST'):
        raise RESTRedirectException(url, params, method)

class Conference(Element):
    """Go to a Conference Room
    room name is body text of Conference element.

    waitSound: sound to play while alone in conference
          Can be a list of sound files separated by comma.
          (default no sound)
    muted: enter conference muted
          (default false)
    startConferenceOnEnter: the conference start when this member joins
          (default true)
    endConferenceOnExit: close conference after all members
        with this attribute set to 'true' leave. (default false)
    stayAlone: if 'false' and member is alone, conference is closed and member kicked out
          (default true)
    maxMembers: max members in conference
          (0 for max : 200)
    enterSound: sound to play when a member enters
          if empty, disabled
          if 'beep:1', play one beep
          if 'beep:2', play two beeps
          (default disabled)
    exitSound: sound to play when a member exits
          if empty, disabled
          if 'beep:1', play one beep
          if 'beep:2', play two beeps
          (default disabled)
    timeLimit: max time in seconds before closing conference
          (default 0, no timeLimit)
    hangupOnStar: exit conference when member press '*'
          (default false)
    recordFilePath: path where recording is saved.
        (default "" so recording wont happen)
    recordFileFormat: file format in which recording tis saved
        (default mp3)
    recordFileName: By default empty, if provided this name will be used for the recording
        (any unique name)
    action: redirect to this URL after leaving conference
    method: submit to 'action' url using GET or POST
    callbackUrl: url to request when call enters/leaves conference
            or has pressed digits matching (digitsMatch) or member is speaking (speakEvent)
    callbackMethod: submit to 'callbackUrl' url using GET or POST
    digitsMatch: a list of matching digits to send with callbackUrl
            Can be a list of digits patterns separated by comma.
    floorEvent: 'true' or 'false'. When this member holds the floor, 
            send notification to callbackUrl. (default 'false')
    """
    DEFAULT_TIMELIMIT = 0
    DEFAULT_MAXMEMBERS = 200

    def __init__(self):
        Element.__init__(self)
        self.full_room = ''
        self.room = ''
        self.moh_sound = None
        self.muted = False
        self.start_on_enter = True
        self.end_on_exit = False
        self.stay_alone = False
        self.time_limit = self.DEFAULT_TIMELIMIT
        self.max_members = self.DEFAULT_MAXMEMBERS
        self.enter_sound = ''
        self.exit_sound = ''
        self.hangup_on_star = False
        self.record_file_path = ""
        self.record_file_format = "mp3"
        self.record_filename = ""
        self.action = ''
        self.method = ''
        self.callback_url = ''
        self.callback_method = ''
        self.speaker = False
        self.conf_id = ''
        self.member_id = ''

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        room = self.text
        if not room:
            raise RESTFormatException('Conference Room must be defined')
        self.full_room = room + '@plivo'
        self.room = room
        self.moh_sound = self.extract_attribute_value('waitSound')
        self.muted = self.extract_attribute_value('muted') \
                        == 'true'
        self.start_on_enter = self.extract_attribute_value('startConferenceOnEnter') \
                                == 'true'
        self.end_on_exit = self.extract_attribute_value('endConferenceOnExit') \
                                == 'true'
        self.stay_alone = self.extract_attribute_value('stayAlone') \
                                == 'true'
        self.hangup_on_star = self.extract_attribute_value('hangupOnStar') \
                                == 'true'
        try:
            self.time_limit = int(self.extract_attribute_value('timeLimit',
                                                          self.DEFAULT_TIMELIMIT))
        except ValueError:
            self.time_limit = self.DEFAULT_TIMELIMIT
        if self.time_limit <= 0:
            self.time_limit = self.DEFAULT_TIMELIMIT
        try:
            self.max_members = int(self.extract_attribute_value('maxMembers',
                                                        self.DEFAULT_MAXMEMBERS))
        except ValueError:
            self.max_members = self.DEFAULT_MAXMEMBERS
        if self.max_members <= 0 or self.max_members > self.DEFAULT_MAXMEMBERS:
            self.max_members = self.DEFAULT_MAXMEMBERS

        self.enter_sound = self.extract_attribute_value('enterSound')
        self.exit_sound = self.extract_attribute_value('exitSound')

        self.record_file_path = self.extract_attribute_value("recordFilePath")
        if self.record_file_path:
            self.record_file_path = os.path.normpath(self.record_file_path)\
                                                                    + os.sep
        self.record_file_format = \
                            self.extract_attribute_value("recordFileFormat")
        if self.record_file_format not in ('wav', 'mp3'):
            raise RESTFormatException("Format must be 'wav' or 'mp3'")
        self.record_filename = \
                            self.extract_attribute_value("recordFileName")

        self.method = self.extract_attribute_value("method")
        if not self.method in ('GET', 'POST'):
            raise RESTAttributeException("method must be 'GET' or 'POST'")
        self.action = self.extract_attribute_value("action")

        self.callback_url = self.extract_attribute_value("callbackUrl")
        self.callback_method = self.extract_attribute_value("callbackMethod")
        if not self.callback_method in ('GET', 'POST'):
            raise RESTAttributeException("callbackMethod must be 'GET' or 'POST'")
        self.digits_match = self.extract_attribute_value("digitsMatch")
        self.floor = self.extract_attribute_value("floorEvent") == 'true'

    def _prepare_moh(self, outbound_socket):
        sound_files = []
        if not self.moh_sound:
            return sound_files
        outbound_socket.log.info('Fetching remote sound from restxml %s' % self.moh_sound)
        try:
            response = outbound_socket.send_to_url(self.moh_sound, params={}, method='POST')
            doc = etree.fromstring(response)
            if doc.tag != 'Response':
                outbound_socket.log.warn('No Response Tag Present')
                return sound_files

            # build play string from remote restxml
            for element in doc:
                # Play element
                if element.tag == 'Play':
                    child_instance = Play()
                    child_instance.parse_element(element)
                    child_instance.prepare(outbound_socket)
                    sound_file = child_instance.sound_file_path
                    if sound_file:
                        sound_file = get_resource(outbound_socket, sound_file)
                        loop = child_instance.loop_times
                        if loop == 0:
                            loop = MAX_LOOPS  # Add a high number to Play infinitely
                        # Play the file loop number of times
                        for i in range(loop):
                            sound_files.append(re_root(sound_file, outbound_socket.save_dir))
                        # Infinite Loop, so ignore other children
                        if loop == MAX_LOOPS:
                            break
                # Wait element
                elif element.tag == 'Wait':
                    child_instance = Wait()
                    child_instance.parse_element(element)
                    pause_secs = child_instance.length
                    pause_str = 'file_string://silence_stream://%s' % (pause_secs * 1000)
                    sound_files.append(pause_str)
        except Exception, e:
            outbound_socket.log.warn('Fetching remote sound from restxml failed: %s' % str(e))
        finally:
            outbound_socket.log.info('Fetching remote sound from restxml done')
            return sound_files

    def _notify_enter_conf(self, outboundsocket):
        if not self.callback_url or not self.conf_id or not self.member_id:
            return
        params = {}
        params['ConferenceName'] = self.room
        params['ConferenceUUID'] = self.conf_id or ''
        params['ConferenceMemberID'] = self.member_id or ''
        params['ConferenceAction'] = 'enter'
        spawn_raw(outboundsocket.send_to_url, self.callback_url, params, self.callback_method)

    def _notify_exit_conf(self, outboundsocket):
        if not self.callback_url or not self.conf_id or not self.member_id:
            return
        params = {}
        params['ConferenceName'] = self.room
        params['ConferenceUUID'] = self.conf_id or ''
        params['ConferenceMemberID'] = self.member_id or ''
        params['ConferenceAction'] = 'exit'
        spawn_raw(outboundsocket.send_to_url, self.callback_url, params, self.callback_method)

    def _notify_floor_holder(self, outboundsocket):
        if not self.floor or not self.callback_url or not self.conf_id or not self.member_id:
            return
        outboundsocket.log.debug("Floor holder into Conference")
        params = {}
        params['ConferenceName'] = self.room
        params['ConferenceUUID'] = self.conf_id or ''
        params['ConferenceMemberID'] = self.member_id or ''
        params['ConferenceAction'] = 'floor'
        spawn_raw(outboundsocket.send_to_url, self.callback_url, params, self.callback_method)

    def execute(self, outbound_socket):
        flags = []
        # settings for conference room
        outbound_socket.set("conference_controls=none")
        if self.max_members > 0:
            outbound_socket.set("conference_max_members=%d" % self.max_members)
        else:
            outbound_socket.unset("conference_max_members")

        if self.record_file_path:
            file_path = os.path.normpath(self.record_file_path) + os.sep
            if self.record_filename:
                filename = self.record_filename
            else:
                filename = "%s_%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"),
                                      outbound_socket.get_channel_unique_id())
            record_file = "%s%s.%s" % (file_path, filename,
                                        self.record_file_format)
        else:
            record_file = None

        # set moh sound
        mohs = self._prepare_moh(outbound_socket)
        if mohs:
            outbound_socket.set("playback_delimiter=!")
            play_str = '!'.join(mohs)
            play_str = "file_string://silence_stream://1!%s" % play_str
            outbound_socket.set("conference_moh_sound=%s" % play_str)
        else:
            outbound_socket.unset("conference_moh_sound")
        # set member flags
        if self.muted:
            flags.append("mute")
        if self.start_on_enter:
            flags.append("moderator")
        if not self.stay_alone:
            flags.append("mintwo")
        if self.end_on_exit:
            flags.append("endconf")
        flags_opt = ','.join(flags)
        if flags_opt:
            outbound_socket.set("conference_member_flags=%s" % flags_opt)
        else:
            outbound_socket.unset("conference_member_flags")

        # play beep on exit if enabled
        if self.exit_sound == 'beep:1':
            outbound_socket.set("conference_exit_sound=tone_stream://%%(300,200,700)")
        elif self.exit_sound == 'beep:2':
            outbound_socket.set("conference_exit_sound=tone_stream://L=2;%%(300,200,700)")

        # set new kickall scheduled task if timeLimit > 0
        if self.time_limit > 0:
            # set timeLimit scheduled group name for the room
            sched_group_name = "conf_%s" % self.room
            # always clean old kickall tasks for the room
            outbound_socket.api("sched_del %s" % sched_group_name)
            # set new kickall task for the room
            outbound_socket.api("sched_api +%d %s conference %s kick all" \
                                % (self.time_limit, sched_group_name, self.room))
            outbound_socket.log.warn("Conference: Room %s, timeLimit set to %d seconds" \
                                    % (self.room, self.time_limit))
        # really enter conference room
        outbound_socket.log.info("Entering Conference: Room %s (flags %s)" \
                                        % (self.room, flags_opt))
        res = outbound_socket.conference(self.full_room, lock=False)
        if not res.is_success():
            outbound_socket.log.error("Conference: Entering Room %s Failed" \
                                % (self.room))
            return
        # get next event
        event = outbound_socket.wait_for_action()

        # if event is add-member, get Member-ID
        # and set extra features for conference
        # else conference element ending here
        try:
            digit_realm = ''
            if event['Event-Subclass'] == 'conference::maintenance' \
                and event['Action'] == 'add-member':
                self.member_id = event['Member-ID']
                self.conf_id = event['Conference-Unique-ID']
                outbound_socket.log.debug("Entered Conference: Room %s with Member-ID %s" \
                                % (self.room, self.member_id))
                has_floor = event['Floor'] == 'true'
                can_speak = event['Speak'] == 'true'
                is_first = event['Conference-Size'] == '1'
                # notify channel has entered room
                self._notify_enter_conf(outbound_socket)
                # notify floor holder only if :
                # floor is true and member is not muted and member is the first one
                if has_floor and can_speak and is_first:
                    self._notify_floor_holder(outbound_socket)

                # set bind digit actions
                if self.digits_match and self.callback_url:
                    # create event template
                    event_template = "Event-Name=CUSTOM,Event-Subclass=conference::maintenance,Action=digits-match,Unique-ID=%s,Callback-Url=%s,Callback-Method=%s,Member-ID=%s,Conference-Name=%s,Conference-Unique-ID=%s" \
                        % (outbound_socket.get_channel_unique_id(), self.callback_url, self.callback_method, self.member_id, self.room, self.conf_id)
                    digit_realm = "plivo_bda_%s" % outbound_socket.get_channel_unique_id()
                    # for each digits match, set digit binding action
                    for dmatch in self.digits_match.split(','):
                        dmatch = dmatch.strip()
                        if dmatch:
                            raw_event = "%s,Digits-Match=%s" % (event_template, dmatch)
                            cmd = "%s,%s,exec:event,'%s'" % (digit_realm, dmatch, raw_event)
                            outbound_socket.bind_digit_action(cmd)
                # set hangup on star
                if self.hangup_on_star:
                    # create event template
                    raw_event = "Event-Name=CUSTOM,Event-Subclass=conference::maintenance,Action=kick,Unique-ID=%s,Member-ID=%s,Conference-Name=%s,Conference-Unique-ID=%s" \
                        % (outbound_socket.get_channel_unique_id(), self.member_id, self.room, self.conf_id)
                    digit_realm = "plivo_bda_%s" % outbound_socket.get_channel_unique_id()
                    cmd = "%s,*,exec:event,'%s'" % (digit_realm, raw_event)
                    outbound_socket.bind_digit_action(cmd)
                # set digit realm
                if digit_realm:
                    outbound_socket.digit_action_set_realm(digit_realm)

                # play beep on enter if enabled
                if self.member_id:
                    if self.enter_sound == 'beep:1':
                        outbound_socket.bgapi("conference %s play tone_stream://%%(300,200,700) async" % self.room)
                    elif self.enter_sound == 'beep:2':
                        outbound_socket.bgapi("conference %s play tone_stream://L=2;%%(300,200,700) async" % self.room)

                # record conference if set
                if record_file:
                    outbound_socket.bgapi("conference %s record %s" % (self.room, record_file))
                    outbound_socket.log.info("Conference: Room %s, recording to file %s" \
                                    % (self.room, record_file))

                # wait conference ending for this member
                outbound_socket.log.debug("Conference: Room %s, waiting end ..." % self.room)
                while outbound_socket.ready():
                    event = outbound_socket.wait_for_action()
                    if event['Action'] == 'floor-change':
                        self._notify_floor_holder(outbound_socket)
                        continue
                    break

            # unset digit realm
            if digit_realm:
                outbound_socket.clear_digit_action(digit_realm)

        finally:
            # notify channel has left room
            self._notify_exit_conf(outbound_socket)
            outbound_socket.log.info("Leaving Conference: Room %s" % self.room)

            # If action is set, redirect to this url
            # Otherwise, continue to next Element
            if self.action and is_valid_url(self.action):
                params = {}
                params['ConferenceName'] = self.room
                params['ConferenceUUID'] = self.conf_id or ''
                params['ConferenceMemberID'] = self.member_id or ''
                if record_file:
                    params['RecordFile'] = record_file
                self.fetch_rest_xml(self.action, params, method=self.method)

class Dial(Element):
    """Dial another phone number and connect it to this call

    action: submit the result of the dial and redirect to this URL
    method: submit to 'action' url using GET or POST
    hangupOnStar: hangup the b leg if a leg presses start and this is true
    callerId: caller id to be send to the dialed number
    timeLimit: hangup the call after these many seconds. 0 means no timeLimit
    confirmSound: Sound to be played to b leg before call is bridged
    confirmKey: Key to be pressed to bridge the call.
    dialMusic: Play music to a leg while doing a dial to b leg
                Can be a list of files separated by comma
    redirect: if 'false', don't redirect to 'action', only request url
        and continue to next element. (default 'true')
    callbackUrl: url to request when bridge starts and bridge ends
    callbackMethod: submit to 'callbackUrl' url using GET or POST
    """
    DEFAULT_TIMELIMIT = 14400

    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Number',)
        self.method = ''
        self.action = ''
        self.hangup_on_star = False
        self.caller_id = ''
        self.caller_name = ''
        self.time_limit = self.DEFAULT_TIMELIMIT
        self.timeout = -1
        self.dial_str = ''
        self.confirm_sound = ''
        self.confirm_key = ''
        self.dial_music = ''
        self.redirect = True

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        self.action = self.extract_attribute_value('action')
        self.caller_id = self.extract_attribute_value('callerId')
        self.caller_name = self.extract_attribute_value('callerName')
        try:
            self.time_limit = int(self.extract_attribute_value('timeLimit',
                                                      self.DEFAULT_TIMELIMIT))
        except ValueError:
            self.time_limit = self.DEFAULT_TIMELIMIT
        if self.time_limit <= 0:
            self.time_limit = self.DEFAULT_TIMELIMIT
        try:
            self.timeout = int(self.extract_attribute_value("timeout", -1))
        except ValueError:
            self.timeout = -1
        if self.timeout <= 0:
            self.timeout = -1
        self.confirm_sound = self.extract_attribute_value("confirmSound")
        self.confirm_key = self.extract_attribute_value("confirmKey")
        self.dial_music = self.extract_attribute_value("dialMusic")
        self.hangup_on_star = self.extract_attribute_value("hangupOnStar") \
                                                                == 'true'
        self.redirect = self.extract_attribute_value("redirect") == 'true'

        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("method must be 'GET' or 'POST'")
        self.method = method

        self.callback_url = self.extract_attribute_value("callbackUrl")
        self.callback_method = self.extract_attribute_value("callbackMethod")
        if not self.callback_method in ('GET', 'POST'):
            raise RESTAttributeException("callbackMethod must be 'GET' or 'POST'")
        self.digits_match = self.extract_attribute_value("digitsMatch")

    def execute(self, outbound_socket):
        sched_hangup_id = str(uuid.uuid1())
        self.__set_channel_variables(outbound_socket)

        numbers = self.__get_numbers(outbound_socket)
        if not numbers:
            outbound_socket.log.error("Dial Aborted, No Number to dial !")
            return

        self.dial_str = self.__create_dial_string(outbound_socket, numbers, sched_hangup_id)

        self.__play_ringback(outbound_socket)

        hangup_cause = 'NORMAL_CLEARING'
        outbound_socket.log.info("Dial Started %s" % self.dial_str)
        reason = None
        try:
            outbound_socket.execute('flush_dtmf')
            outbound_socket.ring_ready()

            if self.digits_match and self.callback_url:
                self.__set_bind_digit_actions()

            # outbound_socket.api("log notice bridge %s" % self.dial_str)
            outbound_socket.bridge(self.dial_str, lock=False)

            event = self.__wait_for_completion(outbound_socket)
            outbound_socket.log.info("Dial completed %s" % event['Event-Name'])

            reason = self.__get_reason(event, outbound_socket)

            outbound_socket.bgapi("sched_del %s" % sched_hangup_id)
        except Exception, e:
            outbound_socket.log.warn('Error processing transfer result (%s): %s' \
                                      % (e.__class__.__name__, str(e)))
        finally:
            outbound_socket.log.info("Dial Finished with reason: %s" \
                                     % reason.reason)
            if self.action and is_valid_url(self.action):
                self.__redirect(outbound_socket, reason)

    def __prepare_play_string(self, outbound_socket, remote_url):
        sound_files = []
        if not remote_url:
            return sound_files

        if not is_valid_url(remote_url):
            outbound_socket.log.info('Assuming %s is a file' % remote_url)
            local_file = re_root(remote_url, outbound_socket.save_dir)
            if file_exists(local_file):
                sound_files.append(local_file)
            else:
                outbound_socket.log.info('%s does not exist' % remote_url)
            return sound_files

        outbound_socket.log.info('Fetching remote sound from restxml %s' % remote_url)
        try:
            response = outbound_socket.send_to_url(remote_url, params={}, method='POST')
            doc = etree.fromstring(response)
            sound_files = self.__parse_xml(doc)
        except Exception, e:
            outbound_socket.log.warn('Fetching remote sound from restxml failed: %s' % str(e))
        finally:
            outbound_socket.log.info('Fetching remote sound from restxml done for %s' % remote_url)
            return sound_files

    def __parse_xml(self, doc):
        if doc.tag != 'Response':
            outbound_socket.log.warn('No Response Tag Present')
            return sound_files

        # build play string from remote restxml
        for element in doc:
            if element.tag == 'Play':
                child_instance = Play()
                child_instance.parse_element(element)
                child_instance.prepare(outbound_socket)
                sound_file = child_instance.sound_file_path
                if sound_file:
                    sound_file = get_resource(outbound_socket, sound_file)
                    loop = child_instance.loop_times
                    if loop == 0:
                        loop = MAX_LOOPS  # Add a high number to Play infinitely
                    # Play the file loop number of times
                    for i in range(loop):
                        sound_files.append(sound_file)
                    # Infinite Loop, so ignore other children
                    if loop == MAX_LOOPS:
                        break
            elif element.tag == 'Speak':
                child_instance = Speak()
                child_instance.parse_element(element)
                text = child_instance.text
                # escape simple quote
                text = text.replace("'", "\\'")
                loop = child_instance.loop_times
                child_type = child_instance.item_type
                method = child_instance.method
                say_str = ''
                if child_type and method:
                    language = child_instance.language
                    say_args = "%s.wav %s %s %s '%s'" \
                                    % (language, language, child_type, method, text)
                    say_str = "${say_string %s}" % say_args
                else:
                    engine = child_instance.engine
                    voice = child_instance.voice
                    say_str = "say:%s:%s:'%s'" % (engine, voice, text)
                if not say_str:
                    continue
                for i in range(loop):
                    sound_files.append(say_str)
            elif element.tag == 'Wait':
                child_instance = Wait()
                child_instance.parse_element(element)
                pause_secs = child_instance.length
                pause_str = 'file_string://silence_stream://%s' % (pause_secs * 1000)
                sound_files.append(pause_str)

    def __create_number(self, number_instance, outbound_socket):
        num_gw = []
        # skip number object without gateway or number
        if not number_instance.gateways:
            outbound_socket.log.error("Gateway not defined on Number object !")
            return ''
        if not number_instance.number:
            outbound_socket.log.error("Number not defined on Number object  !")
            return ''
        if number_instance.send_digits:
            if number_instance.send_on_preanswer is True:
                option_send_digits = "api_on_media='uuid_recv_dtmf ${uuid} %s'" \
                                                    % number_instance.send_digits
            else:
                option_send_digits = "api_on_answer_2='uuid_recv_dtmf ${uuid} %s'" \
                                                    % number_instance.send_digits
        else:
            option_send_digits = ''
        count = 0
        for gw in number_instance.gateways:
            num_options = []

            if self.callback_url and self.callback_method:
                num_options.append('plivo_dial_callback_url=%s' % self.callback_url)
                num_options.append('plivo_dial_callback_method=%s' % self.callback_method)
                num_options.append('plivo_dial_callback_aleg=%s' % outbound_socket.get_channel_unique_id())

            if option_send_digits:
                num_options.append(option_send_digits)
            try:
                gw_codec = number_instance.gateway_codecs[count]
                num_options.append('absolute_codec_string=%s' % gw_codec)
            except IndexError:
                pass
            try:
                gw_timeout = int(number_instance.gateway_timeouts[count])
                if gw_timeout > 0:
                    num_options.append('leg_timeout=%d' % gw_timeout)
            except (IndexError, ValueError):
                pass
            try:
                gw_retries = int(number_instance.gateway_retries[count])
                if gw_retries <= 0:
                    gw_retries = 1
            except (IndexError, ValueError):
                gw_retries = 1
            extra_dial_string = number_instance.extra_dial_string
            if extra_dial_string:
                num_options.append(extra_dial_string)
            if num_options:
                options = '[%s]' % (','.join(num_options))
            else:
                options = ''
            num_str = "%s%s%s" % (options, gw, number_instance.number)
            dial_num = '|'.join([num_str for retry in range(gw_retries)])
            num_gw.append(dial_num)
            count += 1
        result = '|'.join(num_gw)
        return result

    def __wait_for_completion(self, outbound_socket):
        event = None
        while not outbound_socket.has_hangup() and \
            (event is None or event['Application'] == 'playback'):
            event = outbound_socket.wait_for_action()

        if event['Event-Name'] == 'CHANNEL_UNBRIDGE' \
            or (event is None and outbound_socket.has_hangup()):
            event = outbound_socket.wait_for_action()

        return event

    def __get_numbers(self, outbound_socket):
        # Set numbers to dial from Number nouns
        numbers = []
        for child in self.children:
            if isinstance(child, Number):
                dial_num = self.__create_number(child, outbound_socket)
                if not dial_num:
                    continue
                numbers.append(dial_num)
        return numbers

    def __create_dial_string(self, outbound_socket, numbers, sched_hangup_id):
        dial_str = ':_:'.join(numbers)

        # Set ring flag if dial will ring.
        # But first set plivo_dial_rang to false
        # to be sure we don't get it from an old Dial
        outbound_socket.set("plivo_dial_rang=false")
        ring_flag = "api_on_ring='uuid_setvar %s plivo_dial_rang true',api_on_pre_answer='uuid_setvar %s plivo_dial_rang true'" \
                    % (outbound_socket.get_channel_unique_id(), outbound_socket.get_channel_unique_id())

        # Set time limit: when reached, B Leg is hung up
        dial_time_limit = "api_on_answer_1='sched_api +%d %s uuid_transfer %s -bleg hangup:ALLOTTED_TIMEOUT inline'" \
                      % (self.time_limit, sched_hangup_id, outbound_socket.get_channel_unique_id())

        # Set confirm sound and key or unset if not provided
        dial_confirm = ''
        if self.confirm_sound:
            confirm_sounds = self.__prepare_play_string(outbound_socket, self.confirm_sound)
            if confirm_sounds:
                play_str = '!'.join(confirm_sounds)
                play_str = "file_string://silence_stream://1!%s" % play_str
                # Use confirm key if present else just play music
                if self.confirm_key:
                    confirm_music_str = "group_confirm_file=%s" % play_str
                    confirm_key_str = "group_confirm_key=%s" % self.confirm_key
                else:
                    confirm_music_str = "group_confirm_file=playback %s" % play_str
                    confirm_key_str = "group_confirm_key=exec"
                # Cancel the leg timeout after the call is answered
                confirm_cancel = "group_confirm_cancel_timeout=1"
                dial_confirm = ",%s,%s,%s,playback_delimiter=!" % (confirm_music_str, confirm_key_str, confirm_cancel)

        # Ugly hack to force use of enterprise originate because simple originate lacks speak support in ringback
        # TODO: this hack breaks bridge for some reason, revisit when we implement speak, I guess
        #if len(numbers) < 2:
        #    dial_str += ':_:'

        # Prepend time limit and group confirm to dial string
        return '<%s,%s%s>%s' % (ring_flag, dial_time_limit, dial_confirm, dial_str)

    def __set_channel_variables(self, outbound_socket):
        # Set timeout
        if self.timeout > 0:
            outbound_socket.set("call_timeout=%d" % self.timeout)
        else:
            outbound_socket.unset("call_timeout")

        # Set callerid or unset if not provided
        if self.caller_id == 'none':
            outbound_socket.set("effective_caller_id_number=''")
        elif self.caller_id:
            outbound_socket.set("effective_caller_id_number=%s" % self.caller_id)
        else:
            outbound_socket.unset("effective_caller_id_number")
        # Set callername or unset if not provided
        if self.caller_name == 'none':
            outbound_socket.set("effective_caller_id_name=''")
        elif self.caller_name:
            outbound_socket.set("effective_caller_id_name='%s'" % self.caller_name)
        else:
            outbound_socket.unset("effective_caller_id_name")
        # Set continue on fail
        outbound_socket.set("continue_on_fail=true")
        # Don't hangup after bridge !
        outbound_socket.set("hangup_after_bridge=false")

        # Set hangup on '*' or unset if not provided
        if self.hangup_on_star:
            outbound_socket.set("bridge_terminate_key=*")
        else:
            outbound_socket.unset("bridge_terminate_key")

    def __play_ringback(self, outbound_socket):
        ringbacks = ''
        if self.dial_music and self.dial_music != "none":
            ringbacks = self.__prepare_play_string(outbound_socket, self.dial_music)
            if ringbacks:
                outbound_socket.set("playback_delimiter=!")
                play_str = '!'.join(ringbacks)
                play_str = "file_string://silence_stream://1!%s" % play_str
                outbound_socket.set("bridge_early_media=false")
                outbound_socket.set("instant_ringback=true")
                outbound_socket.set("ringback=%s" % play_str)
            else:
                self.dial_music = None
        if not self.dial_music:
            outbound_socket.set("bridge_early_media=false")
            outbound_socket.set("instant_ringback=true")
            outbound_socket.set("ringback=${us-ring}")
        elif self.dial_music == "none":
            outbound_socket.set("bridge_early_media=false")
            outbound_socket.unset("instant_ringback")
            outbound_socket.unset("ringback")

    def __set_bind_digit_actions(self):
        # create event template
        event_template = "Event-Name=CUSTOM,Event-Subclass=plivo::dial,Action=digits-match,Unique-ID=%s,Callback-Url=%s,Callback-Method=%s" \
            % (outbound_socket.get_channel_unique_id(), self.callback_url, self.callback_method)
        digit_realm = "plivo_bda_dial_%s" % outbound_socket.get_channel_unique_id()
        # for each digits match, set digit binding action
        for dmatch in self.digits_match.split(','):
            dmatch = dmatch.strip()
            if dmatch:
                raw_event = "%s,Digits-Match=%s" % (event_template, dmatch)
                cmd = "%s,%s,exec:event,'%s'" % (digit_realm, dmatch, raw_event)
                outbound_socket.bind_digit_action(cmd)
        outbound_socket.digit_action_set_realm(digit_realm)

    def __redirect(self, outbound_socket, reason):
        params = {}
        # Get ring status
        dial_rang = outbound_socket.get_var("plivo_dial_rang") == 'true'
        if dial_rang:
            params['DialRingStatus'] = 'true'
        else:
            params['DialRingStatus'] = 'false'
        params['DialHangupCause'] = reason.hangup_cause
        params['DialALegUUID'] = outbound_socket.get_channel_unique_id()
        params['DialBLegUUID'] = reason.bleg_uuid or ''
        if self.redirect:
            self.fetch_rest_xml(self.action, params, method=self.method)
        else:
            spawn_raw(outbound_socket.send_to_url, self.action, params, method=self.method)

    def __get_reason(self, event, outbound_socket):
        reason = None
        hangup_cause = event['variable_originate_disposition']
        if hangup_cause == 'ORIGINATOR_CANCEL':
            reason = '%s (A leg)' % hangup_cause
        else:
            reason = '%s (B leg)' % hangup_cause
        if not hangup_cause or hangup_cause == 'SUCCESS':
            hangup_cause = outbound_socket.get_hangup_cause()
            reason = '%s (A leg)' % hangup_cause
            if not hangup_cause:
                hangup_cause = event['variable_bridge_hangup_cause']
                reason = '%s (B leg)' % hangup_cause
                if not hangup_cause:
                    hangup_cause = event['variable_hangup_cause']
                    reason = '%s (A leg)' % hangup_cause
                    if not hangup_cause:
                        hangup_cause = 'NORMAL_CLEARING'
                        reason = '%s (A leg)' % hangup_cause

        bleg_uuid = ''
        if event['Event-Name'] == 'CHANNEL_UNBRIDGE':
            bleg_uuid = event['variable_bridge_uuid'] or ''

        return Result(hangup_cause = hangup_cause, reason = reason, bleg_uuid = bleg_uuid)

class GetDigits(Element):
    """Get digits from the caller's keypad

    action: URL to which the digits entered will be sent
    method: submit to 'action' url using GET or POST
    numDigits: how many digits to gather before returning
    timeout: wait for this many seconds before retry or returning
    finishOnKey: key that triggers the end of caller input
    tries: number of tries to execute all says and plays one by one
    playBeep: play a after all plays and says finish
    validDigits: digits which are allowed to be pressed
    invalidDigitsSound: Sound played when invalid digit pressed
    """
    DEFAULT_MAX_DIGITS = 99
    DEFAULT_TIMEOUT = 5

    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Speak', 'Play', 'Wait')
        self.num_digits = None
        self.timeout = None
        self.finish_on_key = None
        self.action = None
        self.play_beep = ""
        self.valid_digits = ""
        self.invalid_digits_sound = ""
        self.retries = None
        self.sound_files = []
        self.method = ""

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        try:
            num_digits = int(self.extract_attribute_value('numDigits',
                             self.DEFAULT_MAX_DIGITS))
        except ValueError:
            num_digits = self.DEFAULT_MAX_DIGITS
        if num_digits > self.DEFAULT_MAX_DIGITS:
            num_digits = self.DEFAULT_MAX_DIGITS
        if num_digits < 1:
            raise RESTFormatException("GetDigits 'numDigits' must be greater than 0")
        try:
            timeout = int(self.extract_attribute_value("timeout", self.DEFAULT_TIMEOUT))
        except ValueError:
            timeout = self.DEFAULT_TIMEOUT * 1000
        if timeout < 1:
            raise RESTFormatException("GetDigits 'timeout' must be a positive integer")

        finish_on_key = self.extract_attribute_value("finishOnKey")
        self.play_beep = self.extract_attribute_value("playBeep") == 'true'
        self.invalid_digits_sound = \
                            self.extract_attribute_value("invalidDigitsSound")
        self.valid_digits = self.extract_attribute_value("validDigits")

        try:
            retries = int(self.extract_attribute_value("retries"))
        except ValueError:
            retries = 1
        if retries <= 0:
            raise RESTFormatException("GetDigits 'retries' must be greater than 0")

        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("method must be 'GET' or 'POST'")
        self.method = method

        action = self.extract_attribute_value("action")
        if action and is_valid_url(action):
            self.action = action
        else:
            self.action = None
        self.num_digits = num_digits
        self.timeout = timeout * 1000
        self.finish_on_key = finish_on_key
        self.retries = retries

    def prepare(self, outbound_socket):
        for child_instance in self.children:
            if hasattr(child_instance, "prepare"):
                # :TODO Prepare Element concurrently
                child_instance.prepare(outbound_socket)

    def execute(self, outbound_socket):
        for child_instance in self.children:
            if isinstance(child_instance, Play):
                sound_file = child_instance.sound_file_path
                if sound_file:
                    #sound_file = re_root(sound_file, outbound_socket.save_dir)
                    loop = child_instance.loop_times
                    if loop == 0:
                        loop = MAX_LOOPS  # Add a high number to Play infinitely
                    # Play the file loop number of times
                    for i in range(loop):
                        self.sound_files.append(sound_file)
                    # Infinite Loop, so ignore other children
                    if loop == MAX_LOOPS:
                        break
            elif isinstance(child_instance, Wait):
                pause_secs = child_instance.length
                pause_str = 'file_string://silence_stream://%s'\
                                % (pause_secs * 1000)
                self.sound_files.append(pause_str)
            elif isinstance(child_instance, Speak):
                text = child_instance.text
                # escape simple quote
                text = text.replace("'", "\\'")
                loop = child_instance.loop_times
                child_type = child_instance.item_type
                method = child_instance.method
                say_str = ''
                if child_type and method:
                    language = child_instance.language
                    say_args = "%s.wav %s %s %s '%s'" \
                                    % (language, language, child_type, method, text)
                    say_str = "${say_string %s}" % say_args
                else:
                    engine = child_instance.engine
                    voice = child_instance.voice
                    say_str = "say:%s:%s:'%s'" % (engine, voice, text)
                if not say_str:
                    continue
                for i in range(loop):
                    self.sound_files.append(say_str)

        if self.invalid_digits_sound:
            invalid_sound = get_resource(outbound_socket, self.invalid_digits_sound)
        else:
            invalid_sound = ''

        outbound_socket.log.info("GetDigits Started %s" % self.sound_files)
        if self.play_beep:
            outbound_socket.log.debug("GetDigits play Beep enabled")
        outbound_socket.play_and_get_digits(max_digits=self.num_digits,
                            max_tries=self.retries, timeout=self.timeout,
                            terminators=self.finish_on_key,
                            sound_files=self.sound_files,
                            invalid_file=invalid_sound,
                            valid_digits=self.valid_digits,
                            play_beep=self.play_beep)
        event = None
        last_digit = ''
        while True:
            event = outbound_socket.wait_for_action()
            if event['Event-Name'] == 'DTMF':
                last_digit = event['DTMF-Digit']
                continue
            else:
                break

        digits = outbound_socket.get_var('pagd_input')
        #terminated = outbound_socket.get_var('read_terminator_used')
        #outbound_socket.log.info("read_terminator_used -> '%s'" % str(terminated))
        if digits is None and last_digit == self.finish_on_key:
            outbound_socket.log.info("terminator '%s' was pressed" % self.finish_on_key)
            digits = self.finish_on_key
        
        # digits received
        if digits is not None:
            outbound_socket.log.info("GetDigits, Digits '%s' Received" % str(digits))
            if self.action:
                # Redirect
                params = { 'Digits': digits }
                self.fetch_rest_xml(self.action, params, self.method)
            return
        # no digits received
        outbound_socket.log.info("GetDigits, No Digits Received")

class BreakOnAnsweringMachine(Element):
    def __init__(self):
        Element.__init__(self)
        self.nestables = ('GetKeyPresses')
        self.action = None

    def parse_element(self, element, uri = None):
        Element.parse_element(self, element, uri)
        action = self.extract_attribute_value("action")
        if action and is_valid_url(action):
            self.action = action
        else:
            self.action = None

    def execute(self, outbound_socket):
        getkey = None
        for child_instance in self.children:
            if isinstance(child_instance, GetKeyPresses):
                getkey = child_instance
                break
        if getkey is None:
            return

        try:
            outbound_socket.beep_detector = BeepDetector(outbound_socket, False, outbound_socket.get_channel_unique_id())
            outbound_socket.beep_detector.beep_event.append(self._beep_handler)
            outbound_socket.beep_detector.start()
            getkey.run(outbound_socket)
        finally:
            outbound_socket.beep_detector.stop()
            outbound_socket.beep_detector = None

    def _beep_handler(self, beep_state):
        beep_state.outbound_socket.log.debug("in beep handler")
        params = {
            'RequestUUID': beep_state.outbound_socket.get_var('plivo_request_uuid'),
            'CallUUID': beep_state.outbound_socket.get_channel_unique_id()
        }
        self.fetch_rest_xml(self.action, params)

class GetKeyPresses(Element):
    """Get digits from the caller's keypad, like GetDigits, but uses
    different dialplay apps

    action: URL to which the digits entered will be sent
    no_key: URL to hit when no keys are pressed
    method: submit to 'action' url using GET or POST
    numDigits: how many digits to gather before returning
    timeout: milliseconds after key(s) pressed before processing happens
    validDigits: comma-separated digits which are allowed to be pressed
    invalidDigitsSound: Sound played when invalid digit pressed
    """
    DEFAULT_NUM_DIGITS = 6
    DEFAULT_MAX_DIGITS = 6
    DEFAULT_TIMEOUT = 5

    # result DTO of DTMF processing
    class PressResult:
        def __init__(self, **kwargs):
            self.__dict__ = kwargs

    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Speak', 'Play', 'Wait')
        self.num_digits = None
        self.timeout = None
        self.finish_on_key = ''
        self.action = None
        self.no_key = None
        self.valid_digits = ''
        self.method = ''
        self.dtmfs = ''
        self.all_keys = ''

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        try:
            num_digits = int(self.extract_attribute_value('numDigits',
                             self.DEFAULT_NUM_DIGITS))
        except ValueError:
            num_digits = self.DEFAULT_NUM_DIGITS
        if num_digits > self.DEFAULT_MAX_DIGITS:
            num_digits = self.DEFAULT_NUM_DIGITS
        if num_digits < 1:
            raise RESTFormatException(self.name + " 'numDigits' must be greater than 0")
        self.num_digits = num_digits

        try:
            self.timeout = int(self.extract_attribute_value("timeout", self.DEFAULT_TIMEOUT))
        except ValueError:
            self.timeout = self.DEFAULT_TIMEOUT
        if self.timeout < 1:
            raise RESTFormatException(self.name + " 'timeout' must be a positive integer")

        self.valid_digits = self.extract_attribute_value("validDigits")
        self.finish_on_key = self.extract_attribute_value("finishOnKey")

        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("method must be 'GET' or 'POST'")
        self.method = method

        action = self.extract_attribute_value("action")
        if action and is_valid_url(action):
            self.action = action
        else:
            self.action = None

        no_key = self.extract_attribute_value("no_key")
        if no_key and is_valid_url(no_key):
            self.no_key = no_key
        else:
            self.no_key = None

    def execute(self, outbound_socket):
        player = PlaybackTool(outbound_socket)
        #player.start_debug_record()
        self.play_str = player.roll_wait_play_speak(self.children)
        outbound_socket.filter('Event-Name DTMF')
        outbound_socket.filter('Event-Name PLAYBACK_STOP')
        outbound_socket.execute('start_dtmf')
        outbound_socket.log.info("%s Started %s" % (self.name, self.play_str))
        keys_pattern = self.valid_digits.replace(',', '|').replace('*', '\*')
        self.keypress_regex = re.compile('^(?:%s)%s$' % (keys_pattern, \
            '' if self.finish_on_key == '' else self.finish_on_key + '?'))
        outbound_socket.log.debug('key press regex is [ %s ]' % self.keypress_regex.pattern)
        outbound_socket.playback(self.play_str.pop(0))
        playback_ended = False
        pr = None
        try:
            while not outbound_socket.has_hangup():
                event = outbound_socket.wait_for_action(0.5)

                if event['Event-Name'] == 'PLAYBACK_STOP':
                    # playback has ended, wait for a key press or timeout
                    if outbound_socket.beep_detector is not None \
                        and isinstance(outbound_socket.beep_detector.current_state, \
                        DetectingSilence):
                        outbound_socket.beep_detector.run(event)
                    elif len(self.play_str) > 0:
                        outbound_socket.playback(self.play_str.pop(0))
                    else:
                        playback_ended = True
                        outbound_socket.filter_delete('Event-Name PLAYBACK_STOP')
                        pr = self._get_dtmf_or_timeout(outbound_socket)
                elif event['Event-Name'] == 'DTMF':
                    outbound_socket.log.debug('got a DTMF keypress')
                    pr = self._process_dtmf_event(outbound_socket, event)
                elif outbound_socket.beep_detector is not None:
                    outbound_socket.beep_detector.run(event)

                if playback_ended \
                    or (pr is not None and pr.valid_press):
                    break
        finally:
            outbound_socket.execute('stop_dtmf')
            #stop_debug_record(outbound_socket)

        if outbound_socket.has_hangup():
            outbound_socket.log.info("hangup waiting for key press")
            return

        already_pressed = outbound_socket.get_var('plivo_keys_pressed')
        if len(self.all_keys) > 0: 
            all_pressed = self._aggregate(already_pressed, ',', self.all_keys)
            outbound_socket.set_var('plivo_keys_pressed', all_pressed)
            outbound_socket.log.info('all digits pressed: ' + all_pressed)
        elif already_pressed and already_pressed != '':
            outbound_socket.log.info('all digits pressed: ' + already_pressed)

        # got a valid dtmf
        if pr is not None and pr.valid_press:
            outbound_socket.log.info("%s, Digits '%s' Received" % (self.name, self.dtmfs))
            if self.action is None:
                return
            # Redirect
            dtmfs = self.dtmfs if not pr.was_terminated else self.dtmfs[:-1]
            params = { 'Digits': dtmfs }
            if not playback_ended:
                self._kill_playback(outbound_socket)
            self.fetch_rest_xml(self.action, params, self.method)

        # no digits received
        outbound_socket.log.info(self.name + ", No Digits Received")
        if self.no_key is not None:
            self.fetch_rest_xml(self.no_key, { }, self.method)

    def _get_dtmf_or_timeout(self, outbound_socket):
        pr = None
        with Stopwatch() as sw:
            while not outbound_socket.has_hangup() \
                and sw.get_elapsed() < self.timeout:
                countdown = self.timeout - sw.get_elapsed()
                outbound_socket.log.debug("will wait %f more seconds for key press" % countdown)
                event = outbound_socket.wait_for_action(0.5)
                if event['Event-Name'] == 'DTMF':
                    pr = self._process_dtmf_event(outbound_socket, event)
                    if pr.valid_press:
                        break
                elif outbound_socket.beep_detector is not None:
                    outbound_socket.beep_detector.run(event)
        return pr

    def _process_dtmf_event(self, sock, e):
        kp = e['DTMF-Digit']
        self.all_keys = self._aggregate(self.all_keys, ',', kp)
        self.dtmfs += kp
        sock.log.debug("captured '%s'" % self.dtmfs)
        sock.log.debug("dtmf count = %d, max digits = %d" % (len(self.dtmfs), self.num_digits))
        was_terminated = kp == self.finish_on_key
        if len(self.dtmfs) == self.num_digits or was_terminated:
            valid_press = self.keypress_regex.match(self.dtmfs)
            if valid_press is not None:
                return GetKeyPresses.PressResult(valid_press = True, was_terminated = was_terminated)
            else:
                # invalid key press, keep trying
                self.dtmfs = ''
                return GetKeyPresses.PressResult(valid_press = False, was_terminated = was_terminated)
        else:
            return GetKeyPresses.PressResult(valid_press = False, was_terminated = was_terminated)

    def _kill_playback(self, outbound_socket):
        # if we killed playback we'll have to wait for the CHANNEL_EXECUTE_COMPLETE event
        outbound_socket.api('uuid_break %s all' % outbound_socket.get_channel_unique_id())
        playback_event = outbound_socket.wait_for_action(1)
        # make sure queue is empty
        while outbound_socket.get_action_no_wait() is not None:
            pass

    def _aggregate(self, start, char, append_me):
        return '%s%s' % ('' if not start else start + char, append_me)

class AnsweringMachineDetect(Element):
    """Detect person or answering machine

    detectType:
    amdResultUrl: callback URL after detection
    preDetectPause: pause time (ms) before beginning detection
    detectTime: amount of time to wait for detection to complete
    """
    def __init__(self):
        Element.__init__(self)
        self.amd_callback_url = ''
        self.pre_detect_pause = 250
        self.detect_time =  2000
        self.use_mod_amd = True

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        self.amd_callback_url = self.extract_attribute_value('amdResultUrl')
        if not is_valid_url(self.amd_callback_url):
            raise RESTFormatException("amdResultUrl is not a valid URL")
        detect_type = self.extract_attribute_value('detectType')
        if not detect_type:
            detect_type = 'advanced'
        if detect_type not in ('simple', 'advanced'):
            raise RESTFormatException("detectType must be 'simple' or 'advanced'")
        self.use_mod_amd = detect_type == 'advanced'
        self.pre_detect_pause = self.extract_attribute_value('preDetectPause', 250)
        self.detect_time = int(self.extract_attribute_value('detectTime', 2000))

    def execute(self, outbound_socket):
        outbound_socket.filter('Event-Name DETECTED_SPEECH')
        outbound_socket.log.info('amd callback: %s' % self.amd_callback_url)
        outbound_socket.playback('silence_stream://%s' % self.pre_detect_pause)
        outbound_socket.wait_for_action()
        if self.use_mod_amd:
            outbound_socket.execute("voice_start")
        else:
            outbound_socket.execute("detect_speech", "pocketsphinx answer_hello answer_hello")
        pause_incr = 250
        total_pause = 0
        amd_status = None
        amd_result = None
        while amd_status is None and total_pause <= self.detect_time \
            and not outbound_socket.has_hangup():
            event = outbound_socket.wait_for_action(0.25)
            total_pause += pause_incr
            if event['Event-Name'] is not None:
                outbound_socket.log.info('waiting for amd %s' % event['Event-Name'])
            if event['Event-Name'] == 'DETECTED_SPEECH':
                if self.use_mod_amd and event.get_body() == 'amd_complete':
                    outbound_socket.log.info('detect block')
                    amd_status = event['amd_status']
                    amd_result = event['amd_result']
                    break
                else:
                    if event['Speech-Type'] == 'begin-speaking':
                        outbound_socket.execute('detect_speech', 'stop')
                        outbound_socket.wait_for_silence("200 25 0 %s" % self.detect_time)
                        new_e = outbound_socket.wait_for_action()
                        total_pause += self.detect_time
                        if new_e['variable_detected_silence'] == 'false':
                            amd_status = 'machine'
                            amd_result = 'simple-still-talking'
                            break
                        else:
                            amd_result = 'simple-silence-after-hello'
                    elif event['Speech-Type'] == 'detected-speech':
                        outbound_socket.execute('detect_speech', 'stop')
                        speech_result = event.get_body()
                        outbound_socket.log.info("simple AMD, result '%s'" % str(speech_result))
                        if not speech_result:
                            continue
                        try:
                            result = ' '.join(speech_result.splitlines())
                            doc = etree.fromstring(result)
                            sinterp = doc.find('interpretation')
                            if doc.tag != 'result':
                                raise RESTFormatException('No result Tag Present')
                            outbound_socket.log.debug("simple AMD: %s %s %s" % (str(doc), str(sinterp), str(sinput)))
                            confidence = sinterp.get('confidence', '0')
                            if confidence < 75:
                                continue
                            amd_result = 'simple-hello-heard'
                        except Exception, e:
                            outbound_socket.log.error("simple AMD: result failure, cannot parse result: %s" % str(e))

        if amd_status is None:
            outbound_socket.log.info('amd timeout')
            if self.use_mod_amd:
                outbound_socket.execute('voice_stop')

        amd_status = amd_status if amd_status is not None else 'person'
        amd_result = amd_result if amd_result is not None else 'unknown'
        call_uuid = event['uuid'] if self.use_mod_amd else event['Unique-ID']
        outbound_socket.log.info("amd_status: %s for %s" % (amd_status, call_uuid))
        outbound_socket.log.info("amd_result: %s for %s" % (amd_result, call_uuid))
        outbound_socket.api("log info amd_status: %s, amd_result: %s\n" % (amd_status, amd_result))

        if outbound_socket.has_hangup():
            outbound_socket.log.info("hangup during auto detection")
            return

        params = {
            'RequestUUID': outbound_socket.get_var('plivo_request_uuid'),
            'CallUUID': call_uuid,
            'AmdResult': amd_result,
            'AmdStatus': amd_status
        }
        self.fetch_rest_xml(self.amd_callback_url, params)

class LeaveMessage(Element):
    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Speak', 'Play', 'Hangup', 'PlayMany')
        self.wait_for_beep = 30
        self.use_avmd = True
        self.play_str = []

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        #self.wait_for_beep = int(self.extract_attribute_value('waitForBeep', 30))
        self.wait_for_beep = 30
        detect_type = self.extract_attribute_value('detectType', 'avmd')
        if detect_type not in ('avmd', 'spandsp'):
            raise RESTFormatException("valid 'detectType values are 'avmd' or 'spandsp'")
        self.use_avmd = detect_type == 'avmd'

    def _build_play_from_children(self, outbound_socket):
        player = PlaybackTool(outbound_socket)
        play_str = []
        for child_instance in self.children:
            outbound_socket.log.debug(str(child_instance))
            if isinstance(child_instance, PlayMany):
                play_str = play_str + player.roll_wait_play_speak(child_instance.children)
            elif isinstance(child_instance, Play) or isinstance(child_instance, Speak):
                play_str = play_str + player.roll_wait_play_speak(self.children)
                break
        return play_str

    def execute(self, outbound_socket):
        player = PlaybackTool(outbound_socket)
        self.play_str = self._build_play_from_children(outbound_socket)
        #player.start_debug_record(outbound_socket)
        beep_detector = BeepDetector(outbound_socket, self.use_avmd, outbound_socket.get_channel_unique_id())
        beep_detector.beep_event.append(self._beep_handler)
        beep_detector.start()
        outbound_socket.beep_detector = beep_detector
        self._play_and_wait_for_beep(outbound_socket, list(self.play_str))
        beep_detector.stop()

        # wait for most recent playback, which either just "broke" or is still playing
        # likely don't need this anymore, since we break instead of pausing in the beep detector
        #player.playback_wait()

        # if we detected a beep, this is where we leave the message
        # otherwise: play again! why not?
        player.playback_and_wait(self.play_str)
        #player.stop_debug_record(outbound_socket)

    def _beep_handler(self, state):
        state.outbound_socket.log.info('detected VM tone: ' + state.info.beep_tone_name)
        # likely don't need this anymore, since we break instead of pausing in the beep detector
        #state.outbound_socket.execute('break', 'all')

    def _play_and_wait_for_beep(self, outbound_socket, playlist):
        outbound_socket.filter('Event-Name PLAYBACK_STOP')
        guid = outbound_socket.get_channel_unique_id()
        outbound_socket.playback(playlist.pop(0), '!', guid, False)
        elapsed_time = 0.0
        while not outbound_socket.has_hangup():
            with Stopwatch() as sw:
                e = outbound_socket.wait_for_action(0.5)
                elapsed_time += sw.get_elapsed()

            if e['Event-Name'] == 'PLAYBACK_STOP':
                if isinstance(outbound_socket.beep_detector.current_state, DetectingSilence):
                    outbound_socket.beep_detector.run(e)
                else:
                    if len(playlist) == 0:
                        playlist = list(self.play_str)
                    outbound_socket.playback(playlist.pop(0), '!', guid, False)
            else:
                if isinstance(outbound_socket.beep_detector.run(e), Stopped):
                    break

            if elapsed_time > self.wait_for_beep:
                outbound_socket.log.info('%s reached %s sec. timeout waiting for beep' % (guid, elapsed_time))
                break

        outbound_socket.filter_delete('Event-Name PLAYBACK_STOP')

class Hangup(Element):
    """Hangup the call
    schedule: schedule hangup in X seconds (default 0, immediate hangup)
    reason: rejected, busy or "" (default "", no reason)

    Note: when hangup is scheduled, reason is not taken into account.
    """
    def __init__(self):
        Element.__init__(self)
        self.reason = ""
        self.schedule = 0

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        self.schedule = self.extract_attribute_value("schedule", 0)
        reason = self.extract_attribute_value("reason")
        if reason == 'rejected':
            self.reason = 'CALL_REJECTED'
        elif reason == 'busy':
            self.reason = 'USER_BUSY'
        else:
            self.reason = ""

    def execute(self, outbound_socket):
        if self.text:
            self.log.info("Hangup Report: %s" % str(self.text))
        try:
            self.schedule = int(self.schedule)
        except ValueError:
            outbound_socket.log.error("Hangup (scheduled) Failed: bad value for 'schedule'")
            return
        # Schedule the call for hangup at a later time if 'schedule' param > 0
        if self.schedule > 0:
            if not self.reason:
                self.reason = "NORMAL_CLEARING"
            res = outbound_socket.sched_hangup("+%d %s" % (self.schedule, self.reason),
                                               lock=True)
            if res.is_success():
                outbound_socket.log.info("Hangup (scheduled) will be fired in %d secs !" \
                                                            % self.schedule)
            else:
                outbound_socket.log.error("Hangup (scheduled) Failed: %s"\
                                                    % str(res.get_response()))
            return "Scheduled in %d secs" % self.schedule
        # Immediate hangup
        else:
            if not self.reason:
                reason = "NORMAL_CLEARING"
            else:
                reason = self.reason
            outbound_socket.log.info("Hanging up now (%s)" % reason)
            outbound_socket.hangup(reason)
        return self.reason

class Number(Element):
    """Specify phone number in a nested Dial element.

    number: number to dial
    sendDigits: key to press after connecting to the number
    sendOnPreanswer: true or false, if true SendDigits is executed on early media (default false)
    gateways: gateway string separated by comma to dialout the number
    gatewayCodecs: codecs for each gateway separated by comma
    gatewayTimeouts: timeouts for each gateway separated by comma
    gatewayRetries: number of times to retry each gateway separated by comma
    extraDialString: extra freeswitch dialstring to be added while dialing out to number
    """
    def __init__(self):
        Element.__init__(self)
        self.number = ''
        self.gateways = []
        self.gateway_codecs = []
        self.gateway_timeouts = []
        self.gateway_retries = []
        self.extra_dial_string = ''
        self.send_digits = ''
        self.send_on_preanswer = False

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        self.number = element.text.strip()
        # don't allow "|" and "," in a number noun to avoid call injection
        self.number = re.split(',|\|', self.number)[0]
        self.extra_dial_string = \
                                self.extract_attribute_value('extraDialString')
        self.send_digits = self.extract_attribute_value('sendDigits')
        self.send_on_preanswer = self.extract_attribute_value('sendOnPreanswer') == 'true'

        gateways = self.extract_attribute_value('gateways')
        gateway_codecs = self.extract_attribute_value('gatewayCodecs')
        gateway_timeouts = self.extract_attribute_value('gatewayTimeouts')
        gateway_retries = self.extract_attribute_value('gatewayRetries')

        if gateways:
            # get list of gateways
            self.gateways = gateways.split(',')
        # split gw codecs by , but only outside the ''
        if gateway_codecs:
            self.gateway_codecs = \
                            re.split(''',(?=(?:[^'"]|'[^']*'|"[^"]*")*$)''',
                                                            gateway_codecs)
        if gateway_timeouts:
            self.gateway_timeouts = gateway_timeouts.split(',')
        if gateway_retries:
            self.gateway_retries = gateway_retries.split(',')

class Wait(Element):
    """Wait for some time to further process the call

    length: length of wait time in seconds
    """
    def __init__(self):
        Element.__init__(self)
        self.length = 1

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        try:
            length = int(self.extract_attribute_value('length'))
        except ValueError:
            raise RESTFormatException("Wait 'length' must be a positive integer")
        if length < 1:
            raise RESTFormatException("Wait 'length' must be a positive integer")
        self.length = length

    def execute(self, outbound_socket):
        outbound_socket.log.info("Wait Started for %d seconds" \
                                                    % self.length)
        pause_str = 'file_string://silence_stream://%s'\
                                % str(self.length * 1000)
        #outbound_socket.playback(pause_str)
        outbound_socket.execute('playback', pause_str)
        event = outbound_socket.wait_for_action()

class PlayMany(Element):
    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Play', 'Speak', 'Wait')

    def execute(self, outbound_socket):
        player = PlaybackTool(outbound_socket)
        play_us = player.roll_wait_play_speak(self.children)
        outbound_socket.execute('multiset', 'playback_sleep_val=0 playback_delimiter=!')
        outbound_socket.log.debug("Playing %s" % play_us)
        player.playback_and_wait(play_us)

class Play(Element):
    """Play local audio file or at a URL

    url: url of audio file, MIME type on file must be set correctly
    loop: number of time to play the audio - (0 means infinite)
    """
    def __init__(self):
        Element.__init__(self)
        self.audio_directory = ''
        self.loop_times = 1
        self.sound_file_path = ''
        self.temp_audio_path = ''

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        # Extract Loop attribute
        try:
            loop = int(self.extract_attribute_value("loop", 1))
        except ValueError:
            loop = 1
        if loop < 0:
            raise RESTFormatException("Play 'loop' must be a positive integer or 0")
        if loop == 0 or loop > MAX_LOOPS:
            self.loop_times = MAX_LOOPS
        else:
            self.loop_times = loop
        # Pull out the text within the element
        audio_path = element.text.strip()

        if not audio_path:
            raise RESTFormatException("No File to play set !")

        if not is_valid_url(audio_path):
            self.sound_file_path = audio_path
        else:
            # set to temp path for prepare to process audio caching async
            self.temp_audio_path = audio_path

    def prepare(self, outbound_socket):
        if not self.sound_file_path:
            url = normalize_url_space(self.temp_audio_path)
            self.sound_file_path = get_resource(outbound_socket, url)
        else:
            self.sound_file_path = re_root(self.sound_file_path, outbound_socket.save_dir)

    def execute(self, outbound_socket):
        if self.sound_file_path:
            outbound_socket.execute('multiset', 'playback_sleep_val=0 playback_delimiter=!')
            if self.loop_times == 1:
                play_str = self.sound_file_path
            else:
                play_str = "file_string://silence_stream://1!"
                play_str += '!'.join([ self.sound_file_path for x in range(self.loop_times) ])
            outbound_socket.log.debug("Playing %d times" % self.loop_times)
            player = PlaybackTool(outbound_socket)
            player.playback_and_wait(play_str, self.loop_times)
        else:
            outbound_socket.log.error("Invalid Sound File - Ignoring Play")

class PreAnswer(Element):
    """Answer the call in Early Media Mode and execute nested element
    """
    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Play', 'Speak', 'GetDigits', 'Wait', 'GetSpeech', 'Redirect', 'SIPTransfer')

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)

    def prepare(self, outbound_socket):
        for child_instance in self.children:
            if hasattr(child_instance, "prepare"):
                outbound_socket.validate_element(child_instance.get_element(), 
                                                 child_instance)
                child_instance.prepare(outbound_socket)

    def execute(self, outbound_socket):
        outbound_socket.preanswer()
        for child_instance in self.children:
            if hasattr(child_instance, "run"):
                child_instance.run(outbound_socket)
        outbound_socket.log.info("PreAnswer Completed")

class Record(Element):
    """Record audio from caller

    action: submit the result of the record to this URL
    method: submit to 'action' url using GET or POST
    maxLength: maximum number of seconds to record (default 60)
    timeout: seconds of silence before considering the recording complete (default 500)
            Only used when bothLegs is 'false' !
    playBeep: play a beep before recording (true/false, default true)
            Only used when bothLegs is 'false' !
    finishOnKey: Stop recording on this key
    fileFormat: file format (default mp3)
    filePath: complete file path to save the file to
    fileName: Default empty, if given this will be used for the recording
    bothLegs: record both legs (true/false, default false)
              no beep will be played
    redirect: if 'false', don't redirect to 'action', only request url
        and continue to next element. (default 'true')
    """
    def __init__(self):
        Element.__init__(self)
        self.silence_threshold = 500
        self.max_length = None
        self.timeout = None
        self.finish_on_key = ""
        self.file_path = ""
        self.play_beep = ""
        self.file_format = ""
        self.filename = ""
        self.both_legs = False
        self.action = ''
        self.method = ''
        self.redirect = True

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        max_length = self.extract_attribute_value("maxLength")
        timeout = self.extract_attribute_value("timeout")
        finish_on_key = self.extract_attribute_value("finishOnKey")
        self.file_path = self.extract_attribute_value("filePath")
        if self.file_path:
            self.file_path = os.path.normpath(self.file_path) + os.sep
        self.play_beep = self.extract_attribute_value("playBeep") == 'true'
        self.file_format = self.extract_attribute_value("fileFormat")
        if self.file_format not in ('wav', 'mp3'):
            raise RESTFormatException("Format must be 'wav' or 'mp3'")
        self.filename = self.extract_attribute_value("fileName")
        self.both_legs = self.extract_attribute_value("bothLegs") == 'true'
        self.redirect = self.extract_attribute_value("redirect") == 'true'

        self.action = self.extract_attribute_value("action")
        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("method must be 'GET' or 'POST'")
        self.method = method

        # Validate maxLength
        try:
            max_length = int(max_length)
        except (ValueError, TypeError):
            raise RESTFormatException("Record 'maxLength' must be a positive integer")
        if max_length < 1:
            raise RESTFormatException("Record 'maxLength' must be a positive integer")
        self.max_length = str(max_length)
        # Validate timeout
        try:
            timeout = int(timeout)
        except (ValueError, TypeError):
            raise RESTFormatException("Record 'timeout' must be a positive integer")
        if timeout < 1:
            raise RESTFormatException("Record 'timeout' must be a positive integer")
        self.timeout = str(timeout)
        # Finish on Key
        self.finish_on_key = finish_on_key

    def execute(self, outbound_socket):
        if self.filename:
            filename = self.filename
        else:
            filename = "%s_%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"),
                                outbound_socket.get_channel_unique_id())
        mkdir_p(self.file_path)
        record_file = "%s%s.%s" % (self.file_path, filename, self.file_format)

        if self.both_legs:
            outbound_socket.set("RECORD_STEREO=true")
            outbound_socket.api("uuid_record %s start %s" \
                                %  (outbound_socket.get_channel_unique_id(),
                                   record_file)
                               )
            outbound_socket.api("sched_api +%s none uuid_record %s stop %s" \
                                % (self.max_length,
                                   outbound_socket.get_channel_unique_id(),
                                   record_file)
                               )
            outbound_socket.log.info("Record Both Executed")
        else:
            if self.play_beep:
                beep = 'tone_stream://%(300,200,700)'
                outbound_socket.playback(beep)
                event = outbound_socket.wait_for_action()
                # Log playback execute response
                outbound_socket.log.debug("Record Beep played (%s)" \
                                % str(event.get_header('Application-Response')))
            outbound_socket.start_dtmf()
            outbound_socket.log.info("Record Started")
            outbound_socket.record(record_file, self.max_length,
                                self.silence_threshold, self.timeout,
                                self.finish_on_key)
            event = outbound_socket.wait_for_action()
            outbound_socket.stop_dtmf()
            outbound_socket.log.info("Record Completed")

        # If action is set, redirect to this url
        # Otherwise, continue to next Element
        if self.action and is_valid_url(self.action):
            params = {}
            params['RecordingFileFormat'] = self.file_format
            params['RecordingFilePath'] = self.file_path
            params['RecordingFileName'] = filename
            params['RecordFile'] = record_file
            # case bothLegs is True
            if self.both_legs:
                # RecordingDuration not available for bothLegs because recording is in progress
                # Digits is empty for the same reason
                params['RecordingDuration'] = "-1"
                params['Digits'] = ""
            # case bothLegs is False
            else:
                try:
                    record_ms = event.get_header('variable_record_ms')
                    if not record_ms:
                        record_ms = "-1"
                    else:
                        record_ms = str(int(record_ms)) # check if integer
                except (ValueError, TypeError):
                    outbound_socket.log.warn("Invalid 'record_ms' : '%s'" % str(record_ms))
                    record_ms = "-1"
                params['RecordingDuration'] = record_ms
                record_digits = event.get_header("variable_playback_terminator_used")
                if record_digits:
                    params['Digits'] = record_digits
                else:
                    params['Digits'] = ""
            # fetch xml
            if self.redirect:
                self.fetch_rest_xml(self.action, params, method=self.method)
            else:
                spawn_raw(outbound_socket.send_to_url, self.action, params, method=self.method)

class SIPTransfer(Element):
    def __init__(self):
        Element.__init__(self)
        self.sip_url = ""

    def parse_element(self, element, uri=None):
        url = element.text.strip()
        sip_uris = set()
        for sip_uri in url.split(','):
            sip_uri = sip_uri.strip()
            if is_sip_url(sip_uri):
                sip_uris.add(sip_uri)
        self.sip_url = ','.join(list(sip_uris))

    def execute(self, outbound_socket):
        if self.sip_url:
            outbound_socket.log.info("SIPTransfer using sip uri '%s'" % str(self.sip_url))
            outbound_socket.set("plivo_sip_transfer_uri=%s" % self.sip_url)
            if outbound_socket.has_answered():
                outbound_socket.log.debug("SIPTransfer using deflect")
                outbound_socket.deflect(self.sip_url)
            else:
                outbound_socket.log.debug("SIPTransfer using redirect")
                outbound_socket.redirect(self.sip_url) 
            raise RESTSIPTransferException(self.sip_url)
        raise RESTFormatException("SIPTransfer must have a sip uri")

class Redirect(Element):
    """Redirect call flow to another Url.
    Url is set in element body
    method: GET or POST
    """
    def __init__(self):
        Element.__init__(self)
        self.method = ""
        self.url = ""

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("Method must be 'GET' or 'POST'")
        url = element.text.strip()
        if not url:
            raise RESTFormatException("Redirect must have an URL")
        if is_valid_url(url):
            self.method = method
            self.url = url
            return
        raise RESTFormatException("Redirect URL '%s' not valid!" % str(url))

    def execute(self, outbound_socket):
        if self.url:
            self.fetch_rest_xml(self.url, method=self.method)
            return
        raise RESTFormatException("Redirect must have an URL")

class Notify(Element):
    """Callback to Url to notify this element has been executed.
    Url is set in element body
    method: GET or POST
    """
    def __init__(self):
        Element.__init__(self)
        self.method = ""
        self.url = ""

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("Method must be 'GET' or 'POST'")
        url = element.text.strip()
        if not url:
            raise RESTFormatException("Notify must have an URL")
        if is_valid_url(url):
            self.method = method
            self.url = url
            return
        raise RESTFormatException("Notify URL '%s' not valid!" % str(url))

    def execute(self, outbound_socket):
        if not self.url:
            raise RESTFormatException("Notify must have an URL")

        if self.method is None:
            self.method = outbound_socket.default_http_method

        if not self.url:
            self.log.warn("Cannot send %s, no url !" % self.method)
            return None

        params = outbound_socket.session_params

        try:
            http_obj = HTTPRequest(outbound_socket.key, outbound_socket.secret, proxy_url=outbound_socket.proxy_url)
            data = http_obj.fetch_response(self.url, params, self.method, log=outbound_socket.log)
            return data
        except Exception, e:
            self.log.error("Sending to %s %s with %s -- Error: %s" \
                                        % (self.method, self.url, params, e))
        return None

class SayDigits(Element):
    """Say a string of digits

    text: text to say
    language: language to use

    method: PRONOUNCED, ITERATED, COUNTED
    """
    valid_methods = ('PRONOUNCED', 'ITERATED', 'COUNTED')

    def __init__(self):
        Element.__init__(self)
        self.loop_times = 1
        self.language = "en"
        self.item_type = "NUMBER"
        self.method = "ITERATED"

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        # Extract Loop attribute
        self.language = self.extract_attribute_value("language")
        method = self.extract_attribute_value("method")
        if method in self.valid_methods:
            self.method = method
        if not self.text.isdigit():
            raise RESTFormatException("SayDigits 'text' must be a number")

    def execute(self, outbound_socket):
        say_args = "%s %s %s %s" % (self.language, self.item_type, self.method, self.text)
        res = outbound_socket.say(say_args, loops=self.loop_times)
        if res.is_success():
            for i in range(self.loop_times):
                outbound_socket.log.debug("Speaking %d times ..." % (i+1))
                event = outbound_socket.wait_for_action()
                if event.is_empty():
                    outbound_socket.log.warn("SayDigits Break (empty event)")
                    return
                outbound_socket.log.debug("SayDigits %d times done (%s)" \
                            % ((i+1), str(event['Application-Response'])))
                gevent.sleep(0.01)
            outbound_socket.log.info("SayDigits Finished")
            return
        else:
            outbound_socket.log.error("SayDigits Failed - %s" \
                            % str(res.get_response()))
            return

class Speak(Element):
    """Speak text

    text: text to say
    voice: voice to be used based on engine
    language: language to use
    loop: number of times to say this text (0 for unlimited)
    engine: voice engine to be used for Speak (flite, cepstral)

    Extended params - Currently uses Callie (Female) Voice
    type: NUMBER, ITEMS, PERSONS, MESSAGES, CURRENCY, TIME_MEASUREMENT,
          CURRENT_DATE, CURRENT_TIME, CURRENT_DATE_TIME, TELEPHONE_NUMBER,
          TELEPHONE_EXTENSION, URL, IP_ADDRESS, EMAIL_ADDRESS, POSTAL_ADDRESS,
          ACCOUNT_NUMBER, NAME_SPELLED, NAME_PHONETIC, SHORT_DATE_TIME
    method: PRONOUNCED, ITERATED, COUNTED

    Flite Voices  : slt, rms, awb, kal
    Cepstral Voices : (Use any voice here supported by cepstral)
    """
    valid_methods = ('PRONOUNCED', 'ITERATED', 'COUNTED')
    valid_types = ('NUMBER', 'ITEMS', 'PERSONS', 'MESSAGES',
                   'CURRENCY', 'TIME_MEASUREMENT', 'CURRENT_DATE', ''
                   'CURRENT_TIME', 'CURRENT_DATE_TIME', 'TELEPHONE_NUMBER',
                   'TELEPHONE_EXTENSION', 'URL', 'IP_ADDRESS', 'EMAIL_ADDRESS',
                   'POSTAL_ADDRESS', 'ACCOUNT_NUMBER', 'NAME_SPELLED',
                   'NAME_PHONETIC', 'SHORT_DATE_TIME')

    def __init__(self):
        Element.__init__(self)
        self.loop_times = 1
        self.language = "en"
        self.sound_file_path = ""
        self.engine = ""
        self.voice = ""
        self.item_type = ""
        self.method = ""

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)
        # Extract Loop attribute
        try:
            loop = int(self.extract_attribute_value("loop", 1))
        except ValueError:
            loop = 1
        if loop < 0:
            raise RESTFormatException("Speak 'loop' must be a positive integer or 0")
        if loop == 0 or loop > MAX_LOOPS:
            self.loop_times = MAX_LOOPS
        else:
            self.loop_times = loop
        self.engine = self.extract_attribute_value("engine")
        self.language = self.extract_attribute_value("language")
        self.voice = self.extract_attribute_value("voice")
        item_type = self.extract_attribute_value("type")
        if item_type in self.valid_types:
            self.item_type = item_type
        method = self.extract_attribute_value("method")
        if method in self.valid_methods:
            self.method = method

    def execute(self, outbound_socket):
        if self.item_type and self.method:
            say_args = "%s %s %s %s" \
                    % (self.language, self.item_type,
                       self.method, self.text)
        else:
            say_args = "%s|%s|%s" % (self.engine, self.voice, self.text)
        if self.item_type and self.method:
            res = outbound_socket.say(say_args, loops=self.loop_times)
        else:
            res = outbound_socket.speak(say_args, loops=self.loop_times)
        if res.is_success():
            for i in range(self.loop_times):
                outbound_socket.log.debug("Speaking %d times ..." % (i+1))
                event = outbound_socket.wait_for_action()
                if event.is_empty():
                    outbound_socket.log.warn("Speak Break (empty event)")
                    return
                outbound_socket.log.debug("Speak %d times done (%s)" \
                            % ((i+1), str(event['Application-Response'])))
                gevent.sleep(0.01)
            outbound_socket.log.info("Speak Finished")
            return
        else:
            outbound_socket.log.error("Speak Failed - %s" \
                            % str(res.get_response()))
            return

class GetSpeech(Element):
    """Get speech from the caller

    action: URL to which the detected speech will be sent
    method: submit to 'action' url using GET or POST
    timeout: wait for this many seconds before returning
    playBeep: play a beep after all plays and says finish
    engine: engine to be used by detect speech
    grammar: grammar to load
    grammarPath: grammar path directory (default /usr/local/freeswitch/grammar)
    """
    def __init__(self):
        Element.__init__(self)
        self.nestables = ('Speak', 'Play', 'Wait')
        self.num_digits = None
        self.timeout = None
        self.finish_on_key = None
        self.action = None
        self.play_beep = ""
        self.valid_digits = ""
        self.invalid_digits_sound = ""
        self.retries = None
        self.sound_files = []
        self.method = ""

    def parse_element(self, element, uri=None):
        Element.parse_element(self, element, uri)

        self.grammar = self.extract_attribute_value("grammar")
        if not self.grammar:
            raise RESTAttributeException("GetSpeech 'grammar' is mandatory")
        self.grammarPath = self.extract_attribute_value("grammarPath").rstrip(os.sep)

        self.engine = self.extract_attribute_value("engine")
        if not self.engine:
            raise RESTAttributeException("GetSpeech 'engine' is mandatory")

        try:
            timeout = int(self.extract_attribute_value("timeout"))
        except (ValueError, TypeError):
            raise RESTFormatException("GetSpeech 'timeout' must be a positive integer")
        if timeout < 1:
            raise RESTFormatException("GetSpeech 'timeout' must be a positive integer")
        self.timeout = timeout

        self.play_beep = self.extract_attribute_value("playBeep") == 'true'

        action = self.extract_attribute_value("action")

        method = self.extract_attribute_value("method")
        if not method in ('GET', 'POST'):
            raise RESTAttributeException("Method, must be 'GET' or 'POST'")
        self.method = method

        if action and is_valid_url(action):
            self.action = action
        else:
            self.action = None

    def prepare(self, outbound_socket):
        for child_instance in self.children:
            if hasattr(child_instance, "prepare"):
                # :TODO Prepare Element concurrently
                child_instance.prepare(outbound_socket)

    def _parse_speech_result(self, result):
        return speech_result

    def execute(self, outbound_socket):
        speech_result = ''
        grammar_file = ''
        raw_grammar = None
        gpath = None
        raw_grammar = get_grammar_resource(outbound_socket, self.grammar)
        if raw_grammar:
            outbound_socket.log.debug("Found grammar : %s" % str(raw_grammar))
            grammar_file = "%s_%s" % (datetime.now().strftime("%Y%m%d-%H%M%S"),
                                        outbound_socket.get_channel_unique_id())
            gpath = self.grammarPath + os.sep + grammar_file + '.gram'
            outbound_socket.log.debug("Writing grammar to %s" % str(gpath))
            try:
                f = open(gpath, 'w')
                f.write(raw_grammar)
                f.close()
            except Exception, e:
                outbound_socket.log.error("GetSpeech result failure, cannot write grammar: %s" % str(grammar_file))
                grammar_file = ''
        elif raw_grammar is None:
            outbound_socket.log.debug("Using grammar %s" % str(self.grammar))
            grammar_file = self.grammar
        else:
            outbound_socket.log.error("GetSpeech result failure, cannot get grammar: %s" % str(self.grammar))

        if grammar_file:
            if self.grammarPath:
                grammar_full_path = self.grammarPath + os.sep + grammar_file
            else:
                grammar_full_path = grammar_file
            # set grammar tag name
            grammar_tag = os.path.basename(grammar_file)

            for child_instance in self.children:
                if isinstance(child_instance, Play):
                    sound_file = child_instance.sound_file_path
                    if sound_file:
                        loop = child_instance.loop_times
                        if loop == 0:
                            loop = MAX_LOOPS  # Add a high number to Play infinitely
                        # Play the file loop number of times
                        for i in range(loop):
                            self.sound_files.append(re_root(sound_file, outbound_socket.save_dir))
                        # Infinite Loop, so ignore other children
                        if loop == MAX_LOOPS:
                            break
                elif isinstance(child_instance, Wait):
                    pause_secs = child_instance.length
                    pause_str = 'file_string://silence_stream://%s'\
                                    % (pause_secs * 1000)
                    self.sound_files.append(pause_str)
                elif isinstance(child_instance, Speak):
                    text = child_instance.text
                    # escape simple quote
                    text = text.replace("'", "\\'")
                    loop = child_instance.loop_times
                    child_type = child_instance.item_type
                    method = child_instance.method
                    say_str = ''
                    if child_type and method:
                        language = child_instance.language
                        say_args = "%s.wav %s %s %s '%s'" \
                                        % (language, language, child_type, method, text)
                        say_str = "${say_string %s}" % say_args
                    else:
                        engine = child_instance.engine
                        voice = child_instance.voice
                        say_str = "say:%s:%s:'%s'" % (engine, voice, text)
                    if not say_str:
                        continue
                    for i in range(loop):
                        self.sound_files.append(say_str)

            outbound_socket.log.info("GetSpeech Started %s" % self.sound_files)
            if self.play_beep:
                outbound_socket.log.debug("GetSpeech play Beep enabled")
                self.sound_files.append('tone_stream://%(300,200,700)')

            if self.sound_files:
                play_str = "!".join(self.sound_files)
                outbound_socket.set("playback_delimiter=!")
            else:
                play_str = ''

            # unload previous grammars
            outbound_socket.execute("detect_speech", "grammarsalloff")
            # load grammar
            speech_args = "%s %s %s" % (self.engine, grammar_full_path, grammar_tag)
            res = outbound_socket.execute("detect_speech", speech_args)
            if not res.is_success():
                outbound_socket.log.error("GetSpeech Failed - %s" \
                                % str(res.get_response()))
                if gpath:
                    try:
                        os.remove(gpath) 
                    except:
                        pass
                return
            if play_str:
                outbound_socket.playback(play_str)
                event = outbound_socket.wait_for_action()
                # Log playback execute response
                outbound_socket.log.debug("GetSpeech prompt played (%s)" \
                                % str(event.get_header('Application-Response')))
                outbound_socket.execute("detect_speech", "resume")

            timer = gevent.timeout.Timeout(self.timeout)
            timer.start()
            try:
                for x in range(1000):
                    event = outbound_socket.wait_for_action()
                    if event.is_empty():
                        outbound_socket.log.warn("GetSpeech Break (empty event)")
                        outbound_socket.execute("detect_speech", "stop")
                        outbound_socket.bgapi("uuid_break %s all" \
                            % outbound_socket.get_channel_unique_id())
                        return
                    elif event['Event-Name'] == 'DETECTED_SPEECH'\
                        and event['Speech-Type'] == 'detected-speech':
                            speech_result = event.get_body()
                            if speech_result is None:
                                speech_result = ''
                            outbound_socket.log.info("GetSpeech, result '%s'" % str(speech_result))
                            break
            except gevent.timeout.Timeout:
                outbound_socket.log.warn("GetSpeech Break (timeout)")
                outbound_socket.execute("detect_speech", "stop")
                if play_str:
                    outbound_socket.bgapi("uuid_break %s all" \
                        % outbound_socket.get_channel_unique_id())
                return
            finally:
                timer.cancel()
                if gpath:
                    try:
                        os.remove(gpath) 
                    except:
                        pass

            outbound_socket.execute("detect_speech", "stop")
            outbound_socket.bgapi("uuid_break %s all" \
                                % outbound_socket.get_channel_unique_id())

        if self.action:
            params = {'Grammar':'', 'Confidence':'0', 'Mode':'', 'SpeechResult':''}
            if speech_result:
                try:
                    result = ' '.join(speech_result.splitlines())
                    doc = etree.fromstring(result)
                    sinterp = doc.find('interpretation')
                    sinput = doc.find('interpretation/input')
                    if doc.tag != 'result':
                        raise RESTFormatException('No result Tag Present')
                    outbound_socket.log.debug("GetSpeech %s %s %s" % (str(doc), str(sinterp), str(sinput)))
                    params['Grammar'] = sinterp.get('grammar', '')
                    params['Confidence'] = sinput.get('confidence', '0')
                    params['Mode'] = sinput.get('mode', '')
                    params['SpeechResult'] = sinput.text
                except Exception, e:
                    params['Confidence'] = "-1"
                    params['SpeechResultError'] = str(speech_result)
                    outbound_socket.log.error("GetSpeech result failure, cannot parse result: %s" % str(e))
            # Redirect
            self.fetch_rest_xml(self.action, params, self.method)
