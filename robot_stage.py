"""Procedural music accessories and short, interruptible scenes inside the panel."""
import math
import sys
from functools import lru_cache
import cairo


def ease(value):
    value=max(0,min(1,value));return value*value*(3-2*value)


class MusicAct:
    def __init__(self):
        self.stamp=0.;self.playing=False;self.last_gate=0.;self.engaged_until=0.;self.pause_until=0.;self.hidden_gate=0.
        self.level=0.;self.ear_off=0.

    def observe(self,data,now):
        stamp=data.get('stamp',0)
        if not (self.stamp<stamp<=now and now-stamp<1.2):return
        playing=bool(data.get('playing'));gate=data.get('dance_until',0)
        if gate>self.last_gate:
            self.last_gate=gate;self.engaged_until=gate;self.hidden_gate=0.
            if not playing and gate>now:self.pause_until=now+3.5
        if self.playing and not playing and (self.level>.03 or self.engaged_until>now):
            self.pause_until=now+3.5
        if not self.playing and playing and now<self.pause_until:
            self.engaged_until=max(self.engaged_until,now+3);self.pause_until=0.
        self.playing=playing;self.stamp=stamp

    def phase(self,now):
        if not (0<=now-self.stamp<1.2):return None
        if not self.playing and now<self.pause_until:return 'pause'
        if self.playing and now<self.engaged_until:return 'playing'
        return None

    def hide(self):
        self.hidden_gate=self.last_gate
        self.engaged_until=self.pause_until=0.
        self.level=self.ear_off=0.

    def advance(self,dt,now,allowed=True):
        phase=self.phase(now) if allowed else None
        alpha=1-math.exp(-dt*10)
        self.level+=((1. if phase else 0.)-self.level)*alpha
        ear=1. if phase=='pause' else 0. if phase=='playing' else self.ear_off
        self.ear_off+=(ear-self.ear_off)*alpha
        if self.level<.002:self.level=0.

    def snapshot(self):return {'level':self.level,'ear_off':self.ear_off}


class SecretScenes:
    DURATIONS={'star':4.4,'bug':4.8,'window':4.2}
    def __init__(self,rng,now):
        self.rng=rng;self.kind=None;self.started=0.;self.manual=False;self.bag=[]
        self.next_scene=now+rng.uniform(60,120)

    def start(self,kind,now,manual=False):
        self.kind=kind;self.started=now;self.manual=manual

    def cancel(self,now):
        self.kind=None;self.manual=False;self.next_scene=now+self.rng.uniform(180,360)

    def update(self,now,available):
        if self.kind and (not available or now-self.started>=self.DURATIONS[self.kind]):self.cancel(now)
        if self.kind is None and available and now>=self.next_scene:
            if not self.bag:self.bag=['star','bug','window'];self.rng.shuffle(self.bag)
            self.start(self.bag.pop(),now)
        return self.kind is not None

    def snapshot(self,now):return {'kind':self.kind,'age':max(0,now-self.started)} if self.kind else None

    def mode(self,now):
        age=now-self.started
        if self.kind=='star':return 'wink' if 1.1<age<1.8 else 'curious'
        if self.kind=='bug':return 'surprise' if 2.1<age<2.8 else 'curious'
        return 'angry' if 1.2<age<2.5 else 'curious'

    def gaze(self,now):
        age=now-self.started
        if self.kind=='star':x,y=star_position(age)
        elif self.kind=='bug':x,y=bug_position(age)
        else:x,y=window_position(age)+55,165
        return max(-.9,min(.9,(x-240)/170)),max(-.8,min(.8,(y-110)/75))


def rounded(ctx,x,y,w,h,r):
    r=min(r,w/2,h/2);ctx.new_sub_path()
    for cx,cy,start in [(x+w-r,y+r,-math.pi/2),(x+w-r,y+h-r,0),(x+r,y+h-r,math.pi/2),(x+r,y+r,math.pi)]:
        ctx.arc(cx,cy,r,start,start+math.pi/2)
    ctx.close_path()


def glow(ctx,path,width=1.7,alpha=1.):
    ctx.set_line_cap(cairo.LINE_CAP_ROUND);ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    for spread,strength in ((7,.025),(3,.1),(0,.8)):
        ctx.new_path();path(ctx);ctx.set_line_width(width+spread)
        ctx.set_source_rgba(.43,.93,.55,alpha*strength);ctx.stroke()


def line(ctx,points,width=1.6,alpha=1.):
    def path(c):
        c.move_to(*points[0])
        for point in points[1:]:c.line_to(*point)
    glow(ctx,path,width,alpha)


