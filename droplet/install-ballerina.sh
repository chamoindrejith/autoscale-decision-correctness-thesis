#!/usr/bin/env bash
# install-ballerina.sh
#
# Installs the Ballerina compiler/CLI on Ubuntu 24.04.
# Ballerina is the language used for the sample workload in this research.

set -euo pipefail

BAL_VERSION="${BAL_VERSION:-2201.10.0}"

echo ">>> Installing prerequisites"
apt-get install -y default-jre-headless wget

echo ">>> Downloading Ballerina ${BAL_VERSION}"
cd /tmp
wget -q "https://dist.ballerina.io/downloads/${BAL_VERSION}/ballerina-${BAL_VERSION}-swan-lake-linux-x64.deb" -O ballerina.deb

echo ">>> Installing Ballerina"
dpkg -i ballerina.deb || apt-get install -f -y
rm -f ballerina.deb

echo ""
echo "===================================================================="
echo "Ballerina installed. Verify with:"
echo ""
echo "  bal version"
echo ""
echo "Next: build the sample app."
echo "  cd ~/experiment-setup/02-sample-app"
echo "  bal build"
echo "===================================================================="
