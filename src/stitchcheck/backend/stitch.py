"""Stitch pitch along a horizontal seam, so phone tiles can hold as many stitches as StitchingNet tiles.

The seam row is the row band whose colour differs most from the fabric's median colour (thread contrasts with
fabric). Along that band, the colour-difference profile repeats once per stitch; the pitch is the first
autocorrelation peak after the first zero crossing. Weak peaks (textured fabrics, straight continuous thread) and
very short lags (the weave) are reported as not found.
"""
import numpy as np

MIN_STRENGTH = 0.3
MIN_PITCH = 8  # px; shorter repeats are the weave


def colour_contrast(rgb):
    a = rgb.astype(float)
    return np.linalg.norm(a - np.median(a.reshape(-1, 3), 0), axis=2)


def seam_row(contrast, band):
    rows = np.convolve(contrast.mean(1), np.ones(band) / band, mode="same")
    return int(np.argmax(rows))


def stitch_pitch(rgb, band_frac=0.05, min_strength=MIN_STRENGTH, min_pitch=MIN_PITCH):
    """rgb: H x W x 3 array with the seam running horizontally. Returns dict(pitch, strength, row, found)."""
    c = colour_contrast(rgb)
    h, w = c.shape
    band = max(3, int(h * band_frac))
    r = seam_row(c, band)
    prof = c[max(0, r - band // 2): r + band // 2 + 1].mean(0)
    k = max(5, w // 8)
    prof = prof - np.convolve(prof, np.ones(k) / k, mode="same")  # remove slow shading
    prof = prof[k // 2: len(prof) - k // 2]
    if len(prof) < 4 * min_pitch or prof.std() == 0:
        return dict(pitch=None, strength=0.0, row=r, found=False)
    ac = np.correlate(prof, prof, "full")[len(prof) - 1:]
    seg = (ac / ac[0])[:len(ac) // 2]
    zc = int(np.argmax(seg < 0))
    if zc < 2:
        return dict(pitch=None, strength=0.0, row=r, found=False)
    lag = zc + int(np.argmax(seg[zc:]))
    strength = float(seg[lag])
    return dict(pitch=int(lag), strength=round(strength, 3), row=r, found=lag >= min_pitch and strength >= min_strength)
