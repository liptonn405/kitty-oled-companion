#!/usr/bin/env python3
"""Soft OLED robot eyes, mouse interactions and smoothly blended expressions."""
import base64
import fcntl
import io
import math
import os
from pathlib import Path
import random
import re
import select
import shutil
import struct
import sys
import termios
import time
import tty
import zlib
from functools import lru_cache
import robot_bus
from robot_memory import Personality
from robot_physics import EyeSprings
from robot_stage import MusicAct,SecretScenes,draw_music_back,draw_secret

import cairo

WIDTH, HEIGHT = 480, 220
FPS = 60
IMAGE_ID = 7319
BATTERY_ID = 7320
BACKGROUND_ID = 7321
MODES = ('idle','sleep','surprise','tired','error','angry','thinking','dance',
         'curious','bored','suspicious','wake','wink','attentive','pull','success','worried','sad','melancholy','dj_pause')
LENS_ALIASES={'error':'sad','worried':'sad','bored':'melancholy','tired':'melancholy',
              'attentive':'curious','wake':'idle','wink':'idle','dance':'idle','pull':'idle',
              'success':'idle','happy':'idle','laugh':'idle','celebrate':'idle','dj_pause':'idle'}
DISABLED_EXPRESSIONS={'happy':'idle','laugh':'idle','celebrate':'success'}


def smooth(x):
    x=max(0,min(1,x))
    return x*x*(3-2*x)


def opaque_png(surface):
    """Lossless RGB PNG with fast compression; every output canvas is opaque."""
    width,height=surface.get_width(),surface.get_height();stride=surface.get_stride()
    native=bytes(surface.get_data());row_size=width*3+1
    rows=bytearray(height*row_size)
    channels=(2,1,0) if sys.byteorder=='little' else (1,2,3)
    for y in range(height):
        start=y*stride;dest=y*row_size+1
        for channel,offset in enumerate(channels):
            rows[dest+channel:dest+width*3:3]=native[start+offset:start+width*4:4]
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(rows,1))+chunk(b'IEND',b''))


def lens(mode):
    return _lens(LENS_ALIASES.get(mode,mode))


def eye_centres(gaze,strength,offsets=((0,0),(0,0))):
    return [(max(52,min(WIDTH-52,cx+gaze[0]*(16+74*strength)+offsets[i][0])),
             max(60,min(HEIGHT-60,110+gaze[1]*(11+37*strength)+offsets[i][1])))
            for i,cx in enumerate((157,323))]


@lru_cache(maxsize=16)
def _lens(mode):
    """Precompute continuous light falloff, without strokes or stepped outlines."""
    size=192
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,size,size)
    pixels=surface.get_data(); stride=surface.get_stride()
    hw,hh,r=(39,53,35) if mode=='surprise' else (43,7,7) if mode=='sleep' else (46,14,10) if mode=='angry' else (41,36,25) if mode=='sad' else (43,35,28) if mode=='melancholy' else (43,29,17) if mode=='suspicious' else (40,48,29) if mode=='curious' else (43,42,27)
    for iy in range(size):
        y=iy-size/2
        for ix in range(size):
            x=ix-size/2
            qx,qy=abs(x)-hw+r,abs(y)-hh+r
            d=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r
            if mode=='angry':
                # A3: slim eyes, with the upper lids descending toward the bridge.
                d=max(d,-8+.18*x-y)
            elif mode=='sad':
                # C2: rounded lower eye and a curved lid rising at the inner corner.
                d=max(d,(-9-.28*x-.004*x*x-y)/math.sqrt(1+(.28+.008*x)**2))
            elif mode=='melancholy':
                # D2: a half-closed eye; the second eye is lowered and tilted in render.
                d=max(d,-6-y)
            elif mode=='suspicious':
                d=max(d,-(.12*x+12)-y)
            body=1/(1+math.exp(max(-60,min(60,d/2.2))))
            halo=.11*math.exp(-max(0,d)**2/(2*13**2))
            intensity=min(1,body*.87+halo)
            # Slight lens shading gives volume; there is no bright perimeter line.
            shade=.89+.11*math.exp(-((x+13)**2+(y+17)**2)/(2*35**2))
            green=int(255*intensity*shade)
            red=int(255*intensity*(.02+.05*body)*shade)
            blue=int(255*intensity*(.02+.05*body)*shade)

            offset=iy*stride+ix*4
            if sys.byteorder=='little': pixels[offset:offset+4]=bytes((blue,green,red,255))
            else: pixels[offset:offset+4]=bytes((255,red,green,blue))
    surface.mark_dirty()
    return surface


