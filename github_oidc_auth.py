from __future__ import annotations

import os
from typing import Any

ISSUER='https://token.actions.githubusercontent.com'
JWKS=ISSUER+'/.well-known/jwks'
AUDIENCE='raio-x-territorial-monitor'
EXPECTED_REPO=os.getenv('RX_GITHUB_REPOSITORY','rodrigo1994336-netizen/raio-x-territorial')

_JWK_CLIENT=None


def _jwt_modules():
    import jwt
    from jwt import PyJWKClient
    return jwt,PyJWKClient


def verify_github_actions_oidc(token:str) -> dict[str,Any]:
    global _JWK_CLIENT
    if not token or len(token)<100:
        raise ValueError('missing_or_short_oidc_token')
    jwt,PyJWKClient=_jwt_modules()
    if _JWK_CLIENT is None:_JWK_CLIENT=PyJWKClient(JWKS,cache_keys=True,lifespan=3600)
    signing_key=_JWK_CLIENT.get_signing_key_from_jwt(token)
    claims=jwt.decode(token,signing_key.key,algorithms=['RS256'],audience=AUDIENCE,issuer=ISSUER,options={'require':['exp','iat','iss','aud','sub']})
    if claims.get('repository')!=EXPECTED_REPO:
        raise ValueError('unexpected_repository')
    if claims.get('event_name') not in {'schedule','workflow_dispatch'}:
        raise ValueError('unexpected_event_name')
    ref=claims.get('ref') or ''
    if ref and ref!='refs/heads/main':
        raise ValueError('unexpected_ref')
    workflow_ref=claims.get('workflow_ref') or ''
    if workflow_ref and '/.github/workflows/monitoring-15min.yml@' not in workflow_ref:
        raise ValueError('unexpected_workflow_ref')
    return claims


def authorization_from_headers(headers) -> dict[str,Any]:
    value=(headers.get('Authorization') or '').strip()
    if not value.lower().startswith('bearer '):
        raise ValueError('missing_bearer')
    token=value.split(' ',1)[1].strip()
    return verify_github_actions_oidc(token)


print('RX_GITHUB_OIDC_AUTH=ready',flush=True)
