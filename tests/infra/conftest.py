"""Shared fixtures for the infra test harness."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root, resolved from this file's location."""
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_store(repo_root,monkeypatch):
    monkeypatch.syspath_prepend(str(repo_root/"eval"))
    contracts=importlib.import_module("app.delivery_search.contracts")
    class FakeStore:
        """Dict-backed BlobStore stand-in: create-only evidence, one overwritable pointer, read-only states."""
        def __init__(self): self.blobs,self.pointer_writes,self.states={},[],{}
        def get(self,container,key,**kw):
            if (container,key) not in self.blobs: raise contracts.DeliveryRefused("request")
            return self.blobs[(container,key)]
        def json(self,container,key): return json.loads(self.get(container,key))
        def put_once(self,container,key,data):
            if container != "evidence": raise contracts.DeliveryRefused("authority")
            if (container,key) in self.blobs: raise contracts.DeliveryRefused("reserved")
            self.blobs[(container,key)]=bytes(data)
        def put_json(self,key,value): self.put_once("evidence",key,contracts.canonical(value))
        def write_pointer(self,value):
            self.pointer_writes.append(value)
            self.blobs[("evidence","current.json")]=contracts.canonical(value)
        def read_pointer(self): return self.json("evidence","current.json")
        def state(self,target):
            if target not in ("test","demo-prod","service"): raise contracts.DeliveryRefused("target")
            return self.states.get(target)
        def evidence(self,key): return json.loads(self.blobs[("evidence",key)])
    return FakeStore()
