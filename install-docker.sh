#!/usr/bin/env bash
# Instalador de Iris Agent en Docker (Linux/macOS con Docker + Compose).
#
#   sudo ./install-docker.sh
#   sudo ./install-docker.sh --channels whatsapp --phone +573001112233
#   sudo ./install-docker.sh --update
#   sudo ./install-docker.sh --uninstall [--purge]
#
# Servicios: iris-core, iris-bridge, iris-telegram e iris-ollama.
# Opcionalmente instala Notas (memoria persistente) y crea el token de Iris.

set -Eeuo pipefail

APP_DIR=${IRIS_APP_DIR:-/opt/iris}
ENV_FILE=$APP_DIR/.env
COMPOSE_FILE=$APP_DIR/docker-compose.yml
PROJECT=${IRIS_PROJECT:-iris}
NOTAS_DIR=$APP_DIR/notas
NOTAS_PROJECT=${IRIS_NOTAS_PROJECT:-iris-notas}
NOTAS_PORT=${IRIS_NOTAS_PORT:-3001}
NOTAS_REPO=https://github.com/JUANRESTREPO95/notas-app
DEFAULT_MODEL=deepseek-v4.1-flash:cloud
REPO_URL_DEFAULT=https://github.com/JUANRESTREPO95/iris-agent
REPO_REF_DEFAULT=main

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
SOURCE_TMP=""
ORIG_ARGS=("$@")

MODE=install
ASSUME_YES=0
SKIP_SIGNIN=0
SKIP_PAIR=0
PURGE=0
CHANNELS="${IRIS_CHANNELS:-}"
PHONE="${IRIS_PHONE:-}"
TELEGRAM_TOKEN="${IRIS_TELEGRAM_TOKEN:-}"
TELEGRAM_USER="${IRIS_TELEGRAM_USER:-}"
TELEGRAM_BOT=""
MODEL="${IRIS_MODEL:-}"
PROVIDER="${IRIS_PROVIDER:-}"
ANTHROPIC_KEY="${IRIS_ANTHROPIC_KEY:-}"
OAUTH_TOKEN="${IRIS_OAUTH_TOKEN:-}"
CUSTOM_BASE="${IRIS_CUSTOM_BASE:-}"
CUSTOM_TOKEN="${IRIS_CUSTOM_TOKEN:-}"
NOTAS_CHOICE="${IRIS_NOTAS:-}"
NOTAS_URL="${IRIS_NOTAS_URL:-}"
NOTAS_TOKEN_IN="${IRIS_NOTAS_TOKEN:-}"
NOTAS_USER_SET=""
NOTAS_PASS_SET=""
MEMORY_MODE="${IRIS_MEMORY_MODE:-}"
REMINDERS="${IRIS_REMINDERS:-}"
REMINDER_CHANNEL="${IRIS_REMINDER_CHANNEL:-}"
BRIDGE_TOKEN="${IRIS_BRIDGE_TOKEN:-}"

c_reset=$'\033[0m'; c_cyan=$'\033[1;36m'; c_green=$'\033[1;32m'
c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'

log()  { printf '%s[iris]%s %s\n' "$c_cyan" "$c_reset" "$*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$c_green" "$c_reset" "$*"; }
warn() { printf '%s[aviso]%s %s\n' "$c_yellow" "$c_reset" "$*"; }
die()  { printf '%s[error]%s %s\n' "$c_red" "$c_reset" "$*" >&2; exit 1; }
dim()  { printf '%s%s%s\n' "$c_dim" "$*" "$c_reset"; }

print_banner() {
  printf '\n'
  printf '%s  ╭──────────────────────────────────────────────╮%s\n' "$c_cyan" "$c_reset"
  printf '%s  │%s  IRIS · Docker · Claude Code por chat     %s│%s\n' "$c_cyan" "$c_reset" "$c_cyan" "$c_reset"
  printf '%s  ╰──────────────────────────────────────────────╯%s\n' "$c_cyan" "$c_reset"
  printf '\n'
}

usage() {
  cat <<'EOF'
Uso: sudo ./install-docker.sh [opciones]

  --channels NAME    whatsapp | telegram | both
  --phone NUM        número de WhatsApp autorizado (E.164)
  --model NAME       modelo (Ollama o alias de Claude; vacío = predeterminado)
  --provider NAME    ollama-local | claude-subscription | claude-api | custom
  --notas yes|no     instalar Notas como memoria persistente
  --notas-url URL    URL de un Notas existente
  --notas-token T    token de un Notas existente
  --memoria MODE     auto | manual (solo con Notas)
  --recordatorios S|N  activar los recordatorios de Notas
  --canal-recordatorios NAME  telegram | whatsapp
  --yes              no preguntar (usa las variables de entorno)
  --skip-signin      no iniciar sesión en Ollama
  --skip-pair        no emparejar WhatsApp ahora
  --update           actualizar código y reiniciar (conserva .env)
  --uninstall        bajar los servicios
  --purge            con --uninstall: borra también datos y carpeta
  --help             esta ayuda

Variables: IRIS_CHANNELS, IRIS_PROVIDER, IRIS_PHONE, IRIS_MODEL,
IRIS_TELEGRAM_TOKEN, IRIS_TELEGRAM_USER, IRIS_OAUTH_TOKEN,
IRIS_ANTHROPIC_KEY, IRIS_CUSTOM_BASE, IRIS_CUSTOM_TOKEN,
IRIS_NOTAS (yes|no), IRIS_NOTAS_URL, IRIS_NOTAS_TOKEN.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --channels) CHANNELS="${2:-}"; shift 2 ;;
    --phone) PHONE="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --notas) NOTAS_CHOICE="${2:-}"; shift 2 ;;
    --notas-url) NOTAS_URL="${2:-}"; shift 2 ;;
    --notas-token) NOTAS_TOKEN_IN="${2:-}"; shift 2 ;;
    --memoria) MEMORY_MODE="${2:-}"; shift 2 ;;
    --recordatorios) REMINDERS="${2:-}"; shift 2 ;;
    --canal-recordatorios) REMINDER_CHANNEL="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --skip-signin) SKIP_SIGNIN=1; shift ;;
    --skip-pair) SKIP_PAIR=1; shift ;;
    --update) MODE=update; shift ;;
    --uninstall) MODE=uninstall; shift ;;
    --purge) PURGE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Opción desconocida: $1 (usa --help)" ;;
  esac
