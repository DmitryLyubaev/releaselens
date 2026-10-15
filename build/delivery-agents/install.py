"""Build-time verification of reviewed locks and the exact Terraform distribution."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile


def verify_inputs(root: Path) -> dict:
    versions = json.loads((root / "versions.json").read_text(encoding="utf-8"))
    for name in ("requirements.lock","cli-requirements.lock"):
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != versions[name.replace(".lock","_sha256")]:
            raise ValueError("dependency lock digest mismatch")
    if versions["terraform_version"] != "1.15.8" or versions["azure_cli_version"] != "2.81.0":
        raise ValueError("unreviewed tool version")
    return versions


def install_terraform(versions: dict, output: Path, *, opener=urllib.request.urlopen) -> None:
    version = versions["terraform_version"]
    if version != "1.15.8":
        raise ValueError("unreviewed Terraform version")
    url = f"https://releases.hashicorp.com/terraform/{version}/terraform_{version}_linux_amd64.zip"
    with opener(url,timeout=60) as response:
        data = response.read(100*1024*1024+1)
    if len(data) > 100*1024*1024 or hashlib.sha256(data).hexdigest() != versions["terraform_sha256"]:
        raise ValueError("Terraform distribution digest mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        member = archive.getinfo("terraform")
        if member.file_size > 128*1024*1024:
            raise ValueError("unexpected executable size")
        with output.open("xb") as out:
            out.write(archive.read(member))
    output.chmod(0o755)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    parser.add_argument("--terraform-out",type=Path)
    args = parser.parse_args()
    versions = verify_inputs(args.root)
    if args.terraform_out is not None:
        install_terraform(versions,args.terraform_out)