@lru_cache(maxsize=1)
def ear_cushion():
    """Soft squircle studio ear pads in bright OLED white."""
    width,height=92,116
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
    pixels=surface.get_data();stride=surface.get_stride()
    hw,hh,r=19,33,13
    for iy in range(height):
        y=iy-height/2
        for ix in range(width):
            x=ix-width/2
            qx,qy=abs(x)-hw+r,abs(y)-hh+r
            distance=math.hypot(max(qx,0),max(qy,0))+min(max(qx,qy),0)-r
            body=1/(1+math.exp(max(-60,min(60,distance/1.5))))
            halo=.14*math.exp(-max(0,distance)**2/(2*8**2))
            alpha=min(1,body*.92+halo*1.5)
            shade=.72+.28*math.exp(-((x+5)**2+(y+10)**2)/(2*22**2))
            base=min(alpha,(body*.95+halo)*shade)
            red=base*(.94+.04*body);green=base*(.96+.03*body);blue=base
            rgba=tuple(round(v*255) for v in (red,green,blue,alpha))
            offset=iy*stride+ix*4
            pixels[offset:offset+4]=bytes((rgba[2],rgba[1],rgba[0],rgba[3])) if sys.byteorder=='little' else bytes((rgba[3],*rgba[:3]))
    surface.mark_dirty();return surface


@lru_cache(maxsize=1)
def headband_light(alpha=1.):
    light=cairo.LinearGradient(0,12,0,115)
    for at,color in [(0,(1.,1.,1.)),(.45,(.92,.95,.98)),(1,(.75,.80,.85))]:
        light.add_color_stop_rgba(at,*color,alpha)
    return light


def draw_music_back(ctx,act,t,pulse=0.):
    if not act or act['level']<.002:return
    alpha=act['level'];off=act['ear_off'];ctx.save()
    ctx.translate(0,math.sin(t*1.9)*.9-math.sqrt(max(0,pulse))*5)
    def band(c):
        c.move_to(84,110);c.line_to(84,78)
        c.curve_to(84,38,112,20,154,20);c.line_to(326,20)
        c.curve_to(368,20,396+off*12,38,396+off*12,105-off*43)
    ctx.set_line_cap(cairo.LINE_CAP_ROUND);ctx.set_line_join(cairo.LINE_JOIN_ROUND)
    for width,strength in ((44,.03),(34,.06),(26,.12),(20,.24)):
        ctx.new_path();band(ctx);ctx.set_line_width(width)
        ctx.set_source_rgba(.95,.98,1.,alpha*strength);ctx.stroke()
    ctx.new_path();band(ctx);ctx.set_line_width(15)
    ctx.set_source(headband_light(alpha if alpha<.999 else 1.));ctx.stroke()
    ctx.new_path();band(ctx);ctx.set_line_width(6)
    ctx.set_source_rgba(1.,1.,1.,alpha*.55);ctx.stroke()
    texture=ear_cushion()
    for x,y,angle in [(84,110,0),(396+off*12,110-off*45,-off*.22)]:
        ctx.save();ctx.translate(x,y);ctx.rotate(angle)
        ctx.set_source_surface(texture,-46,-58);ctx.paint_with_alpha(alpha);ctx.restore()
    ctx.restore()


def star_position(age):
    if age<1.1:
        p=ease(age/1.1);return 448-117*p,18+139*p
    if age<2.7:return 331,157+2*math.sin(age*5)
    p=ease((age-2.7)/1.7);return 331-180*p,157-190*p


def bug_position(age):
    p=min(1,age/4.8);x=20+440*p
    y=181-78*math.exp(-((age-2.45)/.48)**2)+math.sin(age*9)*2
    return x,y


def window_position(age):
    if age<1.2:return 25+120*ease(age/1.2)
    if age<2.5:return 145+math.sin((age-1.2)*7)*10
    return 145+350*ease((age-2.5)/1.7)


def draw_secret(ctx,scene,t):
    if not scene:return
    kind,age=scene['kind'],scene['age']
    duration=SecretScenes.DURATIONS[kind];alpha=ease(age/.25)*(1-ease((age-duration+.35)/.35))
    if kind=='star':
        x,y=star_position(age)
        if age<1.1 or age>2.7:line(ctx,[(x+17,y-22),(x+6,y-8)],1.3,alpha*.5)
        def star(c):
            for i in range(8):
                angle=-math.pi/2+i*math.pi/4;r=9 if i%2==0 else 3.2
                point=(x+math.cos(angle)*r,y+math.sin(angle)*r)
                c.move_to(*point) if i==0 else c.line_to(*point)
            c.close_path()
        glow(ctx,star,1.8,alpha)
    elif kind=='bug':
        x,y=bug_position(age)
        glow(ctx,lambda c:c.arc(x,y,5,0,math.tau),1.6,alpha)
        for i in range(3):
            dx=(i-1)*4;wiggle=math.sin(age*30+i)*2
            line(ctx,[(x+dx,y),(x+dx-3,y-6-wiggle)],1,alpha*.8)
            line(ctx,[(x+dx,y),(x+dx+3,y+6+wiggle)],1,alpha*.8)
        line(ctx,[(x+4,y-2),(x+9,y-5),(x+11,y-4)],1,alpha)
    else:
        x=window_position(age);y=148
        ctx.set_source_rgba(.004,.015,.007,alpha*.9);rounded(ctx,x,y,126,55,6);ctx.fill()
        glow(ctx,lambda c:rounded(c,x,y,126,55,6),1.4,alpha)
        line(ctx,[(x+1,y+13),(x+125,y+13)],1,alpha*.7)
        for i in range(3):
            glow(ctx,lambda c,cx=x+10+i*8:c.arc(cx,y+7,1.6,0,math.tau),1.1,alpha*.85)
