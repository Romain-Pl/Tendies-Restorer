# tendies-converter

Converts a `.tendies` file (a community-made wallpaper package, originally meant for the "Add Wallpaper" gallery) into a real, working **PosterBoard** configuration, then injects it into an unencrypted iOS backup so it shows up on the device after a full restore.

No jailbreak, no exploit: just the standard iOS backup/restore mechanism (the same one Finder or iTunes uses to restore an iPhone), with custom content inside.

## Where to get `.tendies` files

- **[cowabun.ga/wallpapers](https://cowabun.ga/wallpapers)** — a community gallery of ready-made `.tendies` wallpapers, from the team behind Nugget/Cowabunga. Browse, download, done.
- **[caplayground.vercel.app](https://caplayground.vercel.app/)** — CAPlayground, a free browser-based editor to design your own animated wallpaper (layers, shapes, gradients, images, particles, 3D transforms) and export it as a `.tendies`, no desktop app or sign-in required.

## Why this exists

Tools like [Nugget](https://github.com/leminlimez/Nugget) used to inject this kind of content through a hijacked **partial restore** (SparseRestore, then BookRestore) — internal iOS primitives never meant for third-party use. Apple closed both; on iOS 27, attempting either now triggers a full factory-reset loop.

This project takes a different route: writing straight into the content of a **regular full backup**, the exact mechanism anyone uses when restoring an iPhone from Finder or iTunes. Nothing is being hijacked — the backup format simply doesn't verify the content of the files it restores (no checksum), which leaves the door open to put whatever you want in there, as long as the expected structure is respected.

**Trade-off**: unlike a partial restore, this means wiping and reconfiguring the whole device every time you add something. No miracle shortcut, just a path that still works.

## How it works

PosterBoard (the animated-wallpaper engine behind the "Collections" gallery) stores each wallpaper in **two places** that both need to be written together:

1. the configuration files, under `Library/Application Support/PRBPosterExtensionDataStore/61/Extensions/<provider>/configurations/<UUID>/`
2. an entry in the central SQLite registry (`PBFPosterExtensionDataStoreSQLiteDatabase.sqlite3`, tables `poster` / `posterRoleMembership` / `posterAttributes`)

Without the second one, the files exist on the device but the wallpaper stays invisible — PosterBoard never reads the disk directly, only its registry.

Community `.tendies` files aren't structured like a real configuration (they're missing some files, or have extra ones, depending on whichever tool produced them). `convert.py` automatically detects whichever shape it's given and fills in what's missing.

## Requirements

- **macOS** (via Finder) or **Windows** (via iTunes or the Apple Devices app), with Python 3.8+ (nothing to install, standard library only)
- An unencrypted local backup of the device
- A `.tendies` file to convert

On Windows, close iTunes / the Apple Devices app before running `deploy.py` — Windows locks files that are open in another program, which can make the backup archiving step fail.

`deploy.py` automatically detects the backup location depending on the OS:

| OS | Software | Location |
|---|---|---|
| macOS | Finder | `~/Library/Application Support/MobileSync/Backup/` |
| Windows | iTunes (Apple installer) | `%USERPROFILE%\AppData\Roaming\Apple Computer\MobileSync\Backup\` |
| Windows | iTunes / Apple Devices (Microsoft Store) | `%USERPROFILE%\Apple\MobileSync\Backup\` |

Both Windows locations are checked automatically (whichever one has a backup for the given UDID is used).

## Usage

### Full pipeline (recommended)

Grafts directly into the real local backup used by iTunes/Finder:

```bash
python3 deploy.py file1.tendies [file2.tendies ...] [--select] [--udid <UDID>]
```

What it does automatically:
1. archives the current backup (rename, nothing is lost)
2. copies that archive back to the original backup location
3. grafts each `.tendies` into it (see `convert.py`)
4. verifies the integrity of both databases (`PRAGMA integrity_check`)
5. if any step fails: automatically restores the archive back in place

It does **not** trigger the restore itself — open Finder (macOS) or iTunes/Apple Devices (Windows) and click "Restore Backup" once you're ready.

`--select` immediately activates the last grafted poster as the active wallpaper. With multiple files (or a `.tendies` containing several wallpapers), only the last one processed becomes active; the others are added to the carousel without being selected — pick the one you want on the device afterward.

### Conversion only (without touching the real backup)

Useful for testing a new `.tendies` on a backup copy, risk-free:

```bash
python3 convert.py file.tendies /path/to/a/backup/copy [--select] [--dry-run]
```

`--dry-run` analyzes the file (shape detection, derived identifiers) without writing anything.

## Logs

Every run writes a detailed, timestamped log to `logs/`: every file/folder inserted (fileID, path, size), every SQL row written to the registry, and the full traceback on error. The console output stays short on purpose; the detail goes into the log file.

## Known limitations

- A `.tendies` containing several wallpapers (e.g. seasonal variants) becomes several **independent** carousel entries — not a single poster that would switch automatically.
- The resolution mismatch between the `.tendies` content and the device's actual screen didn't appear to be blocking during testing, but nothing is adapted/resized for it — it's copied as-is.
- No guarantee around unidentified internal mechanisms (e.g. two descriptors linked together for a gyroscope effect) — the tool faithfully copies whatever it finds, without reinterpreting the `.ca` content.

## Safety and common sense

- Never run `deploy.py` without an up-to-date backup of the device beforehand (Finder/iTunes → Back Up Now).
- A full restore wipes and reconfigures the device — time-consuming, but unrelated to the partial-restore mechanisms Apple made dangerous on iOS 27.
- This project only modifies the `AppDomain-com.apple.PosterBoard` domain of the backup; nothing else is touched.
- Personal use on your own device. No warranty provided — test on a backup copy before deploying for real.
