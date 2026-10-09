"""Build the Lambda zip directories in infra/build/<bundle> (TD §10.1). ``make package``.

For each bundle:

1. ``uv export`` the service's pinned dependencies (no dev, no editable installs);
2. ``uv pip install`` them for arm64 Linux (manylinux_2_28), Python 3.12, into the bundle directory;
3. add what the wheel leaves out: ``run.sh`` for Web Adapter services, the built
   MCP Apps views (``ui_dist`` is gitignored, so hatchling skips it) and the
   repository's ``db/`` package for the migrator;
4. drop ``__pycache__`` and write a ``.packaged`` marker the CDK app checks for.

Package on Linux (CI) for deploys: Windows does not keep the executable bit that
``run.sh`` needs as a Lambda handler.

    uv run python scripts/package_lambdas.py                 # all bundles
    uv run python scripts/package_lambdas.py --only workers
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "infra" / "build"


@dataclass(frozen=True)
class Bundle:
    name: str
    export: list[str]  # uv export arguments that select the project or package
    run_sh: Path | None = None
    extras: dict[str, Path] = field(default_factory=dict)  # destination (relative) → source
    cwd: Path = ROOT  # exported local paths are relative to the project, so uv runs from there


BUNDLES = {
    b.name: b
    for b in (
        Bundle(
            "mcp_server",
            ["--package", "watchfloor-mcp-server"],
            run_sh=ROOT / "services/mcp_server/run.sh",
            extras={"mcp_server/ui_dist": ROOT / "services/mcp_server/mcp_server/ui_dist"},
        ),
        Bundle(
            "auth_server",
            ["--package", "watchfloor-auth-server"],
            run_sh=ROOT / "services/auth_server/run.sh",
            extras={"db": ROOT / "db"},  # the seeder Lambda (auth_server.seeder) runs db.seed
        ),
        Bundle("workers", ["--package", "watchfloor-workers"], extras={"db": ROOT / "db"}),
        Bundle(
            "harness_api",
            [],  # its own uv project (ADR-011), exported from its directory
            run_sh=ROOT / "services/harness/api/run.sh",
            cwd=ROOT / "services/harness/api",
        ),
    )
}


def run(cmd: list[str], cwd: Path = ROOT) -> None:
    print("  $", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)  # noqa: S603 - uv with arguments this script builds


def strip_native(target: Path) -> None:
    """Drop debug symbols from native extensions (asyncpg, cryptography ship them unstripped).

    Needs a strip that understands aarch64 ELF; CI packages on Linux. Elsewhere this is skipped.
    """
    tool = shutil.which("aarch64-linux-gnu-strip") or (
        shutil.which("strip") if sys.platform == "linux" else None
    )
    if tool is None:
        print("  (no aarch64 strip available: native extensions keep their debug symbols)", flush=True)
        return
    for lib in target.rglob("*.so*"):
        subprocess.run([tool, "--strip-unneeded", str(lib)], check=False, capture_output=True)  # noqa: S603


def build(bundle: Bundle, out: Path) -> int:
    target = out / bundle.name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as tmp:
        reqs = Path(tmp) / "requirements.txt"
        run(
            [
                "uv",
                "export",
                *bundle.export,
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--no-hashes",
                "--no-header",
                "--output-file",
                str(reqs),
            ],
            bundle.cwd,
        )
        # The python3.12 runtime is Amazon Linux 2023 (glibc 2.34), so manylinux_2_28 wheels run there;
        # some dependencies (asyncpg 0.32) no longer publish manylinux2014 aarch64 wheels.
        run(
            [
                "uv",
                "pip",
                "install",
                "--target",
                str(target),
                "--python-platform",
                "aarch64-manylinux_2_28",
                "--python-version",
                "3.12",
                "--no-deps",
                "-r",
                str(reqs),
            ],
            bundle.cwd,
        )
    if bundle.run_sh is not None:
        shutil.copy2(bundle.run_sh, target / "run.sh")
        (target / "run.sh").chmod(0o755)
    for dest, source in bundle.extras.items():
        if not source.exists():
            raise SystemExit(f"{source} is missing (for {bundle.name}); build it first (make ui)")
        shutil.copytree(
            source, target / dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
        )
    for cache in target.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    strip_native(target)
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    (target / ".packaged").write_text(f"{bundle.name}\n", encoding="utf-8")
    return size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", action="append", choices=sorted(BUNDLES), help="bundle to build (repeatable)")
    ap.add_argument("--out", type=Path, default=OUT)
    opts = ap.parse_args(argv)
    for name in opts.only or sorted(BUNDLES):
        print(f"{name}:", flush=True)
        size = build(BUNDLES[name], opts.out)
        print(f"  {size / 1_048_576:.1f} MB unzipped -> {opts.out / name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
