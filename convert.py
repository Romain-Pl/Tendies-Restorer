#!/usr/bin/env python3
"""
Converts a .tendies file (PosterBoard gallery descriptor) into one or more
real configurations, then injects them into an unencrypted iOS backup
(Manifest.db + fileID blobs + central SQLite registry).

Usage:
    python3 convert.py <file.tendies> <backup_dir> [--select] [--dry-run]

<backup_dir> must be a COPY of an unencrypted iOS backup (never the
original, never a live MobileSync folder). This tool never touches
MobileSync or the iPhone itself: deploying to MobileSync and starting the
restore stay separate, manual steps.

A detailed log of every run is written to logs/ (see logsetup.py): paths,
generated identifiers, every row inserted into the databases, and the full
traceback on error.
"""

import argparse
import hashlib
import plistlib
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from logsetup import get_logger, setup_logging

logger = get_logger()

DOMAIN = "AppDomain-com.apple.PosterBoard"
EXT_BASE = "Library/Application Support/PRBPosterExtensionDataStore/61/Extensions"
REGISTRY_RELPATH = "Library/Application Support/PRBPosterExtensionDataStore/61/PBFPosterExtensionDataStoreSQLiteDatabase.sqlite3"
DEFAULT_PROVIDER = "com.apple.WallpaperKit.CollectionsPoster"
DEFAULT_ROLE = "PRPosterRoleLockScreen"

MODE_DIR = 16895   # 0o40777, confirmed by inspecting a real backup
MODE_FILE = 33279  # 0o100777, confirmed by inspecting a real backup


# ---------------------------------------------------------------------------
# Reading an NSKeyedArchiver plist (the format used by suggestionMetadata.plist)
# ---------------------------------------------------------------------------

def _resolve(objects, ref, memo=None):
    """Recursively resolves the UID references of an NSKeyedArchiver plist
    into a plain Python structure (dict/list/scalar values)."""
    if memo is None:
        memo = {}
    if isinstance(ref, plistlib.UID):
        idx = ref.data
        if idx in memo:
            return memo[idx]
        memo[idx] = None
        resolved = _resolve(objects, objects[idx], memo)
        memo[idx] = resolved
        return resolved
    if isinstance(ref, dict):
        return {k: _resolve(objects, v, memo) for k, v in ref.items() if not k.startswith("$")}
    if isinstance(ref, list):
        return [_resolve(objects, v, memo) for v in ref]
    return ref


