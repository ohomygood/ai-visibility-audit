#!/usr/bin/env bash
# Real-engine check, Gemini free tier only ($0). Never prints the API key.
set -u
cd "$(dirname "$0")/.."
source ~/.bashrc >/dev/null 2>&1 || true
set -a; source config/dev.env; set +a
export AIV_ENGINES="" AIV_ENGINE_MODE=gemini-only AIV_LOG_DIR="$PWD/samples/gemini-real/logs"
# Make sure no paid engine can be reached even by accident.
unset OPENAI_API_KEY PERPLEXITY_API_KEY AIV_OAI_COMPAT_BASE_URL
mode="$1"; shift
for url in "$@"; do
  d=$(python3 -c "import sys,urllib.parse;print(urllib.parse.urlparse(sys.argv[1]).netloc.removeprefix('www.'))" "$url")
  out="samples/gemini-real/$mode/$d"
  s=$(date +%s)
  python3 -m aiv "$mode" "$url" --out "$out" --budget 0 > "$out.summary.json" 2> "$out.stderr.txt"
  echo "$mode $d exit=$? wall=$(( $(date +%s) - s ))s"
done
