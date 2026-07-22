#!/usr/bin/env python3
"""
Convertit un fichier .tendies (descripteur de galerie PosterBoard) en une ou
plusieurs configurations réelles, puis les injecte dans une sauvegarde iOS
non chiffrée (Manifest.db + blobs + registre SQLite central).

Usage:
    python3 convert.py <fichier.tendies> <dossier_backup> [--select] [--dry-run]

Le <dossier_backup> doit être une COPIE d'une sauvegarde iOS non chiffrée
(jamais l'originale, jamais le dossier MobileSync en direct). L'outil ne
touche jamais MobileSync ni l'iPhone : le déploiement vers MobileSync et le
lancement de la restauration restent des étapes manuelles séparées.

Un journal détaillé de chaque exécution est écrit dans logs/ (voir
logsetup.py) : chemins, identifiants générés, chaque ligne insérée dans les
bases, et la trace complète en cas d'erreur.
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

MODE_DIR = 16895   # 0o40777, confirmé par inspection d'une vraie sauvegarde
MODE_FILE = 33279  # 0o100777, confirmé par inspection d'une vraie sauvegarde


# ---------------------------------------------------------------------------
# Lecture d'un plist NSKeyedArchiver (format utilisé par suggestionMetadata.plist)
# ---------------------------------------------------------------------------

def _resolve(objects, ref, memo=None):
    """Résout récursivement les références UID d'un plist NSKeyedArchiver
    en une structure Python normale (dict/list/valeurs scalaires)."""
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
    logger.debug(f"lecture plist NSKeyedArchiver : {path}")
    with open(path, "rb") as f:
        raw = plistlib.load(f)
    objects = raw["$objects"]
    root_ref = raw["$top"]["root"]
    resolved = _resolve(objects, root_ref)
    logger.debug(f"plist résolu ({path.name}) : {resolved}")
    return resolved


# ---------------------------------------------------------------------------
# Détection de la "forme" d'un descripteur et dérivation des identifiants
# manquants. Deux familles observées :
#   - "galerie"   : a un suggestionMetadata.plist, mais pas les fichiers texte
#                   role.identifier / descriptor.identifier
#   - "quasi-config" : a déjà role.identifier / descriptor.identifier, pas de
#                   suggestionMetadata.plist
# Le rôle de cette fonction est d'absorber cette différence une fois pour
# toutes : peu importe la forme reçue, on ressort toujours les 3 informations
# nécessaires (role, descriptor id, provider cible).
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
        role_identifier = role_file.read_text(encoding="utf-8").strip()
        logger.debug(f"role.identifier lu directement dans le fichier : {role_identifier}")
    else:
        logger.debug("role.identifier absent du descripteur")

    if descriptor_id_file.exists():
        descriptor_identifier = descriptor_id_file.read_text(encoding="utf-8").strip()
        logger.debug(f"descriptor.identifier lu directement dans le fichier : {descriptor_identifier}")
    else:
        logger.debug("descriptor.identifier absent du descripteur")

    suggestion_data = None
    if suggestion_file.exists():
        logger.debug(f"suggestionMetadata.plist présent : {suggestion_file}")
        try:
            suggestion_data = read_keyed_archiver_plist(suggestion_file)
        except Exception:
            logger.exception(f"impossible de lire suggestionMetadata.plist ({suggestion_file}), ignoré")
    else:
        logger.debug("suggestionMetadata.plist absent du descripteur")

    if suggestion_data:
        item = suggestion_data.get("suggestedGalleryItem", {})
        if target_provider is None:
            target_provider = item.get("extensionBundleIdentifier")
            logger.debug(f"target_provider dérivé de suggestionMetadata : {target_provider}")
        if descriptor_identifier is None:
            raw_id = item.get("descriptorIdentifier")
            if raw_id:
                # "7400.DYNAMIC" -> "7400" : le suffixe ne sert qu'aux
                # suggestions, jamais vu dans le fichier texte réel.
                descriptor_identifier = raw_id.split(".")[0]
                logger.debug(f"descriptor.identifier dérivé de suggestionMetadata : {raw_id} -> {descriptor_identifier}")

    if descriptor_identifier is None:
        # dernier recours : dérivé du nom du dossier <id>.<nom>-<classe>.wallpaper
        logger.debug("descriptor.identifier toujours inconnu, tentative de dérivation depuis le nom du .wallpaper")
        for version_dir in (descriptor_dir / "versions").glob("*"):
            contents = version_dir / "contents"
            if not contents.is_dir():
                continue
            for wp in contents.glob("*.wallpaper"):
                descriptor_identifier = wp.name.split(".")[0]
                logger.debug(f"descriptor.identifier dérivé du nom de dossier {wp.name} -> {descriptor_identifier}")
                break
            if descriptor_identifier:
                break

    if role_identifier is None:
        role_identifier = DEFAULT_ROLE
        logger.debug(f"role_identifier absent partout, valeur par défaut appliquée : {DEFAULT_ROLE}")

    if target_provider is None:
        target_provider = DEFAULT_PROVIDER
        logger.debug(f"target_provider absent partout, valeur par défaut appliquée : {DEFAULT_PROVIDER}")

    if descriptor_identifier is None:
        logger.error(f"descriptor.identifier introuvable et indérivable pour {descriptor_dir}")
        raise ValueError(f"Impossible de déterminer descriptor.identifier pour {descriptor_dir}")

    result = {
        "role_identifier": role_identifier,
        "descriptor_identifier": descriptor_identifier,
        "target_provider": target_provider,
        "has_suggestion_metadata": suggestion_file.exists(),
    }
    logger.debug(f"résultat inspect_descriptor({descriptor_dir.name}) = {result}")
    return result


def find_descriptors(extract_root: Path):
    """Trouve tous les dossiers descriptors/<UUID> dans le zip extrait."""
    result = []
    descriptors_dirs = list(extract_root.glob("**/descriptors"))
    logger.debug(f"dossiers 'descriptors' trouvés dans le zip extrait : {descriptors_dirs}")
    for descriptors_dir in descriptors_dirs:
        for child in sorted(descriptors_dir.iterdir()):
            if child.is_dir():
                result.append(child)
    logger.debug(f"total descripteurs trouvés : {len(result)} -> {[d.name for d in result]}")
    return result


# ---------------------------------------------------------------------------
# Construction d'une configuration à partir d'un descripteur
# ---------------------------------------------------------------------------

def build_configuration(descriptor_dir: Path, info: dict, staging_root: Path) -> Path:
    new_uuid = str(uuid.uuid4()).upper()
    config_dir = staging_root / new_uuid
    logger.debug(f"copie {descriptor_dir} -> {config_dir} (nouveau posterUUID={new_uuid})")
    shutil.copytree(descriptor_dir, config_dir)

    (config_dir / "com.apple.posterkit.role.identifier").write_text(info["role_identifier"], encoding="utf-8")
    (config_dir / "com.apple.posterkit.provider.descriptor.identifier").write_text(
        info["descriptor_identifier"], encoding="utf-8"
    )
    logger.debug(
        f"role.identifier='{info['role_identifier']}' et "
        f"descriptor.identifier='{info['descriptor_identifier']}' écrits dans {config_dir}"
    )

    return config_dir, new_uuid


# ---------------------------------------------------------------------------
# Construction des blobs MBFile (format NSKeyedArchiver utilisé par Manifest.db)
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
# Injection dans la sauvegarde
# ---------------------------------------------------------------------------

def ensure_ancestor_dirs(cur, now, seen, provider):
    """S'assure que les dossiers Extensions/<provider> et .../configurations
    existent dans Manifest.db (utile si le provider n'a encore aucune config)."""
    for relpath in (
        f"{EXT_BASE}/{provider}",
        f"{EXT_BASE}/{provider}/configurations",
    ):
        if relpath in seen:
            continue
        fid = file_id_for(relpath)
        cur.execute("SELECT 1 FROM Files WHERE fileID=?", (fid,))
        if cur.fetchone():
            logger.debug(f"dossier ancêtre déjà présent dans Manifest.db : {relpath}")
            seen.add(relpath)
            continue
        blob = make_mbfile_blob(relpath, True, 0, now)
        cur.execute(
            "INSERT INTO Files (fileID, domain, relativePath, flags, file) VALUES (?,?,?,2,?)",
            (fid, DOMAIN, relpath, blob),
        )
        logger.info(f"dossier ancêtre créé dans Manifest.db : {relpath} (fileID={fid})")
        seen.add(relpath)


def inject_configuration(backup_dir: Path, config_dir: Path, new_uuid: str, provider: str, now: int):
    manifest_path = backup_dir / "Manifest.db"
    logger.debug(f"ouverture de {manifest_path}")
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
    logger.debug(f"{len(entries)} entrées (dossiers+fichiers) à insérer sous {base_relpath}")

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
        f"Manifest.db : {n_dirs} dossier(s) + {n_files} fichier(s) insérés "
        f"({total_bytes} octets) sous {base_relpath}"
    )
    return base_relpath


