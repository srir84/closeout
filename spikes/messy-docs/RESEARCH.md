# Messy invoice images without a human: research and recommended pipeline

Date: 2026-11-27. Goal: about 30 realistic "messy" home-repair invoice images for testing Qwen3-VL. All 9 fields must have exact ground truth. Everything must be publishable in an MIT repo, with no real personal data.

**How to read the labels.** "Verified" means I checked it today on PyPI, GitHub, Hugging Face, or an official page, or ran it here. "Belief" means it comes from memory or a secondary source and I did not confirm it.

## 1. Short answer

- Do not use image-generation models to draw the invoice. Render each invoice yourself from `truth.json`, then damage the image in code. The truth stays exact because you wrote the text.
- **Augraphy** (MIT) is the best ready-made damage library. It has photocopy, fax, ink bleed, stains, shadows and folds. It has **no** camera perspective, page curl or crumple effect. Its defaults are far too strong. Use tuned settings only.
- Write the crumple, curl and camera angle yourself with OpenCV, in about 60 lines. One random "height map" drives both how the paper bends and how it is shaded. I built a prototype here. It looks convincing, and Tesseract still read the total correctly.
- For handwriting, use Google Fonts handwriting faces (OFL or Apache, licenses verified) plus per-letter jitter. Handwriting AI models exist with MIT code. The cleanest one is **Emuru**, which is trained only on synthetic font data. The others are trained on IAM, which is non-commercial only. They need a GPU and are optional.
- Use hosted image models (gpt-image-2, Nano Banana Pro) only for backgrounds and textures: tables, counters, coffee rings. The terms let you own and publish the output. Their small-text accuracy on dense numeric documents is not proven.
- Real datasets (CORD, SROIE, FUNSD, RVL-CDIP, DocILE) contain no home-repair contractor invoices. Several are non-commercial or have unclear terms, and some hold real names. If you use them, use only CORD, downloaded at eval time, as a small sanity check.

## 2. Damage libraries

### Augraphy (verified)

| Item | Finding |
|---|---|
| License | MIT (PyPI classifier and repo LICENSE) |
| Latest release | 8.2.6, released 2023-12-31. No PyPI release since then. |
| Repo activity | Last commit 2025-07-20 (a bug fix). 601 stars. Effectively in light maintenance. |
| Works today? | Yes. I installed it in a Python 3.12 venv with numpy 2.5.5, OpenCV 5.1.0 and numba 0.68.1. All four test pipelines ran. |
| Effects on the dev branch (50+) | BadPhotoCopy, Faxify, InkBleed, InkMottling, BleedThrough, DirtyDrum, DirtyRollers, LowInkPeriodicLines, LowInkRandomLines, Stains, ShadowCast, LightingGradient, ReflectedLight, LowLightNoise, DepthSimulatedBlur, LensFlare, Moire, ColorPaper, BrightnessTexturize, NoiseTexturize, Markup (pen marks), Scribbles, WaterMark, Folding, BookBinding, PageBorder, Geometric, Squish, Jpeg, and others |
| Perspective / curl / crumple | **None.** `Geometric` only scales, translates, flips, crops, rotates and pads (verified in the docs). `Folding` is a pair of local perspective warps along a line. |
| Gotchas (verified here) | Defaults are extreme. Default BadPhotoCopy + DirtyDrum buried the page in black streaks. Default LightingGradient + ReflectedLight washed out the whole page. Default Folding bends text lines into a wave instead of adding a fold shadow. Default Stains look like grey blobs, not coffee. `Scribbles` downloads fonts from the internet, and the paper utilities download from Figshare, so avoid both for offline, reproducible runs. It writes an `augraphy_cache/` folder in the working directory. ShadowCast and LightingGradient took about 26 s on a 2000 px image. |

Smoke test, scored by Tesseract reading the `TOTAL $19,300.00` line:

| Recipe | Result |
|---|---|
| Faxify + InkMottling | Read correctly |
| Photocopy (defaults) | Amount read. Ugly and unrealistic. |
| Fold + stains (defaults) | Read as `$19,300.09`. A real misread. |
| Phone light (defaults) | Nothing read. The page was washed out. |

The prototype script is `try_crumple2.py` in this folder, with two sample outputs.

### Other tools

