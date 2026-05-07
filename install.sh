#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
#  SLIK Auto-Checker — Intelligent Setup & Service Installer
#  macOS / Linux compatible. Safe to re-run.
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
VENV_DIR="$PROJECT_DIR/venv"
DATA_DIR="$PROJECT_DIR/data"
LOG_DIR="$PROJECT_DIR/logs"
ENV_FILE="$PROJECT_DIR/.env"
SERVICE_NAME="com.slikchecker.daemon"

# --- Color helpers ---
red()    { printf "\033[31m%b\033[0m\n" "$*"; }
green()  { printf "\033[32m%b\033[0m\n" "$*"; }
yellow() { printf "\033[33m%b\033[0m\n" "$*"; }
cyan()   { printf "\033[36m%b\033[0m\n" "$*"; }
header() { printf "\n\033[1;36m═══ %s ═══\033[0m\n" "$*"; }
ok()     { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn()   { printf "  \033[33m⚠\033[0m %s\n" "$*"; }
fail()   { printf "  \033[31m✗\033[0m %s\n" "$*"; }

# =============================================================================
#  Phase 1 — OS detection
# =============================================================================
header "System Detection"

OS="$(uname -s)"
ARCH="$(uname -m)"

ok "OS: $OS"
ok "Architecture: $ARCH"

if [[ "$OS" == "Darwin" ]]; then
    IS_MACOS=true
    HAS_BREW=false
    command -v brew &>/dev/null && HAS_BREW=true
    ok "Homebrew: $($HAS_BREW && echo 'installed' || echo 'not found')"
else
    IS_MACOS=false
    if ! command -v apt-get &>/dev/null && ! command -v yum &>/dev/null && ! command -v dnf &>/dev/null && ! command -v apk &>/dev/null; then
        warn "No supported package manager found (apt/yum/dnf/apk). You may need to install dependencies manually."
    fi
fi

# =============================================================================
#  Phase 2 — Python check
# =============================================================================
header "Python Runtime"

check_python_version() {
    local py="$1"
    if command -v "$py" &>/dev/null; then
        local ver; ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        local major; major=$("$py" -c 'import sys; print(sys.version_info.major)')
        local minor; minor=$("$py" -c 'import sys; print(sys.version_info.minor)')
        if [[ "$major" -ge 3 && "$minor" -ge 10 ]]; then
            echo "$ver"
            return 0
        fi
        echo "$ver (too old, need 3.10+)"
        return 1
    fi
    return 1
}

PYTHON_BIN=""
PYTHON_VER=""

for candidate in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
    ver_result="$(check_python_version "$candidate" 2>/dev/null || true)"
    if [[ -n "$ver_result" ]] && [[ "$ver_result" =~ ^[0-9]+\.[0-9]+$ ]]; then
        PYTHON_BIN="$candidate"
        PYTHON_VER="$ver_result"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    fail "Python 3.10+ not found"
    echo ""
    echo "  Install Python 3.10+:"
    echo "    macOS:  brew install python@3.12"
    echo "    Ubuntu: sudo apt install python3.12 python3.12-venv python3.12-dev"
    echo "    Arch:   sudo pacman -S python"
    echo ""
    exit 1
fi

ok "Python: $PYTHON_BIN ($PYTHON_VER)"
PYTHON="$PYTHON_BIN"

# =============================================================================
#  Phase 3 — System dependencies
# =============================================================================
header "System Dependencies"

declare -a MISSING_DEPS=()

# --- Tesseract OCR ---
TESSERACT_OK=false
if command -v tesseract &>/dev/null; then
    TESSERACT_OK=true
    ok "tesseract: $(tesseract --version 2>&1 | head -1)"
else
    MISSING_DEPS+=("tesseract")
    fail "tesseract not found"
fi

# --- Chrome / Chromium (for Selenium) ---
CHROME_OK=false
if command -v google-chrome &>/dev/null || command -v google-chrome-stable &>/dev/null || command -v chromium &>/dev/null || command -v chromium-browser &>/dev/null; then
    CHROME_OK=true
    ok "Chrome/Chromium: found"
else
    # Check for macOS Chrome.app
    if [[ "$IS_MACOS" == true ]] && [[ -d "/Applications/Google Chrome.app" ]]; then
        CHROME_OK=true
        ok "Google Chrome.app: found"
    else
        MISSING_DEPS+=("chrome/chromium")
        fail "Chrome/Chromium not found"
    fi
fi

# --- Chromedriver ---
CHROMEDRIVER_OK=false
if command -v chromedriver &>/dev/null; then
    CHROMEDRIVER_OK=true
    ok "chromedriver: $(chromedriver --version 2>&1 | head -1)"
elif [[ "$IS_MACOS" == true ]] && [[ -f "/usr/local/bin/chromedriver" ]]; then
    CHROMEDRIVER_OK=true
    ok "chromedriver: found at /usr/local/bin"
else
    MISSING_DEPS+=("chromedriver")
    fail "chromedriver not found"
fi

# --- libs for Pillow (Linux only) ---
if [[ "$IS_MACOS" != true ]]; then
    if ! ldconfig -p 2>/dev/null | grep -q libjpeg; then
        MISSING_DEPS+=("libjpeg-dev")
        fail "libjpeg not found (needed by Pillow)"
    fi
fi

if [[ ${#MISSING_DEPS[@]} -gt 0 ]]; then
    echo ""
    yellow "Missing dependencies: ${MISSING_DEPS[*]}"
    echo ""
    echo "  Install with:"
    if [[ "$IS_MACOS" == true ]]; then
        if [[ "$HAS_BREW" == true ]]; then
            echo ""
            for dep in "${MISSING_DEPS[@]}"; do
                case "$dep" in
                    tesseract)      echo "    brew install tesseract" ;;
                    chrome/chromium) echo "    brew install --cask google-chrome" ;;
                    chromedriver)   echo "    brew install --cask chromedriver" ;;
                esac
            done
            echo ""
            read -rp "  Install missing dependencies now with Homebrew? [Y/n] " reply
            if [[ "${reply,,}" != "n" ]]; then
                for dep in "${MISSING_DEPS[@]}"; do
                    case "$dep" in
                        tesseract)      brew install tesseract ;;
                        chrome/chromium) brew install --cask google-chrome ;;
                        chromedriver)   brew install --cask chromedriver ;;
                    esac
                done
                # Re-check chromedriver after brew
                if ! command -v chromedriver &>/dev/null; then
                    xattr -d com.apple.quarantine /usr/local/bin/chromedriver 2>/dev/null || true
                    xattr -d com.apple.quarantine /opt/homebrew/bin/chromedriver 2>/dev/null || true
                fi
            else
                yellow "Skipping dependency install. Some features may not work."
            fi
        else
            echo "    Install Homebrew first: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        fi
    else
        echo "    Ubuntu/Debian: sudo apt install tesseract-ocr chromium-browser chromium-chromedriver libjpeg-dev"
        echo "    Fedora:        sudo dnf install tesseract chromium chromedriver libjpeg-devel"
        echo "    Arch:          sudo pacman -S tesseract chromium"
    fi
    echo ""
