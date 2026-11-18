#!/usr/bin/env bash

set -uo pipefail

BASE="${BASE:-http://localhost:8000}"
LATENCY_N=0

if [[ "${1:-}" == "--latency" ]]; then
    LATENCY_N="${2:-}"
    if [[ ! "$LATENCY_N" =~ ^[0-9]+$ ]] || [[ "$LATENCY_N" -lt 1 ]]; then
        echo "CHECK latency: FAIL invalid N" >&2
        exit 2
    fi
fi

failures=0
API_KEY=""
JOB_PIDS=()
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

pass_check() {
    echo "CHECK $1: PASS $2"
}

fail_check() {
    echo "CHECK $1: FAIL $2"
    failures=$((failures + 1))
}

info_check() {
    echo "CHECK $1: INFO $2"
}

find_python() {
    local candidates=(
        "$PWD/.venv/bin/python"
        "$PWD/modelmesh-env/bin/python"
        "$HOME/modelmesh/modelmesh-env/bin/python"
    )
    local candidate
    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]] && "$candidate" -c 'import websockets' >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN=$(find_python || true)

health_code=$(curl -sS -o "$TMP_DIR/health.json" -w '%{http_code}' "$BASE/health" 2>/dev/null || true)
ready_code=$(curl -sS -o "$TMP_DIR/ready.json" -w '%{http_code}' "$BASE/ready" 2>/dev/null || true)
if [[ "$health_code" == "200" && "$ready_code" == "200" ]]; then
    pass_check health "health=200 ready=200"
else
    fail_check health "health=$health_code ready=$ready_code"
fi

models_json=$(curl -sS "$BASE/v1/models" 2>/dev/null || true)
if printf '%s' "$models_json" | jq -e 'type == "array" and any(.[]; .name == "doc-ocr") and any(.[]; .name == "asr")' >/dev/null 2>&1; then
    pass_check models "HTTP 200 with doc-ocr and asr"
else
    fail_check models "missing models or HTTP failure"
fi

register_payload='{"email":"baseline@example.com","password":"baseline-pass-1"}'
register_code=$(curl -sS -o "$TMP_DIR/register.json" -w '%{http_code}' \
    -X POST "$BASE/v1/auth/register" \
    -H 'Content-Type: application/json' -d "$register_payload" 2>/dev/null || true)
login_json=$(curl -sS -X POST "$BASE/v1/auth/login" \
    -H 'Content-Type: application/json' -d "$register_payload" 2>/dev/null || true)
JWT=$(printf '%s' "$login_json" | jq -r '.access_token // empty')

if [[ -n "$JWT" ]]; then
    key_json=$(curl -sS -X POST "$BASE/v1/auth/api-keys" \
        -H "Authorization: Bearer $JWT" \
        -H 'Content-Type: application/json' \
        -d '{"rate_limit_per_min":100}' 2>/dev/null || true)
    API_KEY=$(printf '%s' "$key_json" | jq -r '.key // empty')
fi

if [[ -n "$API_KEY" ]]; then
    pass_check auth "registration=$register_code login=200 key=issued"
else
    fail_check auth "registration=$register_code login=$(printf '%s' "$login_json" | jq -r '.detail // "no access_token"')"
fi

poll_job() {
    local job_id="$1"
    local headers="$2"
    local result=""
    local i
    for i in $(seq 1 30); do
        result=$(curl -sS "$BASE/v1/jobs/$job_id" -H "Authorization: Bearer $headers" 2>/dev/null || true)
        local status
        status=$(printf '%s' "$result" | jq -r '.status // empty')
        if [[ "$status" == "success" || "$status" == "failed" ]]; then
            printf '%s' "$result"
            return 0
        fi
        sleep 2
    done
    printf '%s' "$result"
}

ocr_start=$(date +%s)
ocr_job=$(curl -sS -X POST "$BASE/v1/infer/doc-ocr" \
    -H "Authorization: Bearer $API_KEY" \
    -F 'file=@fixtures/sample_document.jpg' 2>/dev/null | jq -r '.job_id // empty')
ocr_final=""
if [[ -n "$ocr_job" ]]; then
    ocr_final=$(poll_job "$ocr_job" "$API_KEY")
