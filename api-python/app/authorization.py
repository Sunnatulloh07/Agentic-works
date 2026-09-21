"""One authorization boundary shared by modern and legacy JWT consumers."""
import os
from .identity_store import ACCOUNT, AuthenticationError, validate_session


def directory_enabled():
    # Production cannot opt out. Legacy tokens require an explicit test/dev opt-in.
    return not (os.getenv('ENV') in {'test','dev'}
                and os.getenv('ALLOW_INSECURE_DEV','').lower()=='true'
                and os.getenv('IDENTITY_DIRECTORY','').lower()=='false')


def authorize_claims(claims):
    kind=claims.get('token_type')
    if kind=='device': return claims
    if kind not in {'user','account'}: raise AuthenticationError('Invalid token type')
    sid=claims.get('sid')
    if sid:
        m=validate_session(sid,claims['sub'],claims['tenant_id'])
        if (kind=='account') != (m['workspace_id']==ACCOUNT): raise AuthenticationError('Scope mismatch')
        return {**claims,'role':m['role'],'workspace_version':m['version']}
    if directory_enabled() or kind=='account' or str(claims.get('sub','')).startswith('usr_'):
        raise AuthenticationError('Session-bound token required')
    return claims