| Tool | License / status (verified) | Use here? |
|---|---|---|
| DocCreator | LGPL-3.0, C++ desktop app (Qt is my belief), last push 2026-10. Effects: bleed-through, ink damage, blur, holes, paper deformation, phantom characters. | No. It is a GUI app and not a Python library. |
| Albumentations | The original MIT repo is **archived** (last release 2.0.8). The successor, AlbumentationsX, is **AGPL** or commercial. | Only pin `albumentations==2.0.8` if you really need its `Perspective` / `ElasticTransform`. Plain OpenCV does the same job. |
| ocrodeg (NVlabs) | **No license file**, last push 2020 | No. Copying it is not allowed. Its idea (smooth random displacement) is trivial to rewrite. |
| Genalog (Microsoft) | MIT, "maintenance mode, limited support", Python 3.6–3.8 badges, needs WeasyPrint system libraries | No |
| shabby-pages (Sparkfish) | MIT, archived | No |
| UVDoc / UVDoc-Dataset | Code MIT. Captured real 3D paper shapes (grids and UV maps) that warp a document texture onto real crumpled-paper geometry. The page names no license for the dataset itself. Its backgrounds come from DTD, which has its own terms. | Optional. It gives the most realistic geometry, but the dataset license is unclear. Use it only as a local tool and do not commit its meshes. Belief: the mesh data is not needed in the repo. |
| Doc3D / doc3D-renderer | Renderer repo has **no license**. I could not find the dataset terms. | No |
| Inv3D (warped invoices, KIT) | CC BY-NC-SA 4.0, and some files are academic-only | No. Non-commercial data conflicts with an MIT repo. Its generator code is MIT. |
| BlenderProc (cloth sim crumple) | GPL-3.0, active | Possible but heavy, more than a day of work. Belief: rendered images are not covered by the GPL. |
| page-dewarp | MIT, active | No. It removes warp. |

**Geometric warping recommendation:** write it yourself with OpenCV (Apache-2.0) using `cv2.remap` and `cv2.warpPerspective`. My prototype is `try_crumple2.py` in this folder. It works like this:

1. Make a height map from 3 sizes of smooth noise, passed through `1 - |noise|` so ridges form, plus 0–2 straight "tent" folds.
2. **Bend.** Move pixels by the gradient of a *heavily blurred* copy of the height map, so text bends smoothly. My first try moved pixels by the fine detail too. Letters then wobbled one by one, and the total looked like `$19,30 0.00`.
3. **Shade.** Light the page with the *fine* height map: brightness = dot product of the surface normal and a light direction.
4. Paste the page on a background with a soft drop shadow. Apply a random perspective tilt, light falloff and sensor noise. Downscale and save as JPEG at quality 60–75.

Result: it looks like a crumpled sheet photographed on a wood table, and Tesseract read the total correctly. It runs in about 2.4 s per image.

## 3. Handwriting

### Fonts (license folder verified in the google/fonts GitHub repo)

| License | Fonts |
|---|---|
| OFL | Caveat, Caveat Brush, Reenie Beanie, Nanum Pen Script, Nanum Brush Script, Indie Flower, Kalam, Patrick Hand, Gochi Hand, Shadows Into Light, Covered By Your Grace, Gloria Hallelujah, Nothing You Could Do, Zeyada, Dawning of a New Day, La Belle Aurore, Cedarville Cursive, Just Me Again Down Here, Sue Ellen Francisco, Handlee, Architects Daughter, Annie Use Your Telescope, Mynerve, Gaegu, Kristi, Sriracha |
| Apache-2.0 | Homemade Apple, Rock Salt, Permanent Marker (good for "marker on a clipboard"), Just Another Hand, Walter Turncoat, Crafty Girls, Schoolbell |

- You can ship OFL and Apache fonts inside an MIT repo. Keep each font's license file next to it.
- Images rendered with these fonts carry no restrictions.
- Belief: the best "contractor's scrawl" faces are Reenie Beanie, Nothing You Could Do, Zeyada, Rock Salt and Homemade Apple. Caveat and Kalam are the most legible.

**Making fonts look less like fonts.** These tricks are cheap and in my belief they matter most.

