#!/usr/bin/env bash
# usage: scripts/copy-schema.sh <schema repository URL> <tag>
set -euo pipefail
repo=${1:?usage: scripts/copy-schema.sh <schema repository URL> <tag>}
tag=${2:?usage: scripts/copy-schema.sh <schema repository URL> <tag>}
dest="$(cd "$(dirname "$0")/.." && pwd)/src/beacon/telemetry-schema"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" "$repo" "$tmp"
(cd "$tmp" && shasum -a 256 -c --quiet SHA256SUMS)

rm -rf "$dest"
mkdir -p "$dest"
cp -R "$tmp/VERSION" "$tmp/SHA256SUMS" "$tmp/schemas" "$tmp/samples" "$dest/"
echo "telemetry schema $(cat "$dest/VERSION") copied to $dest"
