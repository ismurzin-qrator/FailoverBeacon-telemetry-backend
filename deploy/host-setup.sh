#!/usr/bin/env bash
# usage: ssh <user>@<host> 'bash -s' < deploy/host-setup.sh
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v docker >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl >/dev/null
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    printf 'Types: deb\nURIs: https://download.docker.com/linux/debian\nSuites: %s\nComponents: stable\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
        "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.sources
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
fi

for package in git unattended-upgrades; do
    if ! dpkg -s "$package" >/dev/null 2>&1; then
        apt-get update -qq
        apt-get install -y -qq "$package" >/dev/null
    fi
done
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades

mkdir -p /opt/beacon /opt/beacon-deploy
chmod 700 /opt/beacon-deploy
echo "host ready: $(docker --version), $(git --version)"
