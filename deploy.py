#!/usr/bin/env python3
"""
Full pipeline: grafts one or more .tendies straight into the real MobileSync
backup Finder uses for this iPhone.

Usage:
    python3 deploy.py <file1.tendies> [file2.tendies ...] [--select] [--udid ...]

Automatic steps:
  1. archives the old MobileSync backup (rename, nothing is lost)
  2. copies that archive back to the original MobileSync location
  3. grafts each .tendies into it (via convert.py)
  4. verifies the integrity of both databases
  5. if any step fails: automatically restores the archive back in place

Does NOT trigger the restore itself: that stays something you do yourself in
Finder, once you're ready.

A detailed log of every run is written to logs/ (see logsetup.py): paths,
generated identifiers, every row inserted into the databases, and the full
traceback on error.
"""

import argparse
import platform
import shutil
import sys
import time
from pathlib import Path

from convert import convert
from logsetup import get_logger, setup_logging

logger = get_logger()

def candidate_mobilesync_bases():
    """Possible backup folder locations depending on the OS and software in
    use (macOS only has one location; Windows has two, depending on whether
    it's "classic" iTunes or the Apple Devices/Microsoft Store app)."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return [home / "Library" / "Application Support" / "MobileSync" / "Backup"]
    if system == "Windows":
        return [
            home / "AppData" / "Roaming" / "Apple Computer" / "MobileSync" / "Backup",  # classic iTunes
            home / "Apple" / "MobileSync" / "Backup",  # Apple Devices / iTunes from the Microsoft Store
        ]
    raise RuntimeError(f"Unsupported system: {system!r} (macOS and Windows only)")


def list_available_backups():
    """Lists every valid backup folder (containing a Manifest.db) found in
    the known MobileSync locations."""
    found = []
    for base in candidate_mobilesync_bases():
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "Manifest.db").exists():
                found.append(child)
    return found


def find_backup_dir(udid) -> Path:
    if udid:
        candidates = candidate_mobilesync_bases()
        logger.debug(f"backup locations checked for udid={udid}: {candidates}")
        for base in candidates:
            backup_dir = base / udid
            if (backup_dir / "Manifest.db").exists():
                logger.debug(f"backup found: {backup_dir}")
                return backup_dir
        checked = "\n".join(f"  - {base / udid}" for base in candidates)
        logger.error(f"no backup found for udid={udid}")
        raise FileNotFoundError(
            f"No Manifest.db backup found for {udid}. Locations checked:\n{checked}"
        )

    logger.debug("no udid given, auto-detecting among available backups")
    found = list_available_backups()
    if len(found) == 1:
        logger.info(f"Single device detected, using it automatically: {found[0].name}")
        return found[0]
    if not found:
        logger.error("no backup found in the known MobileSync locations")
        raise FileNotFoundError(
            "No iOS backup found in the known MobileSync locations. "
            "Make a backup via Finder/iTunes first."
        )
    listed = "\n".join(f"  - {b.name}" for b in found)
    logger.error(f"several backups found, udid required: {[b.name for b in found]}")
    raise ValueError(
        f"Several backups found, specify which one with --udid <UDID>:\n{listed}"
    )


def _dir_stats(path: Path):
    """Counts files/folders and total size (best-effort, for the log)."""
    try:
        n_files = n_dirs = total = 0
        for p in path.rglob("*"):
            if p.is_dir():
                n_dirs += 1
            else:
                n_files += 1
                total += p.stat().st_size
        return n_files, n_dirs, total
    except Exception:
        logger.exception(f"couldn't compute stats for {path}")
        return None, None, None


def deploy(tendies_paths, select: bool, udid: str = None):
    logger.info(f"=== deploy: udid={udid or '(auto)'} select={select} tendies={list(tendies_paths)} ===")
    backup_dir = find_backup_dir(udid)
    deploy_to_dir(backup_dir, tendies_paths, select)


def deploy_to_dir(backup_dir: Path, tendies_paths, select: bool):
    """Core of deploy() once the MobileSync folder has already been
    determined (by udid on the CLI, or picked explicitly by a caller)."""
    udid = backup_dir.name
    logger.info(f"=== deploy_to_dir: {backup_dir} select={select} tendies={list(tendies_paths)} ===")

    n_files, n_dirs, total = _dir_stats(backup_dir)
    logger.debug(f"current backup before archiving: {n_files} files, {n_dirs} folders, {total} bytes")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = backup_dir.parent / f"{udid}-{timestamp}"

    logger.info(f"[1/4] Archiving the current backup -> {archive_dir.name}")
    t0 = time.monotonic()
    backup_dir.rename(archive_dir)
    logger.debug(f"rename {backup_dir} -> {archive_dir} done in {time.monotonic() - t0:.3f}s")

    try:
        logger.info("[2/4] Copying a working copy back to the MobileSync location...")
        t0 = time.monotonic()
        shutil.copytree(archive_dir, backup_dir)
        logger.debug(f"copytree {archive_dir} -> {backup_dir} done in {time.monotonic() - t0:.1f}s")

        logger.info(f"[3/4] Grafting {len(tendies_paths)} .tendies file(s)")
        for i, tendies_path in enumerate(tendies_paths, 1):
            logger.info(f"--- ({i}/{len(tendies_paths)}) {Path(tendies_path).name} ---")
            convert(Path(tendies_path), backup_dir, select=select, dry_run=False)

        logger.info("[4/4] Done.")
        logger.info(f"Backup ready: {backup_dir}")
        logger.info(f"Previous backup kept at: {archive_dir}")
        logger.info("Open Finder and start the restore when you're ready.")
    except Exception:
        logger.warning("graft failed — rolling back to the previous state...")
        if backup_dir.exists():
            logger.debug(f"removing the partial working copy {backup_dir}")
            shutil.rmtree(backup_dir)
        archive_dir.rename(backup_dir)
        logger.warning(f"rollback done: {archive_dir} -> {backup_dir}. Nothing was lost.")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", nargs="+", help="One or more .tendies files to graft")
    parser.add_argument("--select", action="store_true", help="Immediately activate the grafted poster(s)")
    parser.add_argument(
        "--udid", default=None,
        help="Device UDID (MobileSync folder). If omitted, auto-detected "
             "when only one backup is available.",
    )
    args = parser.parse_args()

    log_path = setup_logging("deploy", sys.argv)
    try:
        deploy(args.tendies, args.select, args.udid)
    except Exception:
        logger.exception("deploy() failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