done

# ── Utilidades ──────────────────────────────────────────────────────────────

confirm() {
  local answer=""
  if [[ $ASSUME_YES -eq 1 ]]; then return 0; fi
  read -rp "$1 [s/N]: " answer </dev/tty || true
  [[ "${answer,,}" == "s" || "${answer,,}" == "si" || "${answer,,}" == "sí" || "${answer,,}" == "y" ]]
}

ask() {
  local __var=$1 __prompt=$2 __default=${3:-} __answer=""
  if [[ $ASSUME_YES -eq 1 ]]; then
    printf -v "$__var" '%s' "$__default"
    return
  fi
  if [[ -n $__default ]]; then
    read -rp "$__prompt [$__default]: " __answer </dev/tty || true
    printf -v "$__var" '%s' "${__answer:-$__default}"
  else
    read -rp "$__prompt: " __answer </dev/tty || true
    printf -v "$__var" '%s' "$__answer"
  fi
}

read_secret() {
  local __var=$1 __prompt=$2 __value=""
  read -rsp "$__prompt: " __value </dev/tty || true
  echo
  printf -v "$__var" '%s' "$__value"
}

ui_menu() {
  local title="$1" prompt="$2"; shift 2
  local -a keys=() labels=() descs=()
  local item key rest label desc
  for item in "$@"; do
    key="${item%%::*}"
    rest="${item#*::}"
    if [[ $rest == *::* ]]; then
      label="${rest%%::*}"
      desc="${rest#*::}"
    else
      label="$rest"
      desc=""
    fi
    keys+=("$key"); labels+=("$label"); descs+=("$desc")
  done
  local n=${#keys[@]} choice="" i
  {
    printf '\n'
    printf '%s╭─ %s%s\n' "$c_cyan" "$title" "$c_reset"
    printf '%s│%s %s\n' "$c_cyan" "$c_reset" "$prompt"
    printf '%s├──────────────────────────────────────────────────────────────%s\n' "$c_dim" "$c_reset"
    for i in "${!keys[@]}"; do
      printf '  %s%2d)%s %-40s %s%s%s\n' \
        "$c_yellow" "$((i + 1))" "$c_reset" "${labels[$i]}" \
        "$c_dim" "${descs[$i]}" "$c_reset"
    done
    printf '%s╰──────────────────────────────────────────────────────────────%s\n' "$c_dim" "$c_reset"
  } >&2
  while :; do
    read -rp "  Elige [1-$n] (Enter = 1): " choice </dev/tty || return 1
    choice="${choice:-1}"
    if [[ $choice =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= n )); then
      REPLY="${keys[$((choice - 1))]}"
      return 0
    fi
    printf '  Opción no válida.\n' >&2
  done
}

rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

dc() { docker compose -f "$COMPOSE_FILE" --project-name "$PROJECT" "$@"; }
notas_dc() { docker compose -f "$NOTAS_DIR/docker-compose.yml" --project-name "$NOTAS_PROJECT" "$@"; }

force_sources() {
  local repo="${IRIS_REPO_URL:-$REPO_URL_DEFAULT}"
  local ref="${IRIS_REPO_REF:-$REPO_REF_DEFAULT}"
  local url="${IRIS_SOURCE_TARBALL:-${repo%/}/archive/refs/heads/${ref}.tar.gz}"
  SOURCE_TMP="$(mktemp -d /tmp/iris-src.XXXXXX)" || die "No pude crear el directorio temporal."
  log "Descargando Iris desde $repo ($ref)…"
  curl -fsSL "$url" -o "$SOURCE_TMP/src.tar.gz" </dev/null \
    || die "No pude descargar el código fuente."
  tar -xzf "$SOURCE_TMP/src.tar.gz" -C "$SOURCE_TMP" --strip-components=1 \
    || die "No pude extraer el código fuente."
  rm -f "$SOURCE_TMP/src.tar.gz"
  [[ -f $SOURCE_TMP/docker-compose.yml ]] || die "El paquete no trae docker-compose.yml."
  SRC_DIR="$SOURCE_TMP"
}

ensure_sources() {
  if [[ -f $SRC_DIR/install-docker.sh && -f $SRC_DIR/docker-compose.yml && -f $SRC_DIR/Dockerfile ]]; then
    return 0
  fi
  force_sources
}

cleanup() { [[ -n ${SOURCE_TMP:-} ]] && rm -rf "$SOURCE_TMP" 2>/dev/null || true; }
trap cleanup EXIT

require_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker no está instalado. Instálalo y vuelve a intentar."
  docker info >/dev/null 2>&1 || die "Docker no responde. Ejecuta con sudo o añade tu usuario al grupo docker."
  docker compose version >/dev/null 2>&1 || die "Falta el plugin 'docker compose'."
}

