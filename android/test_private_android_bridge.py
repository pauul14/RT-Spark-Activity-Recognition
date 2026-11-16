"""Direct FastAPI route/MockTransport tests: no API, hardware or socket."""
import json
import unittest
import httpx
from starlette.requests import Request
from private_android_bridge import DEVICE, MAX_BODY, create_app

TOKEN = 'synthetic-software-test-only-0123456789'

class TestClient:
    """Call the actual route directly without an asyncio socketpair on Windows.

    This tests route/forwarding logic, not an ASGI server or a TLS handshake.
    """
    def __init__(self, app, *, base_url):
        self.app=app;self.base_url=base_url
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self,*args): self.close()
    def request(self,method,path,params=None,headers=None):
        url=httpx.URL(self.base_url+path)
        if params is not None:url=url.copy_merge_params(params)
        request_headers=httpx.Headers({'Host':url.netloc.decode(),**(headers or {})})
        scope={'type':'http','method':method,'scheme':url.scheme,'path':url.path,
               'query_string':url.query,'headers':[(key.lower(),value) for key,value in request_headers.raw]}
        response=self.app.routes[0].endpoint(path=url.path.lstrip('/'),request=Request(scope))
        return httpx.Response(response.status_code,content=response.body,headers=dict(response.headers))
    def get(self,path,**kwargs): return self.request('GET',path,**kwargs)
    def post(self,path,**kwargs): return self.request('POST',path,**kwargs)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.status = 200
        self.headers = {'content-type':'application/json'}
        self.body = b'{"state":"UNVALIDATED","hardware_validated":false,"server_timestamp":"2026-11-05T00:00:00Z"}'
        def handler(request):
            self.requests.append(request)
            return httpx.Response(self.status, content=self.body, headers=self.headers)
        self.upstream = httpx.Client(base_url='http://127.0.0.1:18000',
                                    transport=httpx.MockTransport(handler), follow_redirects=False)
        self.addCleanup(self.upstream.close)
        self.client = TestClient(create_app(self.upstream), base_url='https://10.0.2.2:8443')
        self.addCleanup(self.client.close)
    def get(self, path='current', **kwargs):
        return self.client.get('/api/activities/'+path, params={'device_id':DEVICE},
                               headers={'Authorization':'Bearer '+TOKEN}, **kwargs)
    def test_exact_bytes_validation_flags_timestamps_and_token_preserved(self):
        reply = self.get()
        self.assertEqual(reply.status_code,200)
        self.assertEqual(reply.content,self.body)
        self.assertEqual(reply.headers['cache-control'],'no-store')
        sent = self.requests[0]
        self.assertEqual(sent.headers['authorization'],'Bearer '+TOKEN)
        self.assertEqual(str(sent.url),'http://127.0.0.1:18000/api/activities/current?device_id='+DEVICE)
        self.assertNotIn('cookie',sent.headers)
    def test_missing_or_malformed_auth_never_reaches_upstream(self):
        for auth in ('', 'Bearer short', 'Basic '+TOKEN, 'Bearer '+TOKEN+' '):
            reply=self.client.get('/api/activities/current',params={'device_id':DEVICE},headers={'Authorization':auth})
            self.assertEqual(reply.status_code,401)
        self.assertEqual(self.requests,[])
    def test_ambient_upstream_cookies_are_never_sent(self):
        self.upstream.cookies.set('session','synthetic-cookie-only')
        self.assertEqual(self.get().status_code,200)
        self.assertNotIn('cookie',self.requests[-1].headers)
    def test_redirect_policy_cannot_be_overridden_by_client_defaults(self):
        self.upstream.follow_redirects=True
        self.status=302
        self.headers['location']='https://untrusted.invalid/private'
        self.assertEqual(self.get().status_code,502)
        self.assertEqual(len(self.requests),1)
    def test_host_and_route_and_write_restrictions(self):
        self.assertEqual(self.client.get('/api/activities/current',headers={'Host':'evil.invalid'}).status_code,400)
        self.assertEqual(self.client.get('/docs').status_code,404)
        self.assertEqual(self.client.post('/api/activities/current').status_code,405)
        self.assertEqual(self.requests,[])
    def test_wrong_device_unknown_or_duplicate_query_rejected(self):
        for query in ('device_id=other','device_id='+DEVICE+'&token=hidden',
                      'device_id='+DEVICE+'&device_id='+DEVICE,'device_id='+DEVICE+'&limit=2'):
            reply=self.client.get('/api/activities/current?'+query,headers={'Authorization':'Bearer '+TOKEN})
            self.assertEqual(reply.status_code,400)
        self.assertEqual(self.requests,[])
    def test_history_limit_bounded_and_forwarded(self):
        reply=self.client.get('/api/activities/history',params={'device_id':DEVICE,'limit':20},headers={'Authorization':'Bearer '+TOKEN})
        self.assertEqual(reply.status_code,200)
        self.assertEqual(self.requests[-1].url.params['limit'],'20')
        for value in ('0','101','NaN','1.2','9'*5000):
            self.assertEqual(self.client.get('/api/activities/history',params={'device_id':DEVICE,'limit':value},headers={'Authorization':'Bearer '+TOKEN}).status_code,400)
    def test_auth_failure_is_generic_and_secret_free(self):
        self.status=401;self.body=TOKEN.encode()
        reply=self.get()
        self.assertEqual(reply.status_code,401)
        self.assertNotIn(TOKEN,reply.text)
        self.assertEqual(len(self.requests),1)
    def test_transient_protocol_failure_recovers_once_without_changing_timestamp(self):
        attempts=[]
        def transient(request):
            attempts.append(request)
            if len(attempts)==1:raise httpx.RemoteProtocolError('synthetic disconnect')
            return httpx.Response(200,content=self.body,headers=self.headers)
        with httpx.Client(base_url='http://127.0.0.1:18000',transport=httpx.MockTransport(transient)) as upstream:
            with TestClient(create_app(upstream),base_url='https://10.0.2.2:8443') as client:
                reply=client.get('/api/activities/current',params={'device_id':DEVICE},headers={'Authorization':'Bearer '+TOKEN})
        self.assertEqual(reply.status_code,200);self.assertEqual(reply.content,self.body)
        self.assertEqual(len(attempts),2)
        self.assertTrue(all(r.headers['connection']=='close' for r in attempts))
    def test_redirect_is_not_followed_or_exposed(self):
        self.status=302;self.headers['location']='https://github.com/login?secret='+TOKEN
        reply=self.get()
        self.assertEqual(reply.status_code,502)
        self.assertEqual(len(self.requests),1)
        self.assertNotIn('location',reply.headers)
        self.assertNotIn(TOKEN,reply.text)
    def test_bad_json_html_or_oversize_body_rejected(self):
        for content_type,body in [('text/html',b'<html>login</html>'),('application/json',b'not-json'),
                                  ('application/json',b'{"value":NaN}'),('application/json',b' '*MAX_BODY+b'{}')]:
            self.headers['content-type']=content_type;self.body=body
            self.assertEqual(self.get().status_code,502)
    def test_read_timeout_is_generic_and_retry_is_bounded(self):
        attempts=[]
        def fail(request):
            attempts.append(request)
            raise httpx.ReadTimeout('synthetic error '+TOKEN)
        with httpx.Client(base_url='http://127.0.0.1:18000',transport=httpx.MockTransport(fail)) as upstream:
            with TestClient(create_app(upstream),base_url='https://10.0.2.2:8443') as client:
                reply=client.get('/api/activities/current',params={'device_id':DEVICE},headers={'Authorization':'Bearer '+TOKEN})
        self.assertEqual(reply.status_code,502)
        self.assertEqual(len(attempts),2)
        self.assertNotIn(TOKEN,reply.text)
    def test_total_response_deadline_is_bounded(self):
        times=iter([0,11])
        with TestClient(create_app(self.upstream,clock=lambda:next(times)),base_url='https://10.0.2.2:8443') as client:
            reply=client.get('/api/activities/current',params={'device_id':DEVICE},headers={'Authorization':'Bearer '+TOKEN})
        self.assertEqual(reply.status_code,502)

if __name__=='__main__':
    unittest.main()
