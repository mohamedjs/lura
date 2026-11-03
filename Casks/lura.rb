# Homebrew Cask for Lura, served straight from this repository (no separate tap):
#
#   brew tap mohamedjs/lura https://github.com/mohamedjs/lura
#   brew install --cask lura
#
# It always installs the latest release: each release also carries the macOS
# zips under fixed names (Lura-macos-arm64.zip / Lura-macos-x86_64.zip).
# Update with `brew upgrade --cask --greedy lura`.
cask "lura" do
  arch arm: "arm64", intel: "x86_64"

  version :latest
  sha256 :no_check

  url "https://github.com/mohamedjs/lura/releases/latest/download/Lura-macos-#{arch}.zip"
  name "Lura"
  desc "Voice-first desktop assistant — Arabic and English"
  homepage "https://mohamedjs.github.io/lura/"

  depends_on macos: ">= :monterey"
  # npx runs the built-in Chrome, Playwright, web and files connectors.
  depends_on formula: "node"

  app "Lura.app"
  binary "#{appdir}/Lura.app/Contents/MacOS/lura"

  # Not notarised yet (that needs a paid Apple Developer ID): without this,
  # Gatekeeper says "Lura" Not Opened / Apple could not verify it.
  postflight do
    system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{appdir}/Lura.app"]
  end

  uninstall launchctl: "com.lura.assistant",
            quit:      "com.lura.assistant"

  zap trash: [
    "~/Library/Application Support/Lura",
    "~/Library/LaunchAgents/com.lura.assistant.plist",
  ]
end
