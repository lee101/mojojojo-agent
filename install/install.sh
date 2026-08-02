#!/bin/sh
# Install a checksummed mojojojo-agent release without requiring Python.
set -eu

repo=${MJJ_REPO:-lee101/mojojojo-agent}
version=${MJJ_VERSION:-latest}
install_dir=${MJJ_INSTALL_DIR:-"${HOME:?HOME is required}/.local/bin"}

system=${MJJ_UNAME_S:-$(uname -s)}
machine=${MJJ_UNAME_M:-$(uname -m)}
case "$system" in
  Linux) os=linux ;;
  Darwin) os=macos ;;
  *) echo "mjj: unsupported operating system: $system" >&2; exit 1 ;;
esac
case "$machine" in
  x86_64|amd64) arch=x86_64 ;;
  arm64|aarch64) arch=aarch64 ;;
  *) echo "mjj: unsupported architecture: $machine" >&2; exit 1 ;;
esac

asset="mjj-$os-$arch.tar.gz"
if [ -n "${MJJ_BASE_URL:-}" ]; then
  base=${MJJ_BASE_URL%/}
elif [ "$version" = latest ]; then
  base="https://github.com/$repo/releases/latest/download"
else
  base="https://github.com/$repo/releases/download/$version"
fi

temporary=$(mktemp -d "${TMPDIR:-/tmp}/mjj-install.XXXXXX")
trap 'rm -rf "$temporary"' EXIT HUP INT TERM

download() {
  source_url=$1
  destination=$2
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 15 "$source_url" -o "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 -O "$destination" "$source_url"
  else
    echo "mjj: curl or wget is required" >&2
    exit 1
  fi
}

echo "Downloading $asset..."
download "$base/$asset" "$temporary/$asset"
download "$base/SHA256SUMS" "$temporary/SHA256SUMS"

expected=$(awk -v name="$asset" '{ file=$2; sub(/^\*/, "", file); if (file == name) { print $1; exit } }' "$temporary/SHA256SUMS")
if [ -z "$expected" ]; then
  echo "mjj: $asset has no published checksum" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$temporary/$asset" | awk '{print $1}')
elif command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$temporary/$asset" | awk '{print $1}')
else
  echo "mjj: sha256sum or shasum is required to verify the download" >&2
  exit 1
fi
if [ "$actual" != "$expected" ]; then
  echo "mjj: checksum verification failed for $asset" >&2
  exit 1
fi

tar -xzf "$temporary/$asset" -C "$temporary"
mkdir -p "$install_dir"
if command -v install >/dev/null 2>&1; then
  install -m 0755 "$temporary/mjj" "$install_dir/mjj"
else
  cp "$temporary/mjj" "$install_dir/mjj"
  chmod 0755 "$install_dir/mjj"
fi

echo "Installed mjj to $install_dir/mjj"
case ":${PATH:-}:" in
  *":$install_dir:"*) ;;
  *) echo "Add $install_dir to PATH, then run: mjj auth --probe" ;;
esac
