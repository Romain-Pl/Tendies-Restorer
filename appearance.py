#!/usr/bin/env python3
"""
Enables (or disables) `appearanceAware` in every descriptor's Wallpaper.plist
in a .tendies: the setting that exposes the Auto/Light/Dark selector in the
PosterBoard customization screen.

Usage:
    python3 appearance.py file.tendies [--off] [--output output_file.tendies]

Without --output, writes <name>.appearance.tendies next to the original.
Wallpaper.plist is always rewritten as a binary plist (never XML), matching
the format PosterBoard expects.
"""
import argparse
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

from convert import extract_tendies, find_descriptors, zip_descriptors
from logsetup import get_logger, setup_logging

logger = get_logger()


def find_wallpaper_plists(descriptor_dir: Path):
    """Finds every Wallpaper.plist under a descriptor (one per .wallpaper
    variant — most descriptors only have one)."""
    return sorted(descriptor_dir.glob("versions/*/contents/*.wallpaper/Wallpaper.plist"))


def set_appearance_aware(tendies_path: Path, output_path: Path, enabled: bool = True):
    """Forces appearanceAware=<enabled> on every Wallpaper.plist in the
    .tendies, then rewrites the package to output_path. Returns the list of
    changes (descriptor_name, wallpaper_name, before, after)."""
    staging = Path(tempfile.mkdtemp())
    try:
        extract_root = extract_tendies(tendies_path, staging)
        descriptors = find_descriptors(extract_root)
        if not descriptors:
            raise ValueError(f"No 'descriptors/<UUID>' folder found in {tendies_path}")

        changes = []
        for d in descriptors:
            plists = find_wallpaper_plists(d)
            if not plists:
                logger.warning(f"{d.name}: no Wallpaper.plist found, skipped")
                continue
            for wp in plists:
                with open(wp, "rb") as f:
                    data = plistlib.load(f)
                before = data.get("appearanceAware", False)
                data["appearanceAware"] = enabled
                with open(wp, "wb") as f:
                    plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
                changes.append((d.name, wp.parent.name, before, enabled))
                logger.info(f"{d.name}/{wp.parent.name}: appearanceAware {before} -> {enabled}")

        if not changes:
            raise ValueError("No Wallpaper.plist found in this .tendies, nothing to change")

        zip_descriptors(extract_root, output_path)
        logger.info(f"{len(changes)} Wallpaper.plist changed (appearanceAware={enabled}) -> {output_path}")
        return changes
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", type=Path)
    parser.add_argument("--off", action="store_true", help="Disable appearanceAware instead of enabling it")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    log_path = setup_logging("appearance", sys.argv)
    output = args.output or args.tendies.with_name(args.tendies.stem + ".appearance.tendies")
    try:
        changes = set_appearance_aware(args.tendies, output, enabled=not args.off)
        print(f"Written: {output}")
        for name, wp_name, before, after in changes:
            print(f"  {name}/{wp_name}: {before} -> {after}")
    except Exception:
        logger.exception("set_appearance_aware() failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
