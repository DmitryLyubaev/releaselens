"""Private Blob transport, canonical state reads and atomic no-replay receipts."""
import json
import re

import httpx

from app.delivery_search.contracts import DeliveryRefused, RequestRefused, canonical

# The final twelve shared slots are exclusively for physical index observations.
METADATA_OPERATIONS = 108
METADATA_TOTAL = 120


class BlobStore:
    def __init__(self,host: str,tokens,client: httpx.Client):
        if not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net",host):
            raise DeliveryRefused("target")
        self.host,self.tokens,self.client = host,tokens,client
        self._budget_next = {}

    def _url(self,container: str,key: str) -> str:
        if container not in ("inputs","evidence","test-state","prod-state") or not re.fullmatch(r"[A-Za-z0-9/._-]+",key) or any(p in (".","..","") for p in key.split("/")):
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
        check = "metadata-budget" if key.startswith("budgets/metadata/") else "blob-create"
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

    def reserve_budget(self,name: str,maximum: int,*,start: int=0) -> str:
        if (not re.fullmatch(r"[a-z-]{1,32}",name) or type(maximum) is not int
                or type(start) is not int or not 0<=start<maximum<=200):
            raise DeliveryRefused("authority")
        if name=="metadata":
            if maximum>METADATA_TOTAL or (start>=METADATA_OPERATIONS
                    and (start!=METADATA_OPERATIONS or maximum!=METADATA_TOTAL)):
                raise DeliveryRefused("authority")
            if start<METADATA_OPERATIONS:
                maximum=min(maximum,METADATA_OPERATIONS)
        cursor=(name,start,maximum)
        for i in range(self._budget_next.get(cursor,start),maximum):
            key=f"budgets/{name}/{i:03}.json"
            try:
                self.put_json(key,{"slot":i,"budget":name})
                self._budget_next[cursor]=i+1
                return key
            except DeliveryRefused as exc:
                if exc.code!="reserved": raise
                self._budget_next[cursor]=i+1
        raise DeliveryRefused("reserved")

    def state(self,target: str) -> dict | None:
        if target not in ("test","demo-prod"): raise DeliveryRefused("target")
        container = "test-state" if target == "test" else "prod-state"
        try:
            response = self.client.get(self._url(container,"index.tfstate"),headers={
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