- Render letter by letter. Give each letter a random rotation of ±4°, a scale of ±8%, and a baseline drift that wanders along the line.
- Vary stroke darkness with pen pressure: multiply alpha by smooth noise.
- Use a blue ballpoint color (about RGB 30,40,120) or a pencil grey with a grain texture.
- Spread words unevenly.
- Use 2–3 fonts per document: one for the fields, a sloppier one for notes.
- Add a strike-through and a rewritten amount on some documents. The truth is the rewritten amount.

### Handwriting AI models (GitHub API, verified)

| Model | Code license | Training data | Notes |
|---|---|---|---|
| **Emuru** (aimagelab, 2025) | MIT. The HF model card says MIT. | `font-square-v2`, a **synthetic** dataset made from fonts, MIT on HF | 0.7B parameters. Loads via `transformers` with `trust_remote_code`. Copies the style of a reference image. Best license story. Speed on a Mac is unknown. |
| One-DM (2024–25) | MIT, 582 stars, active | Training commands use "IAM64" folders | Word-level only. Needs a GPU and Google Drive weights. |
| DiffusionPen (2024) | MIT | IAM (belief) | Word-level |
| VATr (2025) / Handwriting-Transformers | MIT | IAM (belief) | GAN-based |
| SDT (2025) | MIT, 1.4k stars | Online Chinese/Japanese, plus English (belief) | Stroke-based |
| sjvasquez/handwriting-synthesis (Graves RNN) | **No license** (repo API shows none) | IAM On-Line | Do not use, despite its fame |
| swechhasingh / X-rayLaser Graves ports | MIT | IAM On-Line | Belief: TensorFlow 1 or old PyTorch, fiddly |

**IAM's terms (official page, via search snippet): "non-commercial research purpose only."**

- Belief: whether a model trained on IAM taints its outputs is legally unsettled.
- For a clean MIT repo, prefer fonts or Emuru.
- Which looks most realistic? Belief: the diffusion models (One-DM, DiffusionPen) look most like real handwriting in their papers. Emuru varies more in style. Fonts with jitter are the most controllable, and for a test set that matters more.
- **Recommended for the 1-day build:** fonts plus jitter. Emuru is a "phase 2" option for 3–5 extra fully handwritten pages, if it runs locally.

## 4. Hosted image generators

| Item | Finding |
|---|---|
| Models today (verified) | OpenAI gpt-image-2 (API, April 2026; listed as retiring Feb 2027 on OpenRouter) and gpt-image-2.5 variants. Google Nano Banana Pro / Nano Banana 2.x. |
| Exact text | An independent img.ly study (26 models) found gpt-image-2, GPT Image 1.5 and Nano Banana Pro rendered every short required string exactly. It did **not** break out results for dense, multi-line, numeric text. Its top-model errors were "dropped words, stray characters, invented punctuation". No invoice benchmark exists. Belief: a full invoice with license numbers, emails and totals will often have at least one wrong character. Edit mode ("put this invoice photo on a counter, crumpled") can quietly change digits. |
| OpenAI terms (effective 2026-01-01, verified) | "We hereby assign to you all our right, title, and interest, if any, in and to Output." Banned: "Use Output to develop models that compete with OpenAI" and claiming output was made by a human. |
| Google Gemini API terms (effective 2026-03-23, verified) | "Google won't claim ownership over that content." You are "responsible for your use of generated content, and … anyone you share it with." You may not use the service "to develop models that compete". |
| Publishing under MIT | Belief, not legal advice: OK. You own the output, or it is uncopyrightable in the US, and either way you can include it. Label these files "AI-generated" in a README. Belief: Google adds an invisible SynthID watermark and OpenAI adds C2PA metadata. Neither matters for a test set. The "competing models" clause binds you, not downstream users. Evaluating a local model is not developing a competing model. |
| Recommended use | **Backgrounds and textures only:** kitchen counter, truck dashboard, clipboard, wood table, a coffee-ring stain on white (you multiply it onto the page), a crumpled-paper shading map. About 10–15 images total costs well under $5. No invoice text is ever sent through the generator, so truth is unaffected. |

Free alternative: draw backgrounds in code, or take textures from a CC0 source. Belief: Poly Haven and ambientCG textures are CC0, so check them before use.

## 5. Real datasets (as an eval-time sanity check only)

