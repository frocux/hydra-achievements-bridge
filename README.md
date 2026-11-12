# Achievement Bridge for Hydra Launcher

## 🇷🇺 [Читать по-русски / Read in Russian →](README.ru.md)

[![tests](https://github.com/frocux/hydra-achievements-bridge/actions/workflows/tests.yml/badge.svg)](https://github.com/frocux/hydra-achievements-bridge/actions/workflows/tests.yml)

> **Alright folks, this thing drove me up the wall — so I went and fixed it!!**
> This repo reroutes your achievements into Hydra Launcher. That bug annoyed the hell
> out of me for ages, so I built a fix and figured I'd share it. Enjoy!)

**This isn't just a bridge for a couple of emulators — it knows 19 of them and roughly
70 places where they stash your achievements.**

**A full pass takes about a second.** Real run: 4 bridges at once (The Henry Stickmin
Collection — 8 achievements, Nodebuster — 13, Click the Button — 9, Moon Sweepers — 8),
done in 1 second. It runs in the background while you play.

**Hydra Launcher only looks for achievement files at a fixed set of paths. This script
finds them anywhere else on your PC and copies them where Hydra is guaranteed to read them.**

`Python 3.9+` · no third-party libraries · Windows

> Heads up: the script talks to you in Russian. The code is plain Python and
> `--help` lists every option, but the console output and comments are Russian.

---

## Why you'd need this

Hydra scans achievement files only at specific paths, always inside a folder named
after the game's Steam appid, like this:

```
%APPDATA%\GSE Saves\<appid>\achievements.json
C:\Users\Public\Documents\Steam\RUNE\<appid>\achievements.ini
```

When the emulator behaves, everything is fine. **Carrion** is a good example: its
emulator acts almost like real Steam, drops achievements exactly where they belong,
and Hydra shows them with no hacks needed. Games like that need no fixing — and the
script leaves them alone.

But sometimes it's a whole different story, and you get zero achievements in the
launcher. Three common cases:

1. **The emulator saves somewhere else.** Some of them (SOVEREIGN, for one) keep
   achievements inside their own `.ini` file next to the game. Hydra never looks there.
2. **The appid got lost.** If `steam_appid.txt` holds junk instead of a number, the
   emulator has no idea which game it is and dumps everything into a folder named `0`.
   Hydra looks in the folder with the real appid and finds nothing. (A very common
   stand-in is `480` — that's Spacewar, Valve's own test app — and the script ignores it.)
3. **There's no emulator at all.** An antivirus ate the cracked `steam_api.dll` and the
   genuine Valve one is left behind. The game's Steam API never initialises, so
   achievements are only tracked by the game's own save file.

The script handles all three: it finds the achievements, works out which game they
belong to, and copies them where Hydra will definitely see them.

There's a fourth case where nothing but a bridge can help. We went and read Hydra's
source: for some emulators it has **no search paths at all**. The list it builds for
3DM, ALI213 and UserStats comes back empty — Hydra physically cannot find those
achievements, no matter how neatly the emulator files them. No launcher update will
change that either. If your game uses one of those, a bridge isn't a convenience,
it's the only road there is.

Mind you — even then the achievements have to exist on disk first. More about that
further down, in "What it can't do".

---

## See it in action

[![Achievement popped in-game, launcher still empty](media/henry-achievement.gif)](media/henry-achievement.mp4)

Most of the time Henry Stickmin dumps its achievements in some hole Hydra never looks
into. In the clip the achievement pops on screen — and the launcher is still empty,
because it lives in the game's own folder under `%APPDATA%`, not where Hydra searches.
That's exactly what this bridge is for: it grabs them there and drops them where they
belong. Unlock it in-game and boom — it's already in Hydra, zero fiddling.

*(click the image for the full 23-second clip)*

And here's a second one, from "Stealing the Diamond" — **BUNP. I TOLD you about stairs.**
Popped in-game, and the bridge picked it up along with the rest.

![BUNP achievement in Henry Stickmin](media/henry-bunp.png)

---

## Features

- **Roughly 70 spots across 19 emulators** — 50 search paths plus 18 base emulator
  folders. Covers Goldberg, GSE, CODEX, RUNE, EMPRESS, OnlineFix, SKIDROW, SmartSteamEmu,
  CreamAPI, RLD!, Reloaded/3DM, DODI, ALI213, Hoodlum/DARKSiDERS, RLE, Razor1911,
  TENOKE, KaOs and SOVEREIGN — plus emulator configs and save folders inside the game
  directory itself.
- **All known formats**: every INI flavour, Goldberg/EMPRESS JSON, SmartSteamEmu's
  binary `stats.bin`, the GreenLuma registry.
- **Unknown formats still work**: any file that looks like achievement data gets run
  through every parser in turn, so new emulators usually just work with no code changes.
- **Any encoding**: UTF-8, cp1251, UTF-16, with or without a BOM. Old INI files from
  Russian repacks read just fine.
- **Safe writes**: files are written to a temp copy and swapped in one move (`os.replace`),
  so Hydra never sees a half-written file — and the previous content stays next to it as `.bak`.
- **Lost appid recovery**: achievements in a nameless folder (like `GSE Saves\0`) are
  matched against each game's achievement schema and routed to the right game.
- **Steam does half the work**: libraries are read from `libraryfolders.vdf`, so every
  `steamapps\common` folder joins the search by itself, and game numbers come from
  `appmanifest_*.acf` — even when the repack left no number near the game. Opt out with `--no-auto`.
- **Busy files don't crash it**: if a game holds a file open, the write is retried a few
  times; if it still fails, the report says so and the run carries on.
- **A skip list**: `ignore.txt` takes path masks, so old copies and junk folders are
  passed by instead of showing up as confusing sources.
- **`--doctor`**: an inspection pass that shows folders, Steam libraries, games, appids and
  emulator saves, then sums up what's missing — and writes nothing.
- **Emulator detection**: reports which emulator a game folder uses, and warns when the
  emulator is missing (genuine Valve DLL present).
- **Game-side saves**: if a game tracks its own achievements (say, a Godot game's
  `save.json`), the script reads it and derives the internal-name → Steam-api-name map
  straight from the game's own files.
- **Adobe AIR games**: Flash ports keep progress in `%APPDATA%\<id>\Local Store` as
  compressed `.sav` files — the script unpacks them and pulls the achievements out.
- **Windows registry**: GreenLuma (which keeps progress in the registry) plus a general
  sweep of `HKCU\Software`, for any emulator that writes there.
- **Tests on the parsers**: `tests` holds a sample file for each main format — every INI
  flavour, Goldberg/EMPRESS JSON, SmartSteamEmu's binary `stats.bin` — and checks exactly
  what comes back out of each one, 72 checks in all. Run `run-tests.bat` (or
  `python -m unittest discover -s tests -t .`): no dependencies, nothing written outside
  a temp folder. Being honest: the GreenLuma registry and the Adobe AIR saves have no
  tests yet, so those paths are covered by nothing but a careful read. The same suite
  runs on GitHub Actions on every push, against Python 3.9 and 3.12 — that's the badge
  at the top of the page.

Output goes to `%APPDATA%\GSE Saves\<appid>\achievements.json` in Goldberg format — the
one place Hydra reads for *any* crack. Existing foreign files are never overwritten; if
that slot is taken, the script falls back to
`%APPDATA%\Goldberg SteamEmu Saves\<appid>\achievements.json`.

---

## How it's different from other bridges

Tools like this are rare, and almost all of them are narrow: one or two emulators,
a manual routine — run it as admin, drag the game folder onto its window, pick an
option from the menu, keep the window open. This one goes the other way.

| | Typical bridges | This one |
|---|---|---|
| Emulators | one or two (usually RUNE and Goldberg) | 19 emulators, ~70 spots, every known format |
| Emulators Hydra can't see at all | nothing to be done | 3DM, ALI213, UserStats — Hydra has no paths for them, the bridge is the only way |
| Unknown format | ignored | a sniffer runs every parser on the file |
| Lost appid | achievements just aren't found | routed by matching names against game schemas |
| Game's own achievement tracking | usually no | Godot and Adobe AIR saves |
| Windows registry | rarely | GreenLuma + a general `HKCU\Software` sweep |
| Setup | drag a folder into a window, run as admin | one line in `roots.txt`, or nothing at all |
| How it runs | while its window is open | quietly from startup, every 10 seconds |
| Antivirus | an `.exe` Defender sometimes eats | a plain `.py` script — nothing to flag |

And it never touches your game folders and never needs admin rights.

---

## Install and run

1. Install Python 3.9+ (tick **Add Python to PATH**).
2. Put this folder anywhere you like, e.g. `C:\Tools\hydra-achievements-bridge`.
3. Open `roots.txt` and list your game folders, one per line. The two most common
   ones (`C:\Games` and `D:\Games`) are already built into the script, so if your
   games live there you can leave this file alone entirely:

```
C:\Games
D:\Games
E:\SteamLibrary\steamapps\common
```

   Steam libraries are found automatically too — the bridge reads Steam's own
   `libraryfolders.vdf` and adds every `steamapps\common` folder it points to.
   It also takes game numbers from Steam's `appmanifest_*.acf` files, which is
   handy when a repack left no number anywhere near the game. Turn all of that
   off with `--no-auto`.

4. Run `run.bat`, then read `report.txt` — it says what was found and where it went.

Want a dry run first? `check.bat` shows everything without writing a thing.
Puzzled why the launcher stays empty? `python bridge.py --doctor` walks through
what the bridge sees — folders, Steam libraries, games, appids, emulator saves —
and ends with a verdict. It reads only and writes nothing.

```
python bridge.py                     normal run
python bridge.py --dry-run           show what would happen, write nothing
python bridge.py --doctor            inspect the PC, write nothing
python bridge.py --watch 10          check every 10 seconds (what autostart uses)
python bridge.py --watch 30          same, but rarer — much easier on your disk
python bridge.py --watch             same, but every 30 seconds
python bridge.py --roots E:\Games    add a game folder
python bridge.py --no-auto           don't auto-detect Steam libraries and appids
python bridge.py --online            allow appid lookups in the Steam store
python bridge.py --quiet             short report
python bridge.py --watch 30 --full   forget what was found, re-read everything
python bridge.py --watch 30 --full-every 60   full sweep every 60 seconds
```

Got a folder you'd rather the bridge skipped? Put a piece of its path into
`ignore.txt`, one per line — the comparison ignores case, so `d:\games\old copies`
is enough. Copy `ignore.example.txt` to `ignore.txt` to start.

While watching, the bridge remembers what it found: it lists each folder once per pass
and re-reads it only when the folder itself changed. The first pass reads the disk, later
ones mostly skip it — a few tenths of a second instead of a couple of seconds on 11 games.
`--full` makes it forget everything and walk the disk from scratch on every pass;
`--full-every SEC` does that every so often (120 seconds by default) as a safety net.

One caveat: Windows writes a folder's timestamp with a step of about 16 ms, and deleting a
file does not always bump it. A new or edited achievement file is picked up on the next
pass; a *deleted* one can linger until the next full sweep — two minutes at most. Nothing
in normal play deletes achievement files, so in practice you won't notice.

Optional autostart: `autostart-on.bat` drops a `pythonw.exe ... --watch 10` shortcut
into your Startup folder; `autostart-off.bat` removes it. No admin rights, no services,
no registry changes. Only one watcher can run at a time — a second one sees the
`bridge.lock` and quits.

**Note:** Hydra reads these files at startup — restart it once after the first run.

---

## It runs in the background — why it might stutter

The bridge wakes up every 10 seconds, checks everything, then sleeps again. The first pass
reads the disk — about a second for 11 games. The ones after it only re-read folders that
changed, which is usually a few tenths of a second. With three hundred games installed even
that is noticeably longer, and it's worth keeping in mind.

Straight talk about the stutter:

- **Your disk wakes up every 10 seconds.** The bridge reads save files and emulator
  folders, so on a laptop the disk barely gets to sleep and you'll hear the fans more.
- **Antivirus.** Defender re-scans every file the bridge touches. That's time too.
- **If your game and the bridge share a disk**, you may catch a micro-hitch during a pass.
  Invisible with a dozen games, possibly noticeable with a huge library.

The fix is one number: use `--watch 30` or `--watch 60` instead of 10. An achievement
then reaches Hydra within half a minute, and in the background almost nothing happens.
If you don't want a background watcher at all, just run `run.bat` by hand after playing —
it does exactly the same thing.

**One more thing, about the in-game toast.** We ran into this: with a 60-second interval
the achievement showed up in Hydra but the in-game notification never fired. Looks like a
race — the bridge writes the file before the game draws its toast, and the game assumes
it's already been shown. We never pinned the exact cause down. With a 10-second interval
we haven't seen it. If your popups go missing, try a shorter interval (`--watch 5`) and
open an issue — we'll figure it out together.

---

## What it never does

- Never deletes or modifies anything inside game folders — it only reads there.
- Never downloads achievements; it only transfers what was actually earned locally.
- Never touches files it didn't create (`state.json` remembers its own files).
- Needs no administrator rights and installs nothing system-wide.

---

## If a game still doesn't show up

Read `report.txt`. It'll say one of these:

- **"appid lost... no idea whose folder this is"** — add a binding to `games.txt` as
  `Game folder name = 1234567`. You'll find the number in the game's Steam store URL:
  `store.steampowered.com/app/1234567/`.
- **"this appid isn't in Hydra's database"** — the game isn't in your Hydra library.
  Add it there first, then run the script again.
- **the appid looks like 480** — that's not a game id, it's Spacewar, Valve's test app.
  Repackers put it in `steam_appid.txt` just so the game launches. The bridge ignores
  that number: put the real one in `games.txt`.
- **"emulator isn't working: the original Valve steam_api is sitting next to it"** — your
  antivirus ate the cracked library. If the game keeps its own achievement log, the
  script will pull them from the save. Otherwise only reinstalling the emulator helps.
- **"both slots are taken by foreign files"** — both `GSE Saves` and
  `Goldberg SteamEmu Saves` already hold files for this game that aren't ours.
- **the game isn't in the report at all, and Hydra shows an empty page** — maybe the
  emulator never recorded anything. That's a different story, and it has its own
  section below: [When the emulator recorded nothing at all](#when-the-emulator-recorded-nothing-at-all).

---

## What it can't do

So we don't oversell this thing. The bridge moves achievements that already exist on
disk. It can't conjure them up, and it never reaches into your game folders.

### When the emulator recorded nothing at all

**If the game never handed the achievement over, there's nothing to move.** Hollow Knight
is the case from this very PC, but the story isn't about Hollow Knight: any build whose
emulator never learns the achievement list behaves exactly the same way. Three checks
tell you which one you're looking at:

1. **Look inside the file Hydra reads.** For Hollow Knight that's
   `C:\Users\Public\Documents\Steam\RUNE\367520\achievements.ini` — it is right there, and
   inside it says `Count=0`, with not a single `[ACHIEVEMENT_NAME]` block. The emulator
   made the file and wrote nothing into it. (The path is printed in the game folder, in
   `steam_emu.ini` — the line that starts with "Game data is stored at".)
2. **Search the game's own log for "achievement".** Unity games keep it in
   `%USERPROFILE%\AppData\LocalLow\<company>\<game>\Player.log`. The game asks the
   emulator about its progress and gets nothing back — over and over, for every
   achievement, on every launch. The counts in that file say it better than we can:
   ```
   95 Failed to retrieve achievement state for ATTUNEMENT
   89 Failed to retrieve achievement state for ENCHANTED
   88 Failed to retrieve achievement state for CHARMED
   ```
   As far as the game is concerned you have no achievements at all — so it never reports
   one, and there is never anything for the emulator to write down.
3. **Check whether the emulator has an achievement list to answer with.** RUNE and
   Goldberg take achievement descriptions from a `steam_settings` folder next to the
   game's `.exe` (`achievements.json` inside it). This repack ships no such folder, so
   the emulator has an empty catalogue to answer from.

**Our diagnosis, and where it stops being one.** The empty catalogue above looks like the
reason: no list, no answers, no written achievements. We didn't prove it — we have one
game's log and one emulator's file, and we can't tell you what RUNE does inside. If you
know its guts better than we do, an issue would be welcome.

**What we do know: the emulator itself is fine.** On this same PC, under the same RUNE,
another game's file is filled in — real unlock times and all:

```
[COMPLETE_PROLOGUE]
Achieved=1
CurProgress=0
MaxProgress=0
UnlockTime=1780259580
```

So it isn't "RUNE is broken". It's this particular build.

**What's worth trying** — all of it inside the game folder, which is exactly why the
bridge won't do it for you:

- give the emulator the achievement list: drop a proper `steam_settings\achievements.json`
  next to the game's `.exe` (the names and ids are on the game's Steam page);
- use another crack of the same game — one that ships with the achievement list;
- if the game keeps its own achievement log, the bridge can read progress from there
  instead (see "Game-side saves" above) — but only if the game keeps one;
- or accept it and move on. Hydra reads that same empty file, so nothing is being hidden
  from you — the achievements genuinely aren't on disk.

**Some emulators hold achievements in memory and only flush them to disk later**, or
when the game exits. RUNE is one of those. Play, quit the game, let the bridge take a
pass — now they're in Hydra.

**Progress that lives on a server can't be copied.** A few builds keep achievements
online. Locally there's nothing to pick up.

**Anonymous names stay anonymous.** SmartSteamEmu keeps not names but their CRC32, so
the report shows entries like `crc:a1b2c3d4`. They do get carried across, but there's
no way to match them against the game's real achievements — so don't count on Hydra
showing those nicely.

**Windows only** — every one of those locations is a Windows path.

**And it won't repair a broken emulator.** If your antivirus ate the cracked library,
the most the bridge can do is pull achievements out of the game's own save file — if
the game keeps one. Reinstalling the emulator is a separate job.

---

## Make it yours

The licence is **MIT** — take it and do whatever you want: change it, cut bits out,
rewrite it, drop it into your own builds, ship it further. One condition: keep the
licence text, and don't be shocked if something doesn't work :) That's what it's out
here for.

The handy part: **it's a single plain-Python file** — no build step, no libraries, no
installer. Grab it, open it in Notepad, start hacking.

Where things live:

| I want to | Look at |
|---|---|
| Add my own emulator folder | the `LOCATION_TEMPLATES` table at the top of `bridge.py` — one line |
| Add a base folder that paths hang off | `EMU_BASE_DIRS`, right there |
| Teach it to recognise a new emulator | `EMU_MARKERS`, right there |
| Parse a format it doesn't know | the parsers `parse_ini`, `parse_json_achievements`, `parse_sse_bin`, and the sniffer `sniff_file` that runs them in turn |
| Change how often it checks | the `--watch` flag, or the number in the startup shortcut |
| Make it ignore what it remembered | `--full`, or `--full-every SEC` for a rarer full sweep |
| Add my game folders and bindings | `roots.txt`, `games.txt` |
| Skip a folder altogether | `ignore.txt` — one path mask per line |
| Games that track achievements themselves | `local-saves.json` |
| See what the bridge finds on this PC | `python bridge.py --doctor` |
| Check I didn't break a format | `run-tests.bat` — a test per main format, samples in `tests/samples` |

Comments in the code are Russian, and every table is labelled in words — a beginner can
follow it. Fork it, add your own emulators, send patches: the tables exist for exactly
that, and there's nothing tricky in them.

---

## Files

```
bridge.py               the script itself
README.md               this page, English (the main one)
README.ru.md            the same page, Russian
LICENSE                 MIT licence
roots.txt               your game folders (fill this in)
games.txt               manual "game folder = appid" bindings
ignore.txt              path masks to skip (a folder you don't want touched)
local-saves.json        manual name maps for games with their own achievement log
state.json              which files the script created (appears after a run)
report.txt              the last run's report
bridge.lock             the lock: so two watchers don't start at once
steam-cache.json        appid lookup cache (only with --online)
local-saves-cache.json  cache of parsed game files
check.bat, run.bat, watch.bat, autostart-*.bat   Windows launchers
autostart-on.ps1, autostart-off.ps1              autostart plumbing
roots.example.txt, games.example.txt             config templates
ignore.example.txt                               template for the skip list
run-tests.bat                                    runs the test suite
tests/                                           tests and the sample files they use
.github/workflows/                               the same tests on every push (GitHub Actions)
media/                                           images and video for this README
```

---

## How it works under the hood

- Search locations and formats are described by tables at the top of `bridge.py` —
  adding a new emulator usually means one more line in a table.
- If a file looks like achievements but the format is unfamiliar, the sniffer kicks in
  and runs every parser on it in turn.
- Odd folders like `GSE Saves\0` are sorted out by meaning: achievement names are
  matched against game schemas, and each one goes to its own game.
- The internal-name → Steam-name map for games with their own achievement log is built
  from the game's own files (in Godot games the real achievement name sits on the line
  right after its `...__DESC` description).
- For Adobe AIR games, files in `Local Store` are unpacked as raw deflate, then read as
  "length + text" records.
- Registry achievements are read-only: known GreenLuma branches first, then a shallow
  sweep of `HKCU\Software`.
- Its own files are written to a temp copy and swapped in one move (`os.replace`), so
  Hydra can never catch a half-written file. The previous content stays next to it as `.bak`.

Storage locations and file formats were learned from
[Achievement Watcher](https://github.com/xan105/Achievement-Watcher) and
[Hydra Launcher](https://github.com/hydralauncher/hydra). Thanks to their authors.

Author: **frocux**.

## License

MIT — see [LICENSE](LICENSE).
