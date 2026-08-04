#!/usr/bin/env python3
"""
Extracts a poster that's already installed — in an iPhone backup or an
Xcode simulator — and repackages it as a .tendies. The reverse of
convert.py/sim_deploy.py: instead of injecting a .tendies into a device,
this pulls one back out of a device.

Usage:
    python3 dump.py --backup <backup_dir> --list
    python3 dump.py --backup <backup_dir> --uuid <UUID> --provider <providerId> --output out.tendies

    python3 dump.py --simulator <UDID> --list
    python3 dump.py --simulator <UDID> --uuid <UUID> --provider <providerId> --output out.tendies

Works both for regular posters (com.apple.WallpaperKit.CollectionsPoster)
and for native providers like com.apple.MercuryPoster: in the latter case,
if the original suggestionMetadata.plist is missing (common for a device's
very first, factory-seeded poster), a minimal replacement is synthesized so
the provider is still auto-detected correctly on reinjection — without it,
convert.py would silently default to CollectionsPoster and injection would
fail.
"""
import argparse
import plistlib
import shutil
import sqlite3
import sys
import tempfile
import uuid as uuidlib
from pathlib import Path

from convert import (
    DEFAULT_PROVIDER,
    EXT_BASE,
    REGISTRY_RELPATH,
    extract_tendies,
    file_id_for,
    find_descriptors,
    inspect_descriptor,
    zip_descriptors,
)
from sim_deploy import REGISTRY_NAME, poster_root_for
from logsetup import get_logger, setup_logging

logger = get_logger()

_LIST_QUERY = """
    SELECT p.UUID, p.providerId, m.roleId, m.roleSortKey,
           COALESCE((SELECT attributePayload FROM posterAttributes
                     WHERE posterUUID = p.UUID AND roleId = m.roleId
                       AND attributeIdentifier = 'SELECTED'), '0')
    FROM poster p
    JOIN posterRoleMembership m ON m.posterUUID = p.UUID
    ORDER BY m.roleId, m.roleSortKey
"""


def _rows_to_posters(rows, descriptor_lookup):
    result = []
    for poster_uuid, provider, role, sort_key, selected in rows:
        result.append({
            "uuid": poster_uuid,
            "provider": provider,
            "role": role,
            "sort_key": sort_key,
            "selected": selected == "1",
            "descriptor_identifier": descriptor_lookup(provider, poster_uuid),
        })
    return result


# ---------------------------------------------------------------------------
# Source: iPhone backup (fileID-addressed storage, via Manifest.db)
# ---------------------------------------------------------------------------

def _registry_path_in_backup(backup_dir: Path) -> Path:
    if not (backup_dir / "Manifest.db").exists():
        raise FileNotFoundError(f"{backup_dir} doesn't look like an iOS backup (no Manifest.db)")
    fid = file_id_for(REGISTRY_RELPATH)
    registry_path = backup_dir / fid[:2] / fid
    if not registry_path.exists():
        raise FileNotFoundError(f"PosterBoard registry not found in {backup_dir}")
    return registry_path


