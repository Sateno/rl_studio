#!/usr/bin/env bash
# Project-local Pango build headers for images that ship the Pango runtime
# libraries but not libpango1.0-dev (manimpango 0.6.1 has no Linux wheel and is
# compiled during setup).
#
# No administrator access and no system-wide changes: the Ubuntu -dev packages
# that match the INSTALLED runtime libraries exactly are downloaded with apt
# (signed archive index; each .deb is also checked against its SHA-256 there)
# and unpacked into the git-ignored .pango-sysroot/ folder. They are only used to
# compile manimpango; the compiled module links the system libraries, so the
# virtual environment does not depend on this folder afterwards.
#
# Usage: bash scripts/pango_sysroot.sh             # create or reuse the folder
#        bash scripts/pango_sysroot.sh --pkg-config-path
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SYSROOT="$ROOT/.pango-sysroot"
TREE="$SYSROOT/root"
MULTIARCH="$(gcc -print-multiarch 2>/dev/null || echo x86_64-linux-gnu)"
SYSTEM_LIBDIR="/usr/lib/$MULTIARCH"
PKG_CONFIG_DIRS="$TREE/usr/lib/$MULTIARCH/pkgconfig:$TREE/usr/share/pkgconfig"
UBUNTU_SOURCES=/etc/apt/sources.list.d/ubuntu.sources
MIN_PANGOCAIRO=1.30

# dev package (headers, pkg-config files)   installed runtime package it must match
PACKAGES=(
  "libpango1.0-dev libpango-1.0-0"
  "libharfbuzz-dev libharfbuzz0b"
  "libfribidi-dev libfribidi0"
  "libthai-dev libthai0"
  "libdatrie-dev libdatrie1"
  "libxft-dev libxft2"
  "libgraphite2-dev libgraphite2-3"
)

if [[ "${1:-}" == --pkg-config-path ]]; then
  echo "$PKG_CONFIG_DIRS"
  exit 0
fi

fail() { echo "pango_sysroot: $*" >&2; exit 1; }

for tool in apt-get apt-cache dpkg-query dpkg-deb pkg-config sha256sum cc; do
  command -v "$tool" >/dev/null 2>&1 || fail "required tool '$tool' is not available"
done

# Exact versions to fetch, taken from the installed runtime libraries.
wanted=()
for pair in "${PACKAGES[@]}"; do
  read -r dev runtime <<<"$pair"
  version="$(dpkg-query -W -f='${Version}' "$runtime" 2>/dev/null)" ||
    fail "runtime package $runtime is not installed; the image must provide the Pango runtime libraries"
  wanted+=("$dev=$version")
done
manifest_packages="$(printf '%s\n' "${wanted[@]}")"

# The headers are usable when pkg-config resolves pangocairo and a test program compiles and links.
check_headers() {
  local flags
  PKG_CONFIG_PATH="$PKG_CONFIG_DIRS" pkg-config --print-errors --exists "pangocairo >= $MIN_PANGOCAIRO" || return 1
  read -ra flags <<<"$(PKG_CONFIG_PATH="$PKG_CONFIG_DIRS" pkg-config --cflags --libs pangocairo)"
  printf '#include <pango/pangocairo.h>\nint main(void) { return pango_cairo_font_map_get_default() == NULL; }\n' |
    cc -x c - -o "$SYSROOT/link-check" "${flags[@]}"
}

if [[ -f "$SYSROOT/manifest.txt" ]] &&
   [[ "$(grep -v '^#' "$SYSROOT/manifest.txt" | cut -d' ' -f1)" == "$manifest_packages" ]] &&
   check_headers 2>/dev/null; then
  echo "Project-local Pango headers are up to date: $SYSROOT"
  exit 0
fi

echo "Preparing project-local Pango headers in $SYSROOT"
rm -rf "$SYSROOT"
mkdir -p "$SYSROOT/apt/lists/partial" "$SYSROOT/apt/cache/archives/partial" "$SYSROOT/apt/sourceparts" "$SYSROOT/debs" "$TREE"

# Project-local apt state; only the image's Ubuntu archive sources are used.
apt_options=(
  -o "Dir::State::Lists=$SYSROOT/apt/lists"
  -o "Dir::Cache=$SYSROOT/apt/cache"
  -o "Debug::NoLocking=1"
)
if [[ -f "$UBUNTU_SOURCES" ]]; then
  apt_options+=(-o "Dir::Etc::SourceList=$UBUNTU_SOURCES" -o "Dir::Etc::SourceParts=$SYSROOT/apt/sourceparts")
fi
apt-get "${apt_options[@]}" -q update >"$SYSROOT/apt/update.log" 2>&1 ||
  fail "apt-get update failed; see $SYSROOT/apt/update.log"
(cd "$SYSROOT/debs" && apt-get "${apt_options[@]}" -q download "${wanted[@]}") ||
  fail "could not download ${wanted[*]}"

{
  echo "# package=version sha256 (checked against the signed Ubuntu archive index)"
  for item in "${wanted[@]}"; do
    dev="${item%%=*}"; version="${item#*=}"
    deb="$(ls "$SYSROOT/debs/${dev}_${version#*:}_"*.deb)"
    expected="$(apt-cache "${apt_options[@]}" show "$item" | awk '/^SHA256:/ {print $2; exit}')"
    actual="$(sha256sum "$deb" | cut -d' ' -f1)"
    [[ -n "$expected" && "$actual" == "$expected" ]] || fail "SHA-256 mismatch for $deb"
    dpkg-deb -x "$deb" "$TREE"
    echo "$item $actual"
  done
} >"$SYSROOT/manifest.tmp"

# pkg-config files: point the prefix at the unpacked tree.
for pc in "$TREE/usr/lib/$MULTIARCH/pkgconfig"/*.pc "$TREE/usr/share/pkgconfig"/*.pc; do
  [[ -f "$pc" ]] && sed -i "s#^prefix=/usr\$#prefix=$TREE/usr#" "$pc"
done
# Development symlinks (libfoo.so -> libfoo.so.N) must reach the installed system libraries.
for link in "$TREE/usr/lib/$MULTIARCH"/*.so; do
  [[ -L "$link" ]] || continue
  target="$SYSTEM_LIBDIR/$(basename "$(readlink "$link")")"
  if [[ -e "$target" ]]; then ln -sfn "$target" "$link"; else rm -f "$link"; fi
done

check_headers || fail "pangocairo >= $MIN_PANGOCAIRO headers are still not usable"
mv "$SYSROOT/manifest.tmp" "$SYSROOT/manifest.txt"
echo "Project-local Pango headers ready (pangocairo $(PKG_CONFIG_PATH="$PKG_CONFIG_DIRS" pkg-config --modversion pangocairo))"
