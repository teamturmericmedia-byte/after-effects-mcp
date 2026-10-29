"""After Effects MCP server for Claude - zero-dependency (stdlib) MCP over stdio.

Talks to a running After Effects by sending ExtendScript with `AfterFX.exe -r` and reading a JSON
result file back. Every script runs inside eval + try/catch with dialogs suppressed, so a bad script
returns an error to Claude instead of freezing After Effects behind a modal popup.
Optional helpers use numpy / opencv / Pillow / ffmpeg when they are installed.
"""
import base64
import glob
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PRELUDE = open(os.path.join(HERE, "prelude.jsx"), encoding="utf-8").read()
GUIDE_PATH = os.path.join(HERE, "guide.md")
WORK = os.path.join(tempfile.gettempdir(), "ae_mcp")   # %TEMP% has no spaces; AfterFX -r breaks on spaces
os.makedirs(WORK, exist_ok=True)
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
PROTOCOL = "2025-06-18"


def log(*a):
    print("[ae-mcp]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- AE discovery
def ae_exe():
    if os.environ.get("AE_PATH") and os.path.exists(os.environ["AE_PATH"]):
        return os.environ["AE_PATH"]
    found = sorted(glob.glob(r"C:\Program Files\Adobe\Adobe After Effects *\Support Files\AfterFX.exe"))
    if not found:
        raise RuntimeError("AfterFX.exe not found. Set the AE_PATH environment variable to it.")
    return found[-1]


def aerender_exe():
    return os.path.join(os.path.dirname(ae_exe()), "aerender.exe")


def ae_running():
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"], capture_output=True,
                             text=True, creationflags=NO_WINDOW).stdout
        return "AfterFX.exe" in out
    except Exception:
        return False


def which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for ext in ("", ".exe"):
            p = os.path.join(d, name + ext)
            if os.path.isfile(p):
                return p
    return None


# ---------------------------------------------------------------- script runner
class AEError(Exception):
    pass


def run_jsx(code, label="Claude", timeout=120, undo=True):
    """Run ExtendScript in the live After Effects. `code` is a function body: use `return` for the result."""
    if not ae_running():
        raise AEError("After Effects is not running. Start it (tool ae_launch) and try again.")
    rid = uuid.uuid4().hex[:10]
    res = os.path.join(WORK, "res_" + rid + ".json").replace("\\", "/")
    src = os.path.join(WORK, "run_" + rid + ".jsx")
    undo_open = 'app.beginUndoGroup(%s);' % json.dumps("MCP: " + label) if undo else ""
    undo_close = "try { app.endUndoGroup(); } catch (e) {}" if undo else ""
    wrapper = PRELUDE + """
(function () {
    var __f = new File(%s); __f.encoding = "UTF-8";
    var __r;
    try { app.beginSuppressDialogs(); } catch (e) {}
    try {
        %s
        var __v = eval("(function () {\\n" + %s + "\\n})()");
        __r = { ok: true, result: __v };
        if (MCP.warnings) __r.warnings = MCP.warnings;
    } catch (e) {
        __r = { ok: false, error: String(e.message || e), line: e.line };
    }
    %s
    try { app.endSuppressDialogs(false); } catch (e) {}
    __f.open("w"); __f.write(MCP.json(__r)); __f.close();
})();
""" % (json.dumps(res), undo_open, json.dumps(code), undo_close)
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(wrapper)
    subprocess.Popen([ae_exe(), "-r", src], creationflags=NO_WINDOW)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(res):
            time.sleep(0.05)
            try:
                with open(res, encoding="utf-8") as fh:
                    data = json.load(fh)
                break
            except (json.JSONDecodeError, PermissionError):
                continue
        time.sleep(0.15)
    else:
        raise AEError("No reply from After Effects after %ds. It may be busy (rendering, RAM preview) or showing "
                      "a dialog - check the AE window, close any popup, then retry." % timeout)
    for p in (res, src):
        try:
            os.remove(p)
        except OSError:
            pass
    if not data.get("ok"):
        raise AEError("ExtendScript error%s: %s" % (" (line %s)" % data["line"] if data.get("line") else "", data.get("error")))
    return data


def args_js(a):
    return "var A = " + json.dumps(a) + ";\n"