# ── Preguntas ───────────────────────────────────────────────────────────────

provider_label() {
  case "$1" in
    claude-subscription) echo "Claude (suscripción Pro/Max/Team)" ;;
    claude-api) echo "Claude (clave de API)" ;;
    ollama-local) echo "Ollama Cloud (contenedor Ollama)" ;;
    custom) echo "Otro endpoint compatible con Anthropic" ;;
    *) echo "$1" ;;
  esac
}

channel_label() {
  case "$1" in
    whatsapp) echo "WhatsApp" ;;
    telegram) echo "Telegram" ;;
    both) echo "WhatsApp + Telegram" ;;
    *) echo "$1" ;;
  esac
}

choose_channels() {
  if [[ -n $CHANNELS ]]; then
    case $CHANNELS in
      whatsapp|telegram|both) return 0 ;;
      *) die "Canal no válido: $CHANNELS" ;;
    esac
  fi
  if [[ $ASSUME_YES -eq 1 ]]; then CHANNELS=telegram; return 0; fi
  ui_menu "Canales de Iris" "¿Por dónde quieres usar Iris?" \
    "telegram::Telegram::bot oficial" \
    "whatsapp::WhatsApp::conexión por QR" \
    "both::WhatsApp + Telegram::los dos a la vez" \
    || die "Selección cancelada."
  CHANNELS="$REPLY"
  ok "Canales: $(channel_label "$CHANNELS")"
}

has_whatsapp() { [[ $CHANNELS == whatsapp || $CHANNELS == both ]]; }
has_telegram() { [[ $CHANNELS == telegram || $CHANNELS == both ]]; }

telegram_username() {
  local token="$1" out
  out="$(curl -fsS --max-time 15 "https://api.telegram.org/bot${token}/getMe" 2>/dev/null || true)"
  [[ -n $out ]] || return 1
  printf '%s' "$out" \
    | sed -n 's/.*"username"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
    | head -n1
}

collect_telegram() {
  local tries=0
  while :; do
    if [[ -z $TELEGRAM_TOKEN ]]; then
      dim "  Crea el bot en Telegram: @BotFather → /newbot → copia el token."
      read_secret TELEGRAM_TOKEN "Token del bot de Telegram" || die "Instalación cancelada."
    fi
    [[ -n $TELEGRAM_TOKEN ]] || die "Necesito el token del bot de Telegram."
    TELEGRAM_BOT="$(telegram_username "$TELEGRAM_TOKEN" || true)"
    if [[ -n $TELEGRAM_BOT ]]; then
      ok "Bot de Telegram: @$TELEGRAM_BOT"
      break
    fi
    warn "Telegram rechazó el token."
    TELEGRAM_TOKEN=""
    tries=$((tries + 1))
    if (( tries >= 3 )); then die "No pude validar el token de Telegram."; fi
  done
  if [[ -z $TELEGRAM_USER ]]; then
    dim "  Tu ID numérico: escríbele a @userinfobot en Telegram y copia el número."
    ask TELEGRAM_USER "ID numérico de Telegram autorizado" "" || die "Instalación cancelada."
  fi
  [[ $TELEGRAM_USER == "*" || $TELEGRAM_USER =~ ^-?[0-9]+$ ]] \
    || die "ID de Telegram no válido: $TELEGRAM_USER"
  dim "  Abre @$TELEGRAM_BOT y pulsa /start."
}

choose_provider() {
  if [[ -n $PROVIDER ]]; then
    case $PROVIDER in
      claude-subscription|claude-api|ollama-local|custom) return 0 ;;
      *) die "Proveedor no válido: $PROVIDER" ;;
    esac
  fi
  if [[ $ASSUME_YES -eq 1 ]]; then PROVIDER=ollama-local; return 0; fi
  ui_menu "Proveedor del modelo" "¿Cómo quieres conectar el modelo de Iris?" \
    "ollama-local::Ollama — contenedor del compose::modelos :cloud" \
    "claude-subscription::Claude — suscripción::Pro/Max/Team" \
    "claude-api::Claude — clave de API::console.anthropic.com" \
    "custom::Otro endpoint::compatible con Anthropic" \
    || die "Selección cancelada."
  PROVIDER="$REPLY"
  ok "Proveedor: $(provider_label "$PROVIDER")"
}

fetch_cloud_models() {
  local html slugs
  html="$(curl -fsSL --max-time 20 'https://ollama.com/search?c=cloud' 2>/dev/null || true)"
  [[ -n $html ]] || return 1
  slugs="$(printf '%s' "$html" \
    | grep -oE 'href="/library/[^"]+"' \
    | sed -E 's|href="/library/||; s|"$||' \
    | sort -u)"
  [[ -n $slugs ]] || return 1
  printf '%s\n' "$slugs"
}

normalize_ollama_model() {
  [[ -n $MODEL ]] || MODEL="$DEFAULT_MODEL"
  [[ $MODEL == *:cloud ]] || MODEL="${MODEL}:cloud"
}

select_claude_model() {
  if [[ -n $MODEL ]]; then return 0; fi
  local choice
  ui_menu "Modelo de Claude" "¿Qué modelo quieres usar?" \
    "__default__::Predeterminado de Claude Code::el que traiga Claude" \
    "sonnet::Sonnet::equilibrado" \
    "opus::Opus::máxima capacidad" \
    "haiku::Haiku::rápido" \
    "__other__::Escribir otro::alias o nombre completo" \
    || die "Selección cancelada."
  choice="$REPLY"
  case "$choice" in
    __default__) MODEL="" ;;
    __other__) ask MODEL "Modelo (alias o nombre completo)" "" ;;
    *) MODEL="$choice" ;;
  esac
}

