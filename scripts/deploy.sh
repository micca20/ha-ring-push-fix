#!/usr/bin/env bash
# Deploy a tagged release to Home Assistant on CT111 and restart HA.
#   scripts/deploy.sh v0.1.0
# Requires: ssh host alias "ct111" (root). Adds `ring_push_fix:` to configuration.yaml if missing.
set -euo pipefail
TAG="${1:?usage: deploy.sh <tag>}"
HOST="${DEPLOY_HOST:-ct111}"
HA_CONFIG="/opt/homeassistant/config"
SSH=(ssh ${SSH_CONFIG:+-F "$SSH_CONFIG"} "$HOST")
SCP=(scp -q ${SSH_CONFIG:+-F "$SSH_CONFIG"})
cd "$(git rev-parse --show-toplevel)"
git rev-parse -q --verify "refs/tags/$TAG" >/dev/null || { echo "tag $TAG not found"; exit 1; }
MANIFEST_VER=$(git show "$TAG:custom_components/ring_push_fix/manifest.json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["version"])')
[ "v$MANIFEST_VER" = "$TAG" ] || { echo "manifest version $MANIFEST_VER != tag $TAG"; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git archive --format=tar.gz -o "$TMP/release.tgz" "$TAG" custom_components/ring_push_fix
"${SCP[@]}" "$TMP/release.tgz" "$HOST:/tmp/ring_push_fix-$TAG.tgz"
"${SSH[@]}" bash -s -- "$TAG" "$HA_CONFIG" <<'REMOTE'
set -euo pipefail
TAG=$1; CFG=$2; STAMP=$(date +%Y%m%d-%H%M%S); REL=/opt/ring-push-fix/releases
X=$(mktemp -d); tar -xzf /tmp/ring_push_fix-$TAG.tgz -C "$X"
mkdir -p "$CFG/custom_components" "$REL"
if [ -d "$CFG/custom_components/ring_push_fix" ]; then
  tar -czf "$REL/ring_push_fix-before-$TAG-$STAMP.tgz" -C "$CFG/custom_components" ring_push_fix
  rm -rf "$CFG/custom_components/ring_push_fix"
fi
cp -a "$X/custom_components/ring_push_fix" "$CFG/custom_components/"
echo "$TAG" > "$CFG/custom_components/ring_push_fix/.release"
if ! grep -qE '^ring_push_fix:' "$CFG/configuration.yaml"; then
  cp "$CFG/configuration.yaml" "$REL/configuration.yaml.before-$TAG-$STAMP"
  printf '\n# Stopgap for python-ring-doorbell#537 (https://github.com/micca20/ha-ring-push-fix)\nring_push_fix:\n' >> "$CFG/configuration.yaml"
  echo "added ring_push_fix: to configuration.yaml"
fi
rm -rf "$X" /tmp/ring_push_fix-$TAG.tgz
{ ls -t "$REL"/ring_push_fix-before-* 2>/dev/null || true; } | tail -n +6 | xargs -r rm -f   # keep 5 rollbacks
docker exec homeassistant python3 -m homeassistant --script check_config -c /config >/dev/null || { echo "check_config failed; not restarting"; exit 1; }
docker restart homeassistant >/dev/null
echo "deployed $TAG to $CFG/custom_components/ring_push_fix; HA restarting"
REMOTE