# ---------------------------------------------------------------- image helpers
def tile_images(paths, width=480, cols=4, labels=None):
    from PIL import Image, ImageDraw
    ims = []
    for p in paths:
        im = Image.open(p).convert("RGB")
        im = im.resize((width, max(1, int(im.height * width / im.width))))
        ims.append(im)
    if not ims:
        raise AEError("No frames were produced.")
    cols = min(cols, len(ims))
    rows = (len(ims) + cols - 1) // cols
    h = ims[0].height
    sheet = Image.new("RGB", (cols * width, rows * h), (16, 16, 16))
    d = ImageDraw.Draw(sheet)
    for i, im in enumerate(ims):
        x, y = (i % cols) * width, (i // cols) * h
        sheet.paste(im, (x, y))
        if labels:
            d.rectangle([x, y, x + 86, y + 20], fill=(0, 0, 0))
            d.text((x + 4, y + 4), labels[i], fill=(255, 255, 0))
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def img_content(jpeg_bytes):
    return {"type": "image", "data": base64.b64encode(jpeg_bytes).decode(), "mimeType": "image/jpeg"}


def text(s):
    if not isinstance(s, str):
        s = json.dumps(s, indent=1)
    return {"type": "text", "text": s}


# ---------------------------------------------------------------- tools
TOOLS = {}


def tool(name, description, props=None, required=None):
    def deco(fn):
        TOOLS[name] = {"fn": fn, "schema": {"name": name, "description": description, "inputSchema": {
            "type": "object", "properties": props or {}, "required": required or []}}}
        return fn
    return deco


S = {"type": "string"}
N = {"type": "number"}
B = {"type": "boolean"}
ANY = {}


@tool("ae_status", "Is After Effects running; version, open project (and unsaved changes), active comp and all comps. Call this first.")
def t_status(a):
    if not ae_running():
        return [text({"running": False, "exe": ae_exe(), "hint": "Use ae_launch to start After Effects."})]
    r = run_jsx("""
        var comps = [];
        for (var i = 1; i <= app.project.numItems; i++) { var it = app.project.item(i);
            if (it instanceof CompItem) comps.push(it.name + "  (" + it.width + "x" + it.height + ", " + it.duration.toFixed(2) + "s, " + it.frameRate + "fps, " + it.numLayers + " layers)"); }
        var ac = app.project.activeItem;
        return { running: true, version: app.version, project: app.project.file ? app.project.file.fsName : null,
                 unsaved: app.project.dirty, items: app.project.numItems,
                 activeComp: (ac && ac instanceof CompItem) ? ac.name : null, comps: comps };
    """, "status", 30, undo=False)
    return [text(r["result"])]


@tool("ae_launch", "Start After Effects (optionally opening a .aep) and wait until it accepts scripts.",
      {"project": S, "wait_seconds": N})
def t_launch(a):
    if not ae_running():
        cmd = [ae_exe()] + ([a["project"]] if a.get("project") else [])
        subprocess.Popen(cmd, creationflags=NO_WINDOW)
    t0, limit = time.time(), a.get("wait_seconds", 180)
    while time.time() - t0 < limit:
        try:
            r = run_jsx("return app.version;", "ping", 8, undo=False)
            if a.get("project") and ae_running():
                run_jsx(args_js(a) + "var f = new File(A.project); if (!app.project.file || app.project.file.fsName !== f.fsName) app.open(f); return true;", "open", 60, undo=False)
            return [text("After Effects %s is ready." % r["result"])]
        except AEError:
            time.sleep(3)
    raise AEError("After Effects did not respond within %ss." % limit)


@tool("ae_run_script",
      "Run ExtendScript in the live After Effects and get the result. `code` is a function body - use `return` to "
      "send data back (objects/arrays become JSON). Errors (even syntax errors) come back as text; no popups. "
      "Runs as one undo step. Helpers available: comp(name?), layer(c, nameOrIndex), prop(owner, 'Transform/Position'), "
      "importFile(path), fx(layer, effectNameOrMatchName, {param: value}), setKeys(prop, [{t, v, interp, easeIn, easeOut}]), "
      "findItem(name, type). Read ae_guide first for big builds.",
      {"code": S, "label": S, "timeout": N}, ["code"])
def t_run(a):
    r = run_jsx(a["code"], a.get("label", "script"), a.get("timeout", 300))
    out = {"result": r.get("result")}
    if r.get("warnings"):
        out["warnings"] = r["warnings"]
    return [text(out)]


@tool("ae_project_tree", "List every project item: name, type, id, size, duration, fps, parent folder, file path.")
def t_tree(a):
    r = run_jsx("""
        var out = [];
        for (var i = 1; i <= app.project.numItems; i++) { var it = app.project.item(i), o = { id: it.id, name: it.name, type: it.typeName, folder: it.parentFolder ? it.parentFolder.name : "" };
            if (!(it instanceof FolderItem)) { o.size = it.width + "x" + it.height; o.duration = it.duration; }
            if (it instanceof CompItem) { o.fps = it.frameRate; o.layers = it.numLayers; }
            if (it instanceof FootageItem && it.file) o.file = it.file.fsName;
            out.push(o); }
        return out;
    """, "tree", 60, undo=False)
    return [text(r["result"])]


@tool("ae_comp_info",
      "Describe a comp's layers: index, name, type, in/out, parent, 3D, blend mode, track matte, transform values, "
      "effects. detail='full' also lists every keyframed or expression-driven property.",
      {"comp": S, "detail": {"type": "string", "enum": ["layers", "full"]}})
def t_comp_info(a):
    r = run_jsx(args_js(a) + """
        var c = comp(A.comp), L = [];
        var walk = function (p, path, out, depth) {
            if (depth > 8) return;
            for (var i = 1; i <= p.numProperties; i++) { var q = p.property(i), qp = path + "/" + q.name;
                try {
                    if (q.propertyType === PropertyType.PROPERTY) {
                        if (q.numKeys > 0 || (q.canSetExpression && q.expressionEnabled)) {
                            var o = { path: qp, keys: q.numKeys };
                            if (q.numKeys) { o.first = [q.keyTime(1), q.keyValue(1)]; o.last = [q.keyTime(q.numKeys), q.keyValue(q.numKeys)]; }
                            if (q.canSetExpression && q.expressionEnabled) o.expression = q.expression;
                            out.push(o); }
                    } else walk(q, qp, out, depth + 1);
                } catch (e) {}
            } };
        for (var i = 1; i <= c.numLayers; i++) { var l = c.layer(i), t = l.property("ADBE Transform Group");
            var kind = "av";
            if (l instanceof TextLayer) kind = "text"; else if (l instanceof ShapeLayer) kind = "shape";
            else if (l instanceof CameraLayer) kind = "camera"; else if (l instanceof LightLayer) kind = "light";
            else if (l.nullLayer) kind = "null"; else if (l.adjustmentLayer) kind = "adjustment";
            else if (l.source instanceof CompItem) kind = "precomp"; else if (l.source && l.source.mainSource instanceof SolidSource) kind = "solid";
            else if (l.source) kind = "footage";
            var o = { i: i, name: l.name, type: kind, "in": l.inPoint, "out": l.outPoint, enabled: l.enabled };   // `in` is reserved unquoted in ES3
            try { if (l.source) o.source = l.source.name; } catch (e) {}
            try { if (l.parent) o.parent = l.parent.name; } catch (e) {}
            try { o.threeD = l.threeDLayer; o.blend = String(l.blendingMode); if (l.hasTrackMatte) o.trackMatte = String(l.trackMatteType); } catch (e) {}
            try { o.position = t.property("ADBE Position").valueAtTime(c.time, false); o.scale = t.property("ADBE Scale").valueAtTime(c.time, false);
                  o.rotation = t.property("ADBE Rotate Z").valueAtTime(c.time, false); o.opacity = t.property("ADBE Opacity").valueAtTime(c.time, false); } catch (e) {}
            try { if (kind === "text") o.text = l.property("ADBE Text Properties").property("ADBE Text Document").value.text; } catch (e) {}
            try { var fxp = l.property("ADBE Effect Parade"), fl = []; for (var k = 1; k <= fxp.numProperties; k++) fl.push(fxp.property(k).name + " [" + fxp.property(k).matchName + "]"); if (fl.length) o.effects = fl; } catch (e) {}
            if (A.detail === "full") { var an = []; walk(l, "", an, 0); if (an.length) o.animated = an; }
            L.push(o); }
        return { comp: c.name, size: [c.width, c.height], duration: c.duration, fps: c.frameRate, time: c.time, layers: L };
    """, "comp info", 120, undo=False)
    return [text(r["result"])]


@tool("ae_create_comp", "Create a composition (optionally open it in the viewer).",
      {"name": S, "width": N, "height": N, "duration": N, "fps": N, "bg_color": {"type": "array"}, "open": B}, ["name"])
def t_create_comp(a):
    r = run_jsx(args_js(a) + """
        var c = app.project.items.addComp(A.name, A.width || 1920, A.height || 1080, 1, A.duration || 10, A.fps || 24);
        if (A.bg_color) c.bgColor = A.bg_color;
        if (A.open !== false) c.openInViewer();
        return { name: c.name, id: c.id };
    """, "create comp " + a["name"])
    return [text(r["result"])]


@tool("ae_add_layer",
      "Add a layer: type solid | adjustment | null | text | shape | footage | camera | light. Options: name, color [r,g,b 0-1], "
      "width/height (solid), file (footage - imported once, reused), text/font (PostScript name)/size/fill [r,g,b]/tracking/justify "
      "(left|center|right), position [x,y], scale, opacity, start/end (seconds), threeD, blend (e.g. ADD, SCREEN, MULTIPLY, OVERLAY).",
      {"comp": S, "type": S, "name": S, "color": {"type": "array"}, "width": N, "height": N, "file": S, "text": S,
       "font": S, "size": N, "fill": {"type": "array"}, "tracking": N, "justify": S, "position": {"type": "array"},
       "scale": N, "opacity": N, "start": N, "end": N, "threeD": B, "blend": S}, ["type"])
def t_add_layer(a):
    r = run_jsx(args_js(a) + """
        var c = comp(A.comp), l, t = A.type;
        if (t === "solid" || t === "adjustment") {
            l = c.layers.addSolid(A.color || [0, 0, 0], A.name || (t === "adjustment" ? "Adjustment" : "Solid"), A.width || c.width, A.height || c.height, 1, c.duration);
            if (t === "adjustment") l.adjustmentLayer = true;
        } else if (t === "null") l = c.layers.addNull(c.duration);
        else if (t === "shape") l = c.layers.addShape();
        else if (t === "camera") l = c.layers.addCamera(A.name || "Camera", [c.width / 2, c.height / 2]);
        else if (t === "light") l = c.layers.addLight(A.name || "Light", [c.width / 2, c.height / 2]);
        else if (t === "footage") l = c.layers.add(importFile(A.file));
        else if (t === "text") {
            l = c.layers.addText(A.text || "TEXT");
            var sp = l.property("ADBE Text Properties").property("ADBE Text Document"), d = sp.value;
            d.resetCharStyle();
            if (A.font) d.font = A.font;
            d.fontSize = A.size || 100; d.applyFill = true; d.fillColor = A.fill || [1, 1, 1]; d.applyStroke = false;
            if (A.tracking !== undefined) d.tracking = Math.max(-1000, Math.min(1000, A.tracking));
            var j = ParagraphJustification.CENTER_JUSTIFY;
            if (A.justify === "left") j = ParagraphJustification.LEFT_JUSTIFY;
            if (A.justify === "right") j = ParagraphJustification.RIGHT_JUSTIFY;
            d.justification = j; sp.setValue(d);
            var r = l.sourceRectAtTime(0, false);
            l.property("ADBE Transform Group").property("ADBE Anchor Point").setValue([r.left + r.width / 2, r.top + r.height / 2]);
            if (!A.position) l.property("ADBE Transform Group").property("ADBE Position").setValue([c.width / 2, c.height / 2]);
        } else throw new Error("Unknown layer type '" + t + "'");
        if (A.name && t !== "camera" && t !== "light") l.name = A.name;
        if (A.threeD) l.threeDLayer = true;
        var tg = l.property("ADBE Transform Group");
        if (A.position) tg.property("ADBE Position").setValue(A.position.length === 2 && l.threeDLayer ? [A.position[0], A.position[1], 0] : A.position);
        if (A.scale !== undefined) tg.property("ADBE Scale").setValue([A.scale, A.scale, A.scale]);
        if (A.opacity !== undefined) tg.property("ADBE Opacity").setValue(A.opacity);
        if (A.start !== undefined) l.inPoint = A.start;
        if (A.end !== undefined) l.outPoint = A.end;
        if (A.blend) l.blendingMode = BlendingMode[A.blend.toUpperCase()];
        return { index: l.index, name: l.name, font: (t === "text") ? l.property("ADBE Text Properties").property("ADBE Text Document").value.font : undefined };
    """, "add " + a["type"])
    return [text(r["result"])]


@tool("ae_set_property",
      "Set a layer property by path (e.g. 'Transform/Position', 'Transform/Scale', 'Effects/Gaussian Blur/Blurriness', "
      "'Text/Source Text' with a string). Give `value` for a static value, `keyframes` [{t, v, interp, easeIn, easeOut}] "
      "to animate (replaces existing keys), and/or `expression` (empty string removes it).",
      {"comp": S, "layer": ANY, "path": S, "value": ANY, "keyframes": {"type": "array"}, "expression": S}, ["layer", "path"])
def t_set_property(a):
    r = run_jsx(args_js(a) + """
        var c = comp(A.comp), l = layer(c, A.layer), p = prop(l, A.path);
        if (A.path.indexOf("Source Text") >= 0 && typeof A.value === "string") { var d = p.value; d.text = A.value; p.setValue(d); }
        else if (A.value !== undefined && A.value !== null) { while (p.numKeys > 0) p.removeKey(1); p.setValue(A.value); }
        if (A.keyframes) { while (p.numKeys > 0) p.removeKey(1); setKeys(p, A.keyframes); }
        if (A.expression !== undefined) { p.expression = A.expression; }
        var err = ""; try { err = p.expressionError; } catch (e) {}
        return { layer: l.name, property: p.name, keys: p.numKeys, value: p.valueAtTime(c.time, false), expressionError: err };
    """, "set " + a["path"])
    out = r["result"]
    if r.get("warnings"):
        out["warnings"] = r["warnings"]
    return [text(out)]


@tool("ae_add_effect",
      "Add an effect to a layer by display name or matchName, with optional params {name or matchName: value}. "
      "Unknown param names come back as warnings - use ae_describe_effect to see the real names.",
      {"comp": S, "layer": ANY, "effect": S, "params": {"type": "object"}}, ["layer", "effect"])
def t_add_effect(a):
    r = run_jsx(args_js(a) + """
        var c = comp(A.comp), l = layer(c, A.layer), e = fx(l, A.effect, A.params);
        return { layer: l.name, effect: e.name, matchName: e.matchName, index: e.propertyIndex };
    """, "effect " + a["effect"])
    out = r["result"]
    if r.get("warnings"):
        out["warnings"] = r["warnings"]
    return [text(out)]


@tool("ae_describe_effect",
      "List an effect's full parameter tree (names, matchNames, value types, defaults, min/max) so values can be set without guessing. "
      "Pass a display name ('Fractal Noise') or matchName ('ADBE Fractal Noise'). With search=true, lists matching effects instead.",
      {"effect": S, "search": B}, ["effect"])
def t_describe_effect(a):
    if a.get("search"):
        r = run_jsx(args_js(a) + """
            var q = A.effect.toLowerCase(), out = [];
            for (var i = 0; i < app.effects.length; i++) { var e = app.effects[i];
                if (e.displayName.toLowerCase().indexOf(q) >= 0 || e.matchName.toLowerCase().indexOf(q) >= 0 || e.category.toLowerCase().indexOf(q) >= 0)
                    out.push(e.displayName + " | " + e.matchName + " | " + e.category); }
            return out.slice(0, 120);
        """, "search effects", 60, undo=False)
        return [text(r["result"])]
    r = run_jsx(args_js(a) + """
        var c = app.project.items.addComp("__mcp_probe", 200, 200, 1, 1, 24), s = c.layers.addSolid([1, 1, 1], "p", 200, 200, 1, 1);
        var out = [];
        var walk = function (p, ind) { for (var i = 1; i <= p.numProperties; i++) { var q = p.property(i), line = ind + i + ". " + q.name + " [" + q.matchName + "]";
            if (q.propertyType === PropertyType.PROPERTY) {
                try { line += " = " + MCP.json(q.value); } catch (er) {}
                try { if (q.hasMin) line += "  min " + q.minValue + " max " + q.maxValue; } catch (er2) {}
                try { if (q.isDropdownEffect) line += "  (dropdown: pass 1-based index)"; } catch (er3) {}
                out.push(line);
            } else if (q.matchName !== "ADBE Effect Built In Params") { out.push(line); walk(q, ind + "   "); } } };
        try {
            var e = fx(s, A.effect);
            out.push("EFFECT " + e.name + " [" + e.matchName + "]");
            walk(e, "  ");
        } finally { c.remove(); }
        return out;
    """, "describe effect", 60, undo=False)
    return [text("\n".join(r["result"]))]


@tool("ae_list_fonts", "Search fonts After Effects can actually use (PostScript names for text layers). Fonts installed only for the current user may be missing until installed for all users.",
      {"query": S})
def t_fonts(a):
    r = run_jsx(args_js(a) + """
        var q = (A.query || "").toLowerCase(), out = [], all = app.fonts.allFonts;
        for (var i = 0; i < all.length; i++) for (var j = 0; j < all[i].length; j++) { var f = all[i][j];
            if (!q || f.familyName.toLowerCase().indexOf(q) >= 0 || f.postScriptName.toLowerCase().indexOf(q) >= 0) out.push(f.familyName + " | " + f.styleName + " | " + f.postScriptName); }
        return out.slice(0, 200);
    """, "fonts", 60, undo=False)
    return [text(r["result"] or "No fonts matched.")]


@tool("ae_import", "Import files into the project (each file only once; existing items are reused). Optional folder name to put them in.",
      {"files": {"type": "array", "items": S}, "folder": S}, ["files"])
def t_import(a):
    r = run_jsx(args_js(a) + """
        var out = [], fold = null;
        if (A.folder) { fold = findItem(A.folder, "folder") || app.project.items.addFolder(A.folder); }
        for (var i = 0; i < A.files.length; i++) { var it = importFile(A.files[i]); if (fold) it.parentFolder = fold;
            out.push({ name: it.name, id: it.id, size: it.width + "x" + it.height, duration: it.duration }); }
        return out;
    """, "import")
    return [text(r["result"])]


def grab_live(a):
    """Render frames of a comp from the live project. Returns (comp name, times, existing png paths)."""
    sub = os.path.join(WORK, "prev_" + uuid.uuid4().hex[:8])
    os.makedirs(sub, exist_ok=True)
    a2 = dict(a, dir=sub.replace("\\", "/"))
    r = run_jsx(args_js(a2) + """
        var c = comp(A.comp), ts = A.times;
        if (!ts || !ts.length) { var n = A.count || 8, s = c.workAreaStart, d = c.workAreaDuration; ts = [];
            for (var i = 0; i < n; i++) ts.push(s + d * (i + 0.5) / n); }
        var files = [];
        for (var k = 0; k < ts.length; k++) { var f = new File(A.dir + "/f_" + (k < 10 ? "0" : "") + k + ".png");
            c.saveFrameToPng(Math.min(ts[k], c.duration - 1 / c.frameRate), f); files.push(f.fsName); }
        return { comp: c.name, times: ts, files: files };
    """, "preview", a.get("timeout", 300), undo=False)
    info = r["result"]
    paths = info["files"]
    t0 = time.time()
    while time.time() - t0 < 120:   # saveFrameToPng writes asynchronously
        if all(os.path.exists(p) and os.path.getsize(p) > 0 for p in paths):
            time.sleep(0.3)
            break
        time.sleep(0.3)
    return info["comp"], info["times"], paths


@tool("ae_preview",
      "LOOK at a comp: renders real frames from the live project (no saving needed) and returns them as one contact-sheet image. "
      "Give `times` (seconds) or `count` frames spread over the work area. Use after every change to verify.",
      {"comp": S, "times": {"type": "array", "items": N}, "count": N, "width": N, "cols": N})
def t_preview(a):
    name, times, paths = grab_live(a)
    ok = [p for p in paths if os.path.exists(p) and os.path.getsize(p) > 0]
    labels = ["%.2fs" % times[paths.index(p)] for p in ok]
    sheet = tile_images(ok, int(a.get("width", 480)), int(a.get("cols", 4)), labels)
    return [text("%s - %d frames at %s" % (name, len(ok), ", ".join(labels))), img_content(sheet)]


@tool("ae_compare",
      "SIDE-BY-SIDE check against a reference: each row = reference frame (left) | live After Effects frame (right) at the "
      "same moment. `reference` = a video (frames pulled with ffmpeg) or an image (used for every row). `ref_offset` = where "
      "the comp's 0s sits in the reference (e.g. 183 when the comp recreates 3:03 onwards). Give comp `times` or `count`. "
      "Use it to match colour, framing, text placement and cut timing to the reference.",
      {"comp": S, "reference": S, "times": {"type": "array", "items": N}, "count": N, "ref_offset": N, "width": N},
      ["reference"])
def t_compare(a):
    from PIL import Image, ImageDraw
    ref = a["reference"]
    if not os.path.exists(ref):
        raise AEError("Reference not found: " + ref)
    name, times, paths = grab_live(a)
    is_img = os.path.splitext(ref)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
    ff = which("ffmpeg")
    if not is_img and not ff:
        raise AEError("ffmpeg is needed to read frames from a reference video.")
    width, off = int(a.get("width", 640)), float(a.get("ref_offset", 0))
    rows = []
    for t, live in zip(times, paths):
        if not (os.path.exists(live) and os.path.getsize(live) > 0):
            continue
        if is_img:
            rimg = ref
        else:
            rimg = os.path.join(WORK, "ref_" + uuid.uuid4().hex[:8] + ".png")
            subprocess.run([ff, "-v", "error", "-y", "-ss", "%.3f" % (t + off), "-i", ref, "-frames:v", "1", rimg],
                           creationflags=NO_WINDOW)
        L = Image.open(live).convert("RGB")
        h = int(L.height * width / L.width)
        L = L.resize((width, h))
        R = Image.open(rimg).convert("RGB") if os.path.exists(rimg) else Image.new("RGB", (width, h), (60, 0, 0))
        R = R.resize((width, int(R.height * width / R.width)))
        rh = max(h, R.height)
        row = Image.new("RGB", (width * 2 + 6, rh), (16, 16, 16))
        row.paste(R, (0, (rh - R.height) // 2))
        row.paste(L, (width + 6, (rh - h) // 2))
        d = ImageDraw.Draw(row)
        for x, lab in ((0, "REF %.2fs" % (t + off)), (width + 6, "LIVE %.2fs" % t)):
            d.rectangle([x, 0, x + 110, 20], fill=(0, 0, 0))
            d.text((x + 4, 4), lab, fill=(255, 255, 0))
        rows.append(row)
    if not rows:
        raise AEError("No frames were produced.")
    sheet = Image.new("RGB", (rows[0].width, sum(r.height + 6 for r in rows)), (16, 16, 16))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height + 6
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=82)
    return [text("%s vs %s - %d rows (left = reference, right = live)" % (name, os.path.basename(ref), len(rows))),
            img_content(buf.getvalue())]


@tool("ae_save_project", "Save the project (optionally Save As to a new .aep path).", {"path": S})
def t_save(a):
    r = run_jsx(args_js(a) + """
        if (A.path) app.project.save(new File(A.path));
        else { if (!app.project.file) throw new Error("Project has never been saved - pass a path."); app.project.save(); }
        return app.project.file.fsName;
    """, "save", 120, undo=False)
    return [text("Saved: " + r["result"])]


@tool("ae_open_project", "Open a .aep project (the current project must be saved or it will ask; pass discard=true to drop changes).",
      {"path": S, "discard": B}, ["path"])
def t_open(a):
    r = run_jsx(args_js(a) + """
        if (A.discard) app.project.close(CloseOptions.DO_NOT_SAVE_CHANGES);
        else if (app.project.dirty) throw new Error("Current project has unsaved changes. Save it first or pass discard:true.");
        app.open(new File(A.path)); return app.project.file.fsName;
    """, "open", 180, undo=False)
    return [text("Opened: " + r["result"])]


@tool("ae_render",
      "Render a comp to a video file with aerender (saves the project first). Returns the output path; with contact_sheet=true also an image "
      "of frames from the finished file (needs ffmpeg).",
      {"comp": S, "output": S, "om_template": S, "rs_template": S, "contact_sheet": B, "timeout": N}, ["comp", "output"])
def t_render(a):
    proj = run_jsx("if (!app.project.file) throw new Error('Save the project first (ae_save_project with a path).'); app.project.save(); return app.project.file.fsName;", "save", 120, undo=False)["result"]
    out = a["output"]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    cmd = [aerender_exe(), "-project", proj, "-comp", a["comp"], "-RStemplate", a.get("rs_template", "Best Settings"),
           "-OMtemplate", a.get("om_template", "H.264 - Match Render Settings - 15 Mbps"), "-output", out]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=a.get("timeout", 1800), creationflags=NO_WINDOW)
    tail = [l for l in (p.stdout + p.stderr).splitlines() if "rror" in l or "Total Time" in l or "Finished" in l][-6:]
    if not os.path.exists(out):
        raise AEError("Render failed:\n" + "\n".join(tail or (p.stdout + p.stderr).splitlines()[-15:]))
    res = [text({"output": out, "size_mb": round(os.path.getsize(out) / 1e6, 2), "seconds": round(time.time() - t0, 1), "log": tail})]
    ff = which("ffmpeg")
    if a.get("contact_sheet") and ff:
        sheet = os.path.join(WORK, "sheet_" + uuid.uuid4().hex[:6] + ".jpg")
        subprocess.run([ff, "-v", "error", "-y", "-i", out, "-vf", "fps=1,scale=480:-1,tile=4x6", "-frames:v", "1", sheet],
                       creationflags=NO_WINDOW)
        if os.path.exists(sheet):
            res.append(img_content(open(sheet, "rb").read()))
    return res


@tool("ae_detect_beats",
      "Find the beat grid of an audio file (any format ffmpeg reads): BPM, kick-drum times and onset times in seconds, "
      "so cuts and word slams can land on hits. Optional start/duration in seconds.",
      {"audio": S, "start": N, "duration": N}, ["audio"])
def t_beats(a):
    import wave
    import numpy as np
    ff = which("ffmpeg")
    if not ff:
        raise AEError("ffmpeg is not on PATH.")
    wav = os.path.join(WORK, "beats_" + uuid.uuid4().hex[:6] + ".wav")
    cmd = [ff, "-v", "error", "-y"] + (["-ss", str(a["start"])] if a.get("start") else []) + ["-i", a["audio"]]
    cmd += (["-t", str(a["duration"])] if a.get("duration") else []) + ["-ac", "1", "-ar", "22050", wav]
    subprocess.run(cmd, check=True, creationflags=NO_WINDOW)
    with wave.open(wav) as wf:
        sr = wf.getframerate()
        x = np.frombuffer(wf.readframes(wf.getnframes()), np.int16) / 32768.0
    os.remove(wav)
    hop, win = 256, 1024
    n = (len(x) - win) // hop
    fr = np.lib.stride_tricks.as_strided(x, (n, win), (x.strides[0] * hop, x.strides[0]))
    spec = np.abs(np.fft.rfft(fr * np.hanning(win), axis=1))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    t = (np.arange(n) * hop + win / 2) / sr

    def peaks(env, gap, k):
        env = (env - env.mean()) / (env.std() + 1e-9)
        thr = np.convolve(env, np.ones(31) / 31, "same") + k
        idx = [i for i in range(1, len(env) - 1) if env[i] > thr[i] and env[i] >= env[i - 1] and env[i] >= env[i + 1]]
        keep = []
        for i in sorted(idx, key=lambda i: -env[i]):
            if all(abs(t[i] - t[j]) >= gap for j in keep):
                keep.append(i)
        return sorted(keep), env

    flux = np.concatenate([[0], np.maximum(np.diff(np.log1p(spec), axis=0), 0).sum(1)])
    low = spec[:, freqs < 150].sum(1)
    on, env = peaks(flux, 0.15, 0.8)
    kick, _ = peaks(np.concatenate([[0], np.maximum(np.diff(low), 0)]), 0.22, 1.0)
    ac = np.correlate(env - env.mean(), env - env.mean(), "full")[len(env) - 1:]
    lags = np.arange(len(ac)) * hop / sr
    sel = (lags > 60 / 180) & (lags < 60 / 70)
    period = float(lags[sel][np.argmax(ac[sel])])
    return [text({"bpm": round(60 / period, 2), "beat_period": round(period, 4),
                  "kicks": [round(float(t[i]), 3) for i in kick], "onsets": [round(float(t[i]), 3) for i in on]})]


def _audit():
    sys.path.insert(0, HERE)
    import audit_tools
    return audit_tools


@tool("ae_cuts", "Find the hard-cut times (seconds) of any video - a reference clip or a render - to match shot timing. "
      "Soft dissolves / double exposures can be missed; confirm with ae_compare or ae_preview.",
      {"video": S, "threshold": N}, ["video"])
def t_cuts(a):
    return [text({"cuts": _audit().cuts(a["video"], float(a.get("threshold", 0.35)))})]


@tool("ae_measure_motion",
      "GATHER the camera movement of a reference video, shot by shot, with feature tracking: pan_x/pan_y (% of frame width per "
      "second; + = content moves right/down, i.e. camera pans left/up), zoom_pct_s (+ = push in), rot_deg_s, shake_pct. "
      "Feed the numbers to ae_camera_move to recreate the same move on a layer. Untrackable shots (flat colour, text) are flagged.",
      {"video": S}, ["video"])
def t_motion(a):
    return [text({"shots": _audit().motion(a["video"]),
                  "how_to_use": "per shot: ae_camera_move(layer, start, end, zoom_pct_s, pan_x_pct_s, pan_y_pct_s, rot_deg_s, shake_pct)"})]


@tool("ae_camera_move",
      "Recreate a measured camera move on a layer (or an adjustment layer to move everything): keyframes scale / position / "
      "rotation between start and end from the per-second rates that ae_measure_motion returns, with ease, plus optional shake "
      "(wiggle). Content motion is reproduced: + pan_x moves the layer right. Scale is raised so the frame stays covered. "
      "`focus` = comp-space point to zoom around (put it on the faces so they stay in frame; default frame centre).",
      {"comp": S, "layer": ANY, "start": N, "end": N, "zoom_pct_s": N, "pan_x_pct_s": N, "pan_y_pct_s": N, "rot_deg_s": N,
       "shake_pct": N, "ease": N, "focus": {"type": "array"}}, ["layer", "start", "end"])
def t_camera_move(a):
    r = run_jsx(args_js(a) + """
        var c = comp(A.comp), l = layer(c, A.layer), t = l.property("ADBE Transform Group");
        var d = A.end - A.start, W = c.width;
        var s = t.property("ADBE Scale"), p = t.property("ADBE Position"), r = t.property("ADBE Rotate Z");
        var s0 = s.valueAtTime(A.start, false), p0 = p.valueAtTime(A.start, false), r0 = r.valueAtTime(A.start, false);
        var z = 1 + (A.zoom_pct_s || 0) * d / 100;
        var dx = (A.pan_x_pct_s || 0) * W / 100 * d, dy = (A.pan_y_pct_s || 0) * W / 100 * d, dr = (A.rot_deg_s || 0) * d;
        // overscan so pans/rotation never reveal an edge (layers smaller than the comp are left alone)
        var need = 1 + (Math.abs(dx) + Math.abs(dy)) / W + Math.abs(dr) / 45;
        var base = (l.width >= c.width && l.height >= c.height) ? Math.max(1, need) : 1;
        var sA = [s0[0] * base, s0[1] * base], sB = [s0[0] * base * z, s0[1] * base * z];
        if (s0.length === 3) { sA.push(s0[2]); sB.push(s0[2]); }
        // zoom around a comp-space focus point (faces), default frame centre: p' = F + (p - F) * zoomRatio
        var F = A.focus || [c.width / 2, c.height / 2];
        var pA = [F[0] + (p0[0] - F[0]) * base, F[1] + (p0[1] - F[1]) * base];
        var pB = [F[0] + (p0[0] - F[0]) * base * z + dx, F[1] + (p0[1] - F[1]) * base * z + dy];
        if (p0.length === 3) { pA.push(p0[2]); pB.push(p0[2]); }
        p0 = pA;
        var e = A.ease === undefined ? 40 : A.ease;
        setKeys(s, [{ t: A.start, v: sA, easeOut: e }, { t: A.end, v: sB, easeIn: e }]);
        setKeys(p, [{ t: A.start, v: p0, easeOut: e }, { t: A.end, v: pB, easeIn: e }]);
        setKeys(r, [{ t: A.start, v: r0, easeOut: e }, { t: A.end, v: r0 + dr, easeIn: e }]);
        if (A.shake_pct) p.expression = "if (time >= " + A.start + " && time < " + A.end + ") value + wiggle(6, " + (A.shake_pct * W / 100 * 1.5).toFixed(1) + ") - value; else value;";
        return { layer: l.name, scale: [sA[0], sB[0]], move_px: [dx, dy], rotate_deg: dr, overscan: base };
    """, "camera move")
    return [text(r["result"])]


@tool("ae_cutout",
      "Cut people/objects out of a photo into a transparent PNG (OpenCV GrabCut) for text-behind-subject and parallax. "
      "rect = [x, y, w, h] around the subject; fg_points / bg_points = [[x, y, radius], ...] to force areas in/out (e.g. faces, "
      "hair). Returns the PNG path and a red-overlay check image - inspect it and add points if something was missed.",
      {"image": S, "output": S, "rect": {"type": "array"}, "fg_points": {"type": "array"}, "bg_points": {"type": "array"},
       "iterations": N}, ["image"])
def t_cutout(a):
    import cv2
    import numpy as np
    img = cv2.imread(a["image"])
    if img is None:
        raise AEError("Could not read image: " + a["image"])
    h, w = img.shape[:2]
    x, y, rw, rh = a.get("rect") or [int(w * 0.05), int(h * 0.03), int(w * 0.9), int(h * 0.97)]
    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[y:y + rh, x:x + rw] = cv2.GC_PR_FGD
    for px, py, r in a.get("fg_points") or []:
        cv2.circle(mask, (int(px), int(py)), int(r), int(cv2.GC_FGD), -1)
    for px, py, r in a.get("bg_points") or []:
        cv2.circle(mask, (int(px), int(py)), int(r), int(cv2.GC_BGD), -1)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(img, mask, None, bgd, fgd, int(a.get("iterations", 6)), cv2.GC_INIT_WITH_MASK)
    al = np.where((mask == 1) | (mask == 3), 255, 0).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(al)
    keep = np.zeros_like(al)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] > 0.01 * h * w:
            keep[lab == i] = 255
    keep = cv2.GaussianBlur(cv2.morphologyEx(keep, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)), (3, 3), 0)
    out = a.get("output") or os.path.splitext(a["image"])[0] + "_cut.png"
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    rgba[..., 3] = keep
    cv2.imwrite(out, rgba)
    chk = img.copy()
    chk[keep < 128] = (0, 0, 255)
    chk = cv2.resize(chk, (960, int(960 * h / w)))
    ok, jpg = cv2.imencode(".jpg", chk, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return [text({"output": out, "foreground_percent": round(float((keep > 128).mean() * 100), 1),
                  "note": "red = removed. Add fg_points over anything missed (faces, beards, hair), bg_points over leftovers."}),
            img_content(jpg.tobytes())]


@tool("ae_guide",
      "The built-in After Effects playbook: workflow, scripting gotchas that cause errors, look recipes (duotone, brush text, "
      "text-behind-subject, parallax, poster rain, beat sync, glitch/flash), and quality checks. Pass a topic to get one section, "
      "or nothing for the table of contents.",
      {"topic": S})
def t_guide(a):
    g = open(GUIDE_PATH, encoding="utf-8").read()
    secs = g.split("\n## ")
    if not a.get("topic"):
        toc = [s.splitlines()[0] for s in secs[1:]]
        return [text(secs[0] + "\n\nSections (pass one as topic, or 'all'):\n- " + "\n- ".join(toc))]
    if a["topic"].lower() == "all":
        return [text(g)]
    q = a["topic"].lower()
    hits = ["## " + s for s in secs[1:] if q in s.splitlines()[0].lower()] or ["## " + s for s in secs[1:] if q in s.lower()]
    return [text("\n\n".join(hits) if hits else "No section matches '%s'." % a["topic"])]


INSTRUCTIONS = """After Effects MCP: drives the user's live Adobe After Effects.
Workflow: ae_status -> inspect (ae_project_tree / ae_comp_info) -> build (dedicated tools for small edits, ae_run_script for
big scene builds) -> ae_preview to LOOK at real frames after every meaningful change (ae_compare when recreating a reference:
reference | live side by side) -> fix -> ae_render.
Before writing any ExtendScript read ae_guide topic 'gotchas' (ES3 language, reserved words, ease dimensions, mask modes...).
Never guess effect parameter names: ae_describe_effect. Never guess fonts: ae_list_fonts.
Keep faces readable, keep frames covered (no image edges), and preview before saying something is done."""


# ---------------------------------------------------------------- protocol
def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:
        return None   # notification
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {"listChanged": False}, "resources": {}},
                "serverInfo": {"name": "after-effects", "version": "1.0.0"}, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [t["schema"] for t in TOOLS.values()]}
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in TOOLS:
            return {"content": [text("Unknown tool " + str(name))], "isError": True}
        try:
            return {"content": TOOLS[name]["fn"](args), "isError": False}
        except AEError as e:
            return {"content": [text(str(e))], "isError": True}
        except Exception as e:   # keep the server alive whatever happens
            log("tool crash", name, repr(e))
            return {"content": [text("%s failed: %r" % (name, e))], "isError": True}
    if method == "resources/list":
        return {"resources": [{"uri": "ae://guide", "name": "After Effects playbook", "mimeType": "text/markdown"}]}
    if method == "resources/read":
        return {"contents": [{"uri": "ae://guide", "mimeType": "text/markdown", "text": open(GUIDE_PATH, encoding="utf-8").read()}]}
    if method == "prompts/list":
        return {"prompts": []}
    raise LookupError(method)


def main():
    try:
        log("started; AE exe:", ae_exe())
    except Exception as e:
        log("started;", e)
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            result = handle(msg)
            if result is None:
                continue
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32601, "message": "Method not found: %s" % e}}
        except Exception as e:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": repr(e)}}
        sys.stdout.buffer.write((json.dumps(reply) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
