// ae_mcp runtime: helpers available to every script the MCP server runs inside After Effects.
// ExtendScript is ES3: no JSON, no let/const, no arrow functions, no Array.map. Avoid nested ?: chains.

var MCP = {};

MCP.q = function (s) {
    var out = '"';
    for (var i = 0; i < s.length; i++) {
        var c = s.charAt(i), code = s.charCodeAt(i);
        if (c === '"') out += '\\"';
        else if (c === "\\") out += "\\\\";
        else if (code < 32 || code > 126) out += "\\u" + ("0000" + code.toString(16)).slice(-4);
        else out += c;
    }
    return out + '"';
};

MCP.json = function (v, d) {
    d = d || 0;
    if (d > 14) return '"<too deep>"';
    if (v === null || v === undefined) return "null";
    var t = typeof v;
    if (t === "number") { if (isFinite(v)) return String(v); return "null"; }
    if (t === "boolean") { if (v) return "true"; return "false"; }
    if (t === "string") return MCP.q(v);
    if (t === "function") return "null";
    if (v instanceof Array) {
        var a = [];
        for (var i = 0; i < v.length; i++) a.push(MCP.json(v[i], d + 1));
        return "[" + a.join(",") + "]";
    }
    if (v.constructor === Object) {
        var o = [];
        for (var k in v) if (v.hasOwnProperty(k)) o.push(MCP.q(k) + ":" + MCP.json(v[k], d + 1));
        return "{" + o.join(",") + "}";
    }
    if (v instanceof Shape) return '"<Shape ' + v.vertices.length + ' pts>"';
    if (v instanceof TextDocument) return MCP.q(v.text);
    return MCP.q(String(v));
};

// ---------- lookup ----------
function findItem(name, type) {
    for (var i = 1; i <= app.project.numItems; i++) {
        var it = app.project.item(i);
        if (it.name !== name) continue;
        if (!type) return it;
        if (type === "comp" && it instanceof CompItem) return it;
        if (type === "footage" && it instanceof FootageItem) return it;
        if (type === "folder" && it instanceof FolderItem) return it;
    }
    return null;
}
function comp(name) {
    if (!name) {
        var a = app.project.activeItem;
        if (a && a instanceof CompItem) return a;
        throw new Error("No comp name given and no active comp. Open a comp or pass its name.");
    }
    var c = findItem(name, "comp");
    if (!c) {
        var names = [];
        for (var i = 1; i <= app.project.numItems; i++) if (app.project.item(i) instanceof CompItem) names.push(app.project.item(i).name);
        throw new Error("Comp '" + name + "' not found. Comps: " + names.join(", "));
    }
    return c;
}
function layer(c, id) {
    if (typeof id === "number") return c.layer(id);
    var l = c.layer(id);
    if (!l) throw new Error("Layer '" + id + "' not found in comp '" + c.name + "'.");
    return l;
}
// path like "Transform/Position", "ADBE Transform Group/ADBE Position", "Effects/Gaussian Blur/Blurriness", "Text/Source Text"
function prop(owner, path) {
    var alias = { "Transform": "ADBE Transform Group", "Effects": "ADBE Effect Parade", "Masks": "ADBE Mask Parade",
                  "Text": "ADBE Text Properties", "Contents": "ADBE Root Vectors Group" };
    var parts = path.split("/"), p = owner;
    for (var i = 0; i < parts.length; i++) {
        var key = parts[i], next = null;
        if (alias[key] && i === 0) key = alias[key];
        try { next = p.property(key); } catch (e) { next = null; }
        if (!next && /^\d+$/.test(key)) { try { next = p.property(parseInt(key, 10)); } catch (e2) { next = null; } }
        if (!next) {
            var kids = [];
            try { for (var k = 1; k <= p.numProperties; k++) kids.push(p.property(k).name + " [" + p.property(k).matchName + "]"); } catch (e3) {}
            throw new Error("Property '" + parts[i] + "' not found under '" + (p.name || "layer") + "'. Children: " + kids.join(", "));
        }
        p = next;
    }
    return p;
}
function importFile(path) {
    var f = new File(path);
    if (!f.exists) throw new Error("File not found: " + path);
    for (var i = 1; i <= app.project.numItems; i++) {
        var it = app.project.item(i);
        if (it instanceof FootageItem && it.file && it.file.fsName === f.fsName) return it;
    }
    return app.project.importFile(new ImportOptions(f));
}

// ---------- effects ----------
function effectMatchName(nameOrMatch) {
    var fx = app.effects;
    for (var i = 0; i < fx.length; i++) if (fx[i].matchName === nameOrMatch) return fx[i].matchName;
    var low = String(nameOrMatch).toLowerCase();
    for (var j = 0; j < fx.length; j++) if (fx[j].displayName.toLowerCase() === low) return fx[j].matchName;
    return nameOrMatch;
}
function fx(l, effect, params) {
    var parade = l.property("ADBE Effect Parade");
    var e = parade.addProperty(effectMatchName(effect));
    if (!e) throw new Error("Could not add effect '" + effect + "'. Use ae_describe_effect / app.effects for the matchName.");
    var warn = [];
    if (params) for (var k in params) if (params.hasOwnProperty(k)) {
        try { e.property(k).setValue(params[k]); } catch (er) { warn.push(k + ": " + er.message); }
    }
    if (warn.length) MCP.warnings = (MCP.warnings || []).concat(warn);
    return e;
}

// ---------- keyframes ----------
function easeDims(p) {
    var vt = p.propertyValueType;
    if (vt === PropertyValueType.TwoD) return 2;
    if (vt === PropertyValueType.ThreeD) return 3;
    return 1;   // 1D, spatial (position), colour, etc.
}
// keys: [{t, v, interp: "linear"|"bezier"|"hold", easeIn: 0-100, easeOut: 0-100}]
function setKeys(p, keys) {
    for (var i = 0; i < keys.length; i++) p.setValueAtTime(keys[i].t, keys[i].v);
    for (var j = 0; j < keys.length; j++) {
        var k = keys[j], idx = p.nearestKeyIndex(k.t), n = easeDims(p);
        var mode = KeyframeInterpolationType.BEZIER;
        if (k.interp === "linear") mode = KeyframeInterpolationType.LINEAR;
        if (k.interp === "hold") mode = KeyframeInterpolationType.HOLD;
        if (k.interp) p.setInterpolationTypeAtKey(idx, mode, mode);
        if (k.easeIn !== undefined || k.easeOut !== undefined) {
            var ei = [], eo = [];
            for (var d = 0; d < n; d++) { ei.push(new KeyframeEase(0, k.easeIn || 0.1)); eo.push(new KeyframeEase(0, k.easeOut || 0.1)); }
            try { p.setTemporalEaseAtKey(idx, ei, eo); } catch (e) { MCP.warnings = (MCP.warnings || []).concat(["ease@" + k.t + ": " + e.message]); }
        }
    }
    return p.numKeys;
}
