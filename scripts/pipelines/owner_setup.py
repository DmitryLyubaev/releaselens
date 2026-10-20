"""The owner's one-time setup helpers. Neither prints a value: only key names, a fixed word or a fixed code.

variables      writes the ReleaseLens-P8-Delivery group's values, from the platform stack's
               `terraform output -json` and the three connections, to a file git ignores.
verify-upload  compares a downloaded copy of the release archive with the local archive's SHA-256.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from app.delivery_search.contracts import DeliveryRefused
from .settings import DEVOPS_ORIGIN,ROLES,check_principals,load_settings

ROOT = Path(__file__).resolve().parents[2]
KEYS = ("P8_TENANT_ID","P8_SUBSCRIPTION_ID","P8_STORAGE_ACCOUNT","P8_SEARCH_GROUP","P8_SEARCH_SERVICE",
        "P8_RELEASE_DIGEST","P8_PRINCIPALS_JSON")


def allowed_output(path: Path,root: Path | None = None) -> bool:
    """Outside the repository, or in a directory git ignores; never a tracked directory."""
    root = (root or ROOT).resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return True
    if relative.parent == Path("."): return False
    result = subprocess.run(["git","check-ignore","-q",relative.parent.as_posix()+"/"],cwd=root,
        capture_output=True,timeout=10)
    return result.returncode == 0


def variables(outputs: dict,connections: dict,digest: str) -> dict:
    """The group's values, checked by the same settings loader every stage uses."""
    try:
        values = {k:v["value"] for k,v in outputs.items()}
        prefix = connections["oidc_path_prefix"]
        principals = {role:{"client_id":values["principals"][role]["client_id"],
            "object_id":values["principals"][role]["object_id"],"issuer":connections[role]["issuer"],
            "subject":connections[role]["subject"],"service_connection_id":connections[role]["service_connection_id"],
            "oidc_request_origin":DEVOPS_ORIGIN,"oidc_path_prefix":prefix} for role in ROLES}
        group = dict(zip(KEYS,(values["tenant_id"],values["subscription_id"],values["storage_account_name"],
            values["search_group"],values["search_service_name"],digest,
            json.dumps(principals,separators=(",",":"),sort_keys=True))))
    except (KeyError,TypeError,AttributeError):
        raise DeliveryRefused("settings") from None
    # The same checks the pipeline applies: every stage's settings loader, then the identity preflight's.
    check_principals(load_settings(group).principals,"settings")
    return group


def write_variables(args) -> None:
    if not allowed_output(args.out,ROOT): raise DeliveryRefused("target")
    try:
        outputs = json.loads(args.platform_outputs.read_text(encoding="utf-8"))
        connections = json.loads(args.connections.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        raise DeliveryRefused("settings") from None
    if not isinstance(outputs,dict) or not isinstance(connections,dict): raise DeliveryRefused("settings")
    group = variables(outputs,connections,args.release_digest)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    try:
        with args.out.open("x",encoding="utf-8") as out: json.dump(group,out,indent=2)
    except FileExistsError:
        raise DeliveryRefused("reserved") from None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command",required=True)
    group = commands.add_parser("variables")
    group.add_argument("--platform-outputs",type=Path,required=True)
    group.add_argument("--connections",type=Path,required=True)
    group.add_argument("--release-digest",required=True)
    group.add_argument("--out",type=Path,required=True)
    upload = commands.add_parser("verify-upload")
    upload.add_argument("--archive",type=Path,required=True)
    upload.add_argument("--digest",required=True)
    args = parser.parse_args(argv)
    if args.command == "verify-upload":
        expected = args.digest.lower()
        try:
            same = re.fullmatch(r"[0-9a-f]{64}",expected) is not None and sha256(args.archive) == expected
        except OSError:
            same = False
        print("match" if same else "mismatch")
        return 0 if same else 1
    try:
        write_variables(args)
    except Exception as exc:
        print("refused: "+(exc.code if isinstance(exc,DeliveryRefused) else "internal"))
        return 2
    print("keys: "+" ".join(KEYS))
    print("written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