select_ollama_model() {
  if [[ -n $MODEL ]]; then normalize_ollama_model; return 0; fi
  local -a models=() ordered=() menu=()
  local m
  while IFS= read -r m; do
    if [[ -n $m ]]; then models+=("$m"); fi
  done < <(fetch_cloud_models || true)
  for m in "${models[@]}"; do
    if [[ $m == "deepseek-v4.1-flash" ]]; then ordered+=("$m"); fi
  done
  for m in "${models[@]}"; do
    if [[ $m != "deepseek-v4.1-flash" ]]; then ordered+=("$m"); fi
  done
  if (( ${#ordered[@]} > 0 )); then models=("${ordered[@]}"); fi
  for m in "${models[@]}"; do menu+=("$m::$m::cloud"); done
  menu+=("__other__::Escribir otro modelo::")
  local choice
  ui_menu "Modelo de Ollama" "Elige el modelo cloud" "${menu[@]}" || die "Selección cancelada."
  choice="$REPLY"
  if [[ $choice == __other__ ]]; then
    ask MODEL "Modelo" "${models[0]:-$DEFAULT_MODEL}"
  else
    MODEL="$choice"
  fi
  normalize_ollama_model
}

collect_provider_config() {
  case $PROVIDER in
    claude-subscription) select_claude_model ;;
    claude-api)
      if [[ -z $ANTHROPIC_KEY ]]; then
        read_secret ANTHROPIC_KEY "Clave API de Anthropic (sk-ant-api...)" || die "Instalación cancelada."
      fi
      [[ -n $ANTHROPIC_KEY ]] || die "Necesito la clave de Anthropic."
      select_claude_model
      ;;
    ollama-local) select_ollama_model ;;
    custom)
      [[ -n $CUSTOM_BASE ]] || ask CUSTOM_BASE "URL base compatible con Anthropic" "https://"
      [[ $CUSTOM_BASE =~ ^https?:// ]] || die "URL no válida."
      [[ -n $CUSTOM_TOKEN ]] || read_secret CUSTOM_TOKEN "Token del endpoint"
      [[ -n $CUSTOM_TOKEN ]] || die "Necesito el token del endpoint."
      [[ -n $MODEL ]] || ask MODEL "Modelo" ""
      [[ -n $MODEL ]] || die "El modelo no puede quedar vacío."
      ;;
  esac
}

# ── Código y configuración ──────────────────────────────────────────────────

copy_sources() {
  install -d "$APP_DIR"
  if [[ "$(readlink -f "$SRC_DIR")" == "$(readlink -f "$APP_DIR")" ]]; then
    log "El instalador corre desde $APP_DIR: reutilizo el código."
    return 0
  fi
  local saved_env=""
  if [[ -f $ENV_FILE ]]; then
    saved_env="$(mktemp)"
    cp -a "$ENV_FILE" "$saved_env"
  fi
  find "$APP_DIR" -mindepth 1 -maxdepth 1 \
    ! -name .env ! -name notas -exec rm -rf {} + 2>/dev/null || true
  tar -C "$SRC_DIR" --exclude=.git --exclude=.env -cf - . | tar -C "$APP_DIR" -xf -
  if [[ -n $saved_env ]]; then cp -a "$saved_env" "$ENV_FILE"; rm -f "$saved_env"; fi
  chmod +x "$APP_DIR/install.sh" "$APP_DIR/install-docker.sh" 2>/dev/null || true
}

write_env() {
  umask 077
  {
    echo "# Iris Agent — Docker (generado por install-docker.sh)"
    echo "IRIS_CHANNELS=$CHANNELS"
    echo "IRIS_PHONE=$PHONE"
    echo "IRIS_TELEGRAM_TOKEN=$TELEGRAM_TOKEN"
    echo "IRIS_TELEGRAM_USER=$TELEGRAM_USER"
    echo "IRIS_PROVIDER=$PROVIDER"
    echo "IRIS_MODEL=$MODEL"
    echo "IRIS_BRIDGE_TOKEN=$BRIDGE_TOKEN"
    echo "NOTAS_API_URLS=$NOTAS_URL"
    echo "NOTAS_API_URL=${NOTAS_URL%%,*}"
    echo "NOTAS_API_TOKEN=${NOTAS_TOKEN_IN:-}"
    echo "IRIS_MEMORY_MODE=${MEMORY_MODE:-manual}"
    echo "IRIS_REMINDERS=${REMINDERS:-off}"
    echo "IRIS_REMINDER_INTERVAL=60"
    echo "IRIS_REMINDER_CHANNEL=${REMINDER_CHANNEL:-auto}"
    echo "IRIS_TIMEZONE=${TZ_NAME:-America/Bogota}"
    echo "IRIS_OUTBOX=/var/lib/iris/outbox"
    echo "IRIS_MAX_FILE_MB=100"
    case $PROVIDER in
      ollama-local)
        echo "ANTHROPIC_BASE_URL=http://iris-ollama:11434"
        echo "ANTHROPIC_AUTH_TOKEN=ollama"
        echo "ANTHROPIC_API_KEY=ollama"
        ;;
      claude-subscription)
        echo "CLAUDE_CODE_OAUTH_TOKEN=$OAUTH_TOKEN"
        echo "ANTHROPIC_BASE_URL="
        echo "ANTHROPIC_AUTH_TOKEN="
        echo "ANTHROPIC_API_KEY="
        ;;
      claude-api)
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
        echo "ANTHROPIC_BASE_URL="
        echo "ANTHROPIC_AUTH_TOKEN="
        ;;
      custom)
        echo "ANTHROPIC_BASE_URL=$CUSTOM_BASE"
        echo "ANTHROPIC_AUTH_TOKEN=$CUSTOM_TOKEN"
        echo "ANTHROPIC_API_KEY="
        ;;
    esac
  } >"$ENV_FILE"
  umask 022
  chmod 0600 "$ENV_FILE"
}