class Mind:
    def __init__(self, now=None, seed=None, personality=None):
        now=time.monotonic() if now is None else now
        self.rng=random.Random(seed)
        self.personality=personality or Personality(seed=seed);self.springs=EyeSprings()
        self.pointer_down=False;self.pointer_start=None;self.press_time=0.;self.pointer_distance=0.
        self.anger_started=now;self.git_conflict=False;self.git_queue=[];self.seen_git=set()
        self.git_badge='';self.badge_until=0.
        self.started=now; self.last_tick=now; self.last_activity=now
        self.state='wake'; self.previous='idle'; self.changed=now; self.until=now+2.6
        self.next_emotion=now+self.rng.uniform(10,18); self.emotion_bag=[];self.last_random=None
        self.next_blink=now+self.rng.uniform(2.5,5)
        self.blink_start=None; self.opening=1.0
        self.eye_openings=(.05,.05);self.wink_side=0
        self.follow_until=now; self.next_follow=now
        self.mouse=(0.0,0.0); self.gaze=[0.0,0.0]
        self.clicks=[]; self.manual_sleep=False
        self.battery_percent=None; self.on_ac=None
        self.command_running=False; self.last_result=None
        self.dance_until=0; self.music_pulse=0; self.pulse=0
        self.last_typing=0;self.work_gaze=(-.7,-.4);self.is_random=False
        self.work_signal=0;self.typing_window=None;self.mouse_gaze=None
        self.look_strength=0.;self.pending_result=None
        self.performance=MusicAct();self.secrets=SecretScenes(self.rng,now);self.scene_owned=False;self.scene_mode=None

    def stop_secret(self,now):
        if self.secrets.kind:
            self.secrets.cancel(now)
            if self.scene_owned and self.state==self.scene_mode:self.change('idle',now)
        self.scene_owned=False;self.scene_mode=None

    def tracking_mode(self,now):
        age=now-self.last_typing
        if not self.last_typing or age<0 or age>=2:return None
        return 'typing' if age<=.20 else 'mouse'

    def change(self, state, now, duration=0):
        if state in ('happy','laugh'):state='idle';duration=0
        elif state=='celebrate':state='success'
        self.is_random=False
        if state=='angry':self.anger_started=now
        if state!=self.state:
            self.previous,self.state=self.state,state
            self.changed=now
        self.until=now+duration if duration else float('inf')

    def event(self, kind, value, now):
        if kind in ('press','key'):self.stop_secret(now)
        if kind!='move' or self.tracking_mode(now):self.last_activity=now
        if kind=='press':
            self.pointer_down=True;self.pointer_start=value;self.press_time=now;self.pointer_distance=0.
            grabbed=self.springs.press(value,eye_centres(self.gaze,self.look_strength,self.springs.positions))
            if grabbed:
                self.last_typing=0;self.manual_sleep=False;self.change('pull',now)
        elif kind=='drag':
            if self.pointer_down:
                self.pointer_distance=math.dist(value,self.pointer_start)
                if self.springs.move(value):
                    self.personality.record('overpull');self.change('angry',now,4)
        elif kind=='release':
            if not self.pointer_down:return
            if self.springs.held is not None and math.dist(value,self.pointer_start)>self.pointer_distance:
                self.event('drag',value,now)
            self.pointer_down=False;held=self.springs.release()
            if self.springs.broken:return
            if self.pointer_distance<6 and now-self.press_time<.65:
                self.event('click',(0,0),now)
                if held and self.state=='pull':self.change('idle',now)
            elif held:
                self.personality.record('gentle');self.change('idle',now)
        elif kind=='leave':
            self.pointer_down=False
            if self.springs.break_grip():
                self.personality.record('overpull');self.change('angry',now,4)
        elif kind=='click':
            self.manual_sleep=False
            self.personality.record('click')
            self.clicks=[t for t in self.clicks if now-t<2]
            self.clicks.append(now)
            if len(self.clicks)>=5:
                self.change('angry',now,4); self.clicks=[]
        elif kind=='move':
            self.mouse=value
            if self.tracking_mode(now)=='mouse' and self.mouse_gaze is None:self.mouse_gaze=value
        elif kind=='key':
            if value in ('1','2','3'):
                self.last_typing=0;self.manual_sleep=False;self.performance.hide();self.change('idle',now)
                self.secrets.start({'1':'star','2':'bug','3':'window'}[value],now,manual=True)
                return
            state={'s':'sleep','a':'angry','g':'sad','t':'melancholy','u':'surprise',' ':'idle'}.get(value)
            if state:
                self.last_typing=0
                self.manual_sleep=state=='sleep'
                self.change(state,now,4 if state=='angry' else 2.8 if state in ('sad','melancholy') else 1.8 if state=='surprise' else 0)

    def context(self, data, now):
        battery=data.get('battery') or {}
        online=battery.get('online')
        self.battery_percent=battery.get('percent')
        if self.on_ac is False and online is True:
            self.manual_sleep=False; self.last_activity=now
            self.personality.record('charging')
            if self.springs.held is None and (self.state!='angry' or self.scene_owned):self.change('wake',now,1.4)
        if online is not None: self.on_ac=online
        commands=data.get('commands',[])
        self.command_running=any(c.get('phase')=='running' for c in commands)
        if self.is_random and self.command_running:self.change('idle',now)
        done=[c for c in commands if c.get('phase')=='done' and 0<=now-c.get('stamp',0)<3]
        if done:
            last=max(done,key=lambda c:c['stamp'])
            if last.get('id')!=self.last_result:
                self.last_result=last.get('id')
                if not self.command_running and self.springs.held is None and (self.state!='angry' or self.scene_owned):
                    self.last_activity=now; self.manual_sleep=False
                    emotion='success' if last.get('status',0)==0 else 'sad'
                    if self.tracking_mode(now):self.pending_result=(emotion,now+3)
                    else:self.change(emotion,now,2.1)
        music=data.get('music') or {}
        self.performance.observe(music,now)
        fresh=0<=now-music.get('stamp',0)<1.2
        active=fresh and music.get('playing') and music.get('audio_ok')
        if self.performance.hidden_gate and music.get('dance_until',0)<=self.performance.hidden_gate:active=False
        self.dance_until=music.get('dance_until',0) if active else 0
        self.music_pulse=max(0,min(1,music.get('pulse',0))) if active else 0
        if self.is_random and now<self.dance_until:self.change('idle',now)
        typing=data.get('typing') or {};stamp=typing.get('stamp',0)
        if self.work_signal<stamp<=now and now-stamp<1.5:
            self.work_signal=stamp
            input_stamp=typing.get('input_stamp',stamp if typing.get('kind')=='cursor' else 0)
            new_input=self.last_typing<input_stamp<=now and now-input_stamp<.5
            if new_input:
                self.last_typing=input_stamp;self.last_activity=now;self.manual_sleep=False
                self.typing_window=typing.get('input_window',typing.get('window_id'))
                self.follow_until=input_stamp+2
            if new_input or typing.get('window_id')==self.typing_window:
                self.work_gaze=tuple(max(-1,min(1,v)) for v in typing.get('gaze',self.work_gaze))
            mouse=typing.get('mouse_gaze')
            if mouse is not None:self.mouse_gaze=tuple(max(-1,min(1,v)) for v in mouse)
        states=data.get('git',[])
        if 'git' in data:self.git_conflict=bool(max(states,key=lambda s:s.get('stamp',0)).get('conflict')) if states else False
        events=sorted((event for state in states for event in state.get('events',[])),key=lambda e:e.get('stamp',0))
        for event in events:
            kind=event.get('kind');identity=event.get('id')
            if identity not in self.seen_git and kind in ('commit','push','conflict','resolved') and 0<=now-event.get('stamp',0)<12:
                self.seen_git.add(identity);self.personality.record(kind)
                self.git_queue.append((kind,now+10))
        if len(self.seen_git)>100:self.seen_git={event.get('id') for event in events}

    def random_emotion(self,now):
        if not self.emotion_bag:
            self.emotion_bag=['tired','melancholy','curious','sad','wink','surprise'] if self.personality.mood=='sleepy' else ['curious','melancholy','sad','suspicious','wink','surprise']
            self.rng.shuffle(self.emotion_bag)
            if self.emotion_bag[-1]==self.last_random:self.emotion_bag.reverse()
        state=self.emotion_bag.pop();self.last_random=state
        self.wink_side=self.rng.randrange(2)
        duration=(1.2,2.4) if self.personality.mood=='playful' else (3.,4.5) if self.personality.mood=='sleepy' else (2.2,3.5)
        self.change(state,now,1.1 if state=='wink' else self.rng.uniform(*duration))
        self.is_random=True
        interval=(8,15) if self.personality.mood=='playful' else (21,36) if self.personality.mood=='sleepy' else (12,24)
        self.next_emotion=now+self.rng.uniform(*interval)

    def tick(self, now):
        dt=min(.35,max(0,now-self.last_tick)); self.last_tick=now
        self.personality.advance(dt,now);self.springs.advance(dt,self.personality.mood)
        following=self.tracking_mode(now) if self.springs.held is None else None
        music_allowed=not following and self.springs.held is None and not self.command_running and not self.git_conflict and not self.git_queue and now>=self.badge_until and self.state not in ('angry','sleep','pull','sad','melancholy','tired')
        self.performance.advance(dt,now,music_allowed)
        scene_state_ok=self.state=='idle' or (self.secrets.kind is not None and (not self.scene_owned or self.state==self.scene_mode))
        available=not following and self.springs.held is None and not self.pointer_down and not self.command_running and not self.git_conflict and not self.git_queue and self.pending_result is None and now>=self.badge_until and not self.performance.phase(now) and self.performance.level<.03 and (now-self.last_activity>=3 or self.secrets.manual) and scene_state_ok
        was_scene=self.scene_owned
        scene_active=self.secrets.update(now,available)
        if was_scene and not scene_active and self.state==self.scene_mode:self.change('idle',now)
        self.scene_owned=scene_active
        if scene_active:
            mode=self.secrets.mode(now)
            if mode!=self.state:self.change(mode,now)
            self.scene_mode=mode
        else:self.scene_mode=None
        if following and self.state not in ('attentive','angry'):
            self.change('attentive',now)
        if not following and self.pending_result and self.springs.held is None:
            emotion,expires=self.pending_result;self.pending_result=None
            if now<expires and self.state!='angry':self.change(emotion,now,2.1)
        bases=('idle','sleep','tired','thinking','dance','attentive','worried','dj_pause')
        if not following and self.springs.held is None and (self.state in bases or now>=self.until):
            while self.git_queue:
                kind,expires=self.git_queue.pop(0)
                if now>=expires:continue
                self.git_badge={'commit':'COMMIT','push':'PUSH OK','conflict':'MERGE CONFLICT','resolved':'RESOLVED'}[kind]
                self.badge_until=now+3
                if kind=='conflict':self.change('worried',now)
                else:self.change('success',now,2.8)
                break
        if (self.state not in bases or self.is_random) and now>=self.until:
            self.change('idle',now)
        if self.state in bases and not self.is_random:
            low=self.battery_percent is not None and self.battery_percent<=20 and self.on_ac is not True
            timeout=60 if self.personality.mood=='playful' else 28 if self.personality.mood=='sleepy' else 40
            base='sleep' if self.manual_sleep else 'attentive' if following else 'dance' if now<self.dance_until else 'thinking' if self.command_running else 'dj_pause' if self.performance.phase(now)=='pause' else 'sleep' if now-self.last_activity>=timeout else 'worried' if self.git_conflict else 'tired' if low else 'idle'
            if base!=self.state: self.change(base,now)
            if self.state=='idle' and now>=self.next_emotion:
                self.random_emotion(now)
        if self.state not in ('sleep','wake','wink'):
            if self.blink_start is None and now>=self.next_blink: self.blink_start=now
            if self.blink_start is not None:
                elapsed=now-self.blink_start
                if elapsed<.11: self.opening=1-.95*smooth(elapsed/.11)
                elif elapsed<.15: self.opening=.05
                elif elapsed<.31: self.opening=.05+.95*smooth((elapsed-.15)/.16)
                else:
                    self.opening=1; self.blink_start=None
                    self.next_blink=now+self.rng.uniform(3,6)
        else: self.opening=1
        self.eye_openings=(self.opening,self.opening)
        if self.state=='wake':
            progress=(now-self.changed)/min(1.25,self.until-self.changed)
            self.eye_openings=(.05+.95*smooth(progress),)*2
        elif self.state=='wink':
            elapsed=now-self.changed
            closed=smooth(elapsed/.18) if elapsed<.18 else 1-smooth((elapsed-.27)/.3)
            eyes=[1.,1.];eyes[self.wink_side]=1-.95*closed;self.eye_openings=tuple(eyes)
        t=now-self.started
        target=(.55*math.sin(t*.45),.16*math.sin(t*.31))
        if self.state=='sleep': target=(0,.25)
        if self.state in ('angry','error','sad'):target=(0,.1 if self.state=='sad' else -.1)
        if self.state=='thinking': target=(.65,-.5)
        if self.state=='dj_pause':target=(0.,0.)
        if self.state=='pull':target=tuple(self.gaze)
        if self.state=='worried':target=(.15*math.sin(t*2.6),-.3)
        if self.state=='wake':target=(.6*math.sin((now-self.changed)*3),-.1)
        if self.state=='curious':target=(.55,-.35)
        if self.state=='suspicious':target=(-.55,0)
        if self.state in ('bored','melancholy'):target=(-.35+.12*math.sin(t*.65),.22)
        if following and self.state=='attentive':
            target=self.work_gaze if following=='typing' else self.mouse_gaze or self.work_gaze
        if scene_active:target=self.secrets.gaze(now)
        alpha=1-math.exp(-dt*(15 if following else 8))
        for i in range(2): self.gaze[i]+=(target[i]-self.gaze[i])*alpha
        strength=self.look_strength if self.springs.held is not None else .55 if scene_active else 1. if following and self.state=='attentive' else 0.
        self.look_strength+=(strength-self.look_strength)*(1-math.exp(-dt*12))
        target_pulse=self.music_pulse if self.state=='dance' and now<self.dance_until else 0
        rate=42 if target_pulse>self.pulse else 9
        self.pulse+=(target_pulse-self.pulse)*(1-math.exp(-dt*rate))
        return self

    def frame_rate(self,now):
        return 4 if self.state=='sleep' and now-self.changed>=.35 else FPS


