# -*- coding: utf-8 -*-
# Copyright (c) 2011 Plivo Team. See LICENSE for details

import base64
import re
import uuid
import os
import os.path
import errno
import flask
from flask import request
from werkzeug.exceptions import Unauthorized
import gevent.queue

from plivo.utils.files import check_for_wav, mkdir_p, re_root
from plivo.rest.freeswitch.helpers import is_valid_url, get_conf_value, \
                                            get_post_param, get_http_param, get_resource, \
                                            normalize_url_space, \
                                            HTTPRequest

def auth_protect(decorated_func):
    def wrapper(obj):
        if obj._validate_http_auth() and obj._validate_ip_auth():
            return decorated_func(obj)
    wrapper.__name__ = decorated_func.__name__
    wrapper.__doc__ = decorated_func.__doc__
    return wrapper

class PlivoWavRestApi(object):
    _config = None
    _rest_inbound_socket = None
    allowed_ips = []
    key = ''
    secret = ''

    def _validate_ip_auth(self):
        """Verify request is from allowed ips
        """
        if not self.allowed_ips:
            return True
        for ip in self.allowed_ips:
            if ip.strip() == request.remote_addr.strip():
                return True
        raise Unauthorized("IP Auth Failed")

    def _validate_http_auth(self):
        """Verify http auth request with values in "Authorization" header
        """
        if not self.key or not self.secret:
            return True
        try:
            auth_type, encoded_auth_str = \
                request.headers['Authorization'].split(' ', 1)
            if auth_type == 'Basic':
                decoded_auth_str = base64.decodestring(encoded_auth_str)
                auth_id, auth_token = decoded_auth_str.split(':', 1)
                if auth_id == self.key and auth_token == self.secret:
                    return True
        except (KeyError, ValueError, TypeError):
            pass
        raise Unauthorized("HTTP Auth Failed")

    def send_response(self, Success, Message, **kwargs):
        if Success is True:
            self.log.info(Message)
            return flask.jsonify(Success=True, Message=Message, **kwargs)
        self.log.error(Message)
        return flask.jsonify(Success=False, Message=Message, **kwargs)

    @auth_protect
    def save_wav(self):
        self.log.debug("RESTAPI SaveWav called")
        result = False
        name = get_post_param(request, "WavPath") 
        file = request.files['file']

        if not name:
            msg = "WavPath parameter missing"
            return self.send_response(Success=result, Message=msg)

        if not file:
            msg = "no file uploaded"
            return self.send_response(Success=result, Message=msg)
        
        self.log.debug(str(file))

        fullpath = re_root(name, self.save_dir)
        pathname = os.path.dirname(fullpath)
        self.log.debug("saving %s to disk" % fullpath)
        try:
            mkdir_p(pathname)
            file.save(fullpath)
        except Exception as ex:
            self.log.debug("save of %s failed: %s" % (fullpath, str(ex)))
            msg = "failed to save %s" % name
            return self.send_response(Success=result, Message=msg)

        if not check_for_wav(fullpath):
            msg = "WAV %s is invalid or does not exist" % name
            return self.send_response(Success=result, Message=msg)

        result = True
        msg = "WAV saved to %s" % name
        return self.send_response(Success=result, Message=msg, Name=name)

    @auth_protect
    def check_wav(self):
        self.log.debug("RESTAPI CheckWav called")
        result = False
        name = get_post_param(request, "WavName") 

        if not name: name = get_http_param(request, "WavName")
        if not name:
            msg = "WavName parameter missing"
            return self.send_response(Success=result, Message=msg)

        fullpath = re_root(name, self.save_dir)
        if not check_for_wav(fullpath):
            msg = "WAV %s is invalid or does not exist" % name
            return self.send_response(Success=result, Message=msg)

        result = True
        msg = "WAV %s exists" % name
        return self.send_response(Success=result, Message=msg, Name=name)

    @auth_protect
    def get_wav(self):
        self.log.debug("RESTAPI GetWav called")
        result = False
        name = get_post_param(request, "WavName") 

        if not name: name = get_http_param(request, "WavName")
        if not name:
            msg = "WavName parameter missing"
            return self.send_response(Success=result, Message=msg)

        if not check_for_wav(name):
            msg = "WAV %s is invalid or does not exist" % name
            return self.send_response(Success=result, Message=msg)

        result = True
        msg = "WAV %s exists" % name
        return flask.send_file(name)