fi
ocr_elapsed=$(( $(date +%s) - ocr_start ))
ocr_status=$(printf '%s' "$ocr_final" | jq -r '.status // empty')
ocr_text=$(printf '%s' "$ocr_final" | jq -r '.result.text // ""')
if [[ "$ocr_status" == "success" ]] && [[ "$ocr_text" == *"HELLO"* ]]; then
    pass_check doc-ocr-job "status=success seconds=$ocr_elapsed text_contains_HELLO=yes"
else
    fail_check doc-ocr-job "status=$ocr_status seconds=$ocr_elapsed"
fi

asr_start=$(date +%s)
asr_job=$(curl -sS -X POST "$BASE/v1/infer/asr" \
    -H "Authorization: Bearer $API_KEY" \
    -F 'file=@fixtures/sample_audio.wav' 2>/dev/null | jq -r '.job_id // empty')
asr_final=""
if [[ -n "$asr_job" ]]; then
    asr_final=$(poll_job "$asr_job" "$API_KEY")
fi
asr_elapsed=$(( $(date +%s) - asr_start ))
asr_status=$(printf '%s' "$asr_final" | jq -r '.status // empty')
if [[ "$asr_status" == "success" ]]; then
    pass_check asr-job "status=success seconds=$asr_elapsed"
else
    fail_check asr-job "status=$asr_status seconds=$asr_elapsed"
fi

if [[ -n "$PYTHON_BIN" ]]; then
    "$PYTHON_BIN" test_ws_client.py "$API_KEY" asr fixtures/sample_audio.wav >"$TMP_DIR/ws-asr.raw" 2>&1
    ws_asr_rc=$?
    sed -E "s/$API_KEY/[REDACTED]/g" "$TMP_DIR/ws-asr.raw" >"$TMP_DIR/ws-asr.out"
    sed -E "s/$API_KEY/[REDACTED]/g" "$TMP_DIR/ws-asr.out" >/dev/null
    if [[ $ws_asr_rc -eq 0 ]]; then
        pass_check ws-asr "exit=0 final_result=yes"
    else
        fail_check ws-asr "exit=$ws_asr_rc output=$(grep -v 'api_key=' "$TMP_DIR/ws-asr.out" | tr '\n' ' ' | cut -c1-160)"
    fi

    "$PYTHON_BIN" test_ws_client.py "$API_KEY" doc-ocr fixtures/sample_document.jpg >"$TMP_DIR/ws-doc.raw" 2>&1
    ws_doc_rc=$?
    sed -E "s/$API_KEY/[REDACTED]/g" "$TMP_DIR/ws-doc.raw" >"$TMP_DIR/ws-doc.out"
    sed -E "s/$API_KEY/[REDACTED]/g" "$TMP_DIR/ws-doc.out" >/dev/null
    if [[ $ws_doc_rc -eq 0 ]]; then
        pass_check ws-doc-ocr "exit=0 final_result=yes"
    else
        fail_check ws-doc-ocr "exit=$ws_doc_rc output=$(grep -v 'api_key=' "$TMP_DIR/ws-doc.out" | tr '\n' ' ' | cut -c1-160)"
    fi
else
    fail_check ws-asr "WebSocket Python environment unavailable"
    fail_check ws-doc-ocr "WebSocket Python environment unavailable"
fi

limit_key_json=$(curl -sS -X POST "$BASE/v1/auth/api-keys" \
    -H "Authorization: Bearer $JWT" \
    -H 'Content-Type: application/json' \
    -d '{"rate_limit_per_min":2}' 2>/dev/null || true)
LIMIT_KEY=$(printf '%s' "$limit_key_json" | jq -r '.key // empty')
rate_codes=""
if [[ -n "$LIMIT_KEY" ]]; then
    for i in 1 2 3; do
        code=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$BASE/v1/infer/doc-ocr" \
            -H "Authorization: Bearer $LIMIT_KEY" \
            -F 'file=@fixtures/sample_document.jpg' 2>/dev/null || true)
        rate_codes="${rate_codes}${rate_codes:+,}${code}"
    done
