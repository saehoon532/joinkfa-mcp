import time
import httpx
import pytest
from kfa_lookup import KfaClient, Session, LookupError, MENU, Job

@pytest.mark.asyncio
async def test_connection_reset_retries_without_exposing_secrets(monkeypatch):
    async def no_sleep(seconds):
        pass
    monkeypatch.setattr('kfa_lookup.asyncio.sleep', no_sleep)
    calls = []
    def handler(req):
        calls.append(req)
        raise httpx.ReadError('sensitive cookie must never appear')
    s = Session('owner','id',time.time()+300,{'JSESSIONID':'test'})
    async with KfaClient(s,httpx.MockTransport(handler)) as client:
        with pytest.raises(LookupError,match='^UPSTREAM_CONNECTION_RESET$'):
            await client.json(MENU,{'SYS_CD':'CETUS'})
    assert len(calls) == 3

@pytest.mark.asyncio
@pytest.mark.parametrize('status,code', [(403,'AUTH_REQUIRED_OR_ACCESS_DENIED'), (429,'UPSTREAM_RATE_LIMITED')])
async def test_no_retry_access_denial_or_rate_limit(status,code):
    calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(status)
    s=Session('owner','id',time.time()+300,{'JSESSIONID':'test'})
    async with KfaClient(s,httpx.MockTransport(handler)) as client:
        with pytest.raises(LookupError,match=code):
            await client.json(MENU,{})
    assert len(calls)==1

@pytest.mark.asyncio
async def test_resume_same_queue_after_network_failure():
    class Client:
        failed=True
        async def authenticate(self):
            if self.failed:
                raise LookupError('UPSTREAM_CONNECTION_RESET')
        async def json(self,*args):
            return {'matchList':[],'totalCount':0}
    client=Client()
    job=Job('name',2026)
    result=await job.advance(client)
    assert result['retryable'] and result['count'] is None
    assert len(job.queue)==1
    client.failed=False
    result=await job.advance(client)
    assert result['complete'] and result['count']==0
