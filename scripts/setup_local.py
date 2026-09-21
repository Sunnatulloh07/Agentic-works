"""Generate local-only config without printing secrets. Run from repository root."""
import json
import os
import secrets
from pathlib import Path
root=Path(__file__).resolve().parents[1]
env=root/'api-python/.env'
if env.exists():raise SystemExit('Existing .env preserved. Edit it manually.')
body='\n'.join(['ENV=dev','PIPELINE_MODE=platform','IDENTITY_DIRECTORY=true','IDENTITY_BOOTSTRAP_ENABLED=false','JWT_SECRET='+secrets.token_urlsafe(48),
               'ADMIN_TOKEN='+secrets.token_urlsafe(32),'TELEGRAM_WEBHOOK_SECRET='+secrets.token_urlsafe(24),
               'CORS_ORIGINS=http://localhost:3000','META_VERIFY_TOKEN='+secrets.token_urlsafe(24),
               '# Add provider keys here; never commit this file.',''])
fd=os.open(env,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(body)
config=root/'config/integrations.json'
if not config.exists():config.write_text('{}\n')
print('Created local .env and empty integration mapping. No secrets printed.')
