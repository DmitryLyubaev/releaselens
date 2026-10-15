"""Allowlisted receipts from actual container observations and pushed image metadata."""
import argparse
import json
import os
from pathlib import Path
import re
import sys


def create_receipt(source,config,cli,terraform,agent,python):
    versions={"terraform":terraform["terraform_version"],"azure_cli":cli["azure-cli"],
        "agent":agent.strip(),"python":".".join(str(n) for n in python[:2])}
    if (not re.fullmatch(r"[0-9a-f]{40}",source) or not re.fullmatch(r"sha256:[0-9a-f]{64}",config)
        or versions!={"terraform":"1.15.8","azure_cli":"2.81.0","agent":"5.280.0","python":"3.12"}):
        raise ValueError("unexpected source/image/toolchain")
    return {"kind":"container-toolchain","source_sha":source,"config_digest":config,"versions":versions}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--binding",action="store_true")
    args=parser.parse_args()
    if args.binding:
        metadata=json.load(sys.stdin)[0]
        source=metadata["Config"]["Labels"]["org.opencontainers.image.revision"]
        digests={value.split("@",1)[1] for value in metadata["RepoDigests"]}
        if len(digests)!=1 or not re.fullmatch(r"[0-9a-f]{40}",source): raise ValueError("ambiguous image")
        record={"source_sha":source,"config_digest":metadata["Id"],"digest":digests.pop()}
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}",record[k]) for k in ("config_digest","digest")):
            raise ValueError("unexpected image digest")
        print("P8_IMAGE_BINDING="+json.dumps(record,sort_keys=True,separators=(",",":")))
    else:
        record=create_receipt(os.environ["P8_SMOKE_SOURCE"],os.environ["P8_SMOKE_CONFIG_DIGEST"],
            json.loads(Path("/tmp/cli-version.json").read_bytes()),json.loads(Path("/tmp/tf-version.json").read_bytes()),
            Path("/tmp/agent-version.txt").read_text(),sys.version_info)
        print("P8_TOOLCHAIN_RECEIPT="+json.dumps(record,sort_keys=True,separators=(",",":")))


if __name__=="__main__": main()