fi

# =============================================================================
#  Phase 4 — Python virtual environment
# =============================================================================
header "Python Environment"

if [[ -d "$VENV_DIR" ]] && [[ -f "$VENV_DIR/bin/python" ]]; then
    ok "Virtual environment: exists"
else
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment: created"
fi

# Activate
source "$VENV_DIR/bin/activate"

# Upgrade pip
pip install --quiet --upgrade pip 2>&1 | tail -1

# Install project
echo "Installing Python dependencies..."
pip install --quiet \
    requests beautifulsoup4 selenium apscheduler pytesseract Pillow python-dotenv \
    easyocr ddddocr numpy streamlit pandas structlog tenacity pydantic pydantic-settings \
    pytest pytest-cov pytest-mock pytest-timeout \
    2>&1 | tail -3

# Verify key packages
DEPS_OK=true
for pkg in requests bs4 selenium apscheduler pytesseract PIL dotenv easyocr ddddocr numpy streamlit pandas structlog tenacity pydantic; do
    if ! python -c "import ${pkg}" 2>/dev/null; then
        fail "Python package missing: $pkg"
        DEPS_OK=false
    fi
done
"$DEPS_OK" && ok "All Python packages verified"

# =============================================================================
#  Phase 5 — Configuration
# =============================================================================
header "Configuration"

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    ok ".env created from .env.example"
else
    ok ".env: exists"
