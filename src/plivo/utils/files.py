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

def re_root(old_root, new_root):
    dir_name = os.path.dirname(old_root)
    if os.path.isabs(dir_name) : dir_name = dir_name.lstrip('/')
    pathname = os.path.join(new_root, dir_name)
    filename = os.path.basename(old_root)
    return os.path.join(pathname, filename)
