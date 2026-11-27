# Fonts used by the messy-set and town-reply generators

All fonts were downloaded unchanged from the [google/fonts](https://github.com/google/fonts) repository
(`main` branch, 2026-11-27). Each folder keeps the font's own license file next to the font.
OFL and Apache-2.0 fonts may be shipped inside this MIT repo. Images rendered with them carry no
restrictions.

| Folder | Font file(s) | License | Upstream folder | Used for |
|---|---|---|---|---|
| `lato/` | Lato-Regular.ttf, Lato-Bold.ttf | SIL OFL 1.1 (`OFL.txt`) | `ofl/lato` | Modern invoice layout, band letterhead |
| `ptserif/` | PT_Serif-Web-Regular.ttf, PT_Serif-Web-Bold.ttf | SIL OFL 1.1 | `ofl/ptserif` | Classic letter layout, clerk letters |
| `ptsans/` | PT_Sans-Web-Regular.ttf, PT_Sans-Web-Bold.ttf | SIL OFL 1.1 | `ofl/ptsans` | Office form, invoice-book print |
| `courierprime/` | CourierPrime-Regular.ttf, CourierPrime-Bold.ttf | SIL OFL 1.1 | `ofl/courierprime` | Receipts, fax header, typewriter memo |
| `kalam/` | Kalam-Regular.ttf, Kalam-Bold.ttf | SIL OFL 1.1 | `ofl/kalam` | Handwriting (fields, fixes) |
| `patrickhand/` | PatrickHand-Regular.ttf | SIL OFL 1.1 | `ofl/patrickhand` | Handwriting |
| `gochihand/` | GochiHand-Regular.ttf | SIL OFL 1.1 | `ofl/gochihand` | Handwriting |
| `indieflower/` | IndieFlower-Regular.ttf | SIL OFL 1.1 | `ofl/indieflower` | Handwriting |
| `caveat/` | Caveat-Variable.ttf (upstream name `Caveat[wght].ttf`) | SIL OFL 1.1 | `ofl/caveat` | Handwriting, notes |
| `reeniebeanie/` | ReenieBeanie.ttf | SIL OFL 1.1 | `ofl/reeniebeanie` | Scrawled notes, signatures |
| `permanentmarker/` | PermanentMarker-Regular.ttf | Apache-2.0 (`LICENSE.txt`) | `apache/permanentmarker` | Marker-written corrected totals |
| `justanotherhand/` | JustAnotherHand-Regular.ttf | Apache-2.0 | `apache/justanotherhand` | Notes |
| `homemadeapple/` | HomemadeApple-Regular.ttf | Apache-2.0 | `apache/homemadeapple` | Signatures |

The OFL's Reserved Font Names (for example "Lato", "PT Sans", "Gochi Hand") only matter if a font is
modified and redistributed; these files are unmodified. Caveat was renamed on disk only to avoid
brackets in the path; the font data is unchanged.

No handwriting AI model or IAM-derived data is used: handwriting is these fonts drawn letter by letter
with random tilt, scale and baseline drift (`make_messy.hand`).
