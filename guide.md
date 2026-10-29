# After Effects playbook for Claude

Hard-won rules from real builds in this project (lyric videos, poster rain, parallax animatics) on After Effects 2026.
Read **gotchas** before writing ExtendScript; read the matching **recipe** before building a look.

## workflow

1. `ae_status` - is AE running, which project, unsaved changes, comps.
2. Inspect before touching: `ae_project_tree`, `ae_comp_info` (detail "full" shows keyframes and expressions).
3. Plan the shot list from the reference (times, framing, words, transitions) before building.
4. Build:
   - Small edits: `ae_add_layer`, `ae_set_property`, `ae_add_effect`.
   - Whole scenes: one `ae_run_script` per scene or comp. Data-driven (a SHOTS array at the top), so a re-run rebuilds cleanly. Remove the old comp by name first.
5. `ae_preview` after every meaningful change: real frames, real look. Check faces, edges and text legibility.
   When recreating a reference, use `ae_compare` (reference | live side by side; `ref_offset` = section start in the reference).
6. Fix, preview again. Only then `ae_render` (`contact_sheet: true` gives a final check).
7. Put new work in a new comp unless told otherwise; keep the user's earlier comps untouched. Save before risky steps.

## gotchas

Language (ExtendScript = ES3):
- No `let`/`const`, no arrow functions, no `JSON`, no `Array.map`/`forEach`/`indexOf` on arrays (strings are fine). Use `MCP.json(v)` to serialise.
- Reserved words break the whole script: `short`, `int`, `long`, `byte`, `char`, `float`, `double`, `final`, `native`, `package`, `goto`, `class`, `enum`, `export`, `import`, `super`, `interface`, `public`, `private`, `static`, `boolean`, `transient`, `synchronized`, `throws`, `volatile`, `abstract`, `implements`, `protected`. Never use them as variable names.
- Reserved words also break as unquoted object keys: write `{ "in": 0, "out": 1 }`, not `{ in: 0 }`.
- Nested `a ? b : c ? d : e` chains mis-evaluate. Use if/else.
- Declare helpers as `var f = function () {}`, not `function f() {}` inside blocks.

Keyframes and properties:
- `setTemporalEaseAtKey` needs one `KeyframeEase` per dimension: Scale = 3 (even on 2D layers), 2D props = 2, Position (spatial) = 1, 1D = 1. The `setKeys()` helper handles this.
- Removing every key from Time Remap switches remapping off. Overwrite the keys, then delete the extras.
- Text tracking is clamped to -1000..1000.
- Dropdown effect params take a 1-based integer.
- Effect property lists can be flat. Fractal Noise Scale is `ADBE Fractal Noise-0010`, not under "Transform". Run `ae_describe_effect` before setting params.
- Polar Coordinates "Interpolation" is 0-1, not 0-100. Drop Shadow opacity is 0-255.
- `mask.maskMode = MaskMode.SUBTRACT` (an attribute, not a property).
- `prop.moveTo()` / `layer.moveTo()` invalidates the old handle. Re-fetch by index.
- Concatenating some 2D values into strings throws "invalid numeric result". Read `.value` into a variable first, inside try.
- CC Particle World sub-properties are not reachable by name. Position, scale or rotate the layer instead (the emitter is the layer centre).

Mattes and precomps:
- A Set Matte or track-matte source precomp must be as long as the main comp. A 26 s source silently blanked every word after 26 s in a 35 s comp.
- `layer.setTrackMatte(matteLayer, TrackMatteType.ALPHA)`, then `matteLayer.enabled = false`.

Fonts:
- Fonts installed "for current user only" are invisible to AE, even after a restart. Only all-users installs show. `ae_list_fonts` before using one.
- When a brush or display font is not usable live, render the words to PNGs (Pillow) and animate the images.

Environment:
- `AfterFX.exe -r` fails silently on script paths with spaces. The server uses %TEMP%.
- A modal dialog in AE blocks all scripting. This server suppresses dialogs and wraps code in eval, so errors come back as text.
- `aerender` renders the saved .aep. `ae_render` saves first. Make the output folder before rendering.

## quality

- Faces must be recognisable in every shot. Frame on faces (track face centre and head height per photo) and keep them off letterbox bars and under-text zones.
- No image edges ever: clamp "cover" photos so the frame stays filled through every move, and add a full-length base solid under everything.
- Camera moves must be visible. Pure zooms on stills read as static. Use 2.5D parallax (cutout people move about 2x the plate) or a big truck or orbit.
- Same look and colour as the reference: sample its palette and map photos with Tritone or Tint plus Levels plus Posterize.
- Words are placeholders unless the user gives their own. Never put a song's lyrics on screen.
- Preview at least 1 frame per shot. Look for blank text (matte or duration bugs), black borders and crushed faces.