def battery_info(root=Path('/sys/class/power_supply')):
    batteries=[]; connected=[]
    for path in root.glob('*'):
        try:
            if (path/'type').read_text().strip()=='Battery':
                batteries.append((float((path/'capacity').read_text()),(path/'status').read_text().strip()))
            elif (path/'online').exists(): connected.append((path/'online').read_text().strip()=='1')
        except (OSError,ValueError): pass
    if not batteries: return {}
    online=any(connected) if connected else any(s in ('Charging','Full','Not charging') for _,s in batteries)
    return {'percent':sum(v for v,_ in batteries)/len(batteries),'online':online}


class Context:
    def __init__(self):
        self.battery={}; self.next_battery=0; self.commands=[]; self.next_commands=0
        self.music={};self.typing={};self.next_signals=0
        self.git=[];self.next_git=0
    def read(self,now):
        if now>=self.next_battery:
            self.battery=battery_info(); self.next_battery=now+3
        if now>=self.next_commands:
            self.commands=robot_bus.command_states(); self.next_commands=now+.18
        if now>=self.next_git:
            self.git=robot_bus.git_states();self.next_git=now+.2
        if now>=self.next_signals:
            self.music=robot_bus.read('music.json');self.typing=robot_bus.read('typing.json')
            following=0<=now-self.typing.get('input_stamp',0)<2
            self.next_signals=now+(.04 if following or now<self.music.get('dance_until',0) else .18)
        return {'battery':self.battery,'commands':self.commands,'music':self.music,'typing':self.typing,'git':self.git}