| Dataset | License (verified unless noted) | Content | Real personal data? | Home-repair? |
|---|---|---|---|---|
| **CORD v2** | CC BY 4.0 | 1,000 Indonesian shop and restaurant receipts, phone photos | Store info dropped from the labels "due to Indonesian legal issues". Images are real receipts (belief: few customer names). | No |
| SROIE (ICDAR 2019) | I could not find official terms (the RRC site failed a TLS check). HF mirrors claim CC BY 4.0 or MIT. Those are uploader claims. | 1,000 Malaysian shop receipts | Real store names and addresses | No |
| FUNSD | **"solely for non-commercial, research and educational purposes"**, "may never be made generally available" | 199 scanned forms, from RVL-CDIP (belief) | Yes, real names on tobacco-industry forms (belief) | No |
| RVL-CDIP | "other". Points to the Legacy Tobacco Library copyright page. | 400k grayscale scans of at most 1000 px, including an "invoice" class (label 11) | Yes, real 1980s–90s business documents | No. Old industry invoices. |
| DocILE | Code MIT. Data needs a token from rossum.ai, and the terms were not found. | 6,680 real business documents, mostly invoices, plus 100k synthetic | Real company data (belief) | No |
| mychen76/invoices-and-receipts_ocr_v1/v2 | No license stated | About 2.2k invoices, names look generated | Unknown | No |
| katanaml-org/invoices-donut-data-v1 | MIT (uploader) | Small, synthetic-looking invoices | Belief: no | No |
| WildReceipt | Apache-2.0 (HF mirror) | Receipts | Real stores | No |
| WildDoc (ByteDance, 2025) | Code GPL-3.0, no data license stated | Real phone photos of DocVQA/ChartQA pages, 4 conditions each | Same as the source datasets | No, but it is the closest "real photo" benchmark |

**Recommendation:** skip real data for the 9-field test. None of these sets has contractor invoices, licenses or warranty fields.

If you want a quick "does the model survive real photos" check:

- Use **CORD test split only**, CC BY 4.0, fetched by a script at eval time with `datasets.load_dataset("naver-clova-ix/cord-v2", split="test")`.
- Score only total-amount accuracy.
- Never commit images, and never send them to hosted models.
- Report only aggregate numbers.

## 6. Published synthetic-document generators

| Tool | License (verified) | What it does | Fit |
|---|---|---|---|
| SynthDoG (inside `clovaai/donut`) | MIT. Repo last pushed 2024-07. | Renders random text on paper textures over background photos, with perspective and lighting effects, via SynthTIGER | It is the *idea* to copy: page over background plus effects. Its text is random, not form-shaped. |
| SynthTIGER | MIT | Text-line images | No |
| Genalog | MIT, maintenance mode | HTML template, then degradation, then OCR alignment | Same idea as the plan below, but outdated |
| TextRecognitionDataGenerator | MIT, active | Single text lines | No |
| SynthText | Apache-2.0 | Text pasted into scene photos | No |
| Inv3D generator | Code MIT, data NC | Invoice templates, then 3D warp in Blender | Data unusable. The idea matches. |
| Recent benchmarks: DocPTBench (2025-11), Real5-OmniDocBench, MDPBench, PureDocBench (2026) | Paper CC BY 4.0, data licenses not checked | These show VLMs drop sharply on photographed documents. PureDocBench pairs Clean, Digital-simulated and Real versions of each page. | Supports doing exactly this test |

## 7. Recommended pipeline (about 1 day for a coding agent)

Put it in a new script next to `make_docs.py`, for example `make_messy.py`. Reuse `make_job`, the layouts and the `truth.json` schema. Add an `effects` list per document, recording each effect and its parameters. Use a fixed seed per document.

**Dependencies:**

- `augraphy==8.2.6`
- `opencv-python`, `numpy`, `Pillow`
- OFL and Apache font files, committed under `fonts/` with their license files
- Optional: `python-doctr` or `paddleocr` (both Apache-2.0) as a second reader

### Steps