load_env_file() {
  [[ -f $ENV_FILE ]] || die "No hay instalación ($ENV_FILE)."
  # shellcheck disable=SC1090
  set -a; . "$ENV_FILE"; set +a
}

set_env_var() {
  local key="$1" value="$2" file="${3:-$ENV_FILE}"
  if grep -q "^${key}=" "$file"; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$file"
    rm -f "${file}.bak"
  else
    echo "${key}=${value}" >>"$file"
  fi
}

# ── Ollama ──────────────────────────────────────────────────────────────────

ollama_ready() {
  dc exec -T iris-ollama ollama list >/dev/null 2>&1
}

start_ollama() {
  log "Levantando Ollama…"
  dc up -d --build iris-ollama
  local i
  for i in $(seq 1 60); do
    if ollama_ready; then break; fi
    sleep 2
  done
  ollama_ready || die "Ollama no arrancó. Revisa: docker compose logs iris-ollama"
  ok "Ollama listo."
}

ollama_signin() {
  if [[ $SKIP_SIGNIN -eq 1 ]]; then return 0; fi
  echo
  log "Sesión de Ollama Cloud"
  dim "Se mostrará una URL. Ábrela en el navegador, autoriza y vuelve aquí."
  dc exec iris-ollama ollama signin </dev/tty || warn "El inicio de sesión no terminó."
  local out
  out="$(mktemp)"
  local i
  for i in 1 2 3 4 5; do
    if dc exec -T iris-ollama ollama run "$MODEL" "Responde solo: OK" >"$out" 2>&1 </dev/null \
       && ! grep -qiE "need to be signed in|sign in was cancelled|failed to sign in" "$out"; then
      ok "Sesión de Ollama verificada."
      rm -f "$out"
      return 0
    fi
    warn "Aún no puedo usar $MODEL."
    dim "  Reintenta con: docker compose exec iris-ollama ollama signin"
    if [[ $ASSUME_YES -eq 1 ]]; then break; fi
    read -rp "Completa el inicio de sesión y pulsa Enter (o escribe 'saltar'): " answer </dev/tty || true
    if [[ "${answer:-}" == "saltar" ]]; then break; fi
  done
  rm -f "$out"
  warn "Sigue sin sesión verificada de Ollama."
}

# ── Notas (memoria persistente) ─────────────────────────────────────────────

notas_health() { curl -fsS -m 3 "http://127.0.0.1:$NOTAS_PORT/api/health" >/dev/null 2>&1; }

notas_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$NOTAS_DIR/.env" 2>/dev/null | head -n1 | tr -d '"'
}

