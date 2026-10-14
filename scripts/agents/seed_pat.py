"""Private, account-bound Key Vault setup gate. No secret value crosses Terraform."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import time


def seed(config: dict, *, run=subprocess.run, clock=time.monotonic, sleep=time.sleep) -> bool:
    try:
        account_result = run(["az", "account", "show", "--output", "json"], capture_output=True,
                             text=True, check=False, timeout=30)
        account = json.loads(account_result.stdout) if account_result.returncode == 0 else {}
        user = account.get("user", {})
        denied = config["forbidden_domains"]
        if isinstance(denied, str):
            denied = json.loads(denied)
        if (not isinstance(denied, list) or not denied
                or any(not isinstance(d, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+", d) for d in denied)):
            raise ValueError("missing private account boundary")
        if (account.get("id") != config["subscription"] or account.get("tenantId") != config["tenant"]
                or account.get("state") != "Enabled" or user.get("type") != "user"
                or user.get("name", "").casefold() != config["owner"].casefold()
                or any(config["owner"].casefold().endswith("@" + d.casefold()) for d in denied)):
            print("pat_ready=false category=account", flush=True)
            return False
        if (not re.fullmatch(r"[a-zA-Z0-9-]{3,24}", config["vault_name"])
                or not re.fullmatch(r"[a-z0-9-]{1,24}", config["session"])
                or not re.fullmatch(r"[0-9a-f]{40}", config["source"])):
            raise ValueError("invalid binding")
        receipt = Path(config["receipt"])
        binding = {key: config[key] for key in ("vault_name", "subscription", "tenant", "owner", "session", "source")}
        binding["forbidden_domains"] = denied
        if receipt.exists():
            if json.loads(receipt.read_text(encoding="utf-8")) != {**binding, "pat_ready": True}:
                raise ValueError("receipt binding")
            print("pat_ready=true category=existing_receipt", flush=True)
            return True
        pat = Path(config["pat_file"])
        value = pat.read_text(encoding="utf-8")
        if not value or value != value.strip() or any(c.isspace() for c in value):
            raise ValueError("PAT format")
        value = ""  # The Azure CLI reads the private file itself; never pass value in argv/env.
        deadline = clock() + 120
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            result = run(["az", "keyvault", "secret", "set", "--vault-name", config["vault_name"],
                          "--name", "azp-token", "--file", str(pat), "--encoding", "utf-8",
                          "--subscription", config["subscription"], "--output", "none", "--only-show-errors"],
                         capture_output=True, text=True, check=False, timeout=min(30, remaining))
            if result.returncode == 0:
                receipt.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents silently replacing another session's receipt.
                with receipt.open("x", encoding="utf-8") as stream:
                    json.dump({**binding, "pat_ready": True}, stream)
                print("pat_ready=true category=uploaded", flush=True)
                return True
            sleep(min(5, max(0, deadline - clock())))
        print("pat_ready=false category=readiness_timeout", flush=True)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("pat_ready=false category=input_or_command", flush=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("vault-name", "subscription", "tenant", "owner", "pat-file", "receipt", "session", "source", "forbidden-domains"):
        parser.add_argument("--" + option, default=os.environ.get("P7_" + option.upper().replace("-", "_")))
    args = vars(parser.parse_args())
    if any(value is None for value in args.values()):
        print("pat_ready=false category=missing_input")
        return 2
    return 0 if seed(args) else 1


if __name__ == "__main__":
    raise SystemExit(main())
