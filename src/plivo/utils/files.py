import os
import os.path
import errno

def check_for_wav(name):
    if not name.endswith(".wav"): return False

    try:
        with open(name) as f: pass
        return True
    except IOError as e:
        return False

def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path): pass
        else: raise
