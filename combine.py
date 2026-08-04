#!/usr/bin/env python3
"""
Combines several .tendies (or several descriptors from the same .tendies)
into one, grouping their descriptors under a shared `family`.

Usage:
    python3 combine.py file1.tendies file2.tendies [...] \
        --family "MyCollection" --output combo.tendies

The order given on the command line is preserved in the final package (each
descriptor is renamed with a prefix that guarantees this order despite the
alphabetical sort used by convert.find_descriptors() at injection time).

Observed Apple limit: 10 descriptors maximum in a single .tendies.

Known limitation: this reproduces the ONLY grouping mechanism that actually
exists in the PosterBoard registry (a shared `family` string), but real
devices confirm this does not reproduce Apple's native swipe UI — a real
Apple wallpaper with a genuinely working swipe (Titanium, iPhone 15 Pro) only
ever has ONE registry entry for the whole family, with the sibling color
variants pulled from a private, system-side gallery catalog that isn't part
of any backup or .tendies. See the README's "Known limitations" section for
the full writeup. This flag is kept for cosmetic/forward-compatibility
purposes; don't expect a working native swipe from it.
"""
import argparse
import plistlib
import shutil
import sys
import tempfile
from pathlib import Path

from appearance import find_wallpaper_plists
from convert import extract_tendies, find_descriptors, zip_descriptors
from logsetup import get_logger, setup_logging

logger = get_logger()

MAX_DESCRIPTORS = 10


def extract_descriptors(tendies_path: Path):
    """Extracts a .tendies into a temporary folder and returns the list of
    its descriptors, each with its current display name (derived from its
    Wallpaper.plist). The temporary folder is NOT cleaned up here: it stays
    usable while the caller reorders/renames variants before the final call
    to combine_variants(); clean it up yourself afterwards."""
    staging = Path(tempfile.mkdtemp())
    extract_root = extract_tendies(tendies_path, staging)
    descriptors = find_descriptors(extract_root)
    if not descriptors:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError(f"No descriptor found in {tendies_path.name}")

    result = []
    for d in descriptors:
        plists = find_wallpaper_plists(d)
        display_name = None
        if plists:
            with open(plists[0], "rb") as f:
                display_name = plistlib.load(f).get("name")
        result.append({
            "path": d,
            "staging_root": staging,
            "source_name": tendies_path.name,
            "descriptor_name": d.name,
            "display_name": display_name or d.name,
        })
    return result


def combine_variants(entries, output_path: Path, family_name: str):
    """entries: an ORDERED list of dicts {"path": already-extracted
    descriptor Path, "display_name": str}. The list order becomes the swipe
    order. Writes the combined package to output_path and returns a summary
    (position, source_folder_name, display_name)."""
    if not family_name or not family_name.strip():
        raise ValueError("The family name can't be empty")
    if not entries:
        raise ValueError("No variant to combine")
    if len(entries) > MAX_DESCRIPTORS:
        raise ValueError(
            f"{len(entries)} variants selected, maximum {MAX_DESCRIPTORS} — "
            "Apple rejects a .tendies beyond that count."
        )

    staging = Path(tempfile.mkdtemp())
    descriptors_out = staging / "combined" / "descriptors"
    descriptors_out.mkdir(parents=True)

    summary = []
    try:
        for index, entry in enumerate(entries):
            source_dir = Path(entry["path"])
            display_name = entry.get("display_name") or source_dir.name

            # Ordered hex prefix: guarantees that the alphabetical sort done
            # by find_descriptors() (used at injection time) restores this
            # order, regardless of the original UUID. Valid UUID shape (32
            # hex digits, dashes in the right places) — never referenced
            # internally (checked by grepping real .tendies files), so
            # renaming to this is safe.
            dest_name = f"{index:08X}-0000-4000-8000-000000000000"
            dest = descriptors_out / dest_name
            shutil.copytree(source_dir, dest)

            plists = find_wallpaper_plists(dest)
            if not plists:
                logger.warning(f"{source_dir.name}: no Wallpaper.plist, family/name not applied")
            for wp in plists:
                with open(wp, "rb") as f:
                    data = plistlib.load(f)
                before_family = data.get("family")
                before_name = data.get("name")
                data["family"] = family_name
                data["name"] = display_name
                default = data.get("assets", {}).get("lockAndHome", {}).get("default")
                if isinstance(default, dict):
                    default["name"] = display_name
                with open(wp, "wb") as f:
                    plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)
                logger.info(
                    f"[{index + 1}/{len(entries)}] {source_dir.name} -> {dest_name}: "
                    f"family {before_family!r} -> {family_name!r}, name {before_name!r} -> {display_name!r}"
                )

            summary.append((index + 1, entry.get("source_name", source_dir.name), display_name))

        zip_descriptors(descriptors_out.parent, output_path)
        logger.info(f"{len(entries)} descriptor(s) combined under family={family_name!r} -> {output_path}")
        return summary
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", nargs="+", type=Path, help=".tendies files to combine, in the desired swipe order")
    parser.add_argument("--family", required=True, help="Family name shared by all variants")
    parser.add_argument("--output", required=True, type=Path, help="Combined .tendies file to write")
    args = parser.parse_args()

    log_path = setup_logging("combine", sys.argv)
    staging_roots = []
    try:
        entries = []
        for tendies_path in args.tendies:
            found = extract_descriptors(tendies_path)
            staging_roots.append(found[0]["staging_root"])
            entries.extend(found)

        summary = combine_variants(entries, args.output, args.family)
        print(f"Written: {args.output} ({len(summary)} descriptor(s), family={args.family!r})")
        for position, source, name in summary:
            print(f"  {position}. {source} -> {name!r}")
    except Exception:
        logger.exception("combine_variants() failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)
    finally:
        for root in staging_roots:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
