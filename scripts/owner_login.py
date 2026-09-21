"""Create a local owner session; save token locally rather than printing it."""
import json
import os
from pathlib import Path
import urllib.request
root=Path(__file__).resolve().parents[1]
values={}
for line in (root/'api-python/.env').read_text().splitlines():
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1);values[k]=v
request=urllib.request.Request('http://127.0.0.1:8000/auth/token',
    data=json.dumps({'tenant_id':'demo-retail','subject':'local-owner','role':'owner'}).encode(),
    headers={'Content-Type':'application/json','X-Admin-Token':values['ADMIN_TOKEN']},method='POST')
with urllib.request.urlopen(request,timeout=10) as r:token=json.load(r)['access_token']
p=root/'owner-token.local.txt';fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
with os.fdopen(fd,'w') as f:f.write(token)
print('Owner session saved to owner-token.local.txt. Paste it into the local UI; do not share or commit.')
