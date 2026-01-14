"""Terminal app detection mappings.

This module contains the mapping tables used to detect terminal applications
from environment variables like TERM_PROGRAM and __CFBundleIdentifier.
"""

# Terminal app name mapping from TERM_PROGRAM to app name
TERMINAL_APP_MAP = {
    # Native terminals - macOS
    "Apple_Terminal": "Terminal",
    "iTerm.app": "iTerm",
    "Alacritty": "Alacritty",
    "alacritty": "Alacritty",
    "kitty": "kitty",
    "WezTerm": "WezTerm",
    "Hyper": "Hyper",
    "Ghostty": "Ghostty",
    # IDEs - VS Code family
    "vscode": "Visual Studio Code",
    "cursor": "Cursor",
    "Windsurf": "Windsurf",
    # IDEs - Other
    "Zed": "Zed",
    "Apple_Antigravity": "Antigravity",
    "rio": "Rio",
    "foot": "foot",
    # Linux terminals
    "gnome-terminal": "Gnome-terminal",
    "konsole": "Konsole",
    "xfce4-terminal": "Xfce4-terminal",
    "tilix": "Tilix",
    "terminator": "Terminator",
    # Windows terminals
    "Windows Terminal": "WindowsTerminal",
    "ConEmu": "ConEmu",
    "ConEmu64": "ConEmu64",
    "Cmder": "Cmder",
    "Fluent Terminal": "Fluent Terminal",
}

# macOS bundle ID to app name mapping (prioritized over TERM_PROGRAM)
MACOS_BUNDLE_MAP = {
    # Apple
    "com.apple.Terminal": "Terminal",
    # Third-party terminals
    "com.googlecode.iterm2": "iTerm",
    "io.alacritty": "Alacritty",
    "net.kovidgoyal.kitty": "kitty",
    "com.github.wez.wezterm": "WezTerm",
    "co.zeit.hyper": "Hyper",
    "com.mitchellh.ghostty": "Ghostty",
    # IDEs - VS Code family
    "com.microsoft.VSCode": "Visual Studio Code",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.codeium.windsurf": "Windsurf",
    # IDEs - Other
    "dev.zed.Zed": "Zed",
    "dev.zed.Zed-Preview": "Zed",
    "com.apple.dt.Antigravity": "Antigravity",
    # JetBrains
    "com.jetbrains.intellij": "IntelliJ IDEA",
    "com.jetbrains.intellij.ce": "IntelliJ IDEA CE",
    "com.jetbrains.pycharm": "PyCharm",
    "com.jetbrains.pycharm.ce": "PyCharm CE",
    "com.jetbrains.webstorm": "WebStorm",
    "com.jetbrains.goland": "GoLand",
    "com.jetbrains.phpstorm": "PhpStorm",
    "com.jetbrains.rubymine": "RubyMine",
    "com.jetbrains.clion": "CLion",
    "com.jetbrains.datagrip": "DataGrip",
    "com.jetbrains.rider": "Rider",
    "com.jetbrains.AppCode": "AppCode",
}
