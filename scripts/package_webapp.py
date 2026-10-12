"""Deterministic publish-output ZIP. Never open settings or identity files."""
from pathlib import Path, PurePosixPath
import hashlib
import sys
import zipfile

REQUIRED = {"ReleaseLens.Web.dll", "ReleaseLens.Web.deps.json", "ReleaseLens.Web.runtimeconfig.json"}


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    if "\\" in name or ":" in name or path.is_absolute() or any(ord(c) < 32 for c in name): return False
    if not name or str(path) != name.rstrip("/") or any(part.startswith(".") for part in path.parts): return False
    lower = path.name.casefold()
    return not (lower.startswith("appsettings") or lower.endswith((".tfvars", ".tfstate", ".pdb")) or "credential" in lower or lower in {"terraform.tfvars.json", "local.settings.json"})


def create_zip(publish_dir: Path, destination: Path) -> str:
    files = []
    for path in sorted(publish_dir.rglob("*")):
        name = path.relative_to(publish_dir).as_posix()
        if path.is_symlink(): raise ValueError("package_refused")
        if path.is_file() and path.suffix.casefold() == ".pdb": continue
        if path.is_symlink() or not safe_name(name): raise ValueError("package_refused")
        if path.is_file(): files.append((path, name))
    if not REQUIRED <= {name for _, name in files}: raise ValueError("package_refused")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path, name in files:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    return hashlib.sha256(destination.read_bytes()).hexdigest()


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3: raise ValueError()
        print("package_sha256=" + create_zip(Path(sys.argv[1]), Path(sys.argv[2])))
    except Exception:
        print("status=package_refused")
        sys.exit(1)
