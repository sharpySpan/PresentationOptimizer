Back-to-back presentations without the set-up time: one folder, put the files in order, press Start.

## Download

| Computer | File |
|---|---|
| Mac with Apple chip (M1 and newer), macOS 12+ | `Showrunner-macOS-AppleSilicon.dmg` |
| Mac with Intel chip, macOS 12+ | `Showrunner-macOS-Intel.dmg` |
| Windows 10/11 (no admin rights needed) | `Showrunner-Windows-Setup.exe` |
| Linux (Intel/AMD) | `Showrunner-linux-x86_64.tar.gz` → unpack, run `./install.sh` |
| Linux on ARM (Ubuntu 24.04 or newer) | `Showrunner-linux-aarch64.tar.gz` → unpack, run `./install.sh` |

**First launch.** The app isn't signed by Apple or Microsoft yet:
- **Mac:** open it once; when macOS refuses, go to **System Settings → Privacy & Security** and click **Open Anyway** (older macOS: right-click the app → **Open**).
- **Windows:** SmartScreen → **More info** → **Run anyway**.

**Nothing to set up.** Install, open, and put the presentations in *Documents → Showrunner Presentations* (or drag them onto the list). If the computer has no PowerPoint or Keynote, Showrunner downloads the free LibreOffice converter by itself the first time it starts (~350 MB, one time only, no admin rights needed).

## New in 1.2.0
**Fixes**
- "No such file or directory … pymupdf/layout/…" when preparing presentations in the installed app
- Files still being copied into the folder (USB stick, network) are prepared once the copy has finished
- A file replaced during preparation is prepared again; an outdated `Name.pdf` next to a newer `Name.pptx` is no longer shown
- A broken or missing video/sound in a deck no longer makes the whole talk unplayable
- PowerPoint can't hang Showrunner anymore (hidden activation/repair dialogs, password-protected decks)
- If PowerPoint/Keynote is installed but can't convert, LibreOffice is fetched as a backup
- iPhone (HEVC) and other video formats are converted to H.264, so they play on every computer
- The converter download uses the computer's own certificates and proxy (works on school networks)
- The show never freezes while a late file is being prepared; press S to skip it meanwhile
- Beamer unplugged or plugged in mid-show: the windows move to the right screens
- Runs on macOS 12 and newer (was 14); Windows installs without admin rights
- Quitting while converting no longer crashes; unexpected errors show a message and go to `showrunner.log`

**New**
- Animated GIFs play on the slide (every PDF export freezes them)
- Preparing is about 3× faster: several presentations are converted at once
- Linux on ARM; every installer is started and self-tested before it's published

## What it does
- Zero set-up: watched folder, slide converter fetched automatically, screens picked automatically
- `Name.pptx` + `Name.pdf`: the PDF is shown (looks exactly like the original), notes and sound come from the .pptx
- Setup screen with a preview of every file; order by drag, name, date or random
- Beamer shows the slides; the monitor shows a console with speaker notes, next slide, timer and running order
- All audio and video levelled to the same loudness, master volume 0–200%, **Alt+S** to mute
- Arrow keys and presentation clickers work in both windows