1. **Truth first.** Make `truth.json` entries, which hold the 9 fields, before any drawing. Also store the exact *printed string* of each field: the date as rendered, the total as rendered (`$19,300.00` vs `19300`), and the license as written.
2. **Render clean.** Draw at 200–300 dpi in PIL: printed layouts, handwritten layouts, form-plus-handwriting, carbon-copy and thermal. Save the clean PNG and the box of each field. PIL `textbbox` gives these, so a reader check can crop them.
3. **Degrade.** Apply the recipe for the document's category (table below). Use only a whitelist of Augraphy effects with tuned values. Keep the moves in code (pixel shifts and perspective) mild enough that text never tears.
4. **Multi-page.** Damage each page on its own. Then write the pages as one PDF with Pillow: `img.save("x.pdf", save_all=True, append_images=[...])`. Belief: Pillow's PDF writer is fine for images. Put the total on page 2.
5. **Readability gate.** Do not ask a person.
   - Reader A: Tesseract, or doctr/PaddleOCR, on the full image. Normalise the text by removing spaces, `,` and `$`, and fixing `O→0` and `l→1`. Then look for each printed field string, allowing at most 1 character difference for long strings and none for the total.
   - Reader B: one strong hosted VLM. Prompt: "transcribe exactly the total, license number and email". This is allowed because all the data is fake.
   - Keep the image if **either** reader finds the total *and* at least 7 of 9 fields. Otherwise reduce effect strength by about 30% and retry, up to 3 times.
   - If it still fails, reject it. Or keep it with `"readable": false`, scored separately as "even hard for strong readers".
   - Log every attempt in `truth.json`.
6. **Contact sheet.** Save a thumbnail grid and look at it for 2 minutes. This catches "technically readable but obviously fake" images. Not required for correctness.
7. **Determinism.** Augraphy uses numpy and `random` internally, so seed both before each image. Belief: some effects use numba and may still vary slightly between machines. Commit the output images, not just the script.

### Recipes for about 30 images (about 5 per category)

| Category | Render | Damage (all strengths random within the range) |
|---|---|---|
| **1. Crumpled phone photo** | Any printed layout | Height-map crumple: 3 noise sizes, displacement blur σ≈25 px, displacement scale 15–30, 0–2 folds. Lambert shading, light from a random upper side. Paste on a background (a generated kitchen-counter or wood image), perspective tilt with corners moved 3–12% of the width, drop shadow. `ShadowCast(shadow_opacity_range=(0.25,0.5))` for a hand or phone shadow. One-sided falloff of 20–40%. Gaussian noise σ 3–6. Downscale to 0.5–0.7. JPEG at 60–75. |
| **2. Folded-in-thirds letter on a counter** | Classic layout | Two horizontal tent folds at 1/3 and 2/3 of the height, made with the height map and stronger shading along the creases. Small crumple. Coffee ring: a radial ring mask (outer alpha 0.35, brown about RGB 120,80,40) multiplied on, about 50% of the time *not* over the total. Then do the category 1 camera step. |
| **3. Photocopy / fax / low-res scan** | Form or classic layout | `InkBleed(intensity_range=(0.2,0.4))`, `BadPhotoCopy(noise_value=(100,180), noise_sparsity=(0.7,0.95), noise_concentration=(0.05,0.2))` (belief: this is the light setting; test it), `LowInkPeriodicLines(count_range=(1,3))`, a 0.5–1.5° skew. Fax: `Faxify(monochrome=1, halftone=1)` at 100–150 dpi, or downscale to 0.4 and threshold. Photocopy grey edges: draw a dark border band. JPEG at 50. |
| **4. Handwritten invoice-book page (carbon copy)** | A pre-printed invoice-book form in red or black ink on pink, yellow or white paper (`ColorPaper`), with fields filled in a handwriting font with jitter | Carbon look: draw the handwriting twice, the second copy offset 1–2 px in blue-purple at 40% alpha, blurred σ 0.8. Alpha drops with "pen pressure" noise. Patchy carbon via `InkMottling`. Torn perforation edge: a random jagged mask on one side. Then a mild category 1 camera step. |
| **5. Printed form with handwritten fixes** | Modern or form layout | The printed total is struck through with a 2–3 px wavy line, and the new total is handwritten in the margin. **Truth = handwritten total.** A handwritten "10 yr warranty" note, `Markup(markup_type="underline")` near the date, and a pen signature scribble drawn as a Bezier stroke. Then a scan (category 3, light) or a photo (category 1, light). |
| **6. Faded thermal receipt** | Narrow 576 px receipt (80 mm at 203 dpi, belief) in a mono or dot font | Fade: brightness gamma 1.8–2.8 on the ink only. A horizontal fade gradient, since one end fades more. `LowInkRandomLines`. A slight grey-yellow paper. The long strip curls: a cylindrical curl warp along its length. Photograph it on a background with the category 1 camera step. |
| **(mixed) Multi-page PDF** | 2–3 pages: invoice, terms and a warranty page; total on page 2 or a summary on page 3 | Page 1 a scan, page 2 a slightly rotated photo, page 3 clean. Fold this into categories 3 and 5 so the total stays at about 30. |