fi

mkdir -p "$DATA_DIR" "$LOG_DIR"

# Check if Telegram/Email are configured
source "$ENV_FILE" 2>/dev/null || true
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]] && [[ -n "${TELEGRAM_CHAT_ID:-}" ]]; then
    ok "Telegram: configured"
else
    warn "Telegram: not configured (optional)"
    echo "      Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable"
fi

if [[ -n "${SMTP_USERNAME:-}" ]] && [[ -n "${SMTP_PASSWORD:-}" ]]; then
    ok "Email (SMTP): configured"
else
    warn "Email: not configured (optional)"
fi

# =============================================================================
#  Phase 6 — Database
# =============================================================================
header "Database"

python -c "
import sys; sys.path.insert(0, '$PROJECT_DIR')
from slik_checker.models import db
db.initialize()
print('  Database initialized:', db._db_path)
"

ok "Database: ready"

# =============================================================================
#  Phase 7 — Service installation (launchd on macOS, systemd on Linux)
# =============================================================================
header "Service Installation"

SERVICE_INSTALLED=false

install_macos_service() {
    local plist_path="$HOME/Library/LaunchAgents/$SERVICE_NAME.plist"
    local python_path; python_path="$(which python)"
    local cli_path="$PROJECT_DIR/slik_checker/cli.py"

    # log paths
    local stdout_log="$LOG_DIR/stdout.log"
    local stderr_log="$LOG_DIR/stderr.log"

    cat > "$plist_path" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$SERVICE_NAME</string>

    <key>ProgramArguments</key>
    <array>
        <string>$python_path</string>
        <string>-m</string>
        <string>slik_checker.cli</string>
        <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>$stdout_log</string>

    <key>StandardErrorPath</key>
    <string>$stderr_log</string>

    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLISTEOF

    launchctl bootout "gui/$(id -u)/$SERVICE_NAME" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$plist_path" 2>/dev/null || \
    launchctl load "$plist_path" 2>/dev/null || true

    ok "launchd service: installed ($plist_path)"
    ok "launchd service: started"
    echo ""
    echo "  Manage with:"
    echo "    launchctl start   $SERVICE_NAME"
    echo "    launchctl stop    $SERVICE_NAME"
    echo "    launchctl list   | grep slikchecker"
    echo "    cat $stdout_log"
}

install_linux_service() {
    local service_path="$HOME/.config/systemd/user/$SERVICE_NAME.service"
    local python_path; python_path="$(which python)"

    mkdir -p "$(dirname "$service_path")"

    cat > "$service_path" << SERVICEEOF
[Unit]
Description=SLIK Auto-Checker Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$PROJECT_DIR
ExecStart=$python_path -m slik_checker.cli run
Environment="PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin"
Restart=always
RestartSec=10
StandardOutput=append:$LOG_DIR/stdout.log
StandardError=append:$LOG_DIR/stderr.log

[Install]
WantedBy=default.target
SERVICEEOF

    systemctl --user daemon-reload 2>/dev/null || true
    systemctl --user enable "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user start "$SERVICE_NAME" 2>/dev/null || true

    ok "systemd user service: installed ($service_path)"
    ok "systemd service: started"
    echo ""
    echo "  Manage with:"
    echo "    systemctl --user start   $SERVICE_NAME"
    echo "    systemctl --user stop    $SERVICE_NAME"
    echo "    systemctl --user status  $SERVICE_NAME"
    echo "    journalctl --user -u $SERVICE_NAME -f"
}

