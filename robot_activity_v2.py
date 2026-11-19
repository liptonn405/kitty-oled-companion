"""Kitty watcher: share cursor movement, never key presses or screen contents."""
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time

_last=()
_published=0
_root=None


def sample(boss,now):
    global _last,_published,_root
    window=boss.active_window
    active=window is not None and window.is_focused and window.user_vars.get('role') in ('work','work2')
    cursor=window.screen.cursor if active else None
    position=(window.id,cursor.x,cursor.y) if active else None
    switched=bool(position) and (not _last or _last[0]!=position[0])
    changed=position!=_last
    if not changed and (not active or now-_published<1):return
    if changed and not switched and position is not None and now-_published<.2:return
    gaze=(0.,0.)
    robot=next((w for w in boss.window_id_map.values() if active and w.user_vars.get('role')=='robot' and w.os_window_id==window.os_window_id),None)
    if active and robot is not None:
        a,b=window.geometry,robot.geometry
        cols,rows=max(1,a.xnum),max(1,a.ynum)
        x=a.left+(min(cols-.5,max(.5,cursor.x+.5)))*(a.right-a.left)/cols
        y=a.top+(min(rows-.5,max(.5,cursor.y+.5)))*(a.bottom-a.top)/rows
        dx,dy=x-(b.left+b.right)/2,y-(b.top+b.bottom)/2
        # A shared distance preserves the actual angle and avoids clamping both
        # panels to the same upper-left corner of the eyes.
        distance=math.sqrt(dx*dx+dy*dy+max(80,min(b.right-b.left,b.bottom-b.top)*.75)**2)
        gaze=(dx/distance,dy/distance)
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
            json.dump({'stamp':now,'gaze':gaze,'active':bool(active),
                       'window_id':window.id if active else None,
                       'kind':'focus' if switched else 'cursor' if changed else 'heartbeat'},stream,separators=(',',':'))
        os.replace(path,_root/'typing.json');_published=now;_last=position
    finally:
        if path is not None:path.unlink(missing_ok=True)


def on_load(boss,data):
    from kitty.fast_data_types import add_timer
    from kitty.launch import watcher_modules
    # Kitty caches watcher modules. A distinct v2 path loads this fix in a running
    # dashboard; silence the old timer so it cannot overwrite the new coordinates.
    for path,module in watcher_modules.items():
        if Path(path)==Path(__file__).with_name('robot_activity.py') and isinstance(module,dict):
            old=module.get('sample')
            if callable(old):old.__globals__['sample']=lambda *args:None
    def tick(timer_id):
        try:sample(boss,time.monotonic())
        except (OSError,AttributeError,RuntimeError):pass
    add_timer(tick,.25,True)
