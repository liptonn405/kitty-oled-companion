#!/usr/bin/env python3
"""Small, private local state exchange. Command text is never recorded."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from functools import lru_cache


@lru_cache(maxsize=1)
def directory():
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir())
    session=os.environ.get('GRUVBOX_DESKTOP_SESSION') or os.environ.get('KITTY_PID','default')
    session=re.sub(r'[^A-Za-z0-9_-]','_',session)[:64]
    root=runtime/f'arch-gruvbox-robot-{os.getuid()}'/session
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    return root


def read(name):
    try:
        value=json.loads((directory()/name).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def publish(name, data):
    root=directory(); temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=root,prefix='.event-',delete=False) as f:
            temporary=Path(f.name)
            json.dump(data,f,separators=(',',':'))
        os.replace(temporary,root/name)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def command_event(phase,pid,status=0):
    name=f'command-{int(pid)}.json'; now=time.monotonic()
    if phase=='start':
        data={'id':f'{pid}-{time.time_ns()}','pid':int(pid),'phase':'running','started':now,'stamp':now}
    else:
        data=read(name)
        if not data or data.get('phase')!='running': return
        data.update(phase='done',status=int(status),stamp=now)
    publish(name,data)


def command_states():
    values=[]
    for path in directory().glob('command-*.json'):
        try:
            data=json.loads(path.read_text())
            os.kill(int(data['pid']),0)
            values.append(data)
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return values


def git_states():
    values=[]
    for path in directory().glob('git-*.json'):
        try:
            data=json.loads(path.read_text());os.kill(int(data['pid']),0)
            values.append(data)
        except (OSError,ValueError,KeyError,TypeError):continue
    return values


if __name__=='__main__':
    if len(sys.argv)>=3:
        command_event(sys.argv[1],int(sys.argv[2]),int(sys.argv[3]) if len(sys.argv)>3 else 0)
