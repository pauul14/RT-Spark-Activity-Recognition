"""Prepared local HTTPS bridge to an OWNER-AUTHENTICATED Codespaces tunnel.

Does not create a tunnel, change visibility, supply GitHub credentials, archive
telemetry or generate predictions. Importing this module opens no connection.
Use existing backend dependencies. This is not an Internet-facing proxy.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

DEVICE = 'rtspark-003000423433511539303938'
MAX_BODY = 65536

def failure(status):
    return JSONResponse({'error': 'Private API bridge request failed'}, status_code=status,
                        headers={'Cache-Control': 'no-store'})

def create_app(upstream, *, device=DEVICE, listen_port=8443, clock=time.monotonic):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', device):
        raise ValueError('Exact non-secret device identifier required')
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.forwarded_requests = 0  # Count only; never retain headers or tokens.
    allowed_hosts = {f'10.0.2.2:{listen_port}', f'127.0.0.1:{listen_port}'}

    @app.api_route('/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    def relay(path: str, request: Request):
        if request.headers.get('host') not in allowed_hosts:
            return failure(400)
        if request.method != 'GET':
            return failure(405)
        routes = {'api/activities/current', 'api/activities/history'}
        if path not in routes:
            return failure(404)
        # The unchanged FastAPI upstream validates the application token. Never
        # use or accept a GitHub token/session here. No secret is persisted.
        auth = request.headers.get('authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        if not 32 <= len(token) <= 512 or any(not 33 <= ord(c) <= 126 for c in token):
            return failure(401)
        query = request.query_params
        allowed = {'device_id', 'limit'} if path.endswith('history') else {'device_id'}
        if (set(query) - allowed or any(len(query.getlist(k)) != 1 for k in query)
                or query.get('device_id') != device):
            return failure(400)
        params = {'device_id': device}
        if 'limit' in query:
            if not 1 <= len(query['limit']) <= 3 or not query['limit'].isascii() or not query['limit'].isdigit() or not 1 <= int(query['limit']) <= 100:
                return failure(400)
            params['limit'] = str(int(query['limit']))
        started = clock()
        for attempt in range(2):
          try:
            app.state.forwarded_requests += 1
            # Never forward client cookies/Host, or follow an upstream redirect.
            outgoing = upstream.build_request('GET', '/'+path, params=params,
                                 headers={'Authorization': auth, 'Accept': 'application/json', 'Connection':'close'})
            # httpx's cookie jar can learn Set-Cookie even when client cookies
            # were never forwarded. Strip ambient cookies on every request.
            outgoing.headers.pop('cookie', None)
            with closing(upstream.send(outgoing, stream=True, follow_redirects=False)) as reply:
                if reply.status_code in (401, 403, 404):
                    return failure(reply.status_code)
                if reply.status_code != 200:
                    return failure(502)
                if reply.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                    return failure(502)
                body = bytearray()
                for block in reply.iter_bytes(chunk_size=4096):
                    if len(body)+len(block) > MAX_BODY or clock()-started > 4:
                        return failure(502)
                    body.extend(block)
                if clock()-started > 4:
                    return failure(502)
                json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                # Exact upstream JSON bytes and timestamps: no freshening,
                # reclassification, synthetic fallback or validation flag change.
                return Response(bytes(body), media_type='application/json',
                                headers={'Cache-Control': 'no-store'})
          except (httpx.ReadTimeout,httpx.ConnectError,httpx.ConnectTimeout,httpx.RemoteProtocolError):
            if attempt == 0 and clock()-started < 2:
                continue  # One bounded retry, GET only, same authenticated route.
            return failure(502)
          except (httpx.HTTPError, ValueError, UnicodeError, RecursionError):
            return failure(502)
        return failure(502)
    return app

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--authorized-private-tunnel-demo', action='store_true')
    parser.add_argument('--certificate', type=Path, required=True)
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--device-id', default=DEVICE)
    parser.add_argument('--tunnel-port', type=int, choices=(8000,18000), default=18000)
    args = parser.parse_args()
    if not args.authorized_private_tunnel_demo:
        parser.error('Separate authorization and a verified owner-authenticated tunnel are required')
    if not args.certificate.is_file() or not args.key.is_file():
        parser.error('Verified emulator-trusted TLS certificate and private key files required')
    import uvicorn
    # The ONLY upstream is the owner's local tunnel. HTTP exists on loopback
    # inside that tunnel; the app leg is verified HTTPS. Environment proxies,
    # remote destinations, redirects and environment credentials are disabled.
    with httpx.Client(base_url='http://127.0.0.1:'+str(args.tunnel_port), follow_redirects=False,
                      trust_env=False, timeout=httpx.Timeout(1.5),
                      limits=httpx.Limits(max_connections=2, max_keepalive_connections=0)) as upstream:
        uvicorn.run(create_app(upstream,device=args.device_id), host='127.0.0.1', port=8443,
                    ssl_certfile=str(args.certificate), ssl_keyfile=str(args.key),
                    access_log=False, log_level='warning', proxy_headers=False)

if __name__ == '__main__':
    main()
