#!/usr/bin/env bash
# Make an offline map file for NEO-M9N Monitor's Map page: vector tiles (OpenMapTiles
# schema) in an MBTiles file, built from OpenStreetMap *data* with Planetiler in a
# Podman container. No tile server, no account; run it at home, then use the map offline.
#
#   scripts/make-offline-map.sh isle-of-man
#   scripts/make-offline-map.sh great-britain --keep-downloads
#   scripts/make-offline-map.sh --dry-run england
set -euo pipefail

IMAGE="ghcr.io/onthegomap/planetiler:0.10.2"  # Pinned for reproducible maps.
HOSTS=(
  "ghcr.io and pkg-containers.githubusercontent.com: the Planetiler container image (first run only)"
  "download.geofabrik.de: the OpenStreetMap data extract for the area"
  "osmdata.openstreetmap.de: coastline/water polygons"
  "naciscdn.org: Natural Earth low-zoom data"
  "github.com and *.githubusercontent.com: lake centre lines and tile statistics"
)

usage() {
  cat <<'EOF'
Usage: make-offline-map.sh [options] AREA

Makes AREA.mbtiles for NEO-M9N Monitor's Map page (Offline mode).

AREA is a Geofabrik extract name, e.g. isle-of-man, monaco, wales, scotland, england,
great-britain, ireland-and-northern-ireland, germany. See https://download.geofabrik.de/.

Options:
  --output PATH      Where to write the map (default: ~/Documents/NEO-M9N/maps/AREA.mbtiles)
  --keep-downloads   Keep the downloaded data (about 1.3 GB plus the extract) for the next run
  --dry-run          Show what would run and which hosts would be contacted; download nothing
  --yes              Don't ask for confirmation
  --list             Explain area names
  -h, --help         Show this help
EOF
}

area="" output="" dry_run=0 keep=0 assume_yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --output) output="${2:?--output needs a path}"; shift 2 ;;
    --output=*) output="${1#--output=}"; shift ;;
    --keep-downloads) keep=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --yes|-y) assume_yes=1; shift ;;
    --list)
      echo "Area names are Geofabrik extract names: lower case, words joined with hyphens."
      echo "Examples: isle-of-man (small, quick to try), wales, scotland, england, great-britain, ireland-and-northern-ireland,"
      echo "germany, france, netherlands, us/california. Browse https://download.geofabrik.de/ for the full list."
      echo "Smaller areas build faster and make smaller files."
      exit 0 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) [ -z "$area" ] || { echo "Only one AREA, please." >&2; exit 2; }; area="$1"; shift ;;
  esac
done
[ -n "$area" ] || { usage >&2; exit 2; }
if ! [[ "$area" =~ ^[a-z0-9]+([/-][a-z0-9]+)*$ ]]; then
  echo "AREA should look like 'isle-of-man' or 'great-britain' (see --list)." >&2
  exit 2
fi

documents="$(xdg-user-dir DOCUMENTS 2>/dev/null || true)"
[ -n "$documents" ] && [ "$documents" != "$HOME" ] || documents="$HOME/Documents"
name="${area//\//-}"
output="${output:-$documents/NEO-M9N/maps/$name.mbtiles}"
work="${XDG_CACHE_HOME:-$HOME/.cache}/neo-m9n/offline-map-build"
mem_gb=$(awk '/MemAvailable/ {printf "%d", $2 / 1048576}' /proc/meminfo 2>/dev/null || echo 0)
heap_gb=$(( mem_gb / 2 )); [ "$heap_gb" -ge 2 ] || heap_gb=2; [ "$heap_gb" -le 16 ] || heap_gb=16
command=(podman run --rm -e "JAVA_TOOL_OPTIONS=-Xmx${heap_gb}g" -v "$work:/data:Z" "$IMAGE"
         --download --area="$area" --output=/data/output.mbtiles --force)

echo "Offline map for: $area"
echo "Output:          $output"
echo "Working folder:  $work"
echo "Container image: $IMAGE"
echo
echo "This contacts only these hosts (data downloads, not map tile servers):"
for host in "${HOSTS[@]}"; do echo "  - $host"; done
echo "They see your IP address and which area you chose. The app itself never downloads map files."
echo
echo "Command: ${command[*]}"
if [ "$dry_run" = 1 ]; then
  echo "Dry run: nothing was downloaded or written."
  exit 0
fi

command -v podman >/dev/null || { echo "Podman is needed: sudo dnf install podman" >&2; exit 1; }
mkdir -p "$work" "$(dirname "$output")"
free_gb=$(df -Pk "$work" | awk 'NR == 2 {printf "%d", $4 / 1048576}')
echo
echo "Free disk in the working folder: ${free_gb} GB; memory available: ${mem_gb} GB (using up to ${heap_gb} GB)."
echo "A county or small country takes minutes; Great Britain or Germany can take an hour and needs"
echo "several GB of free memory and around 10-20 GB of free disk while building."
if [ "$free_gb" -lt 4 ]; then
  echo "Less than 4 GB free in $work; free some space first." >&2
  exit 1
fi
if [ "$assume_yes" != 1 ]; then
  read -r -p "Continue? [y/N] " answer
  case "$answer" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 1 ;; esac
fi

"${command[@]}"
mv -f "$work/output.mbtiles" "$output"
rm -rf "$work/tmp"
if [ "$keep" != 1 ]; then
  rm -rf "$work/sources" "$work/tile_weights.tsv.gz"
fi
echo
echo "Done: $output ($(du -h "$output" | cut -f1))"
echo "In NEO-M9N Monitor: Map page, Map storage (disk button), Offline map file, Choose; then select Offline."
