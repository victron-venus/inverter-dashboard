# Dashboard screenshot artwork

`dashboard-hero.png` is a perspective presentation of the existing
`Screenshot.png`, which shows **Inverter Dashboard v1.5.0**. It is a historical
interface illustration, not a screenshot of the latest release.

The renderer crops only the top 184 pixels containing the browser controls and
local address, rounds the outer corners, then projects the remaining image onto
a tilted plane with a soft shadow. It preserves the screenshot's application content; no UI elements,
labels, charts or readings are generated or redrawn. The original image remains
available through the README's image link for detailed viewing.

## Regenerate

Install ImageMagick 7, then run from the repository root:

```sh
bash scripts/render-docs-hero.sh
```

The output is a static 1540 × 1400 PNG with no runtime JavaScript or external
image service. The script uses a temporary directory and replaces only the
generated hero after rendering succeeds. Repeated rendering with the same
ImageMagick version and source produces the same file; different ImageMagick
versions can produce different resampling results.

The initial render uses ImageMagick 7.1.2-31. The original screenshot's SHA-256 is
`6947c01530d5a224eb5cb562e4c1cd174b465331403c732e021b5b6c412a80f9`.

When updating the source screenshot, review its version, browser-chrome crop
and projection coordinates, regenerate the hero, and update the caption in
`README.md`. Do not use presentation images as test or release acceptance
evidence.
