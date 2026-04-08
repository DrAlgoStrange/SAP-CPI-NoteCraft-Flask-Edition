#!/bin/bash
# ============================================================
#  Install Groovy for NoteCraft CPI Simulator
#  Run this once on your machine before starting the app.
# ============================================================
set -e

GROOVY_VERSION="4.0.26"
INSTALL_DIR="/opt/groovy"

echo "=== Installing Groovy $GROOVY_VERSION ==="

# Detect OS
if [[ "$OSTYPE" == "msys" || "$OSTYPE" == "win32" ]]; then
    echo "Windows detected. Please install Groovy manually:"
    echo "  1. Download https://groovy.apache.org/download.html"
    echo "  2. Extract to C:\\groovy"
    echo "  3. Add C:\\groovy\\bin to PATH"
    echo "  OR use: choco install groovy"
    exit 0
fi

if command -v groovy &>/dev/null; then
    echo "Groovy already installed: $(groovy --version)"
    exit 0
fi

# Try apt first (Ubuntu/Debian)
if command -v apt-get &>/dev/null; then
    echo "Trying apt-get..."
    sudo apt-get install -y groovy && echo "Done via apt!" && exit 0
fi

# Try brew (macOS)
if command -v brew &>/dev/null; then
    brew install groovy && echo "Done via brew!" && exit 0
fi

# Try SDKMAN
if command -v sdk &>/dev/null; then
    sdk install groovy $GROOVY_VERSION && echo "Done via SDKMAN!" && exit 0
fi

# Manual download
echo "Downloading Groovy binary..."
DOWNLOAD_URL="https://groovy.jfrog.io/artifactory/dist-release-local/groovy-zips/apache-groovy-binary-${GROOVY_VERSION}.zip"
wget -q "$DOWNLOAD_URL" -O /tmp/groovy.zip || curl -L "$DOWNLOAD_URL" -o /tmp/groovy.zip
sudo mkdir -p $INSTALL_DIR
sudo unzip -q /tmp/groovy.zip -d $INSTALL_DIR
GROOVY_BIN=$(find $INSTALL_DIR -name "groovy" -type f | head -1 | xargs dirname)
echo "export PATH=\"$GROOVY_BIN:\$PATH\"" >> ~/.bashrc
export PATH="$GROOVY_BIN:$PATH"
echo "Groovy installed at $GROOVY_BIN"
groovy --version
