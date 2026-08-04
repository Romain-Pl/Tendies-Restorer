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
- For `gui.py` only: a Python built against **Tcl/Tk 8.6+**. macOS's own `/usr/bin/python3` ships the ancient Apple-deprecated Tcl/Tk 8.5, which renders a **blank window** on recent macOS versions (a known Tk 8.5 bug, not a bug in this project — confirmed with a minimal Tkinter test window before assuming otherwise). If `gui.py` opens blank: `brew install python-tk` (installs a modern Python + Tcl/Tk 9 via Homebrew) and run `gui.py` with that Python instead. The CLI tools (`convert.py`, `deploy.py`, etc.) don't use Tkinter and are unaffected either way.
- For `sim_deploy.py` and `dump.py --simulator` only: **Xcode** with the Command Line Tools (`xcrun simctl` must work) and at least one simulator runtime installed. macOS only — the Simulator doesn't exist on Windows, so these two entry points (and the corresponding GUI tab) aren't available there.

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

### Graphical interface

`gui.py` ties the five tools below into a single window (Tkinter, no extra
install beyond a Tcl/Tk 8.6+ Python — see Requirements above):

```bash
python3 gui.py
```

Every action still goes through the same functions as the CLI tools, and is
logged the same way in `logs/`.

**Available in French and English** — a switch in the top-right corner of the
window changes every label, button and dialog immediately, no restart
needed. The starting language is auto-detected from the system locale
(`LC_ALL`/`LC_MESSAGES`/`LANG`/`LANGUAGE`), defaulting to English if none of
those indicate French. See `i18n.py` for the translation catalog. Everything
else in the project — the CLI tools, their `--help` text, console output and
log files — is English-only.

### Combine several `.tendies` under a shared `family` (experimental)

Groups multiple wallpapers (or several descriptors from the same file) under
one shared `family` string — the only grouping field that actually exists in
the PosterBoard registry:

```bash
python3 combine.py file1.tendies file2.tendies [...] --family "MyCollection" --output combo.tendies
```

The order given on the command line is preserved (each descriptor gets an
ordered prefix). Apple caps this at 10 descriptors total. The GUI
additionally lets you reorder variants and rename each one individually
before combining.

**This does not reproduce Apple's native swipe UI** — confirmed on a real
device, see "Known limitations" below for the full writeup. It still
produces one **independent** carousel tile per variant.

### Toggle the Auto/Light/Dark selector

Sets `appearanceAware` in every descriptor's `Wallpaper.plist` (the flag that
exposes the Auto/Light/Dark picker in the customization screen — separate
from whether the wallpaper actually reacts to Dark Mode, which depends on the
`.ca` content itself):

```bash
python3 appearance.py file.tendies [--off] [--output file.appearance.tendies]
```

### Inject into an Xcode Simulator

For fast trial-and-error without wiping a real device: the simulator's
PosterBoard container is a plain folder (no `Manifest.db`, no fileID blobs),
so injection is immediate and reversible.

```bash
python3 sim_deploy.py --list-simulators
python3 sim_deploy.py file.tendies --udid <SIMULATOR_UDID> [--select] [--respring]
```

Boot the simulator at least once before injecting, so PosterBoard has
seeded its registry with the system's default posters. `--respring` restarts
SpringBoard (`launchctl kickstart -k system/com.apple.SpringBoard`) to pick up
the change immediately — never do a full `launchctl reboot userspace` or a
fresh `simctl boot` after injecting, that regenerates the registry from
scratch and wipes the added entries (the copied files on disk survive, the
registry rows don't). Toggling appearance is one command too:
`xcrun simctl ui <udid> appearance dark|light`.

### Dump an already-installed poster back into a `.tendies`

The reverse of `convert.py`/`sim_deploy.py`: extracts a poster that's
already sitting in a backup or a simulator and repackages it as a
`.tendies`, whether it came from a community file originally or is one of
Apple's own built-in posters (`com.apple.MercuryPoster` and other native
providers included, not just `com.apple.WallpaperKit.CollectionsPoster`).

