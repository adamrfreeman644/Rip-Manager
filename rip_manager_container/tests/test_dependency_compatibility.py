import subprocess
import sys


def test_framework_testclient_import_and_lifespan_are_deprecation_free():
    # Fresh interpreter catches import-time warnings even if another test already
    # imported the framework. Warnings become failures; none are hidden.
    result=subprocess.run([sys.executable,'-W','error','-c', '''
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.testclient import TestClient
seen=[]
@asynccontextmanager
async def lifespan(app):
    seen.append('start')
    yield
    seen.append('stop')
app=FastAPI(lifespan=lifespan)
@app.get('/health')
def health(): return {'ok':True}
with TestClient(app) as client:
    assert client.get('/health').json()=={'ok':True}
assert seen==['start','stop']
'''],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
