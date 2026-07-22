#!/usr/bin/env python3
"""
Chaîne complète : greffe un ou plusieurs .tendies directement dans la
sauvegarde MobileSync réelle utilisée par Finder pour cet iPhone.

Usage:
    python3 deploy.py <fichier1.tendies> [fichier2.tendies ...] [--select] [--udid ...]

Étapes automatiques :
  1. archive l'ancienne sauvegarde MobileSync (renommage, aucune perte)
  2. copie cette archive vers l'emplacement MobileSync d'origine
  3. y greffe chaque .tendies (via convert.py)
  4. vérifie l'intégrité des deux bases
  5. si une étape échoue : restaure automatiquement l'archive à sa place

Ne déclenche PAS la restauration elle-même : ça reste une action que tu fais
toi-même dans Finder, une fois prêt.

Un journal détaillé de chaque exécution est écrit dans logs/ (voir
logsetup.py) : chemins, identifiants générés, chaque ligne insérée dans les
bases, et la trace complète en cas d'erreur.
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

DEFAULT_UDID = "00008150-000A22123C78C01C"


def candidate_mobilesync_bases():
    """Emplacements possibles du dossier de sauvegardes selon l'OS et le
    logiciel utilisé (macOS n'a qu'un seul emplacement ; Windows en a deux
    selon que c'est iTunes "classique" ou l'app Apple Devices/Microsoft Store)."""
    system = platform.system()
    home = Path.home()
    if system == "Darwin":
        return [home / "Library" / "Application Support" / "MobileSync" / "Backup"]
    if system == "Windows":
        return [
            home / "AppData" / "Roaming" / "Apple Computer" / "MobileSync" / "Backup",  # iTunes classique
            home / "Apple" / "MobileSync" / "Backup",  # Apple Devices / iTunes Microsoft Store
        ]
    raise RuntimeError(f"Système non supporté : {system!r} (seulement macOS et Windows)")


def find_backup_dir(udid: str) -> Path:
    candidates = candidate_mobilesync_bases()
    logger.debug(f"emplacements de sauvegarde vérifiés pour udid={udid} : {candidates}")
    for base in candidates:
        backup_dir = base / udid
        if (backup_dir / "Manifest.db").exists():
            logger.debug(f"sauvegarde trouvée : {backup_dir}")
            return backup_dir
    checked = "\n".join(f"  - {base / udid}" for base in candidates)
    logger.error(f"aucune sauvegarde trouvée pour udid={udid}")
    raise FileNotFoundError(
        f"Pas de sauvegarde Manifest.db trouvée pour {udid}. Emplacements vérifiés :\n{checked}"
    )


def _dir_stats(path: Path):
    """Compte fichiers/dossiers et taille totale (best-effort, pour le journal)."""
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
        logger.exception(f"impossible de calculer les statistiques de {path}")
        return None, None, None


def deploy(tendies_paths, select: bool, udid: str):
    logger.info(f"=== deploy: udid={udid} select={select} tendies={list(tendies_paths)} ===")

    backup_dir = find_backup_dir(udid)
    logger.debug(f"dossier MobileSync trouvé : {backup_dir}")

    n_files, n_dirs, total = _dir_stats(backup_dir)
    logger.debug(f"sauvegarde actuelle avant archivage : {n_files} fichiers, {n_dirs} dossiers, {total} octets")

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    archive_dir = backup_dir.parent / f"{udid}-{timestamp}"

    logger.info(f"[1/4] Archivage de la sauvegarde actuelle -> {archive_dir.name}")
    t0 = time.monotonic()
    backup_dir.rename(archive_dir)
    logger.debug(f"renommage {backup_dir} -> {archive_dir} effectué en {time.monotonic() - t0:.3f}s")

    try:
        logger.info("[2/4] Copie de travail vers l'emplacement MobileSync...")
        t0 = time.monotonic()
        shutil.copytree(archive_dir, backup_dir)
        logger.debug(f"copytree {archive_dir} -> {backup_dir} effectué en {time.monotonic() - t0:.1f}s")

        logger.info(f"[3/4] Greffe de {len(tendies_paths)} fichier(s) .tendies")
        for i, tendies_path in enumerate(tendies_paths, 1):
            logger.info(f"--- ({i}/{len(tendies_paths)}) {Path(tendies_path).name} ---")
            convert(Path(tendies_path), backup_dir, select=select, dry_run=False)

        logger.info("[4/4] Terminé.")
        logger.info(f"Sauvegarde prête : {backup_dir}")
        logger.info(f"Ancienne sauvegarde conservée : {archive_dir}")
        logger.info("Ouvre Finder et lance la restauration quand tu es prêt.")
    except Exception:
        logger.warning("échec pendant la greffe — restauration de l'état précédent en cours...")
        if backup_dir.exists():
            logger.debug(f"suppression de la copie de travail partielle {backup_dir}")
            shutil.rmtree(backup_dir)
        archive_dir.rename(backup_dir)
        logger.warning(f"rollback terminé : {archive_dir} -> {backup_dir}. Rien n'est perdu.")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tendies", nargs="+", help="Un ou plusieurs fichiers .tendies à greffer")
    parser.add_argument("--select", action="store_true", help="Active immédiatement le(s) poster(s) greffé(s)")
    parser.add_argument("--udid", default=DEFAULT_UDID, help="UDID de l'appareil (dossier MobileSync)")
    args = parser.parse_args()

    log_path = setup_logging("deploy", sys.argv)
    try:
        deploy(args.tendies, args.select, args.udid)
    except Exception:
        logger.exception("échec de deploy()")
        print(f"\n[!] Échec. Détails complets dans : {log_path}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
