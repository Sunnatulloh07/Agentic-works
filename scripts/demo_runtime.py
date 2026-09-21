"""Offline real SQLite demonstration, requires only Python >=3.11."""
import json
import sys
import tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api-python'))
from platform_runtime.engine import Engine
from platform_runtime.tools import build_registry

def main():
    with tempfile.TemporaryDirectory() as directory:
        policy=lambda t,a:{'tools':['records.create','records.list','reports.summary'],'ladder':'human_assisted'}
        e=Engine(Path(directory)/'demo.db',build_registry(),policy)
        tid=e.submit('demo','web','demo-1','ops',[{'tool':'records.create','args':{'kind':'lead','title':'Demo lead','body':'Approved business record'}},{'tool':'records.list','args':{'kind':'lead'}}],'owner')
        e.tick('demo');print('Before approval:',e.get('demo',tid)['status'])
        sid=e.get('demo',tid)['steps'][0]['id'];e.approve('demo',sid,'owner','approved','owner')
        e.tick('demo');e.tick('demo');r=e.get('demo',tid)
        print('After approval:',r['status']);print('Persisted records:',len(r['steps'][1]['result']['records']))
        again=e.submit('demo','web','demo-1','ops',[{'tool':'records.create','args':{'kind':'lead','title':'Demo lead','body':'Approved business record'}},{'tool':'records.list','args':{'kind':'lead'}}],'owner')
        print('Replay reuses same task:',again==tid)
        assert r['status']=='succeeded' and again==tid
if __name__=='__main__':main()