def inject_registry(backup_dir: Path, provider: str, new_uuid: str, role: str, select: bool, now: int):
    fid = file_id_for(REGISTRY_RELPATH)
    registry_path = backup_dir / fid[:2] / fid
    logger.debug(f"registre attendu à {registry_path} (fileID={fid})")
    if not registry_path.exists():
        logger.error(f"registre SQLite introuvable à {registry_path}")
        raise FileNotFoundError(
            f"Registre SQLite introuvable à {registry_path} — la sauvegarde ne contient "
            "pas d'historique PosterBoard, ou le chemin a changé."
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

    apple_epoch_now = now - 978307200  # conversion epoch Unix -> epoch Apple/Cocoa (2001-01-01)
    usage_payload = (
        '{"creationDate":%f,"lastModifiedDate":%f,"extensionAvailable":true,'
        '"attributeType":"PRPosterRoleAttributeTypeUsageMetadata"}' % (apple_epoch_now, apple_epoch_now)
    )
    cur.execute(
        "INSERT INTO posterAttributes (posterUUID, roleId, attributeIdentifier, attributePayload) VALUES (?,?,?,?)",
        (new_uuid, role, "PRPosterRoleAttributeTypeUsageMetadata", usage_payload),
    )
    logger.debug(f"INSERT posterAttributes UsageMetadata pour {new_uuid} : {usage_payload}")

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
            f"SELECTED bascule vers {new_uuid} pour le rôle {role} "
            f"(précédemment sélectionné : {previously_selected or 'aucun'})"
        )

    conn.commit()
    cur.execute("PRAGMA integrity_check")
    check = cur.fetchone()[0]
    conn.close()
    if check != "ok":
        logger.error(f"integrity_check du registre a échoué : {check}")
        raise RuntimeError(f"integrity_check du registre a échoué : {check}")
    logger.debug("integrity_check du registre : ok")

    # Rafraîchit la taille/horodatage du blob du registre dans Manifest.db
    size = registry_path.stat().st_size
    blob = make_mbfile_blob(REGISTRY_RELPATH, False, size, now)
    manifest_conn = sqlite3.connect(backup_dir / "Manifest.db")
    manifest_conn.execute("UPDATE Files SET file=? WHERE fileID=?", (blob, fid))
    manifest_conn.commit()
    manifest_conn.close()
    logger.debug(f"blob Manifest.db du registre rafraîchi (size={size})")

    logger.info(
        f"Registre : poster={new_uuid} provider={provider} role={role} "
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
        logger.error(f"integrity_check de Manifest.db a échoué : {check}")
        raise RuntimeError(f"integrity_check de Manifest.db a échoué : {check}")
    logger.debug("integrity_check de Manifest.db : ok")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def convert(tendies_path: Path, backup_dir: Path, select: bool, dry_run: bool):
    logger.info(f"=== convert: {tendies_path} -> {backup_dir} (select={select}, dry_run={dry_run}) ===")

    if not (backup_dir / "Manifest.db").exists():
        logger.error(f"{backup_dir} ne contient pas de Manifest.db")
        raise FileNotFoundError(f"{backup_dir} ne ressemble pas à une sauvegarde iOS (pas de Manifest.db)")

    now = int(time.time())
    staging = Path(tempfile.gettempdir()) / f"tendies_staging_{uuid.uuid4().hex}"
    staging.mkdir()
    logger.debug(f"dossier de travail temporaire : {staging}")
    try:
        with zipfile.ZipFile(tendies_path) as z:
            all_names = z.namelist()
            names = [n for n in all_names if "__MACOSX" not in n and not n.endswith(".DS_Store")]
            logger.debug(
                f"{tendies_path.name} : {len(all_names)} entrées dans le zip, "
                f"{len(names)} retenues après filtrage __MACOSX/.DS_Store"
            )
            extract_root = staging / "extracted"
            extract_root.mkdir()
            z.extractall(extract_root, members=names)
        logger.debug(f"extraction terminée dans {extract_root}")

        descriptors = find_descriptors(extract_root)
        if not descriptors:
            logger.error(f"aucun dossier 'descriptors/<UUID>' trouvé dans {tendies_path}")
            raise ValueError("Aucun dossier 'descriptors/<UUID>' trouvé dans ce .tendies")

        logger.info(f"{len(descriptors)} descripteur(s) trouvé(s) dans {tendies_path.name}")

        plan = []
        for d in descriptors:
            info = inspect_descriptor(d)
            plan.append((d, info))
            flavor = "galerie (suggestionMetadata)" if info["has_suggestion_metadata"] else "quasi-configuration"
            logger.info(
                f"  - {d.name}: forme={flavor}, provider={info['target_provider']}, "
                f"descriptor.identifier={info['descriptor_identifier']}, role={info['role_identifier']}"
            )

        if dry_run:
            logger.info("[dry-run] Aucune écriture effectuée. Relance sans --dry-run pour appliquer.")
            return

        config_staging = staging / "configurations"
        config_staging.mkdir()

        for d, info in plan:
            config_dir, new_uuid = build_configuration(d, info, config_staging)
            base_relpath = inject_configuration(backup_dir, config_dir, new_uuid, info["target_provider"], now)
            sort_key = inject_registry(
                backup_dir, info["target_provider"], new_uuid, info["role_identifier"], select, now
            )
            logger.info(f"  -> injecté sous {base_relpath} (posterUUID={new_uuid}, roleSortKey={sort_key})")

        verify_manifest(backup_dir)
        logger.info("integrity_check OK sur Manifest.db et sur le registre PosterBoard.")
        logger.info("Prochaine étape manuelle : déployer ce dossier vers MobileSync/Backup/<UDID> et lancer la restauration.")
    finally:
        logger.debug(f"nettoyage du dossier de travail temporaire {staging}")
        shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", type=Path, help="Fichier .tendies à convertir")
    parser.add_argument("backup_dir", type=Path, help="Dossier de sauvegarde iOS (copie, pas l'original)")
    parser.add_argument("--select", action="store_true", help="Active immédiatement ce poster (SELECTED=1)")
    parser.add_argument("--dry-run", action="store_true", help="Analyse seulement, n'écrit rien")
    args = parser.parse_args()

    log_path = setup_logging("convert", sys.argv)
    try:
        convert(args.tendies, args.backup_dir, args.select, args.dry_run)
    except Exception:
        logger.exception("échec de convert()")
        print(f"\n[!] Échec. Détails complets dans : {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
