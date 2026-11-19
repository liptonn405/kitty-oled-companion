"""Daily local personality. Only interaction counters are persisted."""
from collections import Counter
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import random
import tempfile
import time


class Personality:
    def __init__(self,path=None,seed=None,day=None):
        self.path=Path(path) if path is not None else None
        self.day=day or datetime.now().date().isoformat()
        self.base=random.Random(seed).uniform(.43,.7)
        self.counts=Counter();self.pending=Counter();self.minutes=0.;self.pending_minutes=0.
        self.last_overpull=0.;self.pending_overpull_time=0.
        self.last_save=0.;self.last_day_check=0.
        if self.path:
            saved=self._read()
            if saved.get('day')==self.day:
                self.base=saved.get('base',self.base)
                self.counts=Counter(saved.get('counts',{}));self.minutes=saved.get('minutes',0.)
                self.last_overpull=saved.get('last_overpull',0.)

    @classmethod
    def load(cls):
        state=Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
        return cls(state/'arch-gruvbox/nova.json')

    def _read(self):
        try:
            data=json.loads(self.path.read_text())
            return data if isinstance(data,dict) else {}
        except (OSError,ValueError,TypeError):return {}

    @property
    def energy(self):
        good=self.counts['gentle']+self.counts['click']*.25
        wins=self.counts['commit']+self.counts['push']
        recovery=math.exp(-max(0,time.time()-self.last_overpull)/90) if self.last_overpull else 0.
        return max(0,min(1,self.base+.24*math.tanh(good/7)+.14*math.tanh(wins/4)
                         -.22*math.tanh(self.counts['overpull']/3)*recovery-.2*math.tanh(self.minutes/180)))

    @property
    def mood(self):
        return 'playful' if self.energy>=.65 else 'sleepy' if self.energy<.34 else 'calm'

    def record(self,event):
        if event in ('gentle','click','overpull','commit','push','conflict','resolved','charging'):
            self.counts[event]+=1;self.pending[event]+=1
            if event=='overpull':self.last_overpull=self.pending_overpull_time=time.time()

    def advance(self,seconds,now):
        self.minutes+=seconds/60;self.pending_minutes+=seconds/60
        if now-self.last_day_check>=60:
            today=datetime.now().date().isoformat();self.last_day_check=now
            if today!=self.day:
                self.day=today;self.base=random.uniform(.43,.7)
                self.counts.clear();self.pending.clear();self.minutes=self.pending_minutes=0.
                self.last_overpull=self.pending_overpull_time=0.
        self.save(now=now)

    def save(self,force=False,now=None):
        now=time.monotonic() if now is None else now
        if self.path is None or (not force and now-self.last_save<30):return
        temporary=None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with (self.path.parent/'nova.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                old=self._read()
                same=old.get('day')==self.day
                counts=Counter(old.get('counts',{})) if same else Counter()
                counts.update(self.pending)
                minutes=(old.get('minutes',0.) if same else 0.)+self.pending_minutes
                base=old.get('base',self.base) if same else self.base
                last_overpull=max(old.get('last_overpull',0.) if same else 0.,self.pending_overpull_time)
                data={'version':2,'day':self.day,'base':base,'counts':dict(counts),'minutes':round(minutes,3),'last_overpull':last_overpull}
                with tempfile.NamedTemporaryFile(mode='w',dir=self.path.parent,prefix='.nova-',delete=False) as stream:
                    temporary=Path(stream.name);json.dump(data,stream,separators=(',',':'))
                os.replace(temporary,self.path)
                self.base=base;self.counts=counts;self.minutes=minutes
                self.last_overpull=last_overpull;self.pending_overpull_time=0.
                self.pending.clear();self.pending_minutes=0.;self.last_save=now
        except (OSError,ValueError,TypeError):pass
        finally:
            if temporary is not None:temporary.unlink(missing_ok=True)
