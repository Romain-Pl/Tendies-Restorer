"""Traductions FR/EN pour gui.py. Les autres scripts (convert.py, deploy.py,
combine.py, appearance.py, sim_deploy.py) restent en français dans leurs
messages internes/logs ; seule l'interface graphique est bilingue."""
import os

STRINGS = {
    "app.title": {"fr": "Tendies Toolbox", "en": "Tendies Toolbox"},

    "lang.french": {"fr": "Français", "en": "Français"},
    "lang.english": {"fr": "English", "en": "English"},

    "tab.combine": {"fr": "Combiner variantes", "en": "Combine variants"},
    "tab.appearance": {"fr": "AppearanceAware", "en": "AppearanceAware"},
    "tab.backup": {"fr": "Injecter → Backup iPhone", "en": "Inject → iPhone Backup"},
    "tab.simulator": {"fr": "Injecter → Simulateur Xcode", "en": "Inject → Xcode Simulator"},
    "tab.dump": {"fr": "Extraire un poster", "en": "Dump a poster"},

    "common.add": {"fr": "Ajouter…", "en": "Add…"},
    "common.remove_selection": {"fr": "Retirer la sélection", "en": "Remove selection"},
    "common.clear": {"fr": "Vider", "en": "Clear"},
    "common.choose": {"fr": "Choisir…", "en": "Choose…"},
    "common.refresh": {"fr": "Actualiser", "en": "Refresh"},
    "common.browse": {"fr": "Parcourir…", "en": "Browse…"},
    "common.filetype_tendies": {"fr": "Fichiers .tendies", "en": ".tendies files"},
    "common.filetype_all": {"fr": "Tous les fichiers", "en": "All files"},
    "common.dialog.pick_tendies": {"fr": "Choisir un ou plusieurs .tendies", "en": "Choose one or more .tendies files"},

    "log.title": {"fr": "Journal", "en": "Log"},
    "log.open_folder": {"fr": "Ouvrir le dossier des journaux", "en": "Open logs folder"},
    "log.session_path": {"fr": "Journal détaillé de cette session : {path}", "en": "Detailed log for this session: {path}"},
    "log.unhandled_error": {"fr": "exception non gérée dans l'interface", "en": "unhandled exception in the interface"},
    "log.unexpected_title": {"fr": "Erreur inattendue", "en": "Unexpected error"},
    "log.failed_title": {"fr": "Échec", "en": "Failed"},

    "combine.label.variants": {
        "fr": "Variantes à combiner (l'ordre de la liste = l'ordre de swipe) :",
        "en": "Variants to combine (list order = swipe order):",
    },
    "combine.col.num": {"fr": "#", "en": "#"},
    "combine.col.source": {"fr": "Fichier source", "en": "Source file"},
    "combine.col.descriptor": {"fr": "Descripteur", "en": "Descriptor"},
    "combine.col.name": {"fr": "Nom affiché", "en": "Display name"},
    "combine.btn.up": {"fr": "Monter", "en": "Move up"},
    "combine.btn.down": {"fr": "Descendre", "en": "Move down"},
    "combine.btn.rename": {"fr": "Renommer la variante…", "en": "Rename variant…"},
    "combine.btn.remove": {"fr": "Retirer", "en": "Remove"},
    "combine.label.family": {
        "fr": "Nom de family (partagé par toutes les variantes) :",
        "en": "Family name (shared by all variants):",
    },
    "combine.label.output": {"fr": "Fichier .tendies de sortie :", "en": "Output .tendies file:"},
    "combine.btn.combine": {"fr": "Combiner", "en": "Combine"},
    "combine.dialog.save_title": {"fr": "Enregistrer le .tendies combiné", "en": "Save the combined .tendies"},
    "combine.err.no_variant": {"fr": "Ajoute au moins une variante.", "en": "Add at least one variant."},
    "combine.err.empty_family": {"fr": "Le nom de family ne peut pas être vide.", "en": "The family name can't be empty."},
    "combine.err.no_output": {"fr": "Choisis un fichier de sortie.", "en": "Choose an output file."},
    "combine.success": {
        "fr": "{n} descripteur(s) combiné(s) sous family={family!r}.\nÉcrit : {output}",
        "en": "{n} descriptor(s) combined under family={family!r}.\nWritten: {output}",
    },
    "combine.rename.title": {"fr": "Renommer la variante", "en": "Rename variant"},
    "combine.rename.prompt": {
        "fr": "Nom affiché dans le sélecteur PosterBoard :",
        "en": "Display name in the PosterBoard picker:",
    },
    "combine.rename.select_first": {
        "fr": "Sélectionne d'abord une variante dans la liste.",
        "en": "Select a variant in the list first.",
    },
    "combine.err.add_title": {"fr": "Ajouter", "en": "Add"},

    "appearance.label.input": {"fr": "Fichier .tendies :", "en": ".tendies file:"},
    "appearance.radio.on": {"fr": "Activer appearanceAware", "en": "Enable appearanceAware"},
    "appearance.radio.off": {"fr": "Désactiver appearanceAware", "en": "Disable appearanceAware"},
    "appearance.label.output": {"fr": "Fichier de sortie :", "en": "Output file:"},
    "appearance.btn.apply": {"fr": "Appliquer", "en": "Apply"},
    "appearance.dialog.input_title": {"fr": "Choisir un .tendies", "en": "Choose a .tendies file"},
    "appearance.dialog.save_title": {"fr": "Enregistrer sous", "en": "Save as"},
    "appearance.err.no_input": {"fr": "Choisis un fichier .tendies d'entrée.", "en": "Choose an input .tendies file."},
    "appearance.err.no_output": {"fr": "Choisis un fichier de sortie.", "en": "Choose an output file."},
    "appearance.success": {"fr": "Écrit : {output}\n\n{detail}", "en": "Written: {output}\n\n{detail}"},

    "backup.label.files": {"fr": "Fichiers .tendies à greffer :", "en": "Files to graft:"},
    "backup.label.target": {"fr": "Sauvegarde iPhone cible :", "en": "Target iPhone backup:"},
    "backup.checkbox.select": {
        "fr": "Activer immédiatement ce poster (SELECTED)",
        "en": "Activate this poster immediately (SELECTED)",
    },
    "backup.btn.deploy": {"fr": "Greffer dans la sauvegarde", "en": "Graft into backup"},
    "backup.dialog.browse_title": {"fr": "Choisir un dossier de sauvegarde iOS", "en": "Choose an iOS backup folder"},
    "backup.err.not_valid": {
        "fr": "{path} ne contient pas de Manifest.db : ce n'est pas une sauvegarde iOS valide.",
        "en": "{path} doesn't contain Manifest.db: not a valid iOS backup.",
    },
    "backup.err.no_files": {"fr": "Ajoute au moins un fichier .tendies.", "en": "Add at least one .tendies file."},
    "backup.err.no_target": {"fr": "Choisis une sauvegarde cible dans la liste.", "en": "Choose a target backup from the list."},
    "backup.confirm.title": {"fr": "Confirmer", "en": "Confirm"},
    "backup.confirm.message": {
        "fr": "Greffer {n} fichier(s) dans :\n{dir}\n\nLa sauvegarde actuelle sera d'abord archivée à côté "
              "(rien n'est perdu, restauration automatique en cas d'échec). Continuer ?",
        "en": "Graft {n} file(s) into:\n{dir}\n\nThe current backup will be archived alongside first "
              "(nothing is lost, automatic rollback on failure). Continue?",
    },
    "backup.success": {
        "fr": "Greffe terminée dans :\n{dir}\n\nOuvre Finder pour lancer la restauration quand tu es prêt.",
        "en": "Graft complete in:\n{dir}\n\nOpen Finder to start the restore when you're ready.",
    },
    "backup.dialog.title": {"fr": "Backup", "en": "Backup"},
    "backup.dialog.backups_title": {"fr": "Sauvegardes", "en": "Backups"},

    "simulator.label.files": {"fr": "Fichiers .tendies à injecter :", "en": "Files to inject:"},
    "simulator.label.target": {"fr": "Simulateur cible :", "en": "Target simulator:"},
    "simulator.btn.refresh_list": {"fr": "Actualiser la liste", "en": "Refresh list"},
    "simulator.checkbox.select": {"fr": "Activer immédiatement (SELECTED)", "en": "Activate immediately (SELECTED)"},
    "simulator.checkbox.respring": {"fr": "Respring après injection", "en": "Respring after injection"},
    "simulator.btn.inject": {"fr": "Injecter dans le simulateur", "en": "Inject into simulator"},
    "simulator.tools.title": {"fr": "Outils simulateur (trial & error rapide)", "en": "Simulator tools (fast trial & error)"},
    "simulator.tools.respring": {"fr": "Respring", "en": "Respring"},
    "simulator.tools.light": {"fr": "Mode clair", "en": "Light mode"},
    "simulator.tools.dark": {"fr": "Mode sombre", "en": "Dark mode"},
    "simulator.tools.screenshot": {"fr": "Capture d'écran…", "en": "Screenshot…"},
    "simulator.err.no_files": {"fr": "Ajoute au moins un fichier .tendies.", "en": "Add at least one .tendies file."},
    "simulator.err.no_target": {"fr": "Choisis un simulateur dans la liste.", "en": "Choose a simulator from the list."},
    "simulator.success": {"fr": "Injection terminée.", "en": "Injection complete."},
    "simulator.success.respring_suffix": {"fr": " SpringBoard relancé.", "en": " SpringBoard restarted."},
    "simulator.dialog.title": {"fr": "Simulateur", "en": "Simulator"},
    "simulator.dialog.screenshot_save_title": {"fr": "Enregistrer la capture d'écran", "en": "Save screenshot"},
    "simulator.dialog.screenshot_done_title": {"fr": "Capture d'écran", "en": "Screenshot"},
    "simulator.dialog.screenshot_done": {"fr": "Enregistrée : {path}", "en": "Saved: {path}"},

    "dump.label.backup": {"fr": "Sauvegarde source :", "en": "Source backup:"},
    "dump.label.simulator": {"fr": "Simulateur source :", "en": "Source simulator:"},
    "dump.btn.list_posters": {"fr": "Lister les posters", "en": "List posters"},
    "dump.label.posters": {"fr": "Posters disponibles (sélectionne-en un) :", "en": "Available posters (select one):"},
    "dump.col.uuid": {"fr": "UUID", "en": "UUID"},
    "dump.col.provider": {"fr": "Provider", "en": "Provider"},
    "dump.col.descriptor": {"fr": "Descripteur", "en": "Descriptor"},
    "dump.col.role": {"fr": "Rôle", "en": "Role"},
    "dump.col.selected": {"fr": "Actif", "en": "Active"},
    "dump.yes": {"fr": "Oui", "en": "Yes"},
    "dump.no": {"fr": "Non", "en": "No"},
    "dump.label.output": {"fr": "Fichier .tendies de sortie :", "en": "Output .tendies file:"},
    "dump.btn.dump": {"fr": "Extraire", "en": "Dump"},
    "dump.dialog.title": {"fr": "Extraction", "en": "Dump"},
    "dump.dialog.save_title": {"fr": "Enregistrer le poster extrait", "en": "Save the extracted poster"},
    "dump.err.no_source": {
        "fr": "Sélectionne une sauvegarde OU un simulateur source, puis clique sur Lister les posters.",
        "en": "Select a source backup OR simulator, then click List posters.",
    },
    "dump.err.no_selection": {"fr": "Sélectionne un poster dans la liste.", "en": "Select a poster from the list."},
    "dump.err.no_output": {"fr": "Choisis un fichier de sortie.", "en": "Choose an output file."},
    "dump.success": {"fr": "Écrit : {output}", "en": "Written: {output}"},
}


def _detect_default_language() -> str:
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var)
        if val:
            return "fr" if val.lower().startswith("fr") else "en"
    return "en"


class Translator:
    def __init__(self, lang: str = None):
        self.lang = lang or _detect_default_language()

    def t(self, key: str, **kwargs) -> str:
        entry = STRINGS.get(key)
        if entry is None:
            return key
        text = entry.get(self.lang, entry.get("en", key))
        return text.format(**kwargs) if kwargs else text
