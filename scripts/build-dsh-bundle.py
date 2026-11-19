#!/usr/bin/env python3
"""Build an installable npm-format DSH bundle from this Git checkout."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import gzip
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from generate_codex_adapters import COOKBOOKS, DOMAINS, ROOT, plugin_description
from generate_dsh_adapters import adapter_body, cookbook_body

TEMPLATE = ROOT / "dsh-plugin"
BUNDLE_FILES = (
    "package.json", "cordis.patch.yml", "icon.svg", "README.md", "locale/zh.json", "locale/en.json",
)
WORKFLOW_DOCS = {"README.md", "INSTALL_DSH.md", "CONNECTORS.md"}
WORKFLOW_TREES = {"skills", "agents", "references"}
WORKFLOW_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".txt"}
BUNDLE_PATH_RESOLUTION = """## Path Resolution

In the DSH-provided skill resource hint, use the absolute “Base directory for this skill”. Resolve
`../../workflows/` relative to that directory. This is the root for the workflow paths listed here
and for repository-relative references inside those files. For example, resolve
`../../workflows/commercial-legal/CLAUDE.md` from this skill's base directory.

Read bundled files as references; never write to the installed bundle. Write case materials and
outputs in the user's authorized workspace only. Do not use any checkout or user-level repo marker
to locate bundled references. If a bundled path cannot be read, report it instead of guessing.
"""


def workflow_file(path: Path) -> bool:
    parts = path.parts
    if path.suffix not in WORKFLOW_SUFFIXES or any(part.startswith(".") for part in parts):
        return False
    if path.as_posix() in WORKFLOW_DOCS or path.as_posix() == "connectors/catalog.json":
        return True
    if parts[0] == "references":
        return True
    if parts[0] == "managed-agent-cookbooks":
        return len(parts) > 2 and parts[1] in COOKBOOKS
    return parts[0] in DOMAINS and (
        (len(parts) == 2 and parts[1] in {"CLAUDE.md", "README.md"})
        or (len(parts) > 2 and parts[1] in WORKFLOW_TREES)
    )


def workflow_paths() -> set[Path]:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    paths = {Path(name.decode()) for name in tracked.split(b"\0") if name}
    return {path for path in paths if workflow_file(path)}


def read_file(path: Path) -> bytes:
    if not path.is_file() or path.resolve() != path:
        raise ValueError(f"Expected a regular in-tree file: {path}")
    return path.read_bytes()


def content() -> Iterable[tuple[str, bytes]]:
    for name in BUNDLE_FILES:
        yield name, read_file(TEMPLATE / name)
    yield "LICENSE", read_file(ROOT / "LICENSE")
    paths = workflow_paths()
    for domain, meta in DOMAINS.items():
        skills = sorted(
            path.parts[2] for path in paths
            if len(path.parts) == 4 and path.parts[:2] == (domain, "skills") and path.name == "SKILL.md"
        )
        if not skills or Path(domain, "CLAUDE.md") not in paths:
            raise ValueError(f"Missing workflow files for {domain}")
        description = plugin_description(ROOT / domain)
        body = adapter_body(domain, meta, skills, description, path_resolution=BUNDLE_PATH_RESOLUTION)
        yield f"skills/{meta['codex_name']}/SKILL.md", body.encode()
    for cookbook, meta in COOKBOOKS.items():
        root = Path("managed-agent-cookbooks", cookbook)
        if not {root / "README.md", root / "agent.yaml"} <= paths:
            raise ValueError(f"Missing workflow files for {cookbook}")
        body = cookbook_body(cookbook, meta, path_resolution=BUNDLE_PATH_RESOLUTION)
        yield f"skills/{meta['codex_name']}/SKILL.md", body.encode()
    for path in sorted(paths):
        yield f"workflows/{path.as_posix()}", read_file(ROOT / path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    manifest = json.loads(read_file(TEMPLATE / "package.json"))
    out_dir = args.output_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{manifest['name']}-{manifest['version']}.tgz"
    with tempfile.TemporaryDirectory(dir=out_dir) as temp:
        staged = Path(temp) / target.name
        with staged.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as bundle:
                for name, data in content():
                    info = tarfile.TarInfo(f"package/{name}")
                    info.size = len(data)
                    info.mode = 0o644
                    bundle.addfile(info, io.BytesIO(data))
        staged.replace(target)
    print(target)


if __name__ == "__main__":
    main()