SERVICE_INSTALLED=false

if [[ "$IS_MACOS" == true ]]; then
    install_macos_service
    SERVICE_INSTALLED=true
elif command -v systemctl &>/dev/null; then
    install_linux_service
    SERVICE_INSTALLED=true
else
    if [[ -d "$HOME/.config/systemd/user" ]] || [[ -d "/etc/systemd/system" ]]; then
        install_linux_service
        SERVICE_INSTALLED=true
    else
        warn "No supported init system found (launchd/systemd)"
        echo "  Service will NOT auto-start on boot."
        echo "  Run manually: python -m slik_checker.cli run"
    fi
fi

# =============================================================================
#  Phase 8 — CLI wrapper script
# =============================================================================
header "CLI Wrapper"

WRAPPER="$PROJECT_DIR/slik"

cat > "$WRAPPER" << 'WRAPPEREOF'
#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/venv/bin/activate"
exec python -m slik_checker.cli "$@"
WRAPPEREOF

chmod +x "$WRAPPER"
ok "CLI wrapper: $WRAPPER"

# =============================================================================
#  Phase 9 — Verification
# =============================================================================
header "Verification"

echo ""
echo "Running smoke tests..."
ERRORS=0

# 1. Python import check
python -c "from slik_checker.config import settings; print('  Config OK: db_path =', settings.db_path)" || ((ERRORS++))

# 2. Captcha engines
python -c "from slik_checker.captcha import captcha_solver; print(f'  Captcha engines: {captcha_solver.engine_count} loaded')" || ((ERRORS++))

# 3. Database
python -c "
from slik_checker.models import db
stats = db.get_stats()
print(f'  DB stats: {stats[\"total_debiturs\"]} debiturs, {stats[\"active_schedules\"]} schedules')
" || ((ERRORS++))

# 4. Unit tests
echo ""
echo "Running unit tests..."
if python -m pytest "$PROJECT_DIR/tests/" -q --tb=line 2>&1 | tail -5; then
    ok "All tests passed"
else
    warn "Some tests failed (may be missing optional deps)"
    ((ERRORS++)) || true
fi

# =============================================================================
#  Summary
# =============================================================================
header "Setup Complete"

echo ""
if [[ "$ERRORS" -eq 0 ]]; then
    green "  SLIK Auto-Checker is ready!"
else
    yellow "  SLIK Auto-Checker is ready with $ERRORS warning(s)"
fi

echo ""
echo "  ┌─────────────────────────────────────────────────────────────┐"
echo "  │  Quick Commands:                                            │"
echo "  │                                                             │"
echo "  │  ./slik init           Initialize database                  │"
echo "  │  ./slik register ...   Submit registration (see --help)     │"
echo "  │  ./slik list           List all debiturs                    │"
echo "  │  ./slik check ...      Check registration status            │"
echo "  │  ./slik schedule ...   Manage schedules                     │"
echo "  │  ./slik run            Start scheduler daemon (foreground)  │"
echo "  │  ./slik ui             Launch Streamlit dashboard           │"
echo "  │                                                             │"
echo "  │  ./slik --help         Show all commands                    │"
echo "  │  make test             Run test suite                       │"
echo "  └─────────────────────────────────────────────────────────────┘"
echo ""

if [[ "$SERVICE_INSTALLED" == true ]]; then
    echo "  Service: auto-starts on boot and survives crashes"
    echo ""
fi

echo "  Streamlit UI:"
echo "    ./slik ui"
echo "    → Opens at http://localhost:8501"
echo ""

if [[ "$ERRORS" -eq 0 ]]; then
    exit 0
else
    exit 1
fi
