"""Fail-closed CLI. Only counts and stable status codes go to stdout."""
import argparse
from contextlib import suppress
from datetime import datetime, timezone
import json
from pathlib import Path
import traceback

import httpx

from .bundle import read_json, verify_bundle
from .client import upload_bundle, wait_for_count
from .contracts import DeliveryRefused, SearchTarget, exclusive_json
from .gate import run_gate, write_gate
from ..retrieval.azure_auth import TokenSource


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("validate","load","gate"))
    for flag in ("authority","bundle","digest","target","output"):
        parser.add_argument("--"+flag,required=True)
    parser.add_argument("--negative",action="store_true")
    args = parser.parse_args(argv)
    out = Path(args.output)
    try:
        authority = read_json(Path(args.authority))
        if (not authority.get("approval_ref") or authority["release_digest"] != args.digest
            or args.target not in ("test","demo-prod")
            or datetime.now(timezone.utc) >= datetime.fromisoformat(authority["queue_cutoff_at"])):
            raise DeliveryRefused("authority")
        bundle = verify_bundle(Path(args.bundle), args.digest, out / "verified")
        if bundle.manifest.source_sha != authority["source_sha"]:
            raise DeliveryRefused("authority")
        if args.operation == "validate":
            print("status=pass code=validated")
            return 0
        host = authority["search_host"]
        target = SearchTarget("https://"+host, "p8-"+args.target, host)
        if args.negative and (args.operation != "gate" or args.target != "test"):
            raise DeliveryRefused("target")
        tokens = TokenSource("https://search.azure.com/.default", authority["tenant_id"])
        with httpx.Client(timeout=20,follow_redirects=False) as client:
            if args.operation == "load":
                upload_bundle(bundle,target,tokens,client,reservation=out / "upload.intent.json")
                print("status=pass code=loaded count=41825")
                return 0
            exclusive_json(out / "gate.intent.json", {"digest":args.digest,"target":args.target,
                                                      "negative":args.negative})
            result,rows = run_gate(bundle,target,read_json(bundle.root / (
                "negative.json" if args.negative else "s2.json")),tokens,client)
            write_gate(result,rows,out / "evidence")
            print(f"status={'pass' if result.passed else 'fail'} code=gate evaluated={result.evaluated} correct={result.correct}")
            return 0 if result.passed else 1
    except Exception as exc:
        # Retain the original failure; logging an already-refused replay must not raise
        # another exception that bypasses the sanitized CLI boundary.
        with suppress(OSError):
            out.mkdir(parents=True,exist_ok=True)
            with (out / "failure-private.txt").open("x",encoding="utf-8") as file:
                file.write(traceback.format_exc())
        code = exc.code if isinstance(exc,DeliveryRefused) else "internal"
        print(f"status=refused code={code}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
