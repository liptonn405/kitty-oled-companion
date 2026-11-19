#!/usr/bin/env python3
"""Gruvbox desktop panels. Linux counters, MPRIS, and real sink audio only."""
import array
import collections
import curses
import glob
import math
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import robot_bus

BG = '#000000'
PALETTE = ['#7ee787', '#7ee787', '#7ee787', '#9bd8b0', '#a7f3a0', '#8da18f', '#71c49a']
STOP = threading.Event()


def clean(s):
    return ''.join(c for c in str(s) if not unicodedata.category(c).startswith('C'))


def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except (OSError, ValueError):
        return default


def number(path):
    try:
        return float(read(path))
    except ValueError:
        return None


def run(args, timeout=2):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return b''


def put(win, y, x, text, color=0, bold=False):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w-1:
        return
    try:
        win.addnstr(y, max(0, x), clean(text), max(0, w-x-1), curses.color_pair(color+1) | (curses.A_BOLD if bold else 0))
    except curses.error:
        pass


def centered(win, y, text, color=0):
    put(win, y, max(1, (win.getmaxyx()[1]-len(clean(text)))//2), text, color)


def bar(win, y, label, value, suffix, color=2):
    h, w = win.getmaxyx()
    put(win, y, 2, label, color, True)
    put(win, y, max(18, w-len(suffix)-3), suffix, 0)
    n = max(2, w-5)
    fill = 0 if value is None else round(n*min(100, max(0, value))/100)
    put(win, y+1, 2, '━'*fill, color)
    put(win, y+1, 2+fill, '─'*(n-fill), 5)


def spark(data, width):
    chars = '▁▂▃▄▅▆▇█'
    return ''.join(chars[min(7, max(0, int(x*7/100)))] for x in list(data)[-width:])


def cpu_ticks():
    vals = list(map(int, read('/proc/stat').splitlines()[0].split()[1:]))
    return sum(vals[:8]), vals[3]+vals[4]


def temperatures():
    out = {}
    for root in glob.glob('/sys/class/hwmon/hwmon*'):
        name = read(root+'/name')
        vals = [number(x) for x in glob.glob(root+'/temp*_input')]
        vals = [v/1000 for v in vals if v is not None and 0 < v < 150000]
        if vals:
            out[name] = max(vals)
    cpu = next((v for k,v in out.items() if k in ('coretemp','k10temp','cpu_thermal')), None)
    gpu = next((v for k,v in out.items() if k in ('amdgpu','nouveau','i915','xe')), None)
    return cpu, gpu


class Metrics:
    def __init__(self):
        self.ticks = cpu_ticks()
        self.prev_time = time.monotonic()
        self.net = self.network()
        self.cpu_hist = collections.deque(maxlen=200)
        self.mem_hist = collections.deque(maxlen=200)
        self.prev_engines = {}
        self.gpu = next(iter(sorted(glob.glob('/sys/class/drm/card[0-9]/device'))), None)
        driver = Path(self.gpu+'/driver').resolve().name if self.gpu else ''
        self.gpu_name = {'i915':'Intel', 'xe':'Intel', 'amdgpu':'AMD', 'nouveau':'NVIDIA', 'nvidia':'NVIDIA'}.get(driver, driver or 'GPU')

    @staticmethod
    def network():
        rx = tx = 0
        for line in read('/proc/net/dev').splitlines()[2:]:
            name, _, rest = line.partition(':')
            vals = rest.split()
            if name.strip() != 'lo' and len(vals) >= 9:
                rx += int(vals[0]); tx += int(vals[8])
        return rx,tx

    def intel_engines(self):
        # DRM fdinfo counters cover readable processes of this user, not a global GPU total.
        samples = {}
        uid = os.getuid()
        for proc in glob.glob('/proc/[0-9]*'):
            try:
                if os.stat(proc).st_uid != uid:
                    continue
                for fd in glob.glob(proc+'/fdinfo/*'):
                    text = read(fd)
                    if 'drm-client-id:' not in text:
                        continue
                    fields = dict(line.split(':',1) for line in text.splitlines() if ':' in line)
                    cid = fields['drm-client-id'].strip()
                    dev = fields.get('drm-pdev','').strip()
                    for k,v in fields.items():
                        if k.startswith('drm-engine-') and v.strip().endswith(' ns'):
                            samples[(dev,cid,k)] = int(v.split()[0])
            except (OSError,ValueError):
                continue
        return samples

    def sample(self):
        now = time.monotonic(); dt = max(.1,now-self.prev_time)
        ticks = cpu_ticks(); total=ticks[0]-self.ticks[0]
        cpu=100*(1-(ticks[1]-self.ticks[1])/total) if total else 0
        self.ticks=ticks
        mem = {}
        for line in read('/proc/meminfo').splitlines():
            k,v=line.split(':',1); mem[k]=int(v.split()[0])*1024
        used=mem['MemTotal']-mem['MemAvailable']; pct=used/mem['MemTotal']*100
        gpu_pct=None; freq=None; scope=''
        if self.gpu:
            gpu_pct=number(self.gpu+'/gpu_busy_percent')
            card=Path(self.gpu).parent
            freq=number(card/'gt_cur_freq_mhz')
            if gpu_pct is None and self.gpu_name=='Intel':
                cur=self.intel_engines(); engines={}
                for key,value in cur.items():
                    if key in self.prev_engines:
                        engines[key[2]]=engines.get(key[2],0)+max(0,value-self.prev_engines[key])
                if engines:
                    gpu_pct=min(100,max(engines.values())/dt/1e7); scope=' · мои процессы'
                self.prev_engines=cur
        if self.gpu_name=='NVIDIA' and shutil.which('nvidia-smi'):
            values=run(['nvidia-smi','--query-gpu=utilization.gpu,temperature.gpu','--format=csv,noheader,nounits']).decode().splitlines()
            try:
                gpu_pct,ntemp=map(float,values[0].split(','))
            except (ValueError,IndexError):
                ntemp=None
        else:
            ntemp=None
        ct,gt=temperatures(); gt=ntemp if ntemp is not None else gt
        net=self.network(); down=max(0,net[0]-self.net[0])/dt; up=max(0,net[1]-self.net[1])/dt
        self.net=net; self.prev_time=now
        disk=shutil.disk_usage(Path.home())
        self.cpu_hist.append(cpu); self.mem_hist.append(pct)
        return dict(cpu=cpu,used=used,total=mem['MemTotal'],mem=pct,gpu=gpu_pct,freq=freq,scope=scope,ct=ct,gt=gt,down=down,up=up,disk=disk)


def monitor(win):
    obj=Metrics(); next_sample=0; data=None
    win.timeout(100)
    while not STOP.is_set():
        if time.monotonic() >= next_sample:
            data=obj.sample(); next_sample=time.monotonic()+1.5
        win.erase(); h,w=win.getmaxyx()
        put(win,0,2,'SYSTEM / LIVE',1,True)
        put(win,0,max(20,w-11),time.strftime('%H:%M:%S'),5)
        if h<14 or w<32:
            centered(win,h//2,'Увеличь панель',5)
        else:
            d=data; temp=lambda t: f' · {t:.0f}°C' if t is not None else ''
            bar(win,3,'CPU',d['cpu'],f"{d['cpu']:.0f}%"+temp(d['ct']),2)
            put(win,5,2,spark(obj.cpu_hist,w-5),2)
            bar(win,7,'RAM',d['mem'],f"{d['used']/2**30:.1f} / {d['total']/2**30:.1f} GiB",3)
            gpu_label=obj.gpu_name+' GPU'
            suffix=(f"{d['gpu']:.0f}%" if d['gpu'] is not None else 'загрузка н/д')+temp(d['gt'])
            bar(win,11,gpu_label,d['gpu'],suffix,4)
            if h>=19:
                info=(f"{d['freq']:.0f} MHz" if d['freq'] is not None else '')+d['scope']
                put(win,13,2,info or 'Датчики GPU: доступные данные',5)
                put(win,15,2,f"NET   ↓ {d['down']/2**20:.2f}   ↑ {d['up']/2**20:.2f} MiB/s",6)
                disk=d['disk']; put(win,17,2,f"DISK  {disk.used/2**30:.0f} / {disk.total/2**30:.0f} GiB",5)
            if h>=23:
                put(win,20,2,'RAM  '+spark(obj.mem_hist,w-10),3)
            put(win,h-1,2,'q — закрыть панель',5)
        win.refresh()
        if win.getch() in (ord('q'),27):
            return


def beat_envelope(samples,levels,average,pulse):
    n=len(samples)
    rms=math.sqrt(sum((v/32768)**2 for v in samples)/n)
    # Linear bass energy preserves transients that the logarithmic visualizer hides.
    values=[v/32768*(.5-.5*math.cos(2*math.pi*i/(n-1))) for i,v in enumerate(samples)]
    power=0.0
    for frequency in (60,90,130,180):
        k=round(frequency*n/8000); coefficient=2*math.cos(2*math.pi*k/n)
        a=b=0.0
        for value in values:
            current=value+coefficient*a-b; b=a; a=current
        power+=max(0,a*a+b*b-coefficient*a*b)/n**2
    low=math.sqrt(power/4) if rms>.0015 else 0.0
    average=.92*average+.08*low
    relative=low/max(.001,average)
    onset=max(0,(relative-.85)*1.15) if low>.0015 else 0
    level=min(1,max(onset,min(.12,math.sqrt(rms)*.3))) if rms>.0015 else 0.0
    return average,max(level,pulse*.58)


class Player:
    def __init__(self):
        self.info={}; self.bus=None; self.GLib=None; self.cover=b''; self.art_url=None
        self.service=None; self.last_position=0; self.last_query=time.monotonic()
        self.bands=[0.0]*24; self.audio_ok=False; self.audio_error='Подключение к звуку…'
        self.proc=None
        self.pulse=0.0; self.audio_stamp=0.0; self.low_average=0.0

    def call(self,dest,path,interface,method,args=None):
        return self.bus.call_sync(dest,path,interface,method,args,None,0,900,None).unpack()

    def media(self):
        try:
            from gi.repository import Gio,GLib
            self.GLib=GLib
            self.bus=Gio.bus_get_sync(Gio.BusType.SESSION,None)
        except Exception:
            self.info={'title':'Нет доступа к плеерам','artist':'Нужен python-gobject и пользовательская сессия'}
            return
        while not STOP.is_set():
            try:
                names=self.call('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus','ListNames')[0]
                candidates=[]
                for name in names:
                    if name.startswith('org.mpris.MediaPlayer2.'):
                        try:
                            props=self.call(name,'/org/mpris/MediaPlayer2','org.freedesktop.DBus.Properties','GetAll',GLib.Variant('(s)',('org.mpris.MediaPlayer2.Player',)))[0]
                            candidates.append((name,props))
                        except Exception:
                            pass
                candidates.sort(key=lambda x:x[1].get('PlaybackStatus')!='Playing')
                if candidates:
                    name,p=candidates[0]; meta=p.get('Metadata',{})
                    title=meta.get('xesam:title') or 'Без названия'
                    artists=meta.get('xesam:artist',[])
                    info={'title':title,'artist':', '.join(artists) if isinstance(artists,list) else str(artists),'album':meta.get('xesam:album',''),'playing':p.get('PlaybackStatus')=='Playing','length':meta.get('mpris:length',0)/1e6,'player':name.split('.')[-1]}
                    self.last_position=max(0,p.get('Position',0)/1e6); self.last_query=time.monotonic()
                    self.service=name; self.info=info
                    url=meta.get('mpris:artUrl','')
                    if url!=self.art_url:
                        self.art_url=url; self.cover=self.fetch_cover(url)
                else:
                    self.service=None; self.info={}; self.cover=b''; self.art_url=None
            except Exception:
                self.service=None; self.info={}; self.cover=b''
            STOP.wait(1)

    @staticmethod
    def fetch_cover(url):
        try:
            parts=urllib.parse.urlsplit(url)
            if parts.scheme not in ('http','https','file') or (parts.scheme=='file' and parts.netloc not in ('','localhost')):
                return b''
            req=urllib.request.Request(url,headers={'User-Agent':'GruvboxDesktop/1.0'})
            with urllib.request.urlopen(req,timeout=3) as response:
                data=response.read(8*1024*1024+1)
                return data if len(data)<=8*1024*1024 else b''
        except Exception:
            return b''

    def control(self,method):
        if self.bus and self.service:
            try:
                self.call(self.service,'/org/mpris/MediaPlayer2','org.mpris.MediaPlayer2.Player',method)
            except Exception:
                pass

    @staticmethod
    def spectrum(samples):
        n=len(samples); result=[]
        vals=[v/32768*(.5-.5*math.cos(2*math.pi*i/(n-1))) for i,v in enumerate(samples)]
        for i in range(24):
            frequency=65*(3500/65)**(i/23)
            k=round(frequency*n/8000); coeff=2*math.cos(2*math.pi*k/n)
            q1=q2=0.0
            for value in vals:
                q0=value+coeff*q1-q2; q2=q1; q1=q0
            magnitude=math.sqrt(max(0,q1*q1+q2*q2-coeff*q1*q2))/n
            result.append(min(1,max(0,(20*math.log10(max(1e-9,magnitude))+65)/55)))
        return result

    def audio(self):
        if not shutil.which('ffmpeg') or not shutil.which('pactl'):
            self.audio_error='Для спектра нужны ffmpeg и pactl'; return
        while not STOP.is_set():
            sink=run(['pactl','get-default-sink']).decode().strip()
            if not sink:
                self.audio_error='Нет подключения к системному звуку'; STOP.wait(4); continue
            # Capture only the output monitor. Never fall back to the default microphone.
            try:
                self.proc=subprocess.Popen(['ffmpeg','-nostdin','-loglevel','error','-f','pulse','-i',sink+'.monitor','-ac','1','-ar','8000','-f','s16le','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
                pending=b''
                while not STOP.is_set():
                    ready,_,_=select.select([self.proc.stdout],[],[],.4)
                    if not ready:
                        if self.proc.poll() is not None: break
                        continue
                    chunk=os.read(self.proc.stdout.fileno(),4096)
                    if not chunk: break
                    pending+=chunk
                    while len(pending)>=1024:
                        raw,pending=pending[:1024],pending[1024:]
                        samples=array.array('h'); samples.frombytes(raw)
                        if sys.byteorder!='little': samples.byteswap()
                        target=self.spectrum(samples)
                        self.low_average,self.pulse=beat_envelope(samples,target,self.low_average,self.pulse)
                        self.audio_stamp=time.monotonic()
                        self.bands=[max(v,old*.76) for v,old in zip(target,self.bands)]
                        self.audio_ok=True
            except OSError:
                pass
            finally:
                self.audio_ok=False; self.audio_error='Ожидание системного звука'; self.bands=[0.0]*24; self.pulse=0.0
                self.kill_audio()
            STOP.wait(3)

    def kill_audio(self):
        proc=self.proc
        if proc is not None:
            try:
                proc.terminate(); proc.wait(timeout=1)
            except (OSError,subprocess.TimeoutExpired):
                try: proc.kill(); proc.wait(timeout=1)
                except (OSError,subprocess.TimeoutExpired): pass


def music(win):
    player=Player()
    threading.Thread(target=player.media,daemon=True).start()
    threading.Thread(target=player.audio,daemon=True).start()
    win.timeout(65); previous=None
    dance_until=0.0
    sys.stdout.write('\033[?1004h'); sys.stdout.flush()
    try:
        while not STOP.is_set():
            h,w=win.getmaxyx(); info=player.info
            now=time.monotonic()
            robot_bus.publish('music.json',{'stamp':now,'playing':bool(info.get('playing')),
                'audio_ok':player.audio_ok and now-player.audio_stamp<.5,
                'pulse':player.pulse,'dance_until':dance_until})
            signature=(h,w,player.art_url,bool(player.cover))
            rebuild=signature!=previous
            if rebuild:
                win.erase()
                if os.environ.get('KITTY_WINDOW_ID'):
                    sys.stdout.write('\033_Ga=d,d=I,i=4242,q=2\033\\'); sys.stdout.flush()
                previous=signature
            put(win,0,2,'NOW / PLAYING',1,True)
            put(win,0,max(20,w-12),'♫  GRUVBOX',5)
            if h<14 or w<32:
                centered(win,h//2,'Увеличь панель',5)
            else:
                cover_h=min(7,max(4,h-12)); text_x=min(23,max(18,w//3))
                for y in range(2,2+cover_h):
                    put(win,y,text_x,' '*(w-text_x-1))
                if not player.cover:
                    for y in range(2,2+cover_h):
                        put(win,y,2,' '*(text_x-3),5)
                    put(win,2+cover_h//2,7,'♫',1,True)
                put(win,2,text_x,info.get('title','Сейчас тихо'),2,True)
                put(win,4,text_x,info.get('artist','Включи музыку в плеере'),0)
                put(win,5,text_x,info.get('album','или в браузере'),5)
                if cover_h>=6:
                    put(win,7,text_x,('▶  играет' if info.get('playing') else 'Ⅱ  пауза') if info else 'Ожидание трека',4)
                y=3+cover_h
                length=info.get('length',0)
                pos=player.last_position+(time.monotonic()-player.last_query if info.get('playing') else 0)
                if length: pos=min(pos,length)
                clock=lambda t:f'{int(max(0,t))//60}:{int(max(0,t))%60:02d}'
                put(win,y,2,(clock(pos)+'  /  '+clock(length) if info else 'Плеер подключается через MPRIS').ljust(w-4),5)
                width=w-5; filled=int(width*pos/length) if length else 0
                put(win,y+1,2,'━'*filled,4); put(win,y+1,2+filled,'─'*(width-filled),5)
                bottom=h-4; top=y+3; rows=max(1,bottom-top)
                count=min(24,max(1,(w-4)//2)); start=max(2,(w-count*2)//2)
                for row in range(top,bottom):
                    put(win,row,2,' '*(w-4))
                for i in range(count):
                    level=player.bands[int(i*24/count)] if player.audio_ok else 0
                    for j in range(rows):
                        fill=max(0,min(8,round((level*rows-j)*8)))
                        glyph=' ▁▂▃▄▅▆▇█'[fill]
                        put(win,bottom-1-j,start+2*i,glyph,2 if j<rows//2 else 1)
                put(win,h-3,2,('Системный звук · LIVE' if player.audio_ok else player.audio_error).ljust(w-4),5)
                put(win,h-1,2,'Space ⏯   ← / → трек   q выход',5)
            win.refresh()
            if rebuild and player.cover and h>=14 and w>=32 and os.environ.get('KITTY_WINDOW_ID'):
                try:
                    subprocess.run(['kitty','+kitten','icat','--stdin=yes','--transfer-mode=stream','--engine=builtin','--scale-up','--image-id=4242','--place',f'{text_x-4}x{cover_h}@2x2'],input=player.cover,stderr=subprocess.DEVNULL,timeout=3)
                except (OSError,subprocess.SubprocessError):
                    pass
            key=win.getch()
            if key>255:
                focus_key=curses.keyname(key)
                if focus_key==b'kxIN':
                    dance_until=time.monotonic()+10.0
                    continue
                if focus_key==b'kxOUT': continue
            if key==27:
                # Focus reporting is an ESC sequence, not an instruction to quit.
                win.timeout(20)
                second=win.getch(); third=win.getch() if second==ord('[') else -1
                win.timeout(65)
                if second==ord('[') and third==ord('I'):
                    dance_until=time.monotonic()+10.0
                    continue
                if second==ord('[') and third==ord('O'): continue
                if second==-1: break
                continue
            if key==ord('q'): break
            method={ord(' '):'PlayPause',curses.KEY_RIGHT:'Next',curses.KEY_LEFT:'Previous'}.get(key)
            if method: threading.Thread(target=player.control,args=(method,),daemon=True).start()
    finally:
        STOP.set(); player.kill_audio()
        robot_bus.publish('music.json',{'stamp':time.monotonic(),'playing':False,'audio_ok':False,'pulse':0,'dance_until':0})
        sys.stdout.write('\033[?1004l'); sys.stdout.flush()
        if os.environ.get('KITTY_WINDOW_ID'):
            sys.stdout.write('\033_Ga=d,d=I,i=4242,q=2\033\\'); sys.stdout.flush()


def main(win):
    curses.curs_set(0); curses.start_color(); curses.use_default_colors()
    if curses.can_change_color() and curses.COLORS>=256:
        for i,c in enumerate([BG]+PALETTE):
            rgb=[int(c[j:j+2],16)*1000//255 for j in (1,3,5)]
            curses.init_color(230+i,*rgb)
        for i in range(len(PALETTE)):
            curses.init_pair(i+1,231+i,230)
    else:
        for i,c in enumerate([7,3,2,6,1,7,2]): curses.init_pair(i+1,c,-1)
    win.bkgd(' ',curses.color_pair(1)); win.keypad(True)
    (music if len(sys.argv)>1 and sys.argv[1]=='music' else monitor)(win)


if __name__=='__main__':
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        STOP.set()
