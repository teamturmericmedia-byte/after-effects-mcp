"""Objective reference-vs-render measurements for auditing comps.

  python audit_tools.py cuts  <video> [threshold]              -> JSON list of hard-cut times (s)
  python audit_tools.py pair  <ref> <ours> <out.jpg> t1 t2 ... [--offset S]  -> side-by-side rows REF | OURS
  python audit_tools.py strip <video> <out.jpg> [fps] [start] [dur]          -> contact strip with timestamps
  python audit_tools.py color <video> t1 t2 ...                -> JSON per-time mean RGB + shadow/mid/high colours
  python audit_tools.py motion <video>                         -> JSON per-shot camera move: pan/zoom/rotation per second + shake
ffmpeg must be on PATH. Images are small enough to Read.
"""
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageDraw

TMP = tempfile.mkdtemp(prefix="audit_")


def frames(video, fps=12, w=160, start=None, dur=None):
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", str(start)] if start else []) + ["-i", video]
    cmd += (["-t", str(dur)] if dur else []) + ["-vf", "fps=%s,scale=%d:-2" % (fps, w), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", video], capture_output=True, text=True).stdout.strip().split(",")
    vw, vh = int(probe[0]), int(probe[1])
    h = int(round(vh * w / vw / 2) * 2)
    n = len(raw) // (w * h * 3)
    return np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)


def cuts(video, thr=0.35, fps=24):
    f = frames(video, fps, 96).astype(np.float32) / 255
    out = []
    for i in range(1, len(f)):
        ha = np.histogram(f[i - 1], 24, (0, 1))[0].astype(np.float32)
        hb = np.histogram(f[i], 24, (0, 1))[0].astype(np.float32)
        hist = 0.5 * np.abs(ha / ha.sum() - hb / hb.sum()).sum()
        pix = np.abs(f[i] - f[i - 1]).mean()
        score = 0.5 * hist + 2.0 * pix
        if score > thr and (not out or i / fps - out[-1] > 0.15):
            out.append(round(i / fps, 3))
    return out


def grab(video, t, path, w=560):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "%.3f" % max(0, t), "-i", video, "-frames:v", "1",
                    "-vf", "scale=%d:-2" % w, path])
    return path if os.path.exists(path) else None


def label(im, s):
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 8 + 7 * len(s), 18], fill=(0, 0, 0))
    d.text((4, 3), s, fill=(255, 255, 0))


def pair(ref, ours, out, times, offset=0.0, w=560):
    rows = []
    for t in times:
        a = grab(ref, t + offset, os.path.join(TMP, "r.png"), w)
        b = grab(ours, t, os.path.join(TMP, "o.png"), w)
        A = Image.open(a).convert("RGB") if a else Image.new("RGB", (w, 315))
        A = A.resize((w, int(A.height * w / A.width)))
        B = Image.open(b).convert("RGB") if b else Image.new("RGB", (w, 315))
        B = B.resize((w, int(B.height * w / B.width)))
        label(A, "REF %.2fs" % (t + offset)); label(B, "OURS %.2fs" % t)
        h = max(A.height, B.height)
        row = Image.new("RGB", (2 * w + 6, h), (20, 20, 20))
        row.paste(A, (0, 0)); row.paste(B, (w + 6, 0))
        rows.append(row)
    sheet = Image.new("RGB", (rows[0].width, sum(r.height + 6 for r in rows)), (20, 20, 20))
    y = 0
    for r in rows:
        sheet.paste(r, (0, y)); y += r.height + 6
    sheet.save(out, quality=82)
    print(out)


