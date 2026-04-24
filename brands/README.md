# Brand assets

Source and pre-rendered icon files for the Battery Badger Home Assistant integration.

| File | Size | Purpose |
| --- | --- | --- |
| `icon.svg` | vector | Master source, copied from the backend `ui/public/logo.svg` |
| `icon.png` | 256×256 | `home-assistant/brands` `icon.png` (1x) |
| `icon@2x.png` | 512×512 | `home-assistant/brands` `icon@2x.png` (2x) |
| `icon-128.png` | 128×128 | README display |

## Submitting to `home-assistant/brands`

Home Assistant and HACS display integration icons from the
[home-assistant/brands](https://github.com/home-assistant/brands) repo. Custom
integrations live under `custom_integrations/<domain>/`.

To submit:

1. Fork `home-assistant/brands`.
2. Copy `icon.png` and `icon@2x.png` from this directory into
   `custom_integrations/battery_badger/` on the fork.
3. Open a PR. CI runs `python3 -m script.validate` which checks dimensions and
   transparent-space trim.
4. Once merged, remove `ignore: brands` from
   [.github/workflows/validate.yml](../.github/workflows/validate.yml) — the
   HACS action will then require the brand to exist.

## Regenerating the PNGs

`home-assistant/brands` requires icons trimmed of transparent whitespace and
centred on a square canvas. Render the SVG large, trim, then pad:

```bash
inkscape icon.svg --export-type=png --export-filename=_raw.png --export-width=1024 --export-height=1024
for size in 128 256 512; do
  magick _raw.png -trim +repage -resize "${size}x${size}" \
    -gravity center -background none -extent "${size}x${size}" "_out-${size}.png"
done
mv _out-128.png icon-128.png
mv _out-256.png icon.png
mv _out-512.png icon@2x.png
rm _raw.png
```
