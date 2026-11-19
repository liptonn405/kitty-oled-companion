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
_positions={}
_input_stamp=0
_input_window=None
_mouse_positions={}
_mouse_point=None


def aim(robot,x,y):
    box=robot.geometry
    dx,dy=x-(box.left+box.right)/2,y-(box.top+box.bottom)/2
    # Independent axes retain the reading line even in a wide left-hand pane.
    return (dx/math.hypot(dx,max(60,(box.right-box.left)*.9)),
            dy/math.hypot(dy,max(60,(box.bottom-box.top)*.55)))


def mouse_point(windows):
    global _mouse_point
    for window in windows:
        try:
            pos=window.current_mouse_position()
            if pos is None:continue
            x,y=(pos['cell_x'],pos['cell_y']) if isinstance(pos,dict) else (pos.cell_x,pos.cell_y)
            box=window.geometry
            if not (0<=x<box.xnum and 0<=y<box.ynum):continue
            point=(x,y)
            old=_mouse_positions.get(window.id);_mouse_positions[window.id]=point
            if point!=old:
                _mouse_point=(box.left+(x+.5)*(box.right-box.left)/max(1,box.xnum),
                              box.top+(y+.5)*(box.bottom-box.top)/max(1,box.ynum))
        except (AttributeError,KeyError,TypeError,OSError,RuntimeError):continue
    return _mouse_point


def sample(boss,now):
    global _last,_published,_root,_input_stamp,_input_window
    window=boss.active_window
    active=window is not None and window.is_focused and window.user_vars.get('role') in ('work','work2')
    cursor=window.screen.cursor if active else None
    position=(window.id,cursor.x,cursor.y) if active else None
    switched=bool(position) and (not _last or _last[0]!=position[0])
    changed=position!=_last
    moved=False
    if active:
        moved=not switched and _positions.get(window.id)!=position[1:]
        _positions[window.id]=position[1:]
        if moved:_input_stamp=now;_input_window=window.id
    tracking=0<=now-_input_stamp<2 and _input_stamp>0
    if not changed and now-_published<(.04 if tracking else .5):return
    gaze=(0.,0.)
    os_id=window.os_window_id if window is not None else None
    windows=[w for w in boss.window_id_map.values() if w.os_window_id==os_id and getattr(w,'is_visible_in_layout',True)]
    robot=next((w for w in windows if w.user_vars.get('role')=='robot'),None)
    if active and robot is not None:
        a=window.geometry
        cols,rows=max(1,a.xnum),max(1,a.ynum)
        x=a.left+(min(cols-.5,max(.5,cursor.x+.5)))*(a.right-a.left)/cols
        y=a.top+(min(rows-.5,max(.5,cursor.y+.5)))*(a.bottom-a.top)/rows
        gaze=aim(robot,x,y)
    pointer=mouse_point(windows) if tracking else None
    mouse_gaze=aim(robot,*pointer) if robot is not None and pointer is not None else None
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
                       'window_id':window.id if active else None,'input_window':_input_window,
                       'input_stamp':_input_stamp,'mouse_gaze':mouse_gaze,
                       'kind':'cursor' if moved else 'focus' if switched else 'heartbeat'},stream,separators=(',',':'))
        os.replace(path,_root/'typing.json');_published=now;_last=position
    finally:
        if path is not None:path.unlink(missing_ok=True)


def on_load(boss,data):
    from kitty.fast_data_types import add_timer
    from kitty.launch import watcher_modules
    # Kitty caches watcher modules. A distinct v3 path loads this fix in a running
    # dashboard; silence the old timer so it cannot overwrite the new coordinates.
    for path,module in watcher_modules.items():
        if Path(path).parent==Path(__file__).parent and Path(path).name in ('robot_activity.py','robot_activity_v2.py') and isinstance(module,dict):
            old=module.get('sample')
            if callable(old):old.__globals__['sample']=lambda *args:None
    def tick(timer_id):
        try:sample(boss,time.monotonic())
        except (OSError,AttributeError,RuntimeError):pass
    add_timer(tick,.04,True)
