"""Machine-readable evidence gate. Missing evidence means NO-GO, never guessed PASS."""
import argparse
import json
from pathlib import Path

REQUIRED={'python_runtime','node_runner','browser_session_client','http_integration','ui_typecheck','ui_production_build',
          'dependency_security_review','postgres_live_two_tenant','crm_oauth_sync_write_reconcile','mcp_live',
          'oauth_lifecycle','google_provider_acceptance','browser_oauth_acceptance','oidc_mfa','secret_vault','load_fault_injection','encrypted_offsite_restore','staging_pilot'}


def check(evidence):
    gates=evidence.get('gates',{})
    blockers=[]
    for gate in sorted(REQUIRED):
        row=gates.get(gate,{})
        if row.get('status')!='PASS' or not row.get('evidence'):blockers.append(gate)
    return {'release': 'GO' if not blockers else 'NO_GO','blockers':blockers}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('evidence');args=parser.parse_args()
    # Evidence files carry Uzbek text; the default codec is not UTF-8 on Windows.
    result=check(json.loads(Path(args.evidence).read_text(encoding='utf-8')))
    print(json.dumps(result,indent=2));return 0 if result['release']=='GO' else 1


if __name__=='__main__':raise SystemExit(main())
