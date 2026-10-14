#!/bin/sh
# One-time install of the review + validation tooling (Babylon, Playwright, Khronos validator).
set -e
cd "$(dirname "$0")"
npm install --silent
npx playwright install chromium-headless-shell >/dev/null 2>&1 || npx playwright install chromium
python3 -c "import numpy, PIL, cv2" 2>/dev/null || pip3 install --user numpy pillow opencv-python-headless
echo "game-asset-modeling tools ready"
