"""Kitty watcher: share cursor movement, never key presses or screen contents."""
import json
import os
from pathlib import Path
import re
import tempfile
import time

_last=None
_published=0
_root=None


def sample(boss,now):
    global _last,_published,_root
    window=boss.active_window
    if window is None or not window.is_focused:
        _last=None;return
    role=window.user_vars.get('role','')
    if role not in ('work','work2'):
        _last=None;return
    cursor=window.screen.cursor
    position=(window.id,cursor.x,cursor.y)
    previous=_last;_last=position
    if previous is None or previous[0]!=position[0] or previous==position or now-_published<.5:return
    # At the Bash prompt this is typing; during commands output also moves the
    # cursor, so the robot's command/music reactions retain their higher priority.
    gaze=(-.7,-.4)
    robot=next((w for w in boss.window_id_map.values() if w.user_vars.get('role')=='robot' and w.os_window_id==window.os_window_id),None)
    if robot is not None:
        a,b=window.geometry,robot.geometry
        gaze=(max(-1,min(1,((a.left+a.right)-(b.left+b.right))/max(1,b.right-b.left))),
              max(-1,min(1,((a.top+a.bottom)-(b.top+b.bottom))/max(1,b.bottom-b.top))))
    if _root is None:
        runtime=Path(os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir())
        session=os.environ.get('GRUVBOX_DESKTOP_SESSION') or str(os.getpid())
        session=re.sub(r'[^A-Za-z0-9_-]','_',session)[:64]
        _root=runtime/f'arch-gruvbox-robot-{os.getuid()}'/session
        _root.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=_root,prefix='.typing-',delete=False) as stream:
            path=Path(stream.name)
            json.dump({'stamp':now,'gaze':gaze},stream,separators=(',',':'))
        os.replace(path,_root/'typing.json');_published=now
    finally:
        if path is not None:path.unlink(missing_ok=True)


def on_load(boss,data):
    from kitty.fast_data_types import add_timer
    def tick(timer_id):
        try:sample(boss,time.monotonic())
        except (OSError,AttributeError,RuntimeError):pass
    add_timer(tick,.25,True)
