#!/usr/bin/env python3
"""Run exactly the requested Git command; publish only its resulting event type."""
import os
import hashlib
import subprocess
import sys
import time
import robot_bus


def operation(args):
    prefix=[];i=0
    while i<len(args):
        arg=args[i]
        if arg in ('-C','--git-dir','--work-tree') and i+1<len(args):
            prefix.extend(args[i:i+2]);i+=2;continue
        if arg.startswith(('--git-dir=','--work-tree=')) or (arg.startswith('-C') and len(arg)>2):
            prefix.append(arg);i+=1;continue
        if arg in ('-c','--config-env','--namespace','--exec-path') and i+1<len(args):i+=2;continue
        if arg.startswith('-'):i+=1;continue
        return arg,prefix
    return '',prefix


def inspect(prefix,what):
    env=dict(os.environ,GIT_OPTIONAL_LOCKS='0',GIT_TERMINAL_PROMPT='0')
    try:
        result=subprocess.run(['git',*prefix,*what],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=2)
        return result.stdout.strip() if result.returncode==0 else None
    except (OSError,subprocess.TimeoutExpired):return None


def publish(kind,conflict,repo):
    pid=os.environ.get('GRUVBOX_ROBOT_SHELL_PID') or str(os.getppid())
    if not pid.isdigit():return
    name=f'git-{pid}.json';state=robot_bus.read(name)
    now=time.monotonic();events=[e for e in state.get('events',[]) if 0<=now-e.get('stamp',0)<15]
    if kind:events.append({'id':f'{pid}-{time.time_ns()}','kind':kind,'stamp':now})
    robot_bus.publish(name,{'pid':int(pid),'repo':repo,'stamp':now,'conflict':bool(conflict),'events':events[-8:]})


def run(args):
    verb,prefix=operation(args)
    watched=verb in ('commit','push','merge','pull','rebase','cherry-pick','revert','add','reset','restore','checkout','switch','status') and not any(arg in ('--help','-h') for arg in args)
    before=inspect(prefix,['rev-parse','--verify','HEAD']) if verb=='commit' else None
    try:status=subprocess.run(['git',*args]).returncode
    except KeyboardInterrupt:return 130
    except FileNotFoundError:
        print('git: команда не найдена',file=sys.stderr);return 127
    if watched:
        try:
            unresolved=inspect(prefix,['ls-files','--unmerged','-z'])
            if unresolved is not None:
                conflict=bool(unresolved);kind=None
                pid=os.environ.get('GRUVBOX_ROBOT_SHELL_PID') or str(os.getppid())
                previous=robot_bus.read(f'git-{pid}.json')
                directory=inspect(prefix,['rev-parse','--absolute-git-dir'])
                repo=hashlib.sha256(directory or b'').hexdigest()[:16]
                if previous.get('repo')!=repo:previous={}
                if conflict and not previous.get('conflict'):kind='conflict'
                elif not conflict and previous.get('conflict'):kind='resolved'
                if verb=='commit' and status==0:
                    after=inspect(prefix,['rev-parse','--verify','HEAD'])
                    if after is not None and after!=before:kind='commit'
                elif verb=='push' and status==0 and not any(arg in ('--dry-run','-n') for arg in args):kind='push'
                publish(kind,conflict,repo)
        except (OSError,ValueError,TypeError):pass
    return 128-status if status<0 else status


if __name__=='__main__':sys.exit(run(sys.argv[1:]))
