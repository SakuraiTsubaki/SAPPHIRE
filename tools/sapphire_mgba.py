#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from sapphire_rom_expansion import (
    COMMON_BASE,
    EXPANDED_SIZE,
    MAGIC,
    identify_native,
    verify_expanded,
)

MGBA_VERSION = "0.10.5"
ASSETS = {
    "x86_64": {
        "name": "mGBA-0.10.5-appimage-x64.appimage",
        "size": 25208000,
    },
    "aarch64": {
        "name": "mGBA-0.10.5-appimage-arm64.appimage",
        "size": 24576384,
    },
}
RELEASE_BASE = (
    "https://github.com/mgba-emu/mgba/releases/download/"
    f"{MGBA_VERSION}"
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def tool_dir() -> Path:
    override = os.environ.get("SAPPHIRE_TOOL_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return project_root() / ".tools" / "mgba"


def normalize_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    if value in {"aarch64", "arm64"}:
        return "aarch64"
    raise ValueError(f"unsupported mGBA AppImage architecture: {value}")


def managed_path(arch: str | None = None) -> Path:
    key = normalize_arch(arch)
    return tool_dir() / ASSETS[key]["name"]


def find_mgba() -> Path | None:
    for key in ("SAPPHIRE_MGBA", "MGBA_PATH"):
        value = os.environ.get(key)
        if value:
            candidate = Path(value).expanduser()
            if candidate.is_file():
                return candidate.resolve()

    try:
        candidate = managed_path()
    except ValueError:
        candidate = None
    if candidate is not None and candidate.is_file():
        return candidate.resolve()

    for name in ("mgba", "mgba-qt", "mgba-sdl"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    return None


def probe() -> dict:
    executable = find_mgba()
    if executable is None:
        try:
            managed = str(managed_path())
        except ValueError:
            managed = None
        return {
            "installed": False,
            "managed_path": managed,
            "version": None,
        }

    version = None
    error = None
    try:
        proc = subprocess.run(
            [str(executable), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        first = (proc.stdout or "").strip().splitlines()
        version = first[0] if first else None
        if proc.returncode not in (0, None):
            error = f"version probe exited {proc.returncode}"
    except Exception as exc:  # pragma: no cover
        error = str(exc)

    return {
        "installed": True,
        "path": str(executable),
        "version": version,
        "probe_error": error,
    }


def bootstrap(*, force: bool = False, arch: str | None = None) -> dict:
    key = normalize_arch(arch)
    asset = ASSETS[key]
    destination = managed_path(key)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not force:
        if destination.stat().st_size != asset["size"]:
            raise ValueError(
                f"managed mGBA has unexpected size: {destination.stat().st_size} "
                f"!= {asset['size']}; use --force to replace it"
            )
        destination.chmod(destination.stat().st_mode | 0o111)
        return {
            "result": "present",
            "version": MGBA_VERSION,
            "path": str(destination),
            "size": destination.stat().st_size,
        }

    url = f"{RELEASE_BASE}/{asset['name']}"
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SAPPHIRE-mGBA-bootstrap/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as out:
            shutil.copyfileobj(response, out)
    except Exception:
        if partial.exists():
            partial.unlink()
        raise

    size = partial.stat().st_size
    if size != asset["size"]:
        partial.unlink(missing_ok=True)
        raise ValueError(
            f"downloaded mGBA size mismatch: {size} != {asset['size']}"
        )

    partial.replace(destination)
    destination.chmod(destination.stat().st_mode | 0o111)
    return {
        "result": "installed",
        "version": MGBA_VERSION,
        "path": str(destination),
        "size": size,
        "source": url,
    }


def validate_rom(path: Path) -> dict:
    data = path.read_bytes()
    if (
        len(data) == EXPANDED_SIZE
        and data[COMMON_BASE:COMMON_BASE + len(MAGIC)] == MAGIC
    ):
        report = verify_expanded(data)
        return {
            "kind": "expanded",
            "path": str(path),
            "known_source_rom": report["known_source_rom"],
            "size": len(data),
            "directory_count": len(report["directory"]),
            "prefix_patched": report["prefix_patched"],
        }

    report = identify_native(data)
    return {
        "kind": "native",
        "path": str(path),
        **report,
    }


def build_command(executable: Path, rom: Path) -> list[str]:
    return [str(executable), str(rom)]


def run_rom(
    rom: Path,
    *,
    save: Path | None,
    timeout: float | None,
    headless: bool,
    write_save: bool,
) -> dict:
    validation = validate_rom(rom)
    executable = find_mgba()
    if executable is None:
        raise FileNotFoundError(
            "mGBA not found; run 'python tools/sapphire_mgba.py bootstrap' first"
        )

    env = os.environ.copy()
    if headless:
        env.setdefault("SDL_VIDEODRIVER", "dummy")
        env.setdefault("SDL_AUDIODRIVER", "dummy")
    if executable.suffix.lower() == ".appimage":
        env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")

    timed_out = False
    returncode = None
    save_copied_back = False

    if save is None:
        command = build_command(executable, rom.resolve())
        try:
            proc = subprocess.run(
                command,
                env=env,
                timeout=timeout,
                check=False,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
    else:
        if not save.is_file():
            raise FileNotFoundError(save)
        with tempfile.TemporaryDirectory(prefix="sapphire-mgba-") as temp:
            temp_dir = Path(temp)
            temp_rom = temp_dir / rom.name
            temp_save = temp_rom.with_suffix(".sav")
            shutil.copy2(rom, temp_rom)
            shutil.copy2(save, temp_save)
            command = build_command(executable, temp_rom)
            try:
                proc = subprocess.run(
                    command,
                    env=env,
                    timeout=timeout,
                    check=False,
                )
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
            if write_save and temp_save.exists():
                shutil.copy2(temp_save, save)
                save_copied_back = True

    return {
        "result": (
            "smoke_alive"
            if timed_out and timeout is not None
            else "exited"
        ),
        "rom": validation,
        "mgba": str(executable),
        "timeout_seconds": timeout,
        "timed_out": timed_out,
        "returncode": returncode,
        "save_mode": (
            "none"
            if save is None
            else "copy_back"
            if write_save
            else "isolated_copy"
        ),
        "save_copied_back": save_copied_back,
    }


def json_print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "SAPPHIRE mGBA session bootstrap, ROM validation, "
            "and safe smoke runner"
        )
    )
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "probe",
        help="show the mGBA executable visible to this session",
    )

    bp = sub.add_parser(
        "bootstrap",
        help="install pinned mGBA AppImage under .tools/mgba",
    )
    bp.add_argument("--force", action="store_true")
    bp.add_argument("--arch", choices=sorted(ASSETS))

    vp = sub.add_parser(
        "verify-rom",
        help="validate a native or SAPPX10 expanded Sapphire ROM",
    )
    vp.add_argument("rom", type=Path)

    rp = sub.add_parser(
        "run",
        help="validate then launch a Sapphire ROM in mGBA",
    )
    rp.add_argument("rom", type=Path)
    rp.add_argument("--save", type=Path)
    rp.add_argument("--timeout", type=float)
    rp.add_argument("--headless", action="store_true")
    rp.add_argument(
        "--write-save",
        action="store_true",
        help=(
            "copy the temporary mGBA save back to --save; "
            "default is non-destructive"
        ),
    )

    sp = sub.add_parser(
        "smoke",
        help=(
            "headless launch; staying alive until timeout "
            "counts as a smoke pass"
        ),
    )
    sp.add_argument("rom", type=Path)
    sp.add_argument("--save", type=Path)
    sp.add_argument("--seconds", type=float, default=8.0)

    args = ap.parse_args()

    if args.command == "probe":
        json_print(probe())
        return 0
    if args.command == "bootstrap":
        json_print(
            bootstrap(force=args.force, arch=args.arch)
        )
        return 0
    if args.command == "verify-rom":
        json_print(validate_rom(args.rom))
        return 0
    if args.command == "run":
        result = run_rom(
            args.rom,
            save=args.save,
            timeout=args.timeout,
            headless=args.headless,
            write_save=args.write_save,
        )
        json_print(result)
        return (
            0
            if result["returncode"] in (0, None)
            else result["returncode"]
        )
    if args.command == "smoke":
        result = run_rom(
            args.rom,
            save=args.save,
            timeout=args.seconds,
            headless=True,
            write_save=False,
        )
        json_print(result)
        if result["timed_out"]:
            return 0
        return (
            0
            if result["returncode"] == 0
            else (result["returncode"] or 1)
        )

    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
