#!/usr/bin/env bash
# ==============================================================================
# serve_folder.sh
# Käynnistää paikallisen HTTP-tiedostopalvelimen nykyiseen kansioon (tai
# valittuun kansioon), sallii kaikkien alikansioiden selaamisen oletuksena
# ja tarkistaa palvelimen toiminnan curl-komennolla.
# Oletusportti: 8002 (vaihdettavissa argumentilla tai PORT-ympäristömuuttujalla).
# ==============================================================================

set -euo pipefail

PORT="${PORT:-8002}"
HOST="${HOST:-127.0.0.1}"
TARGET_DIR="."
CHECK_ONLY=0

usage() {
    cat <<EOF
Käyttö: $0 [PORTTI] [VALINNAT]

Valinnat:
  -p, --port PORTTI   Palvelimen portti (oletus: 8002)
  -H, --host OSOITE   Kuunneltava osoite (oletus: 127.0.0.1)
  -d, --dir KANSIO    Jaettava juurikansio (oletus: nykyinen kansio .)
      --check         Käynnistä palvelin, testaa juuri- ja alikansiot curlilla ja lopeta
  -h, --help          Näytä tämä ohje
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -H|--host)
            HOST="$2"
            shift 2
            ;;
        -d|--dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            if [[ "$1" =~ ^[0-9]+$ ]]; then
                PORT="$1"
                shift
            else
                echo "Virhe: tuntematon valinta '$1'" >&2
                usage >&2
                exit 1
            fi
            ;;
    esac
done

if ! [[ "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
    echo "Virhe: portin täytyy olla kokonaisluku väliltä 1–65535 (annettu: '$PORT')." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "Virhe: 'curl' vaaditaan palvelimen tarkistamiseen, mutta sitä ei löytynyt." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
    PYTHON_BIN="${SCRIPT_DIR}/.venv/bin/python"
else
    PYTHON_BIN="python3"
fi

ABS_DIR="$(cd "$TARGET_DIR" && pwd)"

# Käynnistetään HTTP-palvelin, joka sallii aina kaikkien alikansioiden listauksen
# (myös silloin, kun kansiossa olisi index.html-tiedosto).
"$PYTHON_BIN" - "$HOST" "$PORT" "$ABS_DIR" <<'PY' &
import http.server
import os
import sys
import urllib.parse

host = sys.argv[1]
port = int(sys.argv[2])
root_dir = sys.argv[3]


class AllSubfoldersHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=root_dir, **kwargs)

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            parts = urllib.parse.urlsplit(self.path)
            if not parts.path.endswith("/"):
                self.send_response(301)
                new_parts = (parts[0], parts[1], parts[2] + "/", parts[3], parts[4])
                self.send_header("Location", urllib.parse.urlunsplit(new_parts))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self.list_directory(path)
        return super().send_head()

    def log_message(self, format, *args):
        sys.stderr.write("[serve_folder] " + (format % args) + "\n")


class ReusableThreadingHTTPServer(http.server.ThreadingHTTPServer):
    allow_reuse_address = True


with ReusableThreadingHTTPServer((host, port), AllSubfoldersHTTPRequestHandler) as httpd:
    httpd.serve_forever()
PY
SERVER_PID=$!

cleanup() {
    if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
        kill "$SERVER_PID" >/dev/null 2>&1 || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

CHECK_HOST="$HOST"
if [[ "$CHECK_HOST" == "0.0.0.0" ]]; then
    CHECK_HOST="127.0.0.1"
fi
BASE_URL="http://${CHECK_HOST}:${PORT}/"

READY=0
for _ in {1..30}; do
    if curl -fsS "$BASE_URL" >/dev/null 2>&1; then
        READY=1
        break
    fi
    if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
        break
    fi
    sleep 0.1
done

if [[ "$READY" -ne 1 ]]; then
    echo "Virhe: palvelin ei vastannut osoitteessa ${BASE_URL}" >&2
    exit 1
fi

echo "Paikallinen tiedostopalvelin on käynnissä: ${BASE_URL}"
echo "Jaettava kansio (kaikki alikansiot selattavissa): ${ABS_DIR}"
echo "Tarkistettu curl-komennolla: curl -I ${BASE_URL}"
curl -fsSI "$BASE_URL" | head -n 5

if [[ "$CHECK_ONLY" -eq 1 ]]; then
    echo "Juuren sisällön esikatselu (curl ${BASE_URL}):"
    curl -fsS "$BASE_URL" | head -n 20
    exit 0
fi

echo "Pysäytä palvelin painamalla Ctrl+C."
wait "$SERVER_PID"
