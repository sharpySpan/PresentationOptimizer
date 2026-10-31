# Showrunner

Back-to-back presentations without the set-up time. Point it at one folder with everyone's
files, put them in order, press Start. Runs on **macOS, Windows and Linux**.

- **Two screens.** The beamer shows only the slides. The monitor gets a console with the current
  and next slide, **speaker notes**, a talk timer, the running order and all sound controls.
- **Same loudness for everyone.** Every audio and video clip is levelled to the same loudness
  (EBU R128, -14 LUFS) while preparing. One master slider goes **0% to 200%**. **Alt+S** mutes.
- **Prepared ahead.** As soon as you pick the folder, every file is converted in the background:
  slides, notes, sound and a preview thumbnail. During the show, switching speakers is instant.

## What it plays

| Files | How |
|---|---|
| `.pptx .ppt .pps(x) .potx .odp` | Converted to slides (PowerPoint on Windows, Keynote on Mac, else LibreOffice). Notes and embedded audio/video are kept. |
| `.key` | Converted by Keynote, so prepare these on a Mac (see below) |
| `.pdf` | As is |
| `.png .jpg .gif …` | One slide |
| `.mp4 .mov .m4v .mkv .avi .webm` | Full-screen video, sound levelled |
| `.webloc .url .gslides` (Google Slides…) | Opens in the browser; sound is *not* controlled there |

**Trade-off:** slides are shown as pictures, so PowerPoint/Keynote animations and transitions
don't play (each slide shows its final state). Audio and video on a slide do play: automatically
if the deck says so, otherwise on the next click, just like PowerPoint.

**Converter needed for slide decks:** PowerPoint (Windows), Keynote (Mac) or the free
[LibreOffice](https://www.libreoffice.org) (any OS). The set-up screen says which one it found.

## Using it

1. **Choose folder…**. Every file appears with a preview and its status: slides, audio/video,
   notes, or a warning.
2. Put them in order: drag, the ▲▼ buttons, or *Sort by name / date / random*. Untick anyone
   who's absent. The order is saved in the folder.
3. Under *Show*, choose which screen is the **Beamer** and which is the **Console**.
   *Test mode* runs both as normal windows, handy for trying it on one laptop.
4. Click **Test sound**, pick the output (HDMI, speaker…), set the level, and press **Start the show**.

Files added to the folder later appear at the bottom and get prepared automatically.

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
- **Linux:** `Showrunner-Linux` → unpack, run `./install.sh`.

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
