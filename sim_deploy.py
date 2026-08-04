#!/usr/bin/env python3
"""
Injects one or more .tendies straight into an Xcode Simulator's PosterBoard
container — no backup, no restore — for fast trial & error instead of
wiping a real iPhone.

Usage:
    python3 sim_deploy.py --list-simulators
    python3 sim_deploy.py file1.tendies [file2.tendies ...] --udid <SIM_UDID> [--select] [--respring]

Unlike deploy.py (a real backup), a simulator's container is a plain folder
on the Mac: no Manifest.db, no fileID-addressed blobs. The SQLite registry
(same schema as on a real device) is edited directly. Boot the simulator at
least once before injecting, so PosterBoard has initialized its registry
with the system's default posters — otherwise a minimal registry is
created, less faithful to a real device.

/!\\ Never run "xcrun simctl boot" followed by a full reboot
("launchctl reboot userspace") after injecting: that regenerates the
registry from scratch and wipes the added entries (the files copied to disk
survive). To refresh the display after injecting, use --respring instead
(a simple SpringBoard restart, without touching the registry).
"""
import argparse
import json
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid as uuidlib
from pathlib import Path

from convert import extract_tendies, find_descriptors, inspect_descriptor
from logsetup import get_logger, setup_logging

logger = get_logger()

SIMULATOR_DEVICES_ROOT = Path.home() / "Library" / "Developer" / "CoreSimulator" / "Devices"
POSTER_SUBPATH = "Library/Application Support/PRBPosterExtensionDataStore/61"
REGISTRY_NAME = "PBFPosterExtensionDataStoreSQLiteDatabase.sqlite3"

_REGISTRY_SCHEMA = """
CREATE TABLE "poster" ("posterId" INTEGER PRIMARY KEY AUTOINCREMENT, "UUID" TEXT UNIQUE ON CONFLICT ROLLBACK, "providerId" TEXT NOT NULL ON CONFLICT ROLLBACK);
CREATE TABLE "posterRoles" ("roleIdentifier" TEXT PRIMARY KEY ON CONFLICT ROLLBACK UNIQUE ON CONFLICT ROLLBACK NOT NULL ON CONFLICT ROLLBACK, "displayName" NOT NULL ON CONFLICT ROLLBACK UNIQUE ON CONFLICT ROLLBACK);
CREATE TABLE "posterRoleMembership" ("posterUUID" TEXT NOT NULL ON CONFLICT ROLLBACK, "roleId" TEXT NOT NULL ON CONFLICT ROLLBACK, "roleSortKey" INTEGER NOT NULL, CONSTRAINT posters FOREIGN KEY (posterUUID) REFERENCES poster(UUID) ON DELETE CASCADE, CONSTRAINT roles FOREIGN KEY (roleId) REFERENCES posterRoles(roleIdentifier) ON DELETE CASCADE);
CREATE TABLE "posterMetadata" ("key" TEXT NOT NULL ON CONFLICT ROLLBACK UNIQUE ON CONFLICT ROLLBACK PRIMARY KEY, "value" TEXT NOT NULL ON CONFLICT ROLLBACK);
CREATE TABLE "posterAttributes" ("posterUUID" TEXT NOT NULL ON CONFLICT ROLLBACK, "roleId" TEXT NOT NULL ON CONFLICT ROLLBACK, "attributeIdentifier" TEXT NOT NULL ON CONFLICT ROLLBACK, "attributePayload" TEXT NOT NULL ON CONFLICT ROLLBACK, CONSTRAINT posters FOREIGN KEY (posterUUID) REFERENCES poster(UUID) ON DELETE CASCADE, CONSTRAINT roles FOREIGN KEY (roleId) REFERENCES posterRoles(roleIdentifier) ON DELETE CASCADE, UNIQUE (posterUUID, roleID, attributeIdentifier));
INSERT INTO posterRoles VALUES ('PRPosterRoleLockScreen','Lock Screen');
INSERT INTO posterRoles VALUES ('PRPosterRoleAmbient','PRPosterRoleAmbient');
INSERT INTO posterMetadata VALUES ('version','2');
INSERT INTO posterMetadata VALUES ('deviceClass','0');
"""