```bash
python3 dump.py --backup /path/to/a/backup/copy --list
python3 dump.py --backup /path/to/a/backup/copy --provider <providerId> --uuid <UUID> --output out.tendies

python3 dump.py --simulator <UDID> --list
python3 dump.py --simulator <UDID> --provider <providerId> --uuid <UUID> --output out.tendies
```

`--list` prints every installed poster (UUID, provider, descriptor
identifier, role, whether it's currently selected) so you can find the
`--uuid`/`--provider` pair to dump. Two things happen automatically:

- Any runtime render cache (`scratch/`, `RuntimeSnapshot*` — only ever
  present on a live simulator/device, never in a backup) is excluded: it's
  regenerated automatically wherever the poster is reinjected, and isn't
  portable between instances.
- If the source is missing `suggestionMetadata.plist` (common on a
  device's very first, factory-seeded poster) and its provider isn't the
  default `CollectionsPoster`, a minimal replacement is synthesized so the
  provider is still auto-detected correctly on reinjection — otherwise
  `convert.py` would silently default to `CollectionsPoster` and the result
  would fail to render. The dump is self-verified immediately after being
  written (re-parsed the same way `convert.py` would) and a warning is
  logged whenever this reconstruction happens.

Dumping one of Apple's built-in posters only produces something useful on
another device/simulator running a close enough iOS version — the scene's
actual rendering is native OS code, not data, so an unknown identifier just
fails to show anything (no error) on an incompatible destination.

## Logs

Every run writes a detailed, timestamped log to `logs/`: every file/folder inserted (fileID, path, size), every SQL row written to the registry, and the full traceback on error. The console output stays short on purpose; the detail goes into the log file.

## Known limitations

- A `.tendies` containing several wallpapers (e.g. seasonal variants) becomes several **independent** carousel entries — each its own tile, no swipe between them. `combine.py` (or the GUI) can group them under a shared `family` string, but **this has been confirmed on a real device not to reproduce Apple's native swipe** — it still produces separate tiles. Direct inspection of the registry schema (`poster` / `posterRoleMembership` / `posterAttributes` tables in `PBFPosterExtensionDataStoreSQLiteDatabase.sqlite3`) shows no grouping mechanism at all. Confirmed the hard way: a real Apple wallpaper with a genuinely working swipe (Titanium, iPhone 15 Pro) turned out to have only **one** registry entry for the whole family. A screen recording of that working swipe (compared frame-by-frame against the dumped descriptor) shows the editor cycling through names — Natural/Blue/White/Black (device set to French: Naturel/Bleu/Blanc/Noir) — and live color-tinting the preview during the swipe, while the descriptor's own `Wallpaper.plist` and `Localizable.loctable` only ever mention "Natural". The sibling variants are drawn from a private, system-side gallery catalog that isn't part of the user's PosterBoard data at all — it isn't reproducible from a `.tendies` file, community-made or dumped from a device. `combine.py`'s `family` field is kept for cosmetic/forward-compatibility purposes but shouldn't be expected to produce a working native swipe.
- The resolution mismatch between the `.tendies` content and the device's actual screen didn't appear to be blocking during testing, but nothing is adapted/resized for it — it's copied as-is.
- No guarantee around unidentified internal mechanisms (e.g. two descriptors linked together for a gyroscope effect) — the tool faithfully copies whatever it finds, without reinterpreting the `.ca` content.

## Safety and common sense

- Never run `deploy.py` without an up-to-date backup of the device beforehand (Finder/iTunes → Back Up Now).
- A full restore wipes and reconfigures the device — time-consuming, but unrelated to the partial-restore mechanisms Apple made dangerous on iOS 27.
- This project only modifies the `AppDomain-com.apple.PosterBoard` domain of the backup; nothing else is touched.
- Personal use on your own device. No warranty provided — test on a backup copy before deploying for real.

## License

[MIT](LICENSE) — do whatever you want with this, no warranty.
