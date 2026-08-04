#!/bin/sh
# Install a checksummed mojojojo-agent release without requiring Python.
set -eu

usage() {
  cat <<'EOF'
Install the mjj coding agent.

Usage: install.sh [options]

  --version VERSION   release tag, for example v0.3.0 (default: latest)
  --install-dir DIR   binary destination (default: ~/.local/bin)
  --repo OWNER/REPO   GitHub repository (default: lee101/mojojojo-agent)
  --base-url URL      release asset directory; useful for mirrors/testing
  -h, --help          show this help

The matching MJJ_VERSION, MJJ_INSTALL_DIR, MJJ_REPO, and MJJ_BASE_URL
environment variables provide the same settings. Command-line options win.
EOF
}

repo=${MJJ_REPO:-lee101/mojojojo-agent}
version=${MJJ_VERSION:-latest}
if [ -n "${MJJ_INSTALL_DIR:-}" ]; then
  install_dir=$MJJ_INSTALL_DIR
elif [ -n "${HOME:-}" ]; then
  install_dir=$HOME/.local/bin
else
  install_dir=
fi
base_override=${MJJ_BASE_URL:-}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version)
      [ "$#" -ge 2 ] || { echo "mjj: --version requires a value" >&2; exit 2; }
      version=$2; shift 2 ;;
    --install-dir)
      [ "$#" -ge 2 ] || { echo "mjj: --install-dir requires a value" >&2; exit 2; }
      install_dir=$2; shift 2 ;;
    --repo)
      [ "$#" -ge 2 ] || { echo "mjj: --repo requires a value" >&2; exit 2; }
      repo=$2; shift 2 ;;
    --base-url)
      [ "$#" -ge 2 ] || { echo "mjj: --base-url requires a value" >&2; exit 2; }
      base_override=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "mjj: unknown installer option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$install_dir" ]; then
  echo "mjj: HOME or --install-dir is required" >&2
  exit 2
fi

repo_owner=${repo%%/*}
repo_name=${repo#*/}
case "$repo_owner" in
  ''|*[!A-Za-z0-9_.-]*)
    echo "mjj: repository must be OWNER/REPO" >&2
    exit 2
    ;;
esac
case "$repo_name" in
  ''|*/*|*[!A-Za-z0-9_.-]*)
    echo "mjj: repository must be OWNER/REPO" >&2
    exit 2
    ;;
esac
case "$version" in
  latest|v[0-9]*) ;;
  [0-9]*) version="v$version" ;;
  *) echo "mjj: invalid release version: $version" >&2; exit 2 ;;
esac

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
if [ -n "$base_override" ]; then
  base=${base_override%/}
elif [ "$version" = latest ]; then
  base="https://github.com/$repo/releases/latest/download"
else
  base="https://github.com/$repo/releases/download/$version"
fi

command -v tar >/dev/null 2>&1 || {
  echo "mjj: tar is required to unpack the release" >&2
  exit 1
}

umask 077
temporary=$(mktemp -d "${TMPDIR:-/tmp}/mjj-install.XXXXXX")
staged=
cleanup() {
  rm -rf "$temporary"
  if [ -n "$staged" ]; then rm -f "$staged"; fi
}
trap cleanup EXIT HUP INT TERM

download() {
  source_url=$1
  destination=$2
  case "$source_url" in
    file://*)
      cp "${source_url#file://}" "$destination"
      return
      ;;
  esac
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 15 \
      "$source_url" -o "$destination"
  elif command -v wget >/dev/null 2>&1; then
    wget -q --tries=3 --timeout=15 -O "$destination" "$source_url"
  else
    echo "mjj: curl or wget is required" >&2
    exit 1
  fi
}

echo "Downloading $asset..."
download "$base/$asset" "$temporary/$asset"
download "$base/SHA256SUMS" "$temporary/SHA256SUMS"

expected=$(awk -v name="$asset" '
  { file=$2; sub(/^\*/, "", file); if (file == name) { print tolower($1); exit } }
' "$temporary/SHA256SUMS")
case "$expected" in
  *[!0-9a-f]*|'')
    echo "mjj: $asset has no valid published checksum" >&2
    exit 1
    ;;
esac
if [ "${#expected}" -ne 64 ]; then
  echo "mjj: $asset has no valid published checksum" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$temporary/$asset" | awk '{print tolower($1)}')
elif command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$temporary/$asset" | awk '{print tolower($1)}')
else
  echo "mjj: sha256sum or shasum is required to verify the download" >&2
  exit 1
fi
if [ "$actual" != "$expected" ]; then
  echo "mjj: checksum verification failed for $asset" >&2
  exit 1
fi

# Refuse archives containing paths, links, or extra payloads. Release archives
# intentionally contain exactly one root entry named mjj.
entries=$(tar -tzf "$temporary/$asset")
if [ "$entries" != mjj ]; then
  echo "mjj: release archive must contain exactly one root file named mjj" >&2
  exit 1
fi
tar -xzf "$temporary/$asset" -C "$temporary" mjj
if [ ! -f "$temporary/mjj" ] || [ -L "$temporary/mjj" ]; then
  echo "mjj: release archive did not contain a regular mjj executable" >&2
  exit 1
fi

mkdir -p "$install_dir"
staged="$install_dir/.mjj.install.$$"
if command -v install >/dev/null 2>&1; then
  install -m 0755 "$temporary/mjj" "$staged"
else
  cp "$temporary/mjj" "$staged"
  chmod 0755 "$staged"
fi

# Catch corrupt or incompatible artifacts before replacing a working install.
if ! "$staged" --version >/dev/null 2>&1; then
  echo "mjj: downloaded executable failed its smoke test" >&2
  exit 1
fi
mv -f "$staged" "$install_dir/mjj"
staged=

echo "Installed mjj to $install_dir/mjj"
case ":${PATH:-}:" in
  *":$install_dir:"*) ;;
  *) echo "Add $install_dir to PATH, then run: mjj auth --probe" ;;
esac
