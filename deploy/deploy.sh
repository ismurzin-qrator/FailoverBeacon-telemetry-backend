#!/usr/bin/env bash
# usage: deploy/deploy.sh <deployment dir> <git tag>
set -euo pipefail
deployment=$(cd "$1" && pwd)
tag=${2:?usage: deploy/deploy.sh <deployment dir> <git tag>}
# shellcheck source=/dev/null
. "$deployment/deploy.env"
cd "$(dirname "$0")/.."
repository=$(git remote get-url origin)

git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null ||
    { echo "$tag is not pushed to $repository" >&2; exit 1; }
uv run --quiet python -c \
    'import sys; from pathlib import Path; from beacon.registry import Registry; Registry.load(Path(sys.argv[1]))' \
    "$deployment/registry.json"

echo "deploying $tag to $HOST"
scp -q "$deployment/registry.json" "$HOST:/opt/beacon-deploy/registry.json"
ssh "$HOST" "set -e
    cd /opt/beacon
    if [ ! -d .git ]; then
        git init -q
        git remote add origin '$repository'
        git config core.sshCommand 'ssh -o StrictHostKeyChecking=accept-new'
    fi
    git fetch -q --force --tags origin
    git checkout -q --force --detach 'refs/tags/$tag'
    git clean -q -ffdx -e /secrets/ -e /.env
    TLS_NAMES='$TLS_NAMES' deploy/remote.sh" </dev/null
