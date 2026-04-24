# Brand assets

Brand icons for Home Assistant's [Brands Proxy API][proxy-api] (available from
HA 2026.3.0 onwards). HA serves these automatically at
`/api/brand_images/custom_integrations/battery_badger/icon.png` etc., taking
priority over anything in the upstream `home-assistant/brands` CDN.

| File | Size | Purpose |
| --- | --- | --- |
| `icon.svg` | vector | Master source, copied from the backend `ui/public/logo.svg` |
| `icon.png` | 256×256 | HA Brands Proxy API (`icon.png`) |
| `icon@2x.png` | 512×512 | HA Brands Proxy API (`icon@2x.png`) |

## Regenerating the PNGs

HA requires icons trimmed of transparent whitespace and centred on a square
canvas. Render the SVG large, trim, then pad:

```bash
inkscape icon.svg --export-type=png --export-filename=_raw.png --export-width=1024 --export-height=1024
for size in 256 512; do
  magick _raw.png -trim +repage -resize "${size}x${size}" \
    -gravity center -background none -extent "${size}x${size}" "_out-${size}.png"
done
mv _out-256.png icon.png
mv _out-512.png icon@2x.png
rm _raw.png
```

[proxy-api]: https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api