install_notas() {
  if [[ -n $NOTAS_TOKEN_IN ]]; then
    ok "Notas externo: se usará el token indicado."
    return 0
  fi
  if notas_health; then
    warn "Ya hay un servicio respondiendo en el puerto $NOTAS_PORT."
    dim "  Si es Notas, crea un token en Ajustes → API y pásalo con --notas-token."
    if [[ $ASSUME_YES -eq 1 ]]; then
      NOTAS_URL="${NOTAS_URL:-http://host.docker.internal:$NOTAS_PORT}"
      return 0
    fi
    read_secret NOTAS_TOKEN_IN "Token de Notas (vacío para omitir)" || true
    NOTAS_URL="${NOTAS_URL:-http://host.docker.internal:$NOTAS_PORT}"
    return 0
  fi

  log "Instalando Notas (memoria persistente)…"
  local tmp saved_env=""
  tmp="$(mktemp -d)"
  curl -fsSL "${NOTAS_REPO}/archive/refs/heads/main.tar.gz" -o "$tmp/notas.tar.gz" </dev/null \
    || die "No pude descargar Notas."
  tar -xzf "$tmp/notas.tar.gz" -C "$tmp" --strip-components=1 \
    || die "No pude extraer Notas."
  rm -f "$tmp/notas.tar.gz"
  if [[ -f $NOTAS_DIR/.env ]]; then
    saved_env="$(mktemp)"
    cp -a "$NOTAS_DIR/.env" "$saved_env"
  fi
  rm -rf "$NOTAS_DIR"
  mv "$tmp" "$NOTAS_DIR"

  if [[ -n $saved_env ]]; then
    cp -a "$saved_env" "$NOTAS_DIR/.env"
    rm -f "$saved_env"
    chmod 0600 "$NOTAS_DIR/.env"
    NOTAS_USER_SET="$(notas_env_value ADMIN_USER)"
    NOTAS_PASS_SET="$(notas_env_value ADMIN_PASSWORD)"
    [[ -n $NOTAS_USER_SET && -n $NOTAS_PASS_SET ]] \
      || die "No pude leer las credenciales de $NOTAS_DIR/.env"
  else
    NOTAS_USER_SET="${IRIS_NOTAS_USER:-admin}"
    NOTAS_PASS_SET="${IRIS_NOTAS_PASSWORD:-$(rand_hex | cut -c1-16)}"
    umask 077
    cat >"$NOTAS_DIR/.env" <<EOF
SECRET_KEY=$(rand_hex)
ADMIN_USER=$NOTAS_USER_SET
ADMIN_PASSWORD=$NOTAS_PASS_SET
TIMEZONE=${TZ_NAME:-America/Bogota}
COOKIE_SECURE=false
APP_NAME=Notas
EOF
    chmod 0600 "$NOTAS_DIR/.env"
    umask 022
  fi

  notas_dc up -d --build
  local i
  for i in $(seq 1 90); do
    if notas_health; then break; fi
    sleep 2
  done
  notas_health || die "Notas no respondió. Revisa: docker compose -f $NOTAS_DIR/docker-compose.yml logs"

  log "Creando token de Iris en Notas…"
  local cookies resp
  cookies="$(mktemp)"
  curl -fsS -c "$cookies" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$NOTAS_USER_SET\",\"password\":\"$NOTAS_PASS_SET\"}" \
    "http://127.0.0.1:$NOTAS_PORT/api/login" >/dev/null </dev/null \
    || die "No pude entrar a Notas con las credenciales generadas."
  resp="$(curl -fsS -b "$cookies" -H 'Content-Type: application/json' \
    -d "{\"name\":\"iris-$(date +%Y%m%d-%H%M%S)\",\"rules\":[{\"path\":\"configuracion.md\",\"level\":\"write\"},{\"path\":\"proyectos\",\"level\":\"write\"},{\"path\":\"Agente.md\",\"level\":\"read\"}]}" \
    "http://127.0.0.1:$NOTAS_PORT/api/tokens" </dev/null)" \
    || die "No pude crear el token de Iris."
  rm -f "$cookies"
  NOTAS_TOKEN_IN="$(printf '%s' "$resp" | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')"
  [[ -n $NOTAS_TOKEN_IN ]] || die "Notas no devolvió el token."
  NOTAS_URL="${NOTAS_URL:-http://host.docker.internal:$NOTAS_PORT}"
  ok "Notas instalado y token creado."
}

notas_question() {
  if [[ -n $NOTAS_TOKEN_IN ]]; then
    if [[ -z $NOTAS_URL ]]; then
      ask NOTAS_URL "URL de Notas" "http://host.docker.internal:$NOTAS_PORT"
    fi
    return 0
  fi
  if [[ -z $NOTAS_CHOICE ]]; then
    if [[ $ASSUME_YES -eq 1 ]]; then
      NOTAS_CHOICE=no
    else
      ask NOTAS_CHOICE "¿Instalar Notas como memoria persistente? (s/n)" "s"
    fi
  fi
  case "${NOTAS_CHOICE,,}" in
    yes|s|si|sí|y)
      install_notas
      ;;
    no|n)
      if [[ -z $NOTAS_TOKEN_IN ]]; then
        dim "  Puedes conectar un Notas existente ahora (Enter para omitir)."
        ask NOTAS_URL "URL de Notas" ""
        if [[ -n $NOTAS_URL ]]; then
          read_secret NOTAS_TOKEN_IN "Token de Notas" || true
          [[ -n $NOTAS_TOKEN_IN ]] || NOTAS_URL=""
        fi
      fi
      ;;
    *) die "Valor no válido para --notas: $NOTAS_CHOICE" ;;
  esac
}

# ── Pareo y pruebas ─────────────────────────────────────────────────────────

pair_whatsapp() {
  has_whatsapp || return 0
  if [[ $SKIP_PAIR -eq 1 ]]; then return 0; fi
  echo
  log "Pareo de WhatsApp"
  dim "Abre WhatsApp → Dispositivos vinculados → Vincular dispositivo."
  dc stop iris-bridge >/dev/null 2>&1 || true
  dc run --rm --no-deps --entrypoint bash iris-bridge -c \
    'rm -rf /var/lib/iris/whatsapp-session && mkdir -p /var/lib/iris/whatsapp-session' \
    </dev/null || true
  dc run --rm --no-deps iris-bridge node /opt/iris/bridge/server.js --pair </dev/tty \
    || warn "El pareo no terminó. Reintenta con: docker compose -f $COMPOSE_FILE run --rm --no-deps iris-bridge node /opt/iris/bridge/server.js --pair"
  dc up -d iris-bridge
}

service_health() {
  local svc="$1" port="$2"
  dc exec -T "$svc" curl -fsS "http://127.0.0.1:${port}/health" >/dev/null 2>&1
}

wait_core() {
  local i
  for i in $(seq 1 60); do
    if service_health iris-core 3004; then return 0; fi
    sleep 2
  done
  return 1
}