fi
if [[ "$rate_codes" == "200,200,429" ]]; then
    pass_check rate-limit "codes=$rate_codes"
else
    fail_check rate-limit "codes=${rate_codes:-none}"
fi

if [[ -n "$API_KEY" ]]; then
    model_id=$(printf '%s' "$models_json" | jq -r '.[] | select(.name=="doc-ocr") | .id' | head -1)
    docker compose exec -T postgres psql -U user -d modelmesh -c \
        "UPDATE api_keys SET is_admin = TRUE WHERE key_hash = encode(digest('$API_KEY','sha256'),'hex');" \
        >/dev/null 2>&1
    patch1=$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH "$BASE/v1/models/$model_id" \
        -H "Authorization: Bearer $API_KEY" \
        -H 'Content-Type: application/json' \
        -d '{"description":"baseline-check"}' 2>/dev/null || true)
    patch2=$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH "$BASE/v1/models/$model_id" \
        -H "Authorization: Bearer $API_KEY" \
        -H 'Content-Type: application/json' \
        -d '{"description":"EasyOCR document text extraction"}' 2>/dev/null || true)
    if [[ "$patch1" == "200" && "$patch2" == "200" ]]; then
        pass_check admin-patch "change=200 restore=200"
    else
        fail_check admin-patch "change=$patch1 restore=$patch2"
    fi
else
    fail_check admin-patch "API key unavailable"
fi

printf 'not an image\n' >"$TMP_DIR/bad.txt"
bad_start=$(date +%s)
bad_job=$(curl -sS -X POST "$BASE/v1/infer/doc-ocr" \
    -H "Authorization: Bearer $API_KEY" \
    -H 'Accept: application/json' \
    -F 'file=@'"$TMP_DIR"'/bad.txt;type=image/jpeg' 2>/dev/null | jq -r '.job_id // empty')
bad_final=""
if [[ -n "$bad_job" ]]; then
    for i in $(seq 1 120); do
        bad_final=$(curl -sS "$BASE/v1/jobs/$bad_job" -H "Authorization: Bearer $API_KEY" 2>/dev/null || true)
        if [[ $(printf '%s' "$bad_final" | jq -r '.status // empty') == "failed" ]]; then
            break
        fi
        sleep 1
    done
fi
bad_elapsed=$(( $(date +%s) - bad_start ))
bad_error=$(printf '%s' "$bad_final" | jq -r '.error // .detail // "unknown"')
info_check bad-upload "seconds=$bad_elapsed error=$bad_error"

if [[ "$LATENCY_N" -gt 0 ]]; then
    for model in doc-ocr asr; do
        fixture="fixtures/sample_document.jpg"
        [[ "$model" == "asr" ]] && fixture="fixtures/sample_audio.wav"
        times_file="$TMP_DIR/${model}.times"
        : >"$times_file"
        for i in $(seq 1 "$LATENCY_N"); do
            start_ns=$(date +%s%N)
            job_id=$(curl -sS -X POST "$BASE/v1/infer/$model" \
                -H "Authorization: Bearer $API_KEY" \
                -F "file=@$fixture" 2>/dev/null | jq -r '.job_id // empty')
            if [[ -n "$job_id" ]]; then
                final=$(poll_job "$job_id" "$API_KEY")
            fi
            end_ns=$(date +%s%N)
            elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
            awk -v ms="$elapsed_ms" 'BEGIN { printf "%.3f\n", ms / 1000 }' >>"$times_file"
        done
        sorted=$(sort -n "$times_file")
        count=$(printf '%s\n' "$sorted" | wc -l)
        median_idx=$(( (count + 1) / 2 ))
        p95_idx=$(( (95 * count + 99) / 100 ))
        median=$(printf '%s\n' "$sorted" | sed -n "${median_idx}p")
        p95=$(printf '%s\n' "$sorted" | sed -n "${p95_idx}p")
        echo "LATENCY $model: median=${median}s p95=${p95}s"
    done
fi

if [[ $failures -eq 0 ]]; then
    echo "SMOKE: PASS"
    exit 0
else
    echo "SMOKE: FAIL ($failures checks failed)"
    exit 1
fi