def read_keyed_archiver_plist(path):
    logger.debug(f"reading NSKeyedArchiver plist: {path}")
    with open(path, "rb") as f:
        raw = plistlib.load(f)
    objects = raw["$objects"]
    root_ref = raw["$top"]["root"]
    resolved = _resolve(objects, root_ref)
    logger.debug(f"resolved plist ({path.name}): {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# Detecting a descriptor's "shape" and deriving any missing identifiers.
# Two shapes observed in the wild:
#   - "gallery"      : has a suggestionMetadata.plist, but not the plain-text
#                       role.identifier / descriptor.identifier files
#   - "near-config"   : already has role.identifier / descriptor.identifier,
#                       no suggestionMetadata.plist
# This function's job is to absorb that difference once and for all:
# whatever shape it's given, it always returns the 3 pieces of information
# needed downstream (role, descriptor id, target provider).
# ---------------------------------------------------------------------------

def inspect_descriptor(descriptor_dir: Path):
    logger.debug(f"inspect_descriptor({descriptor_dir})")
    role_file = descriptor_dir / "com.apple.posterkit.role.identifier"
    descriptor_id_file = descriptor_dir / "com.apple.posterkit.provider.descriptor.identifier"
    suggestion_file = descriptor_dir / "com.apple.posterkit.provider.identifierURL.suggestionMetadata.plist"

    role_identifier = None
    descriptor_identifier = None
    target_provider = None

    if role_file.exists():
        role_identifier = role_file.read_text(encoding="utf-8").strip() or None
        logger.debug(f"role.identifier read directly from file: {role_identifier}")
    else:
        logger.debug("role.identifier missing from descriptor")

    if descriptor_id_file.exists():
        descriptor_identifier = descriptor_id_file.read_text(encoding="utf-8").strip() or None
        logger.debug(f"descriptor.identifier read directly from file: {descriptor_identifier}")
    else:
        logger.debug("descriptor.identifier missing from descriptor")

    suggestion_data = None
    if suggestion_file.exists():
        logger.debug(f"suggestionMetadata.plist present: {suggestion_file}")
        try:
            suggestion_data = read_keyed_archiver_plist(suggestion_file)
        except Exception:
            logger.exception(f"couldn't read suggestionMetadata.plist ({suggestion_file}), ignoring it")
    else:
        logger.debug("suggestionMetadata.plist missing from descriptor")

    if suggestion_data:
        item = suggestion_data.get("suggestedGalleryItem", {})
        if target_provider is None:
            target_provider = item.get("extensionBundleIdentifier")
            logger.debug(f"target_provider derived from suggestionMetadata: {target_provider}")
        if descriptor_identifier is None:
            raw_id = item.get("descriptorIdentifier")
            if raw_id:
                # "7400.DYNAMIC" -> "7400": the suffix is only used for
                # suggestions, never seen in the real plain-text file.
                descriptor_identifier = raw_id.split(".")[0]
                logger.debug(f"descriptor.identifier derived from suggestionMetadata: {raw_id} -> {descriptor_identifier}")

    if target_provider is None and suggestion_file.exists():
        # Safety net: some community suggestionMetadata.plist files have a
        # non-standard NSKeyedArchiver structure ($top with no "root" key),
        # most likely a buggy synthetic reconstruction by whichever tool
        # produced them — see dump.py, which has to work around the same
        # issue. Rather than silently falling back to the wrong default
        # provider, look for the identifier directly among the plist's raw
        # objects.
        try:
            with open(suggestion_file, "rb") as f:
                raw = plistlib.load(f)
            for obj in raw.get("$objects", []):
                if isinstance(obj, str) and obj.startswith("com.apple.") and "Poster" in obj:
                    target_provider = obj
                    logger.warning(
                        f"target_provider recovered by scanning raw \\$objects "
                        f"(non-standard suggestionMetadata structure): {target_provider}"
                    )
                    break
        except Exception:
            logger.exception(f"fallback raw read of {suggestion_file} also failed")

    if descriptor_identifier is None:
        # last resort: derive it from the folder name <id>.<name>-<class>.wallpaper
        logger.debug("descriptor.identifier still unknown, trying to derive it from the .wallpaper folder name")
        for version_dir in (descriptor_dir / "versions").glob("*"):
            contents = version_dir / "contents"
            if not contents.is_dir():
                continue
            for wp in contents.glob("*.wallpaper"):
                descriptor_identifier = wp.name.split(".")[0]
                logger.debug(f"descriptor.identifier derived from folder name {wp.name} -> {descriptor_identifier}")
                break
            if descriptor_identifier:
                break

    if role_identifier is None:
        role_identifier = DEFAULT_ROLE
        logger.debug(f"role_identifier missing everywhere, applying default: {DEFAULT_ROLE}")

    if target_provider is None:
        target_provider = DEFAULT_PROVIDER
        logger.debug(f"target_provider missing everywhere, applying default: {DEFAULT_PROVIDER}")

    if descriptor_identifier is None:
        logger.error(f"descriptor.identifier not found and couldn't be derived for {descriptor_dir}")
        raise ValueError(f"Couldn't determine descriptor.identifier for {descriptor_dir}")

    result = {
        "role_identifier": role_identifier,
        "descriptor_identifier": descriptor_identifier,
        "target_provider": target_provider,
        "has_suggestion_metadata": suggestion_file.exists(),
    }
    logger.debug(f"inspect_descriptor({descriptor_dir.name}) result = {result}")
    return result


def find_descriptors(extract_root: Path):
    """Finds every descriptors/<UUID> folder in the extracted zip."""
    result = []
    descriptors_dirs = list(extract_root.glob("**/descriptors"))
    logger.debug(f"'descriptors' folders found in the extracted zip: {descriptors_dirs}")
    for descriptors_dir in descriptors_dirs:
        for child in sorted(descriptors_dir.iterdir()):
            if child.is_dir():
                result.append(child)
    logger.debug(f"total descriptors found: {len(result)} -> {[d.name for d in result]}")
    return result


# ---------------------------------------------------------------------------
# .tendies zip utilities, shared by convert.py, combine.py and appearance.py:
# extraction (filtering out __MACOSX/.DS_Store) and repackaging a
# descriptors/ folder into a valid .tendies.
# ---------------------------------------------------------------------------

def extract_tendies(tendies_path: Path, staging_root: Path) -> Path:
    """Extracts a .tendies into <staging_root>/extracted and returns that path."""
    with zipfile.ZipFile(tendies_path) as z:
        all_names = z.namelist()
        names = [n for n in all_names if "__MACOSX" not in n and not n.endswith(".DS_Store")]
        logger.debug(
            f"{tendies_path.name}: {len(all_names)} entries in the zip, "
            f"{len(names)} kept after filtering __MACOSX/.DS_Store"
        )
        extract_root = staging_root / "extracted"
        extract_root.mkdir(parents=True, exist_ok=True)
        z.extractall(extract_root, members=names)
    logger.debug(f"extraction finished in {extract_root}")
    return extract_root


def zip_descriptors(source_root: Path, output_path: Path):
    """Zips the descriptors/ folder (under source_root, or source_root
    itself if it's already named 'descriptors') into a valid .tendies."""
    descriptors_dir = source_root / "descriptors"
    if not descriptors_dir.is_dir():
        if source_root.name == "descriptors":
            descriptors_dir = source_root
            source_root = source_root.parent
        else:
            raise ValueError(f"no 'descriptors' folder found under {source_root}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    n_entries = 0
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(descriptors_dir.rglob("*")):
            if path.name == ".DS_Store" or "__MACOSX" in path.parts:
                continue
            z.write(path, path.relative_to(source_root))
            n_entries += 1
    logger.info(f".tendies package written: {output_path} ({n_entries} entries)")


# ---------------------------------------------------------------------------
# Building a configuration from a descriptor
# ---------------------------------------------------------------------------

def build_configuration(descriptor_dir: Path, info: dict, staging_root: Path) -> Path:
    new_uuid = str(uuid.uuid4()).upper()
    config_dir = staging_root / new_uuid
    logger.debug(f"copying {descriptor_dir} -> {config_dir} (new posterUUID={new_uuid})")
    shutil.copytree(descriptor_dir, config_dir)

    (config_dir / "com.apple.posterkit.role.identifier").write_text(info["role_identifier"], encoding="utf-8")
    (config_dir / "com.apple.posterkit.provider.descriptor.identifier").write_text(
        info["descriptor_identifier"], encoding="utf-8"
    )
    logger.debug(
        f"role.identifier='{info['role_identifier']}' and "
        f"descriptor.identifier='{info['descriptor_identifier']}' written to {config_dir}"
    )

    return config_dir, new_uuid


# ---------------------------------------------------------------------------
# Building MBFile blobs (the NSKeyedArchiver format used by Manifest.db)
# ---------------------------------------------------------------------------

def make_mbfile_blob(relative_path: str, is_dir: bool, size: int, now: int) -> bytes:
    inode = (abs(hash((relative_path, now))) % 900000) + 100000
    main_dict = {
        "$class": plistlib.UID(3),
        "Birth": now,
        "Flags": 0,
        "GroupID": 501,
        "InodeNumber": inode,
        "LastModified": now,
        "LastStatusChange": now,
        "Mode": MODE_DIR if is_dir else MODE_FILE,
        "ProtectionClass": 4,
        "RelativePath": plistlib.UID(2),
        "Size": 0 if is_dir else size,
        "UserID": 501,
    }
    data = {
        "$archiver": "NSKeyedArchiver",
        "$version": 100000,
        "$objects": [
            "$null",
            main_dict,
            relative_path,
            {"$classes": ["MBFile", "NSObject"], "$classname": "MBFile"},
        ],
        "$top": {"root": plistlib.UID(1)},
    }
    logger.debug(
        f"make_mbfile_blob relpath={relative_path!r} is_dir={is_dir} size={size} "
        f"mode={main_dict['Mode']} inode={inode}"
    )
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)


def file_id_for(relative_path: str) -> str:
    fid = hashlib.sha1(f"{DOMAIN}-{relative_path}".encode()).hexdigest()
    logger.debug(f"file_id_for({relative_path!r}) = {fid}")
    return fid


# ---------------------------------------------------------------------------
# Injecting into the backup
# ---------------------------------------------------------------------------

def ensure_ancestor_dirs(cur, now, seen, provider):
    """Makes sure the Extensions/<provider> and .../configurations folders
    exist in Manifest.db (needed the first time a provider gets a config)."""
    for relpath in (
        f"{EXT_BASE}/{provider}",
        f"{EXT_BASE}/{provider}/configurations",
    ):
        if relpath in seen:
            continue
        fid = file_id_for(relpath)
        cur.execute("SELECT 1 FROM Files WHERE fileID=?", (fid,))
        if cur.fetchone():
            logger.debug(f"ancestor folder already present in Manifest.db: {relpath}")
            seen.add(relpath)
            continue
        blob = make_mbfile_blob(relpath, True, 0, now)
        cur.execute(
            "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?,?,?,2,?)",
            (fid, DOMAIN, relpath, blob),
        )
        logger.info(f"ancestor folder created in Manifest.db: {relpath} (fileID={fid})")
        seen.add(relpath)


def inject_configuration(backup_dir: Path, config_dir: Path, new_uuid: str, provider: str, now: int):
    manifest_path = backup_dir / "Manifest.db"
    logger.debug(f"opening {manifest_path}")
    conn = sqlite3.connect(manifest_path)
    cur = conn.cursor()

    seen = set()
    ensure_ancestor_dirs(cur, now, seen, provider)

    base_relpath = f"{EXT_BASE}/{provider}/configurations/{new_uuid}"
    entries = []

    def walk(local_dir: Path, rel_prefix: str):
        entries.append((rel_prefix, True, local_dir))
        for child in sorted(local_dir.iterdir()):
            rel_child = f"{rel_prefix}/{child.name}"
            if child.is_dir():
                walk(child, rel_child)
            else:
                entries.append((rel_child, False, child))

    walk(config_dir, base_relpath)
    logger.debug(f"{len(entries)} entries (folders+files) to insert under {base_relpath}")

    n_dirs = n_files = total_bytes = 0
    for relpath, is_dir, local_path in entries:
        fid = file_id_for(relpath)
        if is_dir:
            blob = make_mbfile_blob(relpath, True, 0, now)
            cur.execute(
                "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?,?,?,2,?)",
                (fid, DOMAIN, relpath, blob),
            )
            logger.debug(f"[DIR ] fileID={fid} relpath={relpath}")
            n_dirs += 1
        else:
            size = local_path.stat().st_size
            blob = make_mbfile_blob(relpath, False, size, now)
            cur.execute(
                "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?,?,?,1,?)",
                (fid, DOMAIN, relpath, blob),
            )
            dest_dir = backup_dir / fid[:2]
            dest_dir.mkdir(exist_ok=True)
            shutil.copyfile(local_path, dest_dir / fid)
            logger.debug(f"[FILE] fileID={fid} relpath={relpath} size={size} -> {dest_dir / fid}")
            n_files += 1
            total_bytes += size

    conn.commit()
    conn.close()
    logger.info(
        f"Manifest.db: {n_dirs} folder(s) + {n_files} file(s) inserted "
        f"({total_bytes} bytes) under {base_relpath}"
    )
    return base_relpath


def inject_registry(backup_dir: Path, provider: str, new_uuid: str, role: str, select: bool, now: int):
    fid = file_id_for(REGISTRY_RELPATH)
    registry_path = backup_dir / fid[:2] / fid
    logger.debug(f"expected registry at {registry_path} (fileID={fid})")
    if not registry_path.exists():
        logger.error(f"SQLite registry not found at {registry_path}")
        raise FileNotFoundError(
            f"SQLite registry not found at {registry_path} — this backup has no "
            "PosterBoard history, or the path has changed."
        )

    conn = sqlite3.connect(registry_path)
    cur = conn.cursor()

    cur.execute("INSERT INTO poster (UUID, providerId) VALUES (?, ?)", (new_uuid, provider))
    logger.debug(f"INSERT INTO poster (UUID={new_uuid}, providerId={provider}) rowid={cur.lastrowid}")

    cur.execute(
        "SELECT COALESCE(MAX(roleSortKey), 0) FROM posterRoleMembership WHERE roleId=?",
        (role,),
    )
    next_sort_key = cur.fetchone()[0] + 1
    cur.execute(
        "INSERT INTO posterRoleMembership (posterUUID, roleId, roleSortKey) VALUES (?,?,?)",
        (new_uuid, role, next_sort_key),
    )
    logger.debug(f"INSERT INTO posterRoleMembership (posterUUID={new_uuid}, roleId={role}, roleSortKey={next_sort_key})")

    apple_epoch_now = now - 978307200  # Unix epoch -> Apple/Cocoa epoch (2001-01-01) conversion
    usage_payload = (
        '{"creationDate":%f,"lastModifiedDate":%f,"extensionAvailable":true,'
        '"attributeType":"PRPosterRoleAttributeTypeUsageMetadata"}' % (apple_epoch_now, apple_epoch_now)
    )
    cur.execute(
        "INSERT INTO posterAttributes (posterUUID, roleId, attributeIdentifier, attributePayload) VALUES (?,?,?,?)",
        (new_uuid, role, "PRPosterRoleAttributeTypeUsageMetadata", usage_payload),
    )
    logger.debug(f"INSERT posterAttributes UsageMetadata for {new_uuid}: {usage_payload}")

    if select:
        cur.execute(
            "SELECT posterUUID FROM posterAttributes WHERE roleId=? AND attributeIdentifier='SELECTED' AND attributePayload='1'",
            (role,),
        )
        previously_selected = [row[0] for row in cur.fetchall()]
        cur.execute(
            "UPDATE posterAttributes SET attributePayload='0' WHERE roleId=? AND attributeIdentifier='SELECTED'",
            (role,),
        )
        cur.execute(
            "INSERT INTO posterAttributes (posterUUID, roleId, attributeIdentifier, attributePayload) VALUES (?,?,?,?)",
            (new_uuid, role, "SELECTED", "1"),
        )
        logger.info(
            f"SELECTED switched to {new_uuid} for role {role} "
            f"(previously selected: {previously_selected or 'none'})"
        )

    conn.commit()
    cur.execute("PRAGMA integrity_check")
    check = cur.fetchone()[0]
    conn.close()
    if check != "ok":
        logger.error(f"registry integrity_check failed: {check}")
        raise RuntimeError(f"registry integrity_check failed: {check}")
    logger.debug("registry integrity_check: ok")

    # Refreshes the registry blob's size/timestamp in Manifest.db
    size = registry_path.stat().st_size
    blob = make_mbfile_blob(REGISTRY_RELPATH, False, size, now)
    manifest_conn = sqlite3.connect(backup_dir / "Manifest.db")
    manifest_conn.execute("UPDATE Files SET file=? WHERE fileID=?", (blob, fid))
    manifest_conn.commit()
    manifest_conn.close()
    logger.debug(f"registry Manifest.db blob refreshed (size={size})")

    logger.info(
        f"Registry: poster={new_uuid} provider={provider} role={role} "
        f"roleSortKey={next_sort_key} select={select}"
    )
    return next_sort_key


def verify_manifest(backup_dir: Path):
    conn = sqlite3.connect(backup_dir / "Manifest.db")
    cur = conn.cursor()
    cur.execute("PRAGMA integrity_check")
    check = cur.fetchone()[0]
    conn.close()
    if check != "ok":
        logger.error(f"Manifest.db integrity_check failed: {check}")
        raise RuntimeError(f"Manifest.db integrity_check failed: {check}")
    logger.debug("Manifest.db integrity_check: ok")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def convert(tendies_path: Path, backup_dir: Path, select: bool, dry_run: bool):
    logger.info(f"=== convert: {tendies_path} -> {backup_dir} (select={select}, dry_run={dry_run}) ===")

    if not (backup_dir / "Manifest.db").exists():
        logger.error(f"{backup_dir} has no Manifest.db")
        raise FileNotFoundError(f"{backup_dir} doesn't look like an iOS backup (no Manifest.db)")

    now = int(time.time())
    staging = Path(tempfile.gettempdir()) / f"tendies_staging_{uuid.uuid4().hex}"
    staging.mkdir()
    logger.debug(f"temporary working folder: {staging}")
    try:
        extract_root = extract_tendies(tendies_path, staging)

        descriptors = find_descriptors(extract_root)
        if not descriptors:
            logger.error(f"no 'descriptors/<UUID>' folder found in {tendies_path}")
            raise ValueError("No 'descriptors/<UUID>' folder found in this .tendies")

        logger.info(f"{len(descriptors)} descriptor(s) found in {tendies_path.name}")

        plan = []
        for d in descriptors:
            info = inspect_descriptor(d)
            plan.append((d, info))
            flavor = "gallery (suggestionMetadata)" if info["has_suggestion_metadata"] else "near-configuration"
            logger.info(
                f"  - {d.name}: shape={flavor}, provider={info['target_provider']}, "
                f"descriptor.identifier={info['descriptor_identifier']}, role={info['role_identifier']}"
            )

        if dry_run:
            logger.info("[dry-run] Nothing written. Re-run without --dry-run to apply.")
            return

        config_staging = staging / "configurations"
        config_staging.mkdir()

        for d, info in plan:
            config_dir, new_uuid = build_configuration(d, info, config_staging)
            base_relpath = inject_configuration(backup_dir, config_dir, new_uuid, info["target_provider"], now)
            sort_key = inject_registry(
                backup_dir, info["target_provider"], new_uuid, info["role_identifier"], select, now
            )
            logger.info(f"  -> injected under {base_relpath} (posterUUID={new_uuid}, roleSortKey={sort_key})")

        verify_manifest(backup_dir)
        logger.info("integrity_check OK on Manifest.db and on the PosterBoard registry.")
        logger.info("Next manual step: deploy this folder to MobileSync/Backup/<UDID> and start the restore.")
    finally:
        logger.debug(f"cleaning up temporary working folder {staging}")
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", type=Path, help=".tendies file to convert")
    parser.add_argument("backup_dir", type=Path, help="iOS backup folder (a copy, not the original)")
    parser.add_argument("--select", action="store_true", help="Immediately activate this poster (SELECTED=1)")
    parser.add_argument("--dry-run", action="store_true", help="Analyze only, write nothing")
    args = parser.parse_args()

    log_path = setup_logging("convert", sys.argv)
    try:
        convert(args.tendies, args.backup_dir, args.select, args.dry_run)
    except Exception:
        logger.exception("convert() failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