def render(t, mode='idle', gaze=(0,0), opening=1.0, previous=None, blend=1.0,pulse=0,battery=None,charging=False,eye_openings=None,look_strength=None,offsets=((0,0),(0,0)),anger_age=0,badge='',performance=None,secret=None):
    mode=DISABLED_EXPRESSIONS.get(mode,mode);previous=DISABLED_EXPRESSIONS.get(previous,previous)
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,WIDTH,HEIGHT)
    ctx=cairo.Context(surface); ctx.set_source_rgb(0,0,0); ctx.paint()
    layers=[(mode,1)] if previous is None or previous==mode else [(previous,1-blend),(mode,blend)]
    strength=(1. if mode=='attentive' else 0.) if look_strength is None else look_strength
    centres=eye_centres(gaze,strength,offsets)
    draw_music_back(ctx,performance,t,pulse)
    for state,opacity in layers:
        if opacity<=.001: continue
        bob=math.sin(t*1.9)*.9
        sy=max(.05,opening) if state!='sleep' else 1
        for side,(cx,cy) in enumerate(centres):
            ctx.save()
            if eye_openings is not None and state!='sleep':
                sy=max(.05,eye_openings[side])
            beat=math.sqrt(max(0,min(1,pulse))) if state=='dance' else 0
            tremble=1.5+2.8*math.exp(-max(0,anger_age)*1.6) if state=='angry' else 0
            ctx.translate(cx+math.sin(t*43+side)*tremble,cy+bob-beat*8+math.sin(t*37+side*.7)*tremble)
            ctx.scale(1+beat*.30,sy*(1+beat*.55))
            if state=='angry':ctx.rotate(math.sin(t*5+side)*.035)
            if state in ('sad','error','worried'):ctx.scale(1,1+.035*math.sin(t*1.5+side*.2))
            if state in ('melancholy','bored','tired'):
                ctx.translate(0,-5 if side==0 else 9+1.5*math.sin(t*1.1))
                ctx.rotate(-.015*math.sin(t*1.1) if side==0 else .2+.025*math.sin(t*1.1))
            if state=='sleep':ctx.scale(1,1+.05*math.sin(t*.8))
            if state=='pull':
                dx,dy=offsets[side];distance=math.hypot(dx,dy);angle=math.atan2(dy,dx)
                ctx.rotate(angle);ctx.scale(1+distance/400,1-distance/700);ctx.rotate(-angle)
            if state=='curious':ctx.scale(.9 if side==0 else 1.06,.83 if side==0 else 1.08)
            if state=='attentive':
                # Follow with the whole eye pair; no fixed raised-eye expression.
                interest=math.sin(t*2.4+side*.45)
                ctx.translate(0,interest*1.3)
                ctx.scale(1+.012*interest,1+.018*interest)
            if state in ('angry','error','sad','worried') and side==1:ctx.scale(-1,1)
            ctx.set_operator(cairo.OPERATOR_ADD)
            ctx.set_source_surface(lens(state),-96,-96)
            ctx.paint_with_alpha(opacity*(.85 if state=='tired' else 1))
            if beat:
                ctx.set_operator(cairo.OPERATOR_ADD)
                ctx.set_source_surface(lens(state),-96,-96)
                ctx.paint_with_alpha(opacity*beat*.8)
            ctx.restore()
        if state=='sleep':
            ctx.select_font_face('sans-serif',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_NORMAL)
            for i in range(3):
                phase=(t*.37+i/3)%1
                ctx.set_font_size(13+phase*9)
                ctx.set_source_rgba(.55,.94,.61,math.sin(phase*math.pi)*opacity*.75)
                ctx.move_to(365+phase*35,90-phase*70)
                ctx.show_text('z' if i<2 else 'Z')
        if state=='thinking':
            for i in range(3):
                alpha=(.25+.65*max(0,math.sin(t*4-i*.9)))*opacity
                ctx.set_source_rgba(.47,.9,.54,alpha)
                ctx.arc(222+i*18,178,3,0,math.tau); ctx.fill()
    draw_secret(ctx,secret,t)
    if badge:
        ctx.select_font_face('sans-serif',cairo.FONT_SLANT_NORMAL,cairo.FONT_WEIGHT_NORMAL);ctx.set_font_size(13)
        ctx.set_source_rgba(.49,.91,.53,.85);width=ctx.text_extents(badge)[2]
        ctx.move_to((WIDTH-width)/2,205);ctx.show_text(badge)
    return opaque_png(surface)


