"""Trusted local first-run provisioning. Run with the API's APP_DB and PACKS_DIR.

No credential is placed on command line or printed. No public signup required.
Use the API runtime environment, not a different host database file.
"""
import argparse
import getpass
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'api-python'))


def main():
    parser=argparse.ArgumentParser(description='Provision first identity with a configured tenant pack')
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--workspace-name',required=True)
    args=parser.parse_args()
    from app.packs import load_pack
    from app.identity_store import bootstrap_identity
    if args.workspace=='template':raise SystemExit('Template cannot be provisioned')
    load_pack(args.workspace)
    email=input('Owner email: ').strip();name=input('Owner display name: ').strip()
    password=getpass.getpass('Password (minimum 12 characters): ')
    if password!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords do not match')
    bootstrap_identity(email,password,name,args.workspace,args.workspace_name)
    print('Identity provisioned. Sign in from the dashboard. No tokens printed.')


if __name__=='__main__':main()
