import asyncio,json
from test_reliability import fresh_database
import nodes,updates
from config import BUNDLED_NODE_VERSION


def setup(tmp_path,monkeypatch):
    db=fresh_database(tmp_path,monkeypatch)
    monkeypatch.setattr(updates,"UPDATE_DIR",tmp_path/"updates")
    with db.write() as con:
        for id in ('one','two','simulator','disabled'):
            con.execute("INSERT INTO nodes(id,name,url,enabled) VALUES (?,?,?,?)",(id,id,"http://"+id,int(id!='disabled')))
    return db


def test_every_real_node_must_report_expected_running_api(tmp_path,monkeypatch):
    db=setup(tmp_path,monkeypatch)
    calls=[]
    async def get(url,headers):
        calls.append(url)
        return {"version":BUNDLED_NODE_VERSION if 'one' in url else '0.2.1'}
    monkeypatch.setattr(nodes,"get_json",get)
    result=asyncio.run(nodes.verify_api_deployments())
    assert not result["all_verified"]
    assert result["verified_count"]==1 and result["expected_count"]==2
    assert len(calls)==2
    assert next(x for x in result["nodes"] if x['id']=='two')["deployment_state"]=='version_mismatch'
    assert db.get_setting_json("node_deployments",{})["expected_version"]==BUNDLED_NODE_VERSION


def test_request_stays_pending_until_all_nodes_are_verified(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    updates.write_request("install-nodes.request.json",{"pending":True,"version":BUNDLED_NODE_VERSION,"request_id":"r"})
    async def unreachable(url,headers):
        raise OSError("offline")
    monkeypatch.setattr(nodes,"get_json",unreachable)
    result=asyncio.run(nodes.verify_api_deployments())
    assert not result["all_verified"]
    assert updates.read_json("install-nodes.request.json")["pending"]
    async def correct(url,headers):
        return {"version":BUNDLED_NODE_VERSION}
    monkeypatch.setattr(nodes,"get_json",correct)
    assert asyncio.run(nodes.verify_api_deployments())["all_verified"]
    assert updates.read_json("install-nodes.request.json") is None
    assert updates.read_json("State/github-node-update.json")["state"]=='success'


def test_install_callback_cannot_claim_success_for_unreachable_nodes(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.updates import router
    async def offline(url,headers):
        raise OSError('offline')
    monkeypatch.setattr(nodes,"get_json",offline)
    app=FastAPI();app.include_router(router)
    result=TestClient(app).post('/updates/node-installed',json={'version':BUNDLED_NODE_VERSION}).json()
    assert result['report_received'] and not result['ok']
    assert updates.read_json('State/github-node-update.json')['state']=='verification_pending'


def test_real_node_reporting_simulator_cannot_bypass_deployment_check(tmp_path,monkeypatch):
    setup(tmp_path,monkeypatch)
    async def get(url,headers):
        return {'version':BUNDLED_NODE_VERSION,'simulator':'two' in url}
    monkeypatch.setattr(nodes,'get_json',get)
    result=asyncio.run(nodes.verify_api_deployments())
    assert result['expected_count']==2
    assert not result['all_verified']
    wrong=next(x for x in result['nodes'] if x['id']=='two')
    assert not wrong['simulator']
    assert wrong['deployment_state']=='unexpected_simulator'
