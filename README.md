# After Effects MCP for Claude

Let Claude work directly inside your open **Adobe After Effects**: inspect the project, build comps, animate, add effects,
**look at real frames**, **compare your comp side by side with a reference video**, **measure a reference's camera moves and
copy them**, and render. All of it happens by talking to Claude.

Built and battle-tested on real lyric-video and title-sequence builds in After Effects 2026.

```
You:    "Make a 10 s comp with a big red title that slams in on the beat, then show me."
Claude: ae_create_comp -> ae_add_layer -> ae_set_property -> ae_preview  (and looks at the frames)
```

## Features

- **23 tools**: comps, layers, properties, keyframes with easing, expressions, effects, import, save, render.
- **Live preview**: `ae_preview` renders frames straight from the open project, with no saving or rendering.
- **Side-by-side compare**: `ae_compare` shows a reference frame next to your live comp frame at the same moment.
- **Camera move capture**: `ae_measure_motion` tracks a reference video shot by shot (pan, zoom, rotation, shake).
  `ae_camera_move` recreates the move as eased keyframes with auto-overscan.
- **Shot timing**: `ae_cuts` finds the cut times. `ae_detect_beats` finds the BPM, kicks and onsets of a song.
- **Cutouts**: `ae_cutout` cuts people out of photos for text-behind-subject and parallax shots.
- **No popups**: every script runs sandboxed in `try/catch` with dialogs suppressed. Errors come back to Claude as text
  instead of freezing After Effects. Each action is one undo step.
- **Built-in playbook**: `ae_guide` covers workflow, ExtendScript gotchas, look recipes and quality rules.

## Requirements

- Windows, with Adobe After Effects 2022 or newer
- Python 3.10+ ([python.org](https://www.python.org/downloads/), tick "Add python.exe to PATH")
- Claude Code (desktop app Code tab or CLI), or Claude Desktop
- Optional: [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) (`winget install Gyan.FFmpeg`) for video references and render contact sheets

## Install

### Option A: one click

1. Download this repo: **Code > Download ZIP** and unzip it (for example to `C:\Tools\after-effects-mcp`), or
   ```
   git clone https://github.com/teamturmericmedia-byte/after-effects-mcp.git
   ```
2. Double-click **`install.bat`**. It installs the Python helpers and registers the server with Claude Code.
3. In After Effects, go to **Edit > Preferences > Scripting & Expressions**, tick
   **Allow Scripts to Write Files and Access Network**, then restart After Effects.
4. Open After Effects, start a **new** Claude session and say: **"check After Effects"**. Allow the after-effects tools.

### Option B: manual

```
python -m pip install --user -r requirements.txt
claude mcp add after-effects --scope user -- python "C:\Tools\after-effects-mcp\server.py"
```

Or add it to a config file yourself: `~/.claude.json` or a project's `.mcp.json` for Claude Code, or
`%APPDATA%\Claude\claude_desktop_config.json` for Claude Desktop.

```json
{
  "mcpServers": {
    "after-effects": {
      "command": "python",
      "args": ["C:\\Tools\\after-effects-mcp\\server.py"]
    }
  }
}
```

If After Effects is installed in an unusual place, add `"env": { "AE_PATH": "D:\\...\\Support Files\\AfterFX.exe" }`.

Then do step 3 from Option A (allow scripts in After Effects).

## Tools

| Tool | What it does |
|---|---|
| `ae_status` | is AE running, version, project, unsaved changes, comps |
| `ae_launch` | start AE (optionally with a .aep) and wait until it's ready |
| `ae_project_tree` | every project item with type, size, duration, file path |
| `ae_comp_info` | layers, transforms and effects; `detail: "full"` adds keyframes and expressions |
| `ae_create_comp` | new composition |
| `ae_add_layer` | solid, adjustment, null, text, shape, footage, camera or light, with common options |
| `ae_set_property` | value, keyframes with easing, or expression by path (`Transform/Position`) |
| `ae_add_effect` | add an effect by name or matchName, with params |
| `ae_describe_effect` | an effect's real parameter names and ranges, or search effects |
| `ae_list_fonts` | fonts AE can actually use (PostScript names) |
| `ae_import` | import files (deduplicated), optionally into a folder |
| `ae_preview` | **look**: real frames from the live comp as one contact sheet |
| `ae_compare` | **side by side**: reference frame next to the live frame at the same times |
| `ae_cuts` | shot-cut times of any video |
| `ae_measure_motion` | **gather camera moves** from a reference, per shot |
| `ae_camera_move` | recreate a measured camera move on a layer |
| `ae_run_script` | any ExtendScript, with helper functions; safe error reporting |
| `ae_save_project` / `ae_open_project` | save / save as / open |
| `ae_render` | render to MP4 with aerender, with an optional contact sheet |
| `ae_detect_beats` | BPM, kicks and onsets of a song for beat-synced edits |
| `ae_cutout` | cut people out of a photo, with a check overlay |
| `ae_guide` | the built-in playbook |

## Tutorial: talk to it like a motion designer

1. **"Check After Effects"** shows what's open.
2. **"Make a 1080p 10 s comp called INTRO, dark navy background, big red Anton title that slams in."**
3. **"Show me"** returns live frames. Then: *"Title too small, move it up, add a glow."*
4. **"Recreate 0:44-1:10 of this video with my photos: <link>"**. Claude studies the reference, measures its cuts
   and camera moves, builds the comp, then uses `ae_compare` to tune colour, framing and timing side by side.
5. **"Render it"** gives you an MP4 with a contact sheet.

## How it works

Plain Python with no extra dependencies for the server itself. It speaks MCP over stdio and drives the running
After Effects by sending ExtendScript through `AfterFX.exe -r` from a space-free temp folder, then reads the JSON result back.
`prelude.jsx` adds helpers (`comp()`, `layer()`, `prop()`, `fx()`, `setKeys()`, a JSON encoder for ES3).
`audit_tools.py` holds the measuring code (cuts, camera motion via ORB feature tracking, colour, side-by-side sheets) and
also works standalone: `python audit_tools.py motion reference.mp4`.
`cli.py` calls any tool from a terminal: `python cli.py ae_status`.

## License

MIT, see [LICENSE](LICENSE).
