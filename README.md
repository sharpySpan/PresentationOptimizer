# Showrunner

Back-to-back presentations without the set-up time. Point it at one folder with everyone's
files, put them in order, press Start. Between talks it shows an **Up next: name** screen; the next
presenter presses their clicker and their deck starts.

## What it plays

| Files | Played by |
|---|---|
| `.key` | Keynote |
| `.pptx .ppt .pptm .ppsx .pps .potx` | Keynote (default) or PowerPoint, your choice |
| `.odp` | Converted with LibreOffice, then Keynote/PowerPoint |
| `.pdf` | Built-in full-screen viewer |
| `.png .jpg .gif .heic …` | Built-in (one slide) |
| `.mp4 .mov .m4v` | Built-in video player, with sound |
| `.webloc .url .gslides` (Google Slides, Canva links…) | Opens in the browser in present mode |

When a Keynote/PowerPoint slideshow ends (last slide or Esc), Showrunner closes the file without
saving and takes the screen back for the next person.

## Sound

- Pick the output (HDMI/projector, USB speaker, AirPlay…) once. It's applied to the **system**
  output, which is what Keynote and PowerPoint use.
- Volume is set and mute is switched off **before every talk**, so an HDMI reconnect or a
  student muting the Mac doesn't kill the next video.
- **Play test sound** plays a chime left, then right.
- **Check all files** flags decks that will lose sound: audio/video that's *linked* rather than
  embedded (the file is on the student's own laptop), and Windows-only formats like `.wma`/`.wmv`.

## Using it

1. Open Showrunner, **Choose folder…**.
2. Drag to reorder, or Sort A–Z / Shuffle. Untick anyone who's absent. The order is saved in the
   folder (`.showrunner-order.json`); files added later show up at the bottom by themselves.
3. Choose the screen to present on, press **Start the show**.

During the show: **→ / Space / clicker** = next · **←** = previous · **S** = skip · **Esc** = end
this talk (or leave the show from the Up next screen). Optional *Auto-start next* counts down
and starts the next talk by itself.

## First-run checklist on the presentation Mac

- macOS asks *"Showrunner wants to control Keynote / PowerPoint"*: click **OK** (System Settings →
  Privacy & Security → Automation if you missed it).
- Keynote → Settings → Slideshow: choose the projector under *Present on display*, and enable
  *Exit presentation after last slide*.
- PowerPoint (if used) may ask to **Grant access** to the folder once. Select the folder itself.
- Turn on Do Not Disturb so notifications don't pop up on the projector.
- Do a dry run with **Check all files** the day before.

## Install

Download `Showrunner.dmg`, open it, drag **Showrunner** to Applications. The app isn't notarized
by Apple, so the first time: right-click it → **Open** → **Open**.

### Building the .dmg

On a Mac (Python 3.10+):

```bash
bash packaging/build_mac.sh
```

Produces `dist/Showrunner.app` and `dist/Showrunner.dmg`. Builds are per-architecture; build on
an Apple Silicon Mac for M-series Macs.

No Mac at hand? Push this repo to GitHub: `.github/workflows/build-mac.yml` builds the `.dmg`
for Apple Silicon and Intel and attaches it to the workflow run (Actions tab → run → Artifacts).

## Running from source

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run_showrunner.py
```

Tests: `pip install pytest && python -m pytest`. On Windows/Linux the app runs too (handy for
testing the ordering screen and PDFs), but Keynote/PowerPoint playback and output-device
switching are macOS-only.