def _simctl(*args, timeout=60):
    logger.debug(f"xcrun simctl {' '.join(args)}")
    result = subprocess.run(["xcrun", "simctl", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"xcrun simctl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def list_simulators():
    """Lists every known simulator (xcrun simctl list devices), with its
    state. Simulators that were never booted don't have a 'data' folder yet:
    they still show up, injection will create whatever's needed."""
    raw = _simctl("list", "devices", "--json")
    data = json.loads(raw)
    result = []
    for runtime, devices in data.get("devices", {}).items():
        for dev in devices:
            result.append({
                "udid": dev["udid"],
                "name": dev["name"],
                "state": dev["state"],
                "runtime": runtime,
            })
    result.sort(key=lambda d: (d["state"] != "Booted", d["runtime"], d["name"]))
    return result


def poster_root_for(udid: str) -> Path:
    data_dir = SIMULATOR_DEVICES_ROOT / udid / "data"
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"Data folder not found for {udid} ({data_dir}). "
            "Boot this simulator at least once first (xcrun simctl boot)."
        )
    return data_dir / POSTER_SUBPATH


def ensure_registry(poster_root: Path) -> Path:
    """Returns the path to the simulator's SQLite registry. If it doesn't
    exist yet (simulator never used by PosterBoard), creates a minimal one —
    but a registry already initialized by the system (default posters
    included) is always preferable: boot the simulator and let it run for a
    few seconds before injecting."""
    registry_path = poster_root / REGISTRY_NAME
    if registry_path.exists():
        return registry_path
    logger.warning(f"no registry at {registry_path} — creating a minimal one")
    poster_root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(registry_path)
    conn.executescript(_REGISTRY_SCHEMA)
    conn.commit()
    conn.close()
    return registry_path


def inject_into_simulator(tendies_path: Path, udid: str, select: bool = False):
    """Copies every descriptor from the .tendies into the simulator's
    container and adds the matching registry rows. Returns the list of
    injected posters (descriptor_name, posterUUID, provider, role)."""
    poster_root = poster_root_for(udid)
    registry_path = ensure_registry(poster_root)

    staging = Path(tempfile.mkdtemp())
    try:
        extract_root = extract_tendies(tendies_path, staging)
        descriptors = find_descriptors(extract_root)
        if not descriptors:
            raise ValueError(f"No 'descriptors/<UUID>' folder found in {tendies_path}")

        conn = sqlite3.connect(registry_path)
        cur = conn.cursor()
        now = int(time.time())
        apple_epoch_now = now - 978307200  # Unix epoch -> Apple/Cocoa epoch (2001-01-01)
        results = []
        try:
            for d in descriptors:
                info = inspect_descriptor(d)
                new_uuid = str(uuidlib.uuid4()).upper()
                provider = info["target_provider"]
                role = info["role_identifier"]

                config_dir = poster_root / "Extensions" / provider / "configurations" / new_uuid
                config_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(d, config_dir)
                (config_dir / "com.apple.posterkit.role.identifier").write_text(role, encoding="utf-8")
                (config_dir / "com.apple.posterkit.provider.descriptor.identifier").write_text(
                    info["descriptor_identifier"], encoding="utf-8"
                )

                cur.execute("INSERT INTO poster (UUID, providerId) VALUES (?, ?)", (new_uuid, provider))
                cur.execute(
                    "SELECT COALESCE(MAX(roleSortKey), 0) FROM posterRoleMembership WHERE roleId=?", (role,)
                )
                sort_key = cur.fetchone()[0] + 1
                cur.execute(
                    "INSERT INTO posterRoleMembership (posterUUID, roleId, roleSortKey) VALUES (?,?,?)",
                    (new_uuid, role, sort_key),
                )
                payload = (
                    '{"creationDate":%f,"lastModifiedDate":%f,"extensionAvailable":true,'
                    '"attributeType":"PRPosterRoleAttributeTypeUsageMetadata"}' % (apple_epoch_now, apple_epoch_now)
                )
                cur.execute(
                    "INSERT INTO posterAttributes (posterUUID, roleId, attributeIdentifier, attributePayload) "
                    "VALUES (?,?,?,?)",
                    (new_uuid, role, "PRPosterRoleAttributeTypeUsageMetadata", payload),
                )
                if select:
                    cur.execute(
                        "UPDATE posterAttributes SET attributePayload='0' "
                        "WHERE roleId=? AND attributeIdentifier='SELECTED'",
                        (role,),
                    )
                    cur.execute(
                        "INSERT INTO posterAttributes (posterUUID, roleId, attributeIdentifier, attributePayload) "
                        "VALUES (?,?,?,?)",
                        (new_uuid, role, "SELECTED", "1"),
                    )
                logger.info(
                    f"  -> {d.name} injected under {config_dir} "
                    f"(posterUUID={new_uuid}, provider={provider}, role={role}, roleSortKey={sort_key})"
                )
                results.append((d.name, new_uuid, provider, role))

            conn.commit()
            cur.execute("PRAGMA integrity_check")
            check = cur.fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"registry integrity_check failed: {check}")
        finally:
            conn.close()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    logger.info(f"{len(results)} poster(s) injected into simulator {udid} from {tendies_path.name}")
    return results


def respring(udid: str):
    """Restarts SpringBoard (without resetting anything) so the injection
    takes effect immediately."""
    _simctl("spawn", udid, "launchctl", "kickstart", "-k", "system/com.apple.SpringBoard")
    logger.info(f"SpringBoard restarted on {udid}")


def set_appearance(udid: str, mode: str):
    """Switches Light/Dark mode in one command (equivalent of Features >
    Toggle Appearance in Simulator.app)."""
    if mode not in ("light", "dark"):
        raise ValueError("mode must be 'light' or 'dark'")
    _simctl("ui", udid, "appearance", mode)
    logger.info(f"system appearance -> {mode} on {udid}")


def take_screenshot(udid: str, output_path: Path):
    _simctl("io", udid, "screenshot", str(output_path))
    logger.info(f"screenshot -> {output_path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", nargs="*", type=Path, help="One or more .tendies files to inject")
    parser.add_argument("--udid", help="Target simulator UDID (see --list-simulators)")
    parser.add_argument("--select", action="store_true", help="Immediately activate the injected poster(s)")
    parser.add_argument("--respring", action="store_true", help="Restart SpringBoard after injecting")
    parser.add_argument("--list-simulators", action="store_true", help="List available simulators and exit")
    args = parser.parse_args()

    log_path = setup_logging("sim_deploy", sys.argv)
    try:
        if args.list_simulators:
            for sim in list_simulators():
                print(f"{sim['udid']}  {sim['state']:10s} {sim['name']} ({sim['runtime']})")
            return

        if not args.tendies or not args.udid:
            parser.error("at least one .tendies and --udid are required (or --list-simulators alone)")

        for tendies_path in args.tendies:
            logger.info(f"--- {tendies_path.name} -> simulator {args.udid} ---")
            inject_into_simulator(tendies_path, args.udid, select=args.select)

        if args.respring:
            respring(args.udid)

        logger.info("Done.")
    except Exception:
        logger.exception("sim_deploy failed")
        print(f"\n[!] Failed. Full details in: {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
