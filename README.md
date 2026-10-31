# Showrunner

Back-to-back presentations without the set-up time. Point it at one folder with everyone's
files, put them in order, press Start. Runs on **macOS, Windows and Linux**.

- **Two screens.** The beamer shows only the slides. The monitor gets a console with the current
  and next slide, **speaker notes**, a talk timer, the running order and all sound controls.
- **Same loudness for everyone.** Every audio and video clip is levelled to the same loudness
  (EBU R128, -14 LUFS) while preparing. One master slider goes **0% to 200%**. **Alt+S** mutes.
- **Prepared ahead.** Every file in the folder is converted in the background: slides, notes,
  sound and a preview thumbnail. During the show, switching speakers is instant.
- **Nothing to set up.** Install, open, put the presentations in the folder. That's it.

## What it plays

| Files | How |
|---|---|
| `.pptx .pptm .ppt .pps(x) .potx .odp` | Converted to slides (PowerPoint on Windows, Keynote on Mac, else LibreOffice). Notes, embedded audio/video and animated GIFs are kept. |
| `.key` | Converted by Keynote on a Mac; elsewhere LibreOffice tries (older Keynote files work best, see below) |
| `.pdf` | As is |
| `.png .jpg .gif …` | One slide (GIFs animate) |
| `.mp4 .mov .m4v .mkv .avi .webm` | Full-screen video, sound levelled, iPhone (HEVC) video converted so every computer plays it |
| `.webloc .url .gslides` (Google Slides…) | Opens in the browser; sound is *not* controlled there |

**Trade-off:** slides are shown as pictures, so PowerPoint/Keynote animations and transitions
don't play (each slide shows its final state). Audio, video and animated GIFs on a slide do play:
media automatically if the deck says so, otherwise on the next click, just like PowerPoint.

**Slide decks are converted** by PowerPoint (Windows) or Keynote (Mac) when installed. If neither
is there, Showrunner downloads [LibreOffice](https://www.libreoffice.org) by itself on first
launch: about 350 MB, one time only, no admin rights, checked against the official checksum,
kept in Showrunner's own folder. Decks wait in the list until it's ready and then get prepared
automatically. If the download fails (no internet), it quietly tries again.

**Tip for exact looks:** if a student hands in `Name.pptx` *and* `Name.pdf` (exported from their
own PowerPoint/Keynote), Showrunner shows the PDF and still takes notes and sound from the
`.pptx`. No converter needed, and the slides look exactly as they made them.

## Using it

1. Put the presentations in **Documents → Showrunner Presentations** (created on first launch),
   or drag them onto the list. Every file appears with a preview and its status.
2. Optional: change the order (drag, ▲▼, or *Sort by name / date / random*) or untick anyone
   who's absent. The order is saved in the folder.
3. Press **Start the show**.

Everything else is automatic:
- **Screens:** the beamer goes on the external screen and the console on the laptop.
- **Sound:** follows the computer's current output.
- **New files:** students' files added later appear at the bottom.
- **Replaced files:** they get prepared again.

Use *Choose folder…* to point it at another folder, e.g. a shared drive. *Test mode* runs both
views as normal windows, handy for trying it on one laptop.

### Keys (work in both windows; clickers work too)

| Key | Does |
|---|---|
| → ↓ Space PageDown Enter | Next slide / play the clip on this slide / start the next talk |
| ← ↑ PageUp | Previous slide (or previous presenter on the *Up next* screen) |
| **Alt+S** (or M) | Mute / unmute |
| Alt+↑ / Alt+↓ (or + / −) | Volume ±10% |
| B or . | Black screen |
| Esc | End this talk |
| S | Skip this presenter (on *Up next*) |

### Sound levels

100% plays the levelled audio at half amplitude, and 200% at full. That leaves room to go louder
without distortion. Set the speaker/amp volume once so 100% sounds right in the room.

Decks warn you about *linked* audio/video (the file stayed on the student's laptop). If the
student also puts that file in the folder, Showrunner finds and uses it.

### Prepared files

Prepared material goes in a hidden `.showrunner` folder inside the presentations folder.
Prepare once on a Mac (for `.key` files) and the same folder is ready on Windows/Linux.

## Install

Download from the repository's **Actions** tab (latest run → *Artifacts*):

- **macOS:** `Showrunner-macOS-arm64` (M1 and newer) or `-x86_64` (Intel). Open the `.dmg`, drag
  to Applications. The first time: right-click → **Open** (the app isn't notarized by Apple).
- **Windows:** `Showrunner-Windows` → run `Showrunner-Setup.exe`. SmartScreen may warn about an
  unknown publisher → *More info* → *Run anyway*.
- **Linux:** `Showrunner-linux-x86_64` or `-aarch64` (ARM) → unpack, run `./install.sh`.

### Building yourself

```bash
bash packaging/build_mac.sh            # macOS -> dist/Showrunner.dmg
bash packaging/build_linux.sh          # Linux -> dist/Showrunner-linux-*.tar.gz
```

Windows (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) for the installer):

```powershell
.\packaging\build_windows.ps1
```

## Running from source

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt     # Windows: .venv\Scripts\pip
.venv/bin/python run_showrunner.py
```

Tests: `pip install pytest`, then `python -m pytest`.