def round_box(ctx,x,y,w,h,r):
    r=min(r,w/2,h/2)
    ctx.new_sub_path()
    for cx,cy,start in [(x+w-r,y+r,-math.pi/2),(x+w-r,y+h-r,0),(x+r,y+h-r,math.pi/2),(x+r,y+r,math.pi)]:
        ctx.arc(cx,cy,r,start,start+math.pi/2)
    ctx.close_path()


def battery_png(percent,charging=False):
    """Small OLED indicator using the lenses' continuous green light falloff."""
    width,height=160,80
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
    pixels=surface.get_data();stride=surface.get_stride()
    amount=max(0,min(100,percent))/100
    def box_distance(x,y,cx,cy,hw,hh,r):
        r=min(r,hw,hh);qx,qy=abs(x-cx)-hw+r,abs(y-cy)-hh+r
        return math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r
    def emission(d):
        body=1/(1+math.exp(max(-60,min(60,d/.95))))
        halo=.11*math.exp(-max(0,d)**2/(2*7**2))
        intensity=min(1,body*.87+halo)
        return intensity*(.35+.15*body),intensity,intensity*(.46+.12*body)
    for iy in range(height):
        for ix in range(width):
            outline=abs(box_distance(ix,iy,76,40,52,16,9))-.7
            tip=box_distance(ix,iy,132,40,2,5,2)
            red,green,blue=(v*.46 for v in emission(min(outline,tip)))
            if amount:
                half=46*amount
                fill=box_distance(ix,iy,30+half,40,half,10,6)
                fr,fg,fb=emission(fill)
                shade=.89+.11*math.exp(-((ix-57)**2+(iy-35)**2)/(2*30**2))
                red,green,blue=max(red,fr*shade),max(green,fg*shade),max(blue,fb*shade)
            rgb=tuple(round(255*v) for v in (red,green,blue))
            offset=iy*stride+ix*4
            pixels[offset:offset+4]=bytes((rgb[2],rgb[1],rgb[0],255)) if sys.byteorder=='little' else bytes((255,*rgb))
    surface.mark_dirty();ctx=cairo.Context(surface)
    if charging:
        ctx.move_to(80,30);ctx.line_to(73,40);ctx.line_to(80,40);ctx.line_to(74,50)
        ctx.set_line_cap(cairo.LINE_CAP_ROUND);ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_source_rgba(0,0,0,.75);ctx.set_line_width(4);ctx.stroke_preserve()
        ctx.set_source_rgba(.63,.97,.69,.95);ctx.set_line_width(1.7);ctx.stroke()
    out=io.BytesIO();surface.write_to_png(out);return out.getvalue()


