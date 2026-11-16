"""Explicit read-only HTTPS API checks; run only after endpoint access is authorized.

API_TOKEN is read from the environment, never from arguments or printed.
No broker connection, port forwarding or account action is performed.
"""
import argparse,json,os,re,sys
from urllib.parse import urlsplit
import httpx

def origin(value):
    parts=urlsplit(value)
    if (parts.scheme!='https' or not parts.hostname or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in ('','/') or parts.port==0):
        raise ValueError('An HTTPS API origin is required')
    return value.rstrip('/')

def check(base,token,device,*,transport=None):
    base=origin(base)
    if not token or len(token)<32 or not token.isascii() or any(c.isspace() for c in token):raise ValueError('Missing or invalid API_TOKEN')
    if not re.fullmatch('[A-Za-z0-9_-]{1,64}',device):raise ValueError('Invalid exact device ID')
    with httpx.Client(base_url=base,headers={'Accept':'application/json'},follow_redirects=False,
                      trust_env=False,timeout=10,transport=transport) as client:
        denied=client.get('/health')
        if denied.status_code!=401:raise ValueError('Unauthenticated access must return 401')
        def get(path,params=None):
            with client.stream('GET',path,params=params,headers={'Authorization':'Bearer '+token}) as response:
                if response.status_code!=200:raise ValueError('Authenticated API request failed')
                if not response.headers.get('content-type','').lower().startswith('application/json'):raise ValueError('Expected JSON')
                body=bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body)>65536:raise ValueError('Response exceeded bound')
                return json.loads(body)
        health=get('/health')
        if health.get('status')!='ok' or health.get('database')!='ok':raise ValueError('Database health failed')
        current=get('/api/activities/current',{'device_id':device})
        history=get('/api/activities/history',{'device_id':device,'limit':25})
        if current.get('device_id')!=device or not isinstance(current.get('hardware_validated'),bool):raise ValueError('Current contract failed')
        if current.get('state') not in ('WAITING','OFFLINE','UNVALIDATED','WALKING','RUNNING','UNKNOWN'):raise ValueError('Invalid state')
        items=history.get('items')
        if not isinstance(items,list) or len(items)>25 or any(row.get('device_id')!=device for row in items):raise ValueError('History contract failed')
        return {'result':'PASS','HTTPS_API':True,'unauthenticated_401':True,'database':'ok',
                'current_state':current['state'],'hardware_validated':current['hardware_validated'],
                'history_rows':len(items),'MQTT_connection_tested':False,'hardware_tested':False}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--url',required=True);parser.add_argument('--device-id',required=True)
    args=parser.parse_args()
    try:result=check(args.url,os.getenv('API_TOKEN'),args.device_id)
    except Exception:
        print('FAIL: HTTPS/auth/API health check failed. No credentials or response bodies were printed.');return 1
    print(json.dumps(result,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
