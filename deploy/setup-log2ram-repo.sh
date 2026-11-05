#!/usr/bin/env bash
set -euo pipefail

# Refresh before apt update, including on retries after an interrupted setup.
# Repository and signing-key instructions: https://packages.azlux.fr/
. /etc/os-release
if [[ ! ${VERSION_CODENAME:-} =~ ^[a-z][a-z0-9-]*$ ]]; then
    echo "Cannot determine the OS codename for the log2ram repository." >&2
    exit 1
fi

for tool in curl gpg; do
    command -v "$tool" >/dev/null || {
        echo "Missing required tool: $tool (install curl and gnupg)." >&2
        exit 1
    }
done

repo_tmp_dir="$(mktemp -d)"
trap 'rm -rf "$repo_tmp_dir"' EXIT
mkdir -m 700 "$repo_tmp_dir/gnupg"

echo "==> Verifying the log2ram repository signing key..."
curl -fsSL --connect-timeout 15 --max-time 60 \
    https://azlux.fr/repo.gpg -o "$repo_tmp_dir/key.gpg"
test -s "$repo_tmp_dir/key.gpg"
fingerprint="$(gpg --no-options --batch --homedir "$repo_tmp_dir/gnupg" \
    --show-keys --with-colons "$repo_tmp_dir/key.gpg" |
    awk -F: '$1 == "fpr" {print $10; exit}')"
if [[ "$fingerprint" != 98B824A5FA7D3A10FDB225B7CA548A0A0312D8E6 ]]; then
    echo "Unexpected log2ram signing key; repository files were not changed." >&2
    exit 1
fi

printf 'deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] https://packages.azlux.fr/debian/ %s main\n' \
    "$VERSION_CODENAME" > "$repo_tmp_dir/azlux.list"
sudo install -m 644 "$repo_tmp_dir/key.gpg" /usr/share/keyrings/azlux-archive-keyring.gpg
sudo install -m 644 "$repo_tmp_dir/azlux.list" /etc/apt/sources.list.d/azlux.list

# Upstream recommends its package over Debian's log2ram build on Trixie.
# https://github.com/azlux/log2ram#via-apt-recommended-generalized
if [[ "$VERSION_CODENAME" == trixie ]]; then
    printf 'Package: log2ram\nPin: origin packages.azlux.fr\nPin-Priority: 1001\n' \
        > "$repo_tmp_dir/log2ram.pref"
    sudo install -m 644 "$repo_tmp_dir/log2ram.pref" /etc/apt/preferences.d/log2ram.pref
fi
