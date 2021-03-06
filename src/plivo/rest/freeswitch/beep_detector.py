# -*- coding: utf-8 -*-
# Copyright (c) 2011 Plivo Team. See LICENSE for details.

import time

class BeepInfo:
    def __init__(self, **kwargs):
        self.__dict__ = kwargs

class BeepState:
    def __init__(self, last_state = None):
        if last_state is not None:
            self.info = last_state.info
            self.outbound_socket = last_state.outbound_socket

class BeepDetector:
    def __init__(self, outbound_socket, use_avmd, guid):
        outbound_socket.filter('Event-Name DETECTED_TONE')
        self.beep_event = []
        self.log = outbound_socket.log
        self.initial = StartDetecting(None)
        self.initial.info = BeepInfo(use_avmd = use_avmd, guid = guid, \
            got_beep = False, restart_name = '', restart_sample = '', \
            api_queue = [])
        self.initial.outbound_socket = outbound_socket 

    def start(self):
        self.current_state = self.initial.run(None)
        return self.current_state

    def run(self, e):
        start = self.current_state.__class__.__name__
        self.current_state = self.current_state.run(e)
        self.log.debug('beep state: %s => %s' % (start, self.current_state.__class__.__name__))
        while len(self.current_state.info.api_queue) > 0:
            self.current_state.outbound_socket.api(self.current_state.info.api_queue.pop(0))
        if isinstance(self.current_state, GotBeep):
            for handler in self.beep_event:
                handler(self.current_state)
        return self.current_state

    def stop(self):
        if not isinstance(self.current_state, Stopped):
            self.current_state = StopDetection(self.current_state).run(None)
        return self.current_state

class StartDetecting(BeepState):
    def run(self, e):
        if self.info.use_avmd:
            self.outbound_socket.execute('avmd')
            return DetectingBeepAVMD(self)

        self.outbound_socket.execute('start_tone_detect', 'vm_beeps', self.info.guid, False)
        return DetectingBeep(self)

class DetectingBeepAVMD(BeepState):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        if self.info.use_avmd and e['Event-Name'] == 'CUSTOM':
            if e['Event-Subclass'] is not None and e['Event-Subclass'] == 'avmd::beep':
                self.outbound_socket.wait_for_action() # pop off the most recent playback event
                self.info.got_beep = True
                return StopDetection(self).run(None)
        return self

class DetectingBeep(BeepState):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        tone_name = e['Detected-Tone']
        if tone_name is not None:
            self.outbound_socket.log.info('got ' + tone_name)
            if tone_name != 'SILENCE':
                self.info.beep_tone_name = tone_name
                self.info.pause_time = time.time()
                self.info.beep_time = time.time()
                # pause playback while waiting for silence
                self.outbound_socket.log.info('pause ' + self.info.guid)
                self.info.api_queue.append('uuid_break %s' % self.info.guid)
                return DetectingSilence(self)
        return self 

class DetectingSilence(BeepState):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        if e['Event-Name'] == 'PLAYBACK_STOP':
            self.info.restart_name = e['Playback-File-Path']
            self.info.restart_sample = e['variable_playback_samples']
            self.outbound_socket.log.info('restart' + self.info.restart_name \
                + ' at ' + self.info.restart_sample)

        tone_name = e['Detected-Tone']
        if tone_name is not None and tone_name == 'SILENCE':
            self.outbound_socket.log.info('got silence after beep ' + self.info.beep_tone_name)
            self.info.got_beep = True
            return StopDetection(self).run(None)

        since_beep = time.time() - self.info.beep_time
        if since_beep >= 1.0:
            self.outbound_socket.playback('%s@@%s' % (self.info.restart_name, self.info.restart_sample))
            self.info.restart_name = ''
            self.info.restart_sample = ''
            return DetectingBeep(self)

        return self

class StopDetection(BeepState):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        if self.info.use_avmd:
            self.outbound_socket.execute('avmd', 'stop')
        else:
            self.outbound_socket.execute('stop_tone_detect')
        if self.info.got_beep:
            return GotBeep(self)
        return NoBeep(self)

class Stopped(BeepState):
    pass

class GotBeep(Stopped):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        return self

class NoBeep(Stopped):
    def __init__(self, last_state):
        BeepState.__init__(self, last_state)

    def run(self, e):
        return self

#class PausedDetectingSilence(DetectingSilence):
#    def __init__(self, last_state):
#        BeepState.__init__(self, last_state)
#
#    def run(self, e):
#        pause_dur = time.time() - self.info.pause_time
#        if pause_dur >= 2.0:
#            self.outbound_socket.log.info('unpause %s after %s' % (guid, pause_dur))
#            self.outbound_socket.api('uuid_fileman %s pause' % guid)
#            pause_dur = 0
#            return DetectingBeep(self, e)
#        return DetectingSilence.run(self, e)