def command(keys, payload=b''):
    return b'\x1b_G'+','.join(f'{k}={v}' for k,v in keys.items()).encode()+b';'+payload+b'\x1b\\'


def transmit(png, first=False,image_id=IMAGE_ID):
    data=base64.b64encode(png)
    for start in range(0,len(data),4096):
        keys={'a':'t' if first else 'f','i':image_id,'f':100,'q':2} if start==0 else {}
        if start==0 and not first: keys.update(r=1,X=1)
        keys['m']=int(start+4096<len(data))
        yield command(keys,data[start:start+4096])


def geometry(cols,rows):
    w=max(1,min(cols-4,54))
    h=max(1,min(max(1,rows-5),round(w*HEIGHT/WIDTH/2)))
    w=min(w,max(1,round(h*2*WIDTH/HEIGHT)))
    return max(0,(cols-w)//2),max(0,(rows-h)//2),w,h


def window_pixels(cols,rows):
    try:
        _,_,width,height=struct.unpack('HHHH',fcntl.ioctl(sys.stdin.fileno(),termios.TIOCGWINSZ,b'\0'*8))
    except (AttributeError,OSError,ValueError):width=height=0
    return width or cols*8,height or rows*16


def canvas_position(x,y,cols,rows,pixel_mouse=False,pixels=None):
    width,height=pixels or window_pixels(cols,rows);cw,ch=width/cols,height/rows
    left,top,w,h=geometry(cols,rows);scale=min(w*cw/WIDTH,h*ch/HEIGHT)
    x=(x-1) if pixel_mouse else (x-.5)*cw
    y=(y-1) if pixel_mouse else (y-.5)*ch
    return ((x-left*cw-(w*cw-WIDTH*scale)/2)/scale,
            (y-top*ch-(h*ch-HEIGHT*scale)/2)/scale)


def layout(cols,rows):
    out=sys.stdout.buffer
    # Clearing the screen also removes stored graphics: upload the backdrop afterwards.
    out.write(b'\x1b[48;2;0;0;0m\x1b[2J\x1b[H')
    width,height=window_pixels(cols,rows)
    surface=cairo.ImageSurface(cairo.FORMAT_RGB24,width,height)
    ctx=cairo.Context(surface);ctx.set_source_rgb(0,0,0);ctx.paint()
    png=io.BytesIO();surface.write_to_png(png)
    # Graphics stay opaque even with Kitty/GNOME background transparency enabled.
    # z=-1 places the full-pane backdrop below text but above cell backgrounds.
    for data in transmit(png.getvalue(),True,BACKGROUND_ID):out.write(data)
    out.write(command({'a':'p','i':BACKGROUND_ID,'p':1,'c':cols,'r':rows,'C':1,'z':-1,'q':2}))
    if cols>=22 and rows>=6:
        out.write(b'\x1b[2;3H\x1b[38;2;126;231;135mNOVA / EYES\x1b[39m')
        hint='drag: pull / q: exit'
        out.write(f'\x1b[{rows-1};{max(1,(cols-len(hint))//2)}H\x1b[38;2;113;150;120m{hint}\x1b[39m'.encode())


def show(png, cols,rows,first=False):
    out=sys.stdout.buffer
    # A synchronized update prevents partially uploaded frames from flashing.
    out.write(b'\x1b[?2026h')
    try:
        for data in transmit(png,first):out.write(data)
        if first:
            x,y,w,h=geometry(cols,rows)
            out.write(f'\x1b[{y+1};{x+1}H'.encode())
            out.write(command({'a':'p','i':IMAGE_ID,'p':1,'c':w,'r':h,'C':1,'q':2}))
        else:
            out.write(command({'a':'a','i':IMAGE_ID,'c':1,'q':2}))
    finally:
        out.write(b'\x1b[?2026l');out.flush()


def show_battery(percent,charging,cols,rows,first=False):
    out=sys.stdout.buffer
    if percent is None or cols<22 or rows<6:
        out.write(command({'a':'d','d':'I','i':BATTERY_ID,'q':2}));out.flush();return
    for data in transmit(battery_png(percent,charging),first,BATTERY_ID):out.write(data)
    if first:
        width=6;height=2
        out.write(f'\x1b[2;{cols-width}H'.encode())
        out.write(command({'a':'p','i':BATTERY_ID,'p':1,'c':width,'r':height,'C':1,'q':2}))
    else:
        out.write(command({'a':'a','i':BATTERY_ID,'c':1,'q':2}))
    out.flush()


class Input:
    mouse=re.compile(rb'^\x1b\[<(\d+);(\d+);(\d+)([Mm])')
    csi=re.compile(rb'^\x1b\[[0-?]*[ -/]*[@-~]')
    def __init__(self,pixel_mouse=False,pixels=None):self.pending=b'';self.pixel_mouse=pixel_mouse;self.pixels=pixels
    def feed(self,data,cols,rows):
        self.pending+=data; events=[]
        while self.pending:
            match=self.mouse.match(self.pending)
            if match:
                button,x,y=map(int,match.group(1,2,3)); end=match.group(4)
                self.pending=self.pending[match.end():]
                if button&256:
                    events.append(('leave',None));continue
                point=canvas_position(x,y,cols,rows,self.pixel_mouse,self.pixels)
                if end==b'm' and button&3 in (0,3):events.append(('release',point))
                elif end==b'M' and button&32 and button&3==0:events.append(('drag',point))
                elif end==b'M' and button&3==0 and not button&96:events.append(('press',point))
                elif end==b'M' and button&32:
                    events.append(('move',(max(-1,min(1,(point[0]-WIDTH/2)/(WIDTH/2))),max(-1,min(1,(point[1]-HEIGHT/2)/(HEIGHT/2))))))
                continue
            if self.pending.startswith(b'\x1b['):
                match=self.csi.match(self.pending)
                if match: self.pending=self.pending[match.end():]; continue
                if len(self.pending)<64: break
            if self.pending in (b'\x1b',): break
            char=self.pending[:1]; self.pending=self.pending[1:]
            events.append(('key',char.decode('ascii',errors='ignore')))
        return events


def main():
    if len(sys.argv)>=3 and sys.argv[1]=='--preview':
        mode=sys.argv[3] if len(sys.argv)>3 else 'idle'
        Path(sys.argv[2]).write_bytes(render(1,mode)); return
    if not os.environ.get('KITTY_WINDOW_ID') or not sys.stdin.isatty():
        sys.exit('Эта анимация запускается в интерактивной панели Kitty.')
    settings=termios.tcgetattr(sys.stdin.fileno()); out=sys.stdout.buffer
    try:
        # Build other cached expressions only when first used, instead of a startup burst.
        lens('idle');lens('sleep')
        tty.setcbreak(sys.stdin.fileno())
        out.write(b'\x1b[?1049h\x1b[?25l\x1b[?1003h\x1b[?1006h\x1b[?1016h')
        mind=Mind(personality=Personality.load());context=Context();decoder=Input(pixel_mouse=True)
        size=None;battery_drawn=None;deadline=time.monotonic();last_png=None
        while True:
            now=time.monotonic(); cols,rows=shutil.get_terminal_size((36,18))
            first=size!=(cols,rows)
            if first:
                if size is not None and mind.springs.held is not None:
                    mind.springs.release();mind.pointer_down=False;mind.change('idle',now)
                layout(cols,rows);size=(cols,rows)
            if select.select([sys.stdin],[],[],max(0,deadline-now))[0]:
                data=os.read(sys.stdin.fileno(),4096)
                if not data: break
                quit_now=False
                for kind,value in decoder.feed(data,cols,rows):
                    if kind=='key' and value in ('q','\x03','\x04'): quit_now=True; break
                    mind.event(kind,value,time.monotonic())
                if quit_now: break
            now=time.monotonic(); mind.context(context.read(now),now); mind.tick(now)
            blend=smooth((now-mind.changed)/.28)
            png=render(now-mind.started,mind.state,mind.gaze,mind.opening,mind.previous,blend,mind.pulse,mind.battery_percent,mind.on_ac,mind.eye_openings,mind.look_strength,mind.springs.positions,now-mind.anger_started,mind.git_badge if now<mind.badge_until else '',mind.performance.snapshot(),mind.secrets.snapshot(now))
            if first or png!=last_png:show(png,cols,rows,first);last_png=png
            battery_state=(mind.battery_percent,bool(mind.on_ac))
            if first or battery_state!=battery_drawn:
                show_battery(*battery_state,cols,rows,first or battery_drawn is None or battery_drawn[0] is None)
                battery_drawn=battery_state
            # Every awake state targets 60 fps; settled sleep stays at 4 fps.
            deadline=now+1/mind.frame_rate(now)
            while time.monotonic()<deadline:
                wait=deadline-time.monotonic()
                if not select.select([sys.stdin],[],[],max(0,wait))[0]: break
                data=os.read(sys.stdin.fileno(),4096)
                if not data: return
                for kind,value in decoder.feed(data,cols,rows):
                    if kind=='key' and value in ('q','\x03','\x04'): return
                    mind.event(kind,value,time.monotonic())
                deadline=min(deadline,time.monotonic()+1/FPS)
    except KeyboardInterrupt:
        pass
    finally:
        if 'mind' in locals():mind.personality.save(force=True)
        out.write(command({'a':'d','d':'I','i':IMAGE_ID,'q':2}))
        out.write(command({'a':'d','d':'I','i':BATTERY_ID,'q':2}))
        out.write(command({'a':'d','d':'I','i':BACKGROUND_ID,'q':2}))
        out.write(b'\x1b[?1016l\x1b[?1003l\x1b[?1006l\x1b[0m\x1b[?25h\x1b[?1049l');out.flush()
        termios.tcsetattr(sys.stdin.fileno(),termios.TCSADRAIN,settings)


if __name__=='__main__': main()
