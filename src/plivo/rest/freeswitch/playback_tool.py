import time
import os
import os.path
from datetime import datetime
try:
    import xml.etree.cElementTree as etree
except ImportError:
    from xml.etree.elementtree import ElementTree as etree

import gevent

from plivo.utils.files import re_root
from plivo.rest.freeswitch.helpers import Stopwatch

class PlaybackTool:
    def __init__(self, outbound_socket):
        self.outbound_socket = outbound_socket
        self.play_count = 1

    def roll_wait_play_speak(self, children):
        play_str = []
        for child_instance in children:
            self.outbound_socket.log.debug('rolling %s ' % child_instance.name)
            if child_instance.name == 'Wait':
                play_str.append('silence_stream://%d' % (child_instance.length * 1000))
            if child_instance.name == 'Play':
                sound_file = child_instance.sound_file_path
                if sound_file:
                    sound_file = re_root(sound_file, self.outbound_socket.save_dir)
                    loop = child_instance.loop_times
                    if loop == 0:
                        loop = MAX_LOOPS  # Add a high number to Play infinitely
                    # Play the file loop number of times
                    for x in range(loop):
                        play_str.append(sound_file)
            elif child_instance.name == 'Speak':
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
                for x in range(loop):
                    play_str.append(sound_file)
            #self.outbound_socket.log.debug('play_str: %s' % play_str)

        return play_str

    def __validate_play(self, play_us, count):
        if isinstance(play_us, basestring):
            return (play_us, count)

        if isinstance(play_us, list):
            return ('file_string://' + '!'.join(play_us), len(play_us))

        raise Exception('play argument must be list or string')

    def playback_and_wait(self, play_us, count=1):
        play_info = self.__validate_play(play_us, count)
        play_str = play_info[0]
        play_count = play_info[1]
        self.outbound_socket.filter('Event-Name PLAYBACK_STOP')
        res = self.outbound_socket.playback(play_str)
        if res.is_success():
            event = self.playback_wait(play_count)
            if event is None:
                self.outbound_socket.log.warn("Play Break (empty event)")
                return
            self.outbound_socket.log.debug("Play done (%s)" \
                    % str(event['Application-Response']))
        else:
            self.outbound_socket.log.error("Play Failed - %s" \
                            % str(res.get_response()))
        self.outbound_socket.log.info("Play Finished")
        self.outbound_socket.filter_delete('Event-Name PLAYBACK_STOP')
        return

    def playback_wait(self, count=1, on_execute=False, timeout=300):
        self.play_count = count
        with Stopwatch() as sw:
            f = self.outbound_socket.wait_for_action(5)
            while self.__continue_playback(f, on_execute):
                f = self.outbound_socket.wait_for_action(5)
                if sw.get_elapsed() >= timeout:
                    self.outbound_socket.log.warn('%s sec. timeout waiting for playback to complete' % sw.get_elapsed())
                    return None
            return f

    def __continue_playback(self, event, on_execute):
        valid_stop = False
        if on_execute:
            valid_stop = event['Application'] is None or event['Application'] != 'playback'
        elif event['Event-Name'] == 'PLAYBACK_STOP':
            self.play_count = self.play_count - 1
            valid_stop = self.play_count == 0
            #self.outbound_socket.log.debug('count %d => %d' % (self.play_count + 1, self.play_count))
            
        #self.outbound_socket.log.debug('on_execute: %s valid_stop: %s' % (str(on_execute), str(valid_stop)))
        return not valid_stop and not self.outbound_socket.has_hangup()

    def start_debug_record(self):
        guid = self.outbound_socket.get_channel_unique_id()
        self.outbound_socket.record_file = '/tmp/' + guid + '.wav'
        self.outbound_socket.set("RECORD_STEREO=true")
        self.outbound_socket.api("uuid_record %s start %s" %  (guid, self.outbound_socket.record_file))

    def stop_debug_record(self):
        self.outbound_socket.api("uuid_record %s stop %s" %  (self.outbound_socket.get_channel_unique_id(), self.outbound_socket.record_file))