send_tests() {
  local token
  token="$(sed -n 's/^IRIS_BRIDGE_TOKEN=//p' "$ENV_FILE" | head -n1)"
  if has_whatsapp && [[ -n $PHONE ]]; then
    local digits
    digits="$(printf '%s' "$PHONE" | tr -cd '0-9')"
    if dc exec -T iris-core curl -fsS -X POST "http://iris-bridge:3003/send" \
        -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
        -d "{\"chatId\":\"${digits}@s.whatsapp.net\",\"text\":\"Iris en línea ✅\"}" >/dev/null 2>&1; then
      ok "Mensaje de prueba enviado por WhatsApp."
    else
      warn "No pude enviar el mensaje de prueba por WhatsApp."
    fi
  fi
  if has_telegram && [[ $TELEGRAM_USER != "*" && -n $TELEGRAM_USER ]]; then
    if dc exec -T iris-core curl -fsS -X POST "http://iris-telegram:3005/send" \
        -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
        -d "{\"chatId\":\"$TELEGRAM_USER\",\"text\":\"Iris en línea ✅\"}" >/dev/null 2>&1; then
      ok "Mensaje de prueba enviado por Telegram."
    else
      warn "No pude enviar por Telegram. Abre @$TELEGRAM_BOT y pulsa /start."
    fi
  fi
}

# ── Modos ───────────────────────────────────────────────────────────────────

do_uninstall() {
  require_docker
  if [[ -f $COMPOSE_FILE ]]; then
    log "Bajando servicios de Iris…"
    dc down || true
    if [[ $PURGE -eq 1 ]]; then
      dc down -v || true
      rm -rf "$APP_DIR"
      ok "Iris eliminado con sus volúmenes."
    else
      dim "Se conservaron los volúmenes. Usa --purge para borrarlos."
    fi
  fi
  if [[ -f $NOTAS_DIR/docker-compose.yml ]]; then
    if [[ $PURGE -eq 1 ]] && confirm "¿Borrar también Notas y sus datos?"; then
      notas_dc down -v || true
      rm -rf "$NOTAS_DIR"
      ok "Notas eliminado con sus datos."
    else
      dim "Notas sigue instalado en $NOTAS_DIR."
    fi
  fi
  exit 0
}

do_update() {
  require_docker
  if [[ "$(readlink -f "$SRC_DIR")" == "$(readlink -f "$APP_DIR")" ]]; then
    force_sources
  fi
  ensure_sources
  copy_sources
  load_env_file
  BRIDGE_TOKEN="${IRIS_BRIDGE_TOKEN:-$BRIDGE_TOKEN}"
  log "Reconstruyendo y reiniciando Iris…"
  dc up -d --build
  wait_core || die "El núcleo no respondió. Revisa: docker compose logs iris-core"
  ok "Iris actualizado."
  exit 0
}

# ── Instalación ─────────────────────────────────────────────────────────────

print_banner
require_docker

if [[ $MODE == uninstall ]]; then do_uninstall; fi
if [[ $MODE == update ]]; then do_update; fi

choose_channels
if has_whatsapp; then
  if [[ -z $PHONE ]]; then
    ask PHONE "Número de WhatsApp autorizado (con indicativo, ej. +573001112233)" "" \
      || die "Instalación cancelada."
    PHONE="${PHONE//[[:space:]]/}"
    [[ $PHONE =~ ^\+?[0-9]{8,15}$ ]] || die "Número no válido: $PHONE"
  fi
fi
if has_telegram; then collect_telegram; fi
choose_provider
collect_provider_config

[[ -n $BRIDGE_TOKEN ]] || BRIDGE_TOKEN="$(rand_hex)"

echo
log "Resumen:"
dim "  Canales:   $(channel_label "$CHANNELS")"
if has_whatsapp; then dim "  WhatsApp:  $PHONE"; fi
if has_telegram; then dim "  Telegram:  @$TELEGRAM_BOT · ID $TELEGRAM_USER"; fi
dim "  Proveedor: $(provider_label "$PROVIDER")"
dim "  Modelo:    ${MODEL:-(predeterminado)}"
dim "  Código:    $APP_DIR"
echo
confirm "¿Continuar con la instalación?" || { log "Cancelado."; exit 0; }

ensure_sources
copy_sources

NOTAS_URL="${NOTAS_URL:-}"
NOTAS_TOKEN_IN="${NOTAS_TOKEN_IN:-}"
write_env

# Token de suscripción de Claude (si aplica) antes de levantar los servicios.
if [[ $PROVIDER == claude-subscription && -z $OAUTH_TOKEN ]]; then
  log "Autorización de Claude (suscripción)"
  dim "Se mostrará una URL. Autoriza y copia el token."
  TOKEN_OUT="$(mktemp)"
  dc run --rm iris-core claude setup-token </dev/tty 2>&1 | tee "$TOKEN_OUT" || true
  OAUTH_TOKEN="$(grep -oE 'sk-ant-oat[0-9]*-[A-Za-z0-9_-]+' "$TOKEN_OUT" | tail -1)"
  rm -f "$TOKEN_OUT"
  if [[ -z $OAUTH_TOKEN ]]; then
    read_secret OAUTH_TOKEN "Pega el token (sk-ant-oat01-...)" || die "Instalación cancelada."
  fi
  [[ -n $OAUTH_TOKEN ]] || die "No obtuve el token de la suscripción."
  write_env
fi

