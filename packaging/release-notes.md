Back-to-back presentations without the set-up time: one folder, put the files in order, press Start.

## Download

| Computer | File |
|---|---|
| Mac with Apple chip (M1 and newer) | `Showrunner-macOS-AppleSilicon.dmg` |
| Mac with Intel chip | `Showrunner-macOS-Intel.dmg` |
| Windows 10/11 | `Showrunner-Windows-Setup.exe` |
| Linux (x86_64) | `Showrunner-linux-x86_64.tar.gz` → unpack, run `./install.sh` |

**First launch.** The app isn't signed by Apple or Microsoft yet:
- **Mac:** right-click the app → **Open** → **Open**.
- **Windows:** SmartScreen → **More info** → **Run anyway**.

**Nothing to set up.** Install, open, and put the presentations in *Documents → Showrunner Presentations* (or drag them onto the list). If the computer has no PowerPoint or Keynote, Showrunner downloads the free LibreOffice converter by itself the first time it starts (~350 MB, one time only, no admin rights needed).

## New in 1.1.2
- Fixed: "No such file or directory … pymupdf/layout/…" when preparing presentations in the installed app
- Every installer is now started and run through a real self-test before it's published
- Unexpected errors show a message and are saved to `showrunner.log` instead of failing silently

## What it does
- Zero set-up: watched folder, slide converter fetched automatically, screens picked automatically
- `Name.pptx` + `Name.pdf`: the PDF is shown (looks exactly like the original), notes and sound come from the .pptx
- Setup screen with a preview of every file; order by drag, name, date or random
- Beamer shows the slides; the monitor shows a console with speaker notes, next slide, timer and running order
- All audio and video levelled to the same loudness, master volume 0–200%, **Alt+S** to mute
- Arrow keys and presentation clickers work in both windows
