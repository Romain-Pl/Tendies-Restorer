# tendies-converter

Convertit un fichier `.tendies` (paquet de fond d'écran communautaire, conçu pour la galerie « Ajouter un fond d'écran ») en une configuration **PosterBoard** réelle et fonctionnelle, puis l'injecte dans une sauvegarde iOS non chiffrée pour qu'il apparaisse sur l'appareil après une restauration complète.

Aucun jailbreak, aucun exploit : uniquement le mécanisme de sauvegarde/restauration standard d'iOS (celui que Finder utilise pour restaurer un iPhone), avec un contenu personnalisé à l'intérieur.

## Pourquoi ça existe

Des outils comme [Nugget](https://github.com/leminlimez/Nugget) permettaient d'injecter ce genre de contenu via une **restauration partielle** détournée (SparseRestore, puis BookRestore) — des primitives internes d'iOS jamais destinées à un usage tiers. Apple a fermé ces deux portes ; sur iOS 27, toute tentative déclenche une réinitialisation d'usine complète.

Ce projet contourne le problème autrement : en écrivant directement dans le contenu d'une **sauvegarde complète classique**, le mécanisme que n'importe qui utilise en restaurant un iPhone depuis Finder. Rien n'est détourné — le format de sauvegarde ne vérifie simplement pas le contenu des fichiers qu'il restaure (pas de somme de contrôle), ce qui laisse la porte ouverte à y déposer ce qu'on veut, tant que la structure attendue est respectée.

**Contrepartie** : contrairement à une restauration partielle, ça implique d'effacer et de reconfigurer tout l'appareil à chaque ajout. Pas de solution miracle, juste un chemin qui fonctionne encore.

## Comment ça marche

PosterBoard (le moteur de fond d'écran animé derrière la galerie « Collections ») stocke chaque fond d'écran à **deux endroits** qui doivent être écrits ensemble :

1. les fichiers de configuration, sous `Library/Application Support/PRBPosterExtensionDataStore/61/Extensions/<provider>/configurations/<UUID>/`
2. une entrée dans le registre SQLite central (`PBFPosterExtensionDataStoreSQLiteDatabase.sqlite3`, tables `poster` / `posterRoleMembership` / `posterAttributes`)

Sans la seconde, les fichiers existent bien sur l'appareil mais le fond d'écran reste invisible — PosterBoard ne consulte jamais le disque directement, seulement son registre.

Les `.tendies` de la communauté ne sont pas structurés comme une vraie configuration (ils manquent certains fichiers, ou en ont en trop selon l'outil qui les a produits). `convert.py` détecte automatiquement la forme reçue et complète ce qui manque.

## Prérequis

- **macOS** (via Finder) ou **Windows** (via iTunes ou l'app Apple Devices) avec Python 3.8+ (rien à installer, seulement la bibliothèque standard)
- Une sauvegarde locale **non chiffrée** de l'appareil
- Un `.tendies` à convertir

Sur Windows, ferme iTunes / l'app Apple Devices avant de lancer `deploy.py` — Windows verrouille les fichiers ouverts par un autre programme, ce qui peut faire échouer l'archivage de la sauvegarde en cours.

`deploy.py` détecte automatiquement l'emplacement de la sauvegarde selon l'OS :

| OS | Logiciel | Emplacement |
|---|---|---|
| macOS | Finder | `~/Library/Application Support/MobileSync/Backup/` |
| Windows | iTunes (installeur Apple) | `%USERPROFILE%\AppData\Roaming\Apple Computer\MobileSync\Backup\` |
| Windows | iTunes / Apple Devices (Microsoft Store) | `%USERPROFILE%\Apple\MobileSync\Backup\` |

Les deux emplacements Windows sont vérifiés automatiquement (le premier qui contient une sauvegarde pour l'UDID donné est utilisé).

## Utilisation

### Chaîne complète (recommandé)

Greffe directement dans la sauvegarde locale réelle utilisée par iTunes/Finder :

```bash
python3 deploy.py fichier1.tendies [fichier2.tendies ...] [--select] [--udid <UDID>]
```

Ce que ça fait automatiquement :
1. archive l'ancienne sauvegarde MobileSync (renommage, rien n'est perdu)
2. copie cette archive vers l'emplacement MobileSync d'origine
3. greffe chaque `.tendies` dedans (voir `convert.py`)
4. vérifie l'intégrité des deux bases (`PRAGMA integrity_check`)
5. si une étape échoue : restaure automatiquement l'archive à sa place

Ne déclenche **pas** la restauration elle-même — ouvre Finder (macOS) ou iTunes/Apple Devices (Windows) et lance « Restaurer la sauvegarde » une fois prêt.

`--select` active immédiatement le dernier poster greffé comme fond d'écran actif. Avec plusieurs fichiers (ou un `.tendies` contenant plusieurs wallpapers), seul le dernier traité devient actif ; les autres sont ajoutés au carrousel sans y être sélectionnés — tu choisis ensuite sur l'appareil.

### Conversion seule (sans toucher à MobileSync)

Utile pour tester un nouveau `.tendies` sur une copie de sauvegarde, sans risque :

```bash
python3 convert.py fichier.tendies /chemin/vers/une/copie/de/backup [--select] [--dry-run]
```

`--dry-run` analyse le fichier (détection de forme, identifiants dérivés) sans rien écrire.

## Logs

Chaque exécution écrit un journal détaillé et horodaté dans `logs/` : chaque fichier/dossier inséré (fileID, chemin, taille), chaque ligne SQL du registre, et la trace complète en cas d'erreur. La console reste volontairement courte ; le détail va dans le fichier.

## Limites connues

- Un `.tendies` contenant plusieurs wallpapers (ex: variantes saisonnières) devient plusieurs entrées **indépendantes** du carrousel — pas un poster unique qui changerait automatiquement.
- Le mismatch de résolution entre le contenu du `.tendies` et l'écran réel de l'appareil n'a pas semblé bloquant lors des tests, mais rien n'est modifié/adapté à ce sujet — c'est copié tel quel.
- Aucune garantie sur des mécanismes internes non identifiés (ex: liaison entre deux descripteurs pour un effet gyroscope) — l'outil copie fidèlement ce qu'il trouve, sans réinterpréter le contenu `.ca`.

## Sécurité et bon sens

- Ne jamais lancer `deploy.py` sans avoir une sauvegarde à jour de l'appareil au préalable (Finder/iTunes → Sauvegarder maintenant).
- Une restauration complète efface et reconfigure l'appareil — chronophage, mais sans rapport avec les mécanismes de restauration partielle qu'Apple a rendus dangereux sur iOS 27.
- Ce projet ne modifie que le domaine `AppDomain-com.apple.PosterBoard` de la sauvegarde ; rien d'autre n'est touché.
- Usage personnel sur son propre appareil. Aucune garantie fournie — teste sur une copie de sauvegarde avant de déployer.