def list_posters_in_backup(backup_dir: Path):
    registry_path = _registry_path_in_backup(backup_dir)
    conn = sqlite3.connect(f"file:{registry_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_LIST_QUERY).fetchall()
    finally:
        conn.close()

    def lookup(provider, poster_uuid):
        relpath = f"{EXT_BASE}/{provider}/configurations/{poster_uuid}/com.apple.posterkit.provider.descriptor.identifier"
        fid = file_id_for(relpath)
        p = backup_dir / fid[:2] / fid
        if p.exists():
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception:
                return None
        return None

    return _rows_to_posters(rows, lookup)


def dump_from_backup(backup_dir: Path, provider: str, poster_uuid: str, output_path: Path):
    _registry_path_in_backup(backup_dir)  # validates this is a usable backup
    manifest_conn = sqlite3.connect(f"file:{backup_dir / 'Manifest.db'}?mode=ro", uri=True)
    base = f"{EXT_BASE}/{provider}/configurations/{poster_uuid}"
    try:
        rows = manifest_conn.execute(
            "SELECT fileID, relativePath, flags FROM Files WHERE relativePath LIKE ?", (base + "%",)
        ).fetchall()
    finally:
        manifest_conn.close()
    if not rows:
        raise ValueError(f"No file found for {provider}/{poster_uuid} in this backup")

    staging = Path(tempfile.mkdtemp())
    try:
        raw_dir = staging / "raw"
        for fid, relpath, flags in rows:
            sub = relpath[len(base):].lstrip("/")
            dest = raw_dir / sub if sub else raw_dir
            if flags == 2:
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(backup_dir / fid[:2] / fid, dest)
        logger.info(f"{len(rows)} entrie(s) extracted from the backup for {provider}/{poster_uuid}")
        _finalize_dump(raw_dir, provider, output_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Source: Xcode simulator (a plain folder on the Mac)
# ---------------------------------------------------------------------------

def list_posters_in_simulator(udid: str):
    poster_root = poster_root_for(udid)
    registry_path = poster_root / REGISTRY_NAME
    if not registry_path.exists():
        raise FileNotFoundError(f"Registry not found for simulator {udid}")
    conn = sqlite3.connect(f"file:{registry_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(_LIST_QUERY).fetchall()
    finally:
        conn.close()

    def lookup(provider, poster_uuid):
        f = poster_root / "Extensions" / provider / "configurations" / poster_uuid / "com.apple.posterkit.provider.descriptor.identifier"
        if f.exists():
            try:
                return f.read_text(encoding="utf-8").strip()
            except Exception:
                return None
        return None

    return _rows_to_posters(rows, lookup)


def dump_from_simulator(udid: str, provider: str, poster_uuid: str, output_path: Path):
    poster_root = poster_root_for(udid)
    config_dir = poster_root / "Extensions" / provider / "configurations" / poster_uuid
    if not config_dir.is_dir():
        raise FileNotFoundError(f"Configuration not found: {config_dir}")
    _finalize_dump(config_dir, provider, output_path)


# ---------------------------------------------------------------------------
# Shared: repackaging as .tendies + self-verification
# ---------------------------------------------------------------------------

def _write_synthetic_suggestion_metadata(path: Path, provider: str, descriptor_id: str):
    """Rebuilds a minimal but structurally valid suggestionMetadata.plist
    (same NSKeyedArchiver shape as the real ones), for non-CollectionsPoster
    providers whose original config doesn't have one — without it,
    inspect_descriptor() can't determine the provider and falls back to
    CollectionsPoster by default."""
    identifier = f"{descriptor_id}.DYNAMIC" if descriptor_id else provider
    data = {
        "$archiver": "NSKeyedArchiver",
        "$version": 100000,
        "$objects": [
            "$null",
            {"$class": plistlib.UID(4), "suggestedGalleryItem": plistlib.UID(2)},
            {"$class": plistlib.UID(3), "extensionBundleIdentifier": provider, "descriptorIdentifier": identifier},
            {"$classes": ["NSDictionary", "NSObject"], "$classname": "NSDictionary"},
            {"$classes": ["NSDictionary", "NSObject"], "$classname": "NSDictionary"},
        ],
        "$top": {"root": plistlib.UID(1)},
    }
    with open(path, "wb") as f:
        plistlib.dump(data, f, fmt=plistlib.FMT_BINARY)


def _finalize_dump(raw_dir: Path, provider: str, output_path: Path):
    staging = Path(tempfile.mkdtemp())
    try:
        new_uuid = str(uuidlib.uuid4()).upper()
        dest = staging / "descriptors" / new_uuid
        dest.mkdir(parents=True)

        for f in raw_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(raw_dir)
            # Render cache specific to THIS instance (simulator or device),
            # never present in a real backup but present on a live simulator
            # container: excluded, not portable and not needed (regenerated
            # automatically on reinjection).
            if "scratch" in rel.parts or f.name.startswith("RuntimeSnapshot") or f.name == ".DS_Store":
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, out)

        suggestion_path = dest / "com.apple.posterkit.provider.identifierURL.suggestionMetadata.plist"
        if provider != DEFAULT_PROVIDER and not suggestion_path.exists():
            descriptor_id_file = dest / "com.apple.posterkit.provider.descriptor.identifier"
            descriptor_id = descriptor_id_file.read_text(encoding="utf-8").strip() if descriptor_id_file.exists() else ""
            _write_synthetic_suggestion_metadata(suggestion_path, provider, descriptor_id)
            logger.warning(
                f"suggestionMetadata.plist missing for provider={provider}: rebuilt "
                "one to preserve auto-detection on reinjection"
            )

        zip_descriptors(staging, output_path)

        # Self-verification: the package must read back correctly, the same
        # way convert.py would at injection time.
        check_staging = Path(tempfile.mkdtemp())
        try:
            extract_root = extract_tendies(output_path, check_staging)
            for d in find_descriptors(extract_root):
                info = inspect_descriptor(d)
                logger.info(
                    f"verification: provider={info['target_provider']}, "
                    f"descriptor.identifier={info['descriptor_identifier']}, role={info['role_identifier']}"
                )
        finally:
            shutil.rmtree(check_staging, ignore_errors=True)

        logger.info(f"poster extracted -> {output_path}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--backup", type=Path, help="Source iOS backup folder")
    source.add_argument("--simulator", metavar="UDID", help="Source simulator UDID")
    parser.add_argument("--list", action="store_true", help="List the posters available in the source and exit")
    parser.add_argument("--uuid", help="UUID of the poster to extract (see --list)")
    parser.add_argument("--provider", help="providerId of the poster to extract (see --list)")
    parser.add_argument("--output", type=Path, help=".tendies file to write")
    args = parser.parse_args()

    log_path = setup_logging("dump", sys.argv)
    try:
        posters = list_posters_in_backup(args.backup) if args.backup else list_posters_in_simulator(args.simulator)

        if args.list:
            for p in posters:
                flag = " [SELECTED]" if p["selected"] else ""
                print(f"{p['uuid']}  {p['provider']:45s} {str(p['descriptor_identifier']):20s} "
                      f"role={p['role']} sort={p['sort_key']}{flag}")
            return

        if not args.uuid or not args.provider or not args.output:
            parser.error("--uuid, --provider and --output are required (or use --list alone)")

        if args.backup:
            dump_from_backup(args.backup, args.provider, args.uuid, args.output)
        else:
            dump_from_simulator(args.simulator, args.provider, args.uuid, args.output)
        print(f"Written: {args.output}")
    except Exception:
        logger.exception("dump failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
