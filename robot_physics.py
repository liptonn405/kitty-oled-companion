"""Two spring-mounted eyes with an elastic grip and a fixed break distance."""
import math

GRIP_LIMIT=120.


class EyeSprings:
    def __init__(self):
        self.positions=[[0.,0.],[0.,0.]];self.velocities=[[0.,0.],[0.,0.]]
        self.held=None;self.origin=None;self.target=[0.,0.];self.broken=False;self.distance=0.
        self.start_offset=[0.,0.]

    def press(self,point,centres):
        self.broken=False;self.distance=0.;self.origin=point
        choices=[(((point[0]-x)/58)**2+((point[1]-y)/65)**2,i) for i,(x,y) in enumerate(centres)]
        distance,index=min(choices)
        self.held=index if distance<=1 else None
        self.target=list(self.positions[index]) if self.held is not None else [0.,0.]
        self.start_offset=self.target[:]
        return self.held is not None

    def move(self,point):
        if self.held is None:return False
        dx,dy=point[0]-self.origin[0],point[1]-self.origin[1]
        self.distance=math.hypot(dx,dy)
        if self.distance>GRIP_LIMIT:
            self.break_grip((dx/self.distance,dy/self.distance));return True
        elastic=58*(1-math.exp(-self.distance/58))/max(.001,self.distance)
        self.target=[self.start_offset[0]+dx*elastic,self.start_offset[1]+dy*elastic]
        return False

    def break_grip(self,direction=None):
        if self.held is None:return False
        index=self.held
        if direction is None:
            dx,dy=self.positions[index];length=math.hypot(dx,dy)
            direction=(dx/length,dy/length) if length>.01 else (1.,0.)
        self.velocities[index][0]-=direction[0]*220
        self.velocities[index][1]-=direction[1]*220
        self.held=None;self.broken=True;return True

    def release(self):
        held=self.held is not None;self.held=None
        return held

    def advance(self,dt,mood='calm'):
        stiffness,damping=(175.,15.) if mood=='playful' else (120.,19.) if mood=='sleepy' else (155.,18.)
        count=max(1,math.ceil(dt*120));step=dt/count
        for _ in range(count):
            for i in range(2):
                target=self.target if self.held==i else (0.,0.)
                for axis in range(2):
                    velocity=self.velocities[i][axis]
                    velocity+=((target[axis]-self.positions[i][axis])*stiffness-velocity*damping)*step
                    self.velocities[i][axis]=velocity
                    self.positions[i][axis]+=velocity*step
                    if abs(self.positions[i][axis])<.015 and abs(velocity)<.05:
                        self.positions[i][axis]=self.velocities[i][axis]=0.

    @property
    def moving(self):
        return self.held is not None or any(abs(v)>.04 for pair in self.positions+self.velocities for v in pair)