## recipe: text behind subject

1. Cut the person out (`ae_cutout`, add fg_points on faces and hair, check the red overlay). Inpaint a people-free plate if parallax is wanted.
2. Layer order (top to bottom): small front words, subject cutout, BIG word, plate.
3. Raise the subject so heads overlap the lower half of the big word; scale waist-up photos until the body reaches the frame bottom.
4. Punch the big word on the beat: scale 112% to 100% in 4 frames, then a slow creep.

## recipe: duotone looks

- Red/navy (Verappa): subjects use Tritone shadows navy (0.03, 0.08, 0.12), mids red (0.84, 0.09, 0.13), highlights pink-white (1, 0.88, 0.86). Backgrounds go navy, then steel (0.2, 0.34, 0.44), then red.
- Maroon/cream poster (Pathikichu): Tritone dark (0.10, 0.02, 0.02), maroon (0.36, 0.07, 0.07), cream (0.95, 0.835, 0.55), plus Posterize 7. Backgrounds get Levels output white 0.62 so subjects pop.
- Flat vector cards (Naa Ready): Median 3, then Levels, then Posterize 4, then Tritone black/maroon/tan. Silhouettes use Fill.
- Grunge red lyric (Veezha): B&W people (Tint 100, gamma 1.45, input white 0.7). Keep red washes in the wall precomp only, because an Overlay wash over people crushes dark skin.
- Tritone "Blend With Original" around 20 keeps skin tones readable.

## recipe: brush / distressed text

- Live: Anton or Bebas Neue Bold, plus Roughen Edges (Edge Type Cut, Border 3-18), plus Set Matte (luminance) to a full-length speckle precomp (two Fractal Noise layers, multiplied).
- Dry-brush script words: render with Pillow (Knewave or Permanent Marker), shear 0.25 for italic, cut horizontal streak gaps, then a Linear Wipe reveal over 4 frames.
- Wide-tracked sub-words (tracking 800-1000) with a Text Animator opacity range typed on letter by letter.

## recipe: beat sync

- `ae_detect_beats` on the song section, then snap every cut and word slam to the nearest kick within 0.09 s, rounded to whole frames.
- Hits drive a shake: markers on an adjustment layer, plus a Transform effect expression with exponential decay (scale 104 to 108, about 18 px shake).

## recipe: copy the reference's timing and camera

1. `ae_cuts` on the reference gives the shot boundaries. Put shot starts exactly on those times (frame-rounded), not on your own beat guesses.
2. `ae_measure_motion` on the reference gives per-shot zoom, pan, rotation and shake. Lyric-video references typically push 9-20%/s with 3-8 deg/s roll. A 1-2%/s zoom reads as static, so match the numbers.
3. `ae_camera_move` per shot on the shot's layer (or a per-shot adjustment layer) recreates the move with ease and auto-overscan.
4. `ae_compare` the same times side by side, then `ae_measure_motion` on your render to confirm the numbers match.

## recipe: parallax from a still

- Cutout people (FG) plus inpainted plate (BG) in a precomp. Animate both with the same framing keys, but give the plate about 45% of the move and zoom.
- Whip transition: rotate +/-46 degrees and scale 1.4x over 4 frames with motion blur, out of one shot and into the next.

## recipe: poster rain / falling cards

- Each poster is a 3D layer: random x, falls 260-680 px/s, Rotate X and Y expressions `time * k + offset`, random Z for depth, motion blur on.
- Tint lightly (Tritone blend about 45) so each poster still reads. 20-36 cards per burst, scale 26-50%.

## recipe: transitions

- Negative flash: 2-frame adjustment layer with Invert.
- Foreground wipe: a big dark blurred shape (tree trunk, figure) crossing the lens in about 14 frames.
- Ink-brush wipe: an oversized white solid with an animated rectangular mask shrinking to a strip, plus Turbulent Displace plus Roughen Edges (evolution `time*900`), used as an alpha track matte.
- Scribble sparks: shape layer of random zig-zag strokes with Trim Paths end 0 to 100.

## recipe: render and check

- `ae_render` with "H.264 - Match Render Settings - 15 Mbps". 4K 5 s takes about 50 s; 1080p 30 s about 25 s.
- `contact_sheet: true` returns a 1 fps sheet. For detail, `ae_preview` with exact `times`.
