#!/usr/bin/env bash
# Render the existing v1.5.0 screenshot without redrawing its interface.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_image="$repo_root/images/Screenshot.png"
output_image="$repo_root/images/dashboard-hero.png"

if ! command -v magick >/dev/null 2>&1; then
  printf '%s\n' 'ImageMagick 7 is required (the magick command).' >&2
  exit 1
fi

if [[ "$(magick identify -format '%wx%h' "$source_image")" != '1704x1826' ]]; then
  printf '%s\n' 'Screenshot dimensions changed; review the crop and perspective first.' >&2
  exit 1
fi

render_dir="$(mktemp -d "${TMPDIR:-/tmp}/inverter-dashboard-hero.XXXXXX")"
trap 'rm -rf -- "$render_dir"' EXIT

# The first 184 rows contain browser chrome, including the local server address.
# Keep every application row below it. Four control points project the same
# screenshot onto a tilted plane; labels, charts and values are not recreated.
magick "$source_image" -crop 1704x1642+0+184 +repage -alpha on \
  \( -size 1704x1642 xc:none -fill white \
     -draw 'roundrectangle 0,0 1703,1641 24,24' \) \
  -compose DstIn -composite -compose Over \
  -virtual-pixel transparent -filter Robidoux \
  -define distort:viewport=1540x1400+0+0 \
  -distort Perspective \
  '0,0 150,155 1703,0 1330,80 1703,1641 1370,1150 0,1641 195,1310' \
  -strip "$render_dir/panel.png"

magick "$render_dir/panel.png" -fill '#152033' -colorize 100 \
  -channel A -evaluate multiply 0.22 +channel -blur 0x18 \
  -strip "$render_dir/shadow.png"

magick -size 1540x1400 xc:'#f0f1f3' \
  "$render_dir/shadow.png" -geometry +0+22 -compose Over -composite \
  "$render_dir/panel.png" -geometry +0+0 -compose Over -composite \
  -strip -define png:exclude-chunk=date,time "PNG24:$render_dir/hero.png"

mv -- "$render_dir/hero.png" "$output_image"
printf 'Rendered %s\n' "$output_image"
