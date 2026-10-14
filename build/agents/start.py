"""One-job Azure Pipelines agent. No cloud discovery, token logging or restart loop."""
import os
from pathlib import Path
import re
import signal
import subprocess
import uuid

CLEANUP_TIMEOUT = 30


def agent_name(session: str, mode: str, nonce: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", session):
        raise ValueError("invalid session")
    if mode not in {"worker", "placeholder"} or not re.fullmatch(r"[a-z0-9]{1,16}", nonce):
        raise ValueError("invalid mode or nonce")
    return f"p7-{session}-placeholder" if mode == "placeholder" else f"p7-{session}-worker-{nonce}"


class Shutdown(Exception):
    def __init__(self, signum):
        self.signum = signum


def run_agent(agent_dir: Path, environment: dict[str, str], nonce: str) -> int:
    env = dict(environment)
    token = env.pop("AZP_TOKEN", "")
    try:
        mode = env["P7_MODE"]
        name = agent_name(env["P7_SESSION"], mode, nonce)
        if (not token.strip() or env.get("P7Agent") != "ReleaseLens"
                or not re.fullmatch(r"https://dev\.azure\.com/[A-Za-z0-9_-]+/?", env.get("AZP_URL", ""))
                or not env.get("AZP_POOL", "").strip()):
            raise ValueError("invalid settings")
    except (KeyError, ValueError):
        print("agent_phase=input status=refused", flush=True)
        return 2

    ignored = set(env.get("VSO_AGENT_IGNORE", "").split(","))
    ignored.update({"AZP_TOKEN", "VSTS_AGENT_INPUT_TOKEN", "AZP_URL"})
    env["VSO_AGENT_IGNORE"] = ",".join(sorted(ignored - {""}))
    # Only the configuration child receives the PAT. Listener gets its generated
    # agent credential, not the registration token or configuration input variables.
    config_env = {**env, "VSTS_AGENT_INPUT_TOKEN": token, "VSTS_AGENT_INPUT_AGENT": name,
                  "VSTS_AGENT_INPUT_URL": env["AZP_URL"], "VSTS_AGENT_INPUT_POOL": env["AZP_POOL"],
                  "VSTS_AGENT_INPUT_AUTH": "PAT", "VSTS_AGENT_INPUT_WORK": "_work"}
    child = None
    registered = False
    result = 1
    old_handlers = {}

    def stop(signum, _frame):
        if child is not None and child.poll() is None:
            if os.name == "posix":
                os.killpg(child.pid, signum)
            else:
                child.send_signal(signum)
            try:
                child.wait(timeout=CLEANUP_TIMEOUT)
            except (subprocess.TimeoutExpired, Shutdown):
                if os.name == "posix":
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
        raise Shutdown(signum)

    def execute(argv, child_env, timeout=None):
        nonlocal child
        child = subprocess.Popen(argv, cwd=agent_dir, env=child_env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=os.name == "posix")
        try:
            return child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
            child.wait(timeout=5)
            return 124

    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_handlers[sig] = signal.signal(sig, stop)
        print("agent_phase=register", flush=True)
        result = execute(["bash", "./config.sh", "--unattended", "--disableupdate", "--acceptTeeEula"],
                         config_env, timeout=120)
        if result == 0:
            registered = True
            if mode == "worker":
                print("agent_phase=listen mode=once", flush=True)
                result = execute(["bash", "./run.sh", "--once"], env)
    except Shutdown as error:
        result = 128 + error.signum
    except (OSError, subprocess.SubprocessError):
        result = 1
    finally:
        # Ignore repeated signals during the bounded unregister, not indefinitely.
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        if registered and mode == "worker":
            try:
                cleanup = execute(["bash", "./config.sh", "remove", "--unattended"], config_env,
                                  timeout=CLEANUP_TIMEOUT)
                print(f"agent_phase=unregister status={'pass' if cleanup == 0 else 'failed'}", flush=True)
                if result == 0 and cleanup != 0:
                    result = 1
            except (OSError, subprocess.SubprocessError):
                result = result or 1
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        config_env.clear()
        token = ""
    print(f"agent_phase=exit code={result if result >= 0 else 128 - result}", flush=True)
    return result if result >= 0 else 128 - result


def main() -> int:
    environment = dict(os.environ)
    os.environ.pop("AZP_TOKEN", None)
    return run_agent(Path("/opt/azp"), environment, uuid.uuid4().hex[:12])


if __name__ == "__main__":
    raise SystemExit(main())
