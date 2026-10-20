"""Private Blob transport, canonical state reads, no-replay receipts and the current-release pointer."""
import json
import re

import httpx

from app.delivery_search.contracts import DeliveryRefused, RequestRefused, canonical

POINTER_KEY = "current.json"
STATES = {"test":("test-state","index.tfstate"),"demo-prod":("prod-state","index.tfstate"),
          "service":("service-state","service.tfstate")}


def run_key(release_run_id: int, key: str) -> str:
    """Every artefact of one release run lives under its own evidence prefix."""
    return f"runs/{release_run_id}/{key}"


class BlobStore:
    def __init__(self,host: str,tokens,client: httpx.Client):
        if not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net",host):
            raise DeliveryRefused("target")
        self.host,self.tokens,self.client = host,tokens,client

    def _url(self,container: str,key: str) -> str:
        if container not in ("inputs","evidence","test-state","prod-state","service-state") or not re.fullmatch(r"[A-Za-z0-9/._-]+",key) or any(p in (".","..","") for p in key.split("/")):
            raise DeliveryRefused("target")
        return f"https://{self.host}/{container}/{key}"

    def get(self,container: str,key: str,*,maximum: int=4*1024*1024) -> bytes:
        try:
            with self.client.stream("GET",self._url(container,key),headers={
                "Authorization":f"Bearer {self.tokens.token()}","x-ms-version":"2023-11-03"},
                follow_redirects=False,timeout=20) as response:
                if response.status_code != 200:
                    raise DeliveryRefused("request")
                data = bytearray()
                for block in response.iter_bytes(65536):
                    data.extend(block)
                    if len(data) > maximum: raise DeliveryRefused("request")
                return bytes(data)
        except httpx.HTTPError:
            raise DeliveryRefused("request") from None

    def json(self,container: str,key: str) -> dict:
        try:
            data = json.loads(self.get(container,key))
            if not isinstance(data,dict): raise DeliveryRefused("request")
            return data
        except ValueError:
            raise DeliveryRefused("request") from None

    def put_once(self,container: str,key: str,data: bytes) -> None:
        if container != "evidence" or len(data) > 16*1024*1024:
            raise DeliveryRefused("authority")
        check = "blob-create"
        try:
            response = self.client.put(self._url(container,key),content=data,headers={
                "Authorization":f"Bearer {self.tokens.token()}","x-ms-version":"2023-11-03",
                "x-ms-blob-type":"BlockBlob","If-None-Match":"*"},follow_redirects=False,timeout=30)
            if response.status_code == 412 or (response.status_code == 409
                    and response.headers.get("x-ms-error-code") == "BlobAlreadyExists"):
                raise DeliveryRefused("reserved")
            if response.status_code != 201: raise RequestRefused(check+"-http",response.status_code)
        except httpx.HTTPError:
            raise RequestRefused(check+"-transport") from None

    def put_json(self,key: str,value: dict) -> None:
        self.put_once("evidence",key,canonical(value))

    def write_pointer(self,value: dict) -> None:
        """The one overwriting write: the current-release pointer, with no If-None-Match."""
        try:
            response = self.client.put(self._url("evidence",POINTER_KEY),content=canonical(value),headers={
                "Authorization":f"Bearer {self.tokens.token()}","x-ms-version":"2023-11-03",
                "x-ms-blob-type":"BlockBlob"},follow_redirects=False,timeout=30)
            if response.status_code != 201: raise RequestRefused("pointer-http",response.status_code)
        except httpx.HTTPError:
            raise RequestRefused("pointer-transport") from None

    def read_pointer(self) -> dict:
        return self.json("evidence",POINTER_KEY)

    def state(self,target: str) -> dict | None:
        if target not in STATES: raise DeliveryRefused("target")
        container,key = STATES[target]
        try:
            response = self.client.get(self._url(container,key),headers={
                "Authorization":f"Bearer {self.tokens.token()}","x-ms-version":"2023-11-03"},
                follow_redirects=False,timeout=20)
            if response.status_code == 404 and response.headers.get("x-ms-error-code") == "BlobNotFound":
                return None
            if response.status_code != 200 or len(response.content) > 4*1024*1024:
                raise DeliveryRefused("plan")
            if not response.content and response.headers.get("content-length") == "0":
                return None  # Authenticated empty initial backend placeholder, not a failed read.
            value = response.json()
            if not isinstance(value,dict) or "lineage" not in value or "serial" not in value:
                raise DeliveryRefused("plan")
            return value
        except (httpx.HTTPError,ValueError):
            raise DeliveryRefused("plan") from None
