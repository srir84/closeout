"""Prototype: crumple/fold via one height map -> displacement + shading, then perspective onto a table, light falloff."""
import sys
import numpy as np, cv2
from augraphy import AugraphyPipeline, ShadowCast, Jpeg, BrightnessTexturize

src = cv2.imread(sys.argv[1]).astype(np.float32)
rng = np.random.default_rng(int(sys.argv[3]) if len(sys.argv) > 3 else 5)
h, w = src.shape[:2]

def height_map(h, w, rng, crumple=1.0, folds=1):
    hm = np.zeros((h, w), np.float32)
    # crumple: several octaves of smooth noise, sharpened with abs() to make ridges
    for cell, amp in ((220, 1.0), (90, 0.35), (40, 0.1)):
        n = rng.standard_normal((h // cell + 2, w // cell + 2)).astype(np.float32)
        n = cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
        hm += amp * crumple * (1 - np.abs(n))
    # straight folds: tent function across a random line
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    for _ in range(folds):
        if rng.random() < 0.5:
            d = np.abs(ys - h * rng.uniform(0.3, 0.7))
        else:
            d = np.abs(xs - w * rng.uniform(0.3, 0.7))
        hm += 2.0 * np.clip(1 - d / (0.5 * max(h, w)), 0, 1)
    return hm * 18  # pixels of relief

hm = height_map(h, w, rng)
gy, gx = np.gradient(cv2.GaussianBlur(hm, (0, 0), 2))
dgy, dgx = np.gradient(cv2.GaussianBlur(hm, (0, 0), 25))  # displacement uses only the broad shape
ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
img = cv2.remap(src, xs + dgx * 25, ys + dgy * 25, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
# shading: Lambert with a light from upper-left
light = np.array([-0.5, -0.6, 1.0]); light /= np.linalg.norm(light)
nz = 1 / np.sqrt(gx ** 2 + gy ** 2 + 1)
shade = (-gx * nz * light[0] - gy * nz * light[1] + nz * light[2])
shade = 0.55 + 0.45 * shade / shade.max()
img = np.clip(img * shade[..., None], 0, 255)

# place on a "table": warm wood-ish noise background, perspective tilt
pad = int(0.15 * max(h, w))
H, W = h + 2 * pad, w + 2 * pad
bg = np.full((H, W, 3), (60, 85, 120), np.float32)  # BGR brownish
streak = cv2.resize(rng.standard_normal((H // 4, 8)).astype(np.float32), (W, H)) * 12
bg += streak[..., None]
canvas = bg.copy()
canvas[pad:pad + h, pad:pad + w] = img
mask = np.zeros((H, W), np.float32); mask[pad:pad + h, pad:pad + w] = 1
# soft drop shadow under page
sh = cv2.GaussianBlur(np.roll(mask, (18, 14), (0, 1)), (0, 0), 15)
canvas = np.where(mask[..., None] > 0, canvas, canvas * (1 - 0.5 * sh[..., None]))
q = np.float32([[pad, pad], [W - pad, pad], [W - pad, H - pad], [pad, H - pad]])
j = lambda s: rng.uniform(-s, s)
d = np.float32([[pad + 0.10 * w + j(20), pad + 0.04 * h + j(20)], [W - pad - 0.06 * w + j(20), pad + j(20)],
                [W - pad + 0.02 * w + j(20), H - pad + j(20)], [pad - 0.03 * w + j(20), H - pad - 0.02 * h + j(20)]])
M = cv2.getPerspectiveTransform(q, d)
out = cv2.warpPerspective(canvas, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
# light falloff (vignette + one-sided), phone noise, downscale
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
fall = 1 - 0.35 * (xx / W) - 0.25 * (((xx - W / 2) / W) ** 2 + ((yy - H / 2) / H) ** 2)
out = np.clip(out * fall[..., None] + rng.normal(0, 4, out.shape), 0, 255).astype(np.uint8)
out = AugraphyPipeline(ink_phase=[], paper_phase=[], post_phase=[ShadowCast(shadow_side="left", shadow_opacity_range=(0.3, 0.5), p=1)])(out)
out = cv2.resize(out, (int(W * 0.6), int(H * 0.6)), interpolation=cv2.INTER_AREA)
cv2.imwrite(sys.argv[2], out, [cv2.IMWRITE_JPEG_QUALITY, 72])
