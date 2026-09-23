"""Trusted local first-run provisioning. Run with the API's APP_DB and PACKS_DIR.

No credential is placed on command line or printed. No public signup required.
api-python/.env is loaded the way scripts/run_local.py loads it (process env
wins), so the identity lands in the same APP_DB the API uses.

Interactive (default): prompts for email, display name and password twice.
Non-interactive: --email, --display-name and --password-stdin, which reads ONE
line from stdin. getpass reads the Windows console, not a pipe, so the
interactive form hangs under a pipe there; --password-stdin is the piped form:

    echo "$OWNER_PASSWORD" | python scripts/provision_identity.py --workspace demo-retail \
        --workspace-name Demo --email owner@example.com --display-name Ega --password-stdin
"""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api-python'))


def parser():
    p=argparse.ArgumentParser(description='Provision first identity with a configured tenant pack')
    p.add_argument('--workspace',required=True)
    p.add_argument('--workspace-name',required=True)
    p.add_argument('--email',help='owner email (skips the prompt)')
    p.add_argument('--display-name',help='owner display name (skips the prompt)')
    p.add_argument('--password-stdin',action='store_true',
                   help='read the password from ONE stdin line; requires --email and --display-name')
    p.add_argument('--env-file',type=Path,default=None,help='default: api-python/.env')
    return p


def credentials(args,stdin,ask,ask_secret):
    """(email, display name, password). `ask`/`ask_secret` are input/getpass.getpass."""
    if args.password_stdin:
        if not args.email or not args.display_name:
            raise SystemExit('--password-stdin requires --email and --display-name')
        password=stdin.readline().rstrip('\r\n')
        if not password:raise SystemExit('No password on stdin')
        return args.email.strip(),args.display_name.strip(),password
    email=(args.email or ask('Owner email: ')).strip()
    name=(args.display_name or ask('Owner display name: ')).strip()
    password=ask_secret('Password (minimum 12 characters): ')
    if password!=ask_secret('Confirm password: '):raise SystemExit('Passwords do not match')
    return email,name,password


def main(argv=None):
    args=parser().parse_args(argv)
    import run_local
    run_local.load_environment(args.env_file or run_local.ENV_FILE)
    from app.packs import load_pack
    from app.identity_store import bootstrap_identity
    if args.workspace=='template':raise SystemExit('Template cannot be provisioned')
    load_pack(args.workspace)
    email,name,password=credentials(args,sys.stdin,input,getpass.getpass)
    bootstrap_identity(email,password,name,args.workspace,args.workspace_name)
    print('Identity provisioned. Sign in from the dashboard. No tokens printed.')


if __name__=='__main__':main()