if [[ $PROVIDER == ollama-local ]]; then
  start_ollama
  ollama_signin
  dc exec -T iris-ollama ollama pull "$MODEL" >/dev/null 2>&1 \
    || warn "No pude descargar/verificar '$MODEL'; se intentará al primer uso."
fi

log "Levantando Iris…"
dc up -d --build
wait_core || die "El núcleo no respondió. Revisa: docker compose logs iris-core"
ok "Núcleo en línea."

pair_whatsapp
notas_question

# Si hay Notas, se elige cómo guardar las conversaciones (solo aplica a Iris).
if [[ -n $NOTAS_TOKEN_IN ]]; then
  if [[ -z $MEMORY_MODE ]]; then
    if [[ $ASSUME_YES -eq 1 ]]; then
      MEMORY_MODE=manual
    else
      ui_menu "Memoria de Iris" "¿Cómo guardo las conversaciones?" \
        "manual::Manual::solo con /guardar" \
        "auto::Automático::guardo cada intercambio" \
        || die "Selección cancelada."
      MEMORY_MODE="$REPLY"
    fi
  fi
  case "${MEMORY_MODE,,}" in
    auto|automático|automatico) MEMORY_MODE=auto ;;
    manual) MEMORY_MODE=manual ;;
    *) die "Valor no válido para --memoria: $MEMORY_MODE" ;;
  esac
  set_env_var IRIS_MEMORY_MODE "$MEMORY_MODE"

  if [[ -z $REMINDERS ]]; then
    if [[ $ASSUME_YES -eq 1 ]]; then
      REMINDERS=off
    else
      ask REMINDERS "¿Activar recordatorios de Notas? (s/n)" "s"
    fi
  fi
  case "${REMINDERS,,}" in
    s|si|sí|yes|y|on|1) REMINDERS=on ;;
    n|no|off|0) REMINDERS=off ;;
    *) die "Valor no válido para --recordatorios: $REMINDERS" ;;
  esac
  if [[ $REMINDERS == on && -z $REMINDER_CHANNEL ]]; then
    if has_whatsapp && has_telegram; then
      ui_menu "Recordatorios" "¿Por dónde te aviso los recordatorios?" \
        "telegram::Telegram::bot oficial" \
        "whatsapp::WhatsApp::tu número" \
        || die "Selección cancelada."
      REMINDER_CHANNEL="$REPLY"
    elif has_telegram; then
      REMINDER_CHANNEL=telegram
    else
      REMINDER_CHANNEL=whatsapp
    fi
  fi
  [[ -n $REMINDER_CHANNEL ]] || REMINDER_CHANNEL=auto
  set_env_var IRIS_REMINDERS "$REMINDERS"
  set_env_var IRIS_REMINDER_CHANNEL "$REMINDER_CHANNEL"
  dc up -d iris-core >/dev/null
  wait_core || true
fi

# Si Notas se acaba de instalar, se guardan URL y token y se recrea el núcleo.
if [[ -n $NOTAS_TOKEN_IN ]]; then
  if ! grep -q "^NOTAS_API_TOKEN=${NOTAS_TOKEN_IN}$" "$ENV_FILE" \
     || ! grep -q "^NOTAS_API_URLS=${NOTAS_URL}$" "$ENV_FILE"; then
    sed -i.bak "s|^NOTAS_API_URLS=.*|NOTAS_API_URLS=${NOTAS_URL}|" "$ENV_FILE" 2>/dev/null || true
    grep -q "^NOTAS_API_URLS=" "$ENV_FILE" || echo "NOTAS_API_URLS=${NOTAS_URL}" >>"$ENV_FILE"
    sed -i.bak "s|^NOTAS_API_URL=.*|NOTAS_API_URL=${NOTAS_URL%%,*}|" "$ENV_FILE" 2>/dev/null || true
    sed -i.bak "s|^NOTAS_API_TOKEN=.*|NOTAS_API_TOKEN=${NOTAS_TOKEN_IN}|" "$ENV_FILE" 2>/dev/null || true
    rm -f "${ENV_FILE}.bak"
    dc up -d iris-core >/dev/null
    wait_core || true
  fi
fi

send_tests

echo
ok "Iris quedó instalado (Docker)."
dim "  Servicios:  iris-core  iris-bridge  iris-telegram  iris-ollama"
dim "  Config:     $ENV_FILE"
dim "  Código:     $APP_DIR"
if has_whatsapp; then dim "  WhatsApp:   $PHONE"; fi
if has_telegram; then dim "  Telegram:   @$TELEGRAM_BOT"; fi
dim "  Gestión:    docker compose -f $COMPOSE_FILE ps | logs -f | restart"
if [[ -n $NOTAS_TOKEN_IN ]]; then
  dim "  Notas:      $NOTAS_URL (token guardado en $ENV_FILE)"
  if [[ -n $NOTAS_USER_SET ]]; then
    echo
    warn "Guarda las credenciales de Notas:"
    dim "  Usuario:  $NOTAS_USER_SET"
    dim "  Clave:    $NOTAS_PASS_SET"
  fi
fi
echo
if has_whatsapp && has_telegram; then
  dim "Prueba: escribe por WhatsApp a $PHONE o a @$TELEGRAM_BOT"
elif has_telegram; then
  dim "Prueba: escribe a @$TELEGRAM_BOT"
else
  dim "Prueba: escribe por WhatsApp a $PHONE"
fi