### Keeping the test honest

- Spread "trap" placement: total near a fold or stain in some images, far from it in others.
- Record in `truth.json` which fields are hard (`"degraded_fields": ["total_amount"]`), so scores can be split later.
- Never let damage *change* a value, for example a stain covering one digit so `8` looks like `3`. The gate catches this, and the per-field boxes make it easy to keep stains off value boxes when you want.
- Keep the old clean set. Score both sets with the same scorer, so "drop due to messiness" is a direct number.

## 8. Verified vs belief

**Verified today:**

- Augraphy license, version, last commit, the effect list on dev, and that it installs and runs (Python 3.12, numpy 2.5.5)
- The default-strength failures, the tuned crumple prototype, and the Tesseract results
- DocCreator license and activity, the Albumentations license change and archive
- ocrodeg and doc3D-renderer having no license, UVDoc code being MIT, Inv3D being NC
- Google Fonts license folders for every font listed
- Handwriting repo licenses and last push dates, Emuru's license and training data
- The OpenAI and Gemini terms quotes, gpt-image-2's existence, the img.ly results
- The licenses of CORD, FUNSD, RVL-CDIP, the HF mirror tags and DocILE
- SynthDoG/Donut, SynthTIGER and Genalog licenses and status

**Belief or unverified:**

- Quality ranking of handwriting models, and whether IAM-trained outputs are legally risky
- Accuracy of image generators on dense invoice text
- Copyright and watermark details
- SROIE's official terms
- Personal-data details in SROIE, FUNSD and DocILE
- The exact tuned Augraphy parameter values in the recipes (the crumple recipe was tested; the others were not)
- Thermal printer dpi
- Poly Haven and ambientCG being CC0

## Sources

- Augraphy: https://pypi.org/project/augraphy/ · https://github.com/sparkfish/augraphy · https://augraphy.readthedocs.io/en/latest/doc/source/list_of_augmentations.html
- DocCreator: https://github.com/DocCreator/DocCreator · https://doc-creator.labri.fr
- Albumentations licensing: https://albumentations.ai/blog/2025/01-albumentationsx-dual-licensing/ · https://albumentations.ai/docs/faq
- UVDoc: https://github.com/tanguymagne/UVDoc-Dataset · Inv3D: https://radar.kit.edu/radar/en/dataset/GPweoqzKRRZGOAEE · ocrodeg: https://github.com/NVlabs/ocrodeg
- Google Fonts license folders: https://github.com/google/fonts
- Emuru: https://huggingface.co/blowing-up-groundhogs/emuru · https://huggingface.co/datasets/blowing-up-groundhogs/font-square-v2 · One-DM: https://github.com/dailenson/One-DM · IAM: https://fki.tic.heia-fr.ch/databases/iam-handwriting-database
- OpenAI terms: https://openai.com/policies/row-terms-of-use/ · Gemini terms: https://aistudio.google.com/docs/terms · Text-rendering study: https://img.ly/ai-benchmarks/findings/text-reliability/ · gpt-image-2: https://community.openai.com/t/introducing-gpt-image-2-available-today-in-the-api-and-codex/1379479
- CORD: https://github.com/clovaai/cord · FUNSD terms: https://guillaumejaume.github.io/FUNSD/work · RVL-CDIP: https://huggingface.co/datasets/aharley/rvl_cdip · DocILE: https://github.com/rossumai/docile · WildDoc: https://github.com/bytedance/WildDoc
- Donut/SynthDoG: https://github.com/clovaai/donut · Genalog: https://github.com/microsoft/genalog · DocPTBench: https://arxiv.org/abs/2511.18434 · PureDocBench: https://arxiv.org/abs/2605.07492