def strip(video, out, fps=2, start=None, dur=None, w=320, cols=6):
    f = frames(video, fps, w, start, dur)
    t0 = float(start or 0)
    ims = []
    for i, a in enumerate(f):
        im = Image.fromarray(a); label(im, "%.2fs" % (t0 + i / fps)); ims.append(im)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * ims[0].height), (20, 20, 20))
    for i, im in enumerate(ims):
        sheet.paste(im, ((i % cols) * w, (i // cols) * im.height))
    sheet.save(out, quality=80)
    print(out)


def color(video, times):
    res = []
    for t in times:
        p = grab(video, t, os.path.join(TMP, "c.png"), 320)
        a = np.asarray(Image.open(p).convert("RGB")).reshape(-1, 3).astype(np.float32)
        lum = a @ np.array([0.299, 0.587, 0.114])
        q = np.quantile(lum, [0.15, 0.5, 0.85])
        band = lambda lo, hi: a[(lum >= lo) & (lum <= hi)].mean(0).round().tolist() if ((lum >= lo) & (lum <= hi)).any() else None
        res.append({"t": t, "mean": a.mean(0).round().tolist(), "shadows": band(0, q[0]), "mids": band(q[0], q[2]),
                    "highs": band(q[2], 255), "black_bar_rows_pct": round(float((np.asarray(Image.open(p).convert('L')).max(1) < 18).mean() * 100), 1)})
    print(json.dumps(res))


def motion(video, fps=12, start=None, dur=None, cut_thr=0.35):
    """Camera move per shot from feature tracking (ORB + partial affine between consecutive frames).
    Per shot: pan x/y (% of frame width per second, + = image content moves right/down, i.e. camera pans left/up),
    zoom (% per second, + = push in), rotation (deg/s), shake (frame-to-frame jitter px% after smoothing)."""
    import cv2
    f = frames(video, fps, 480, start, dur)
    t0 = float(start or 0)
    W = f.shape[2]
    cutset = [c - t0 for c in cuts(video, cut_thr)] if start is None else []
    orb = cv2.ORB_create(1500)
    steps = []
    prev = cv2.cvtColor(f[0], cv2.COLOR_RGB2GRAY)
    kp0, d0 = orb.detectAndCompute(prev, None)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    for i in range(1, len(f)):
        g = cv2.cvtColor(f[i], cv2.COLOR_RGB2GRAY)
        kp1, d1 = orb.detectAndCompute(g, None)
        m = None
        if d0 is not None and d1 is not None and len(kp0) > 8 and len(kp1) > 8:
            mt = sorted(bf.match(d0, d1), key=lambda x: x.distance)[:300]
            if len(mt) >= 12:
                a = np.float32([kp0[x.queryIdx].pt for x in mt]); b = np.float32([kp1[x.trainIdx].pt for x in mt])
                m, inl = cv2.estimateAffinePartial2D(a, b, method=cv2.RANSAC, ransacReprojThreshold=3)
                if m is not None and inl is not None and inl.sum() < 10:
                    m = None
        if m is not None:
            s = float(np.hypot(m[0, 0], m[1, 0])); r = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
            steps.append((i / fps, m[0, 2] / W * 100, m[1, 2] / W * 100, (s - 1) * 100, r))
        else:
            steps.append((i / fps, None, None, None, None))
        kp0, d0 = kp1, d1
    bounds = [0.0] + [c for c in cutset if 0 < c < len(f) / fps] + [len(f) / fps]
    shots = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        seg = [s for s in steps if a + 1.0 / fps < s[0] <= b and s[1] is not None]
        if len(seg) < 3:
            shots.append({"start": round(t0 + a, 2), "end": round(t0 + b, 2), "note": "too short / untrackable (flat colour, text, heavy blur)"})
            continue
        arr = np.array([s[1:] for s in seg], np.float32)
        # outlier-robust medians; jitter = std of pan after removing the median trend
        med = np.median(arr, 0)
        jitter = float(np.sqrt(((arr[:, :2] - med[:2]) ** 2).sum(1)).mean())
        shots.append({"start": round(t0 + a, 2), "end": round(t0 + b, 2), "tracked": len(seg),
                      "pan_x_pct_s": round(float(med[0]) * fps, 2), "pan_y_pct_s": round(float(med[1]) * fps, 2),
                      "zoom_pct_s": round(float(med[2]) * fps, 2), "rot_deg_s": round(float(med[3]) * fps, 2),
                      "shake_pct": round(jitter, 3)})
    return shots


if __name__ == "__main__":
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "cuts":
        print(json.dumps(cuts(rest[0], float(rest[1]) if len(rest) > 1 else 0.35)))
    elif cmd == "pair":
        off = 0.0
        if "--offset" in rest:
            k = rest.index("--offset"); off = float(rest[k + 1]); rest = rest[:k] + rest[k + 2:]
        pair(rest[0], rest[1], rest[2], [float(x) for x in rest[3:]], off)
    elif cmd == "strip":
        strip(rest[0], rest[1], float(rest[2]) if len(rest) > 2 else 2, rest[3] if len(rest) > 3 else None, rest[4] if len(rest) > 4 else None)
    elif cmd == "motion":
        print(json.dumps(motion(rest[0]), indent=0))
    elif cmd == "color":
        color(rest[0], [float(x) for x in rest[1:]])
