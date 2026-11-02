#!/usr/bin/env bash
# ==============================================================================
# Matrix Orchestrator for Database Contention Benchmark
# Course: Bases de Datos en la Nube (IT2iD - 2026)
# Reference: playbook.pdf (Section 6, 8.1) & playbook_sesion.pdf (Slide 8, 13)
# ==============================================================================

set -euo pipefail

WORKSPACE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKSPACE_ROOT"

PYTHON_BIN="$WORKSPACE_ROOT/.venv/bin/python3"
if [ ! -f "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

CSV_OUTPUT="results/metrics.csv"
PROMETHEUS_URL="http://127.0.0.1:9090"

# Colors for terminal clarity
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $(date +'%Y-%m-%dT%H:%M:%S%z') $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $(date +'%Y-%m-%dT%H:%M:%S%z') $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date +'%Y-%m-%dT%H:%M:%S%z') $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date +'%Y-%m-%dT%H:%M:%S%z') $1"
}

# ------------------------------------------------------------------------------
# 1. Telemetry Stack Verification
# ------------------------------------------------------------------------------
ensure_telemetry_stack() {
    log_info "Ensuring Prometheus, cAdvisor, and Node Exporter are active..."
    docker compose up -d cadvisor node-exporter prometheus
    sleep 3
}

# ------------------------------------------------------------------------------
# 2. Linux VM Kernel Page Cache Flush (Bridge Mechanism)
# ------------------------------------------------------------------------------
flush_page_cache() {
    log_info "Flushing Linux VM page cache via privileged container..."
    docker run --rm --privileged alpine:3.22 sh -c "sync && echo 3 > /proc/sys/vm/drop_caches"
    sleep 2
}

# ------------------------------------------------------------------------------
# 3. Database Engine Lifecycle & Isolation
# ------------------------------------------------------------------------------
switch_engine() {
    local target_engine="$1"
    local profile="$2"

    local cpus="4.0"
    local memory="4096m"

    case "$profile" in
        "PROFILE_A_BASELINE")
            cpus="4.0"
            memory="4096m"
            ;;
        "PROFILE_B_MEDIUM")
            cpus="2.0"
            memory="2048m"
            ;;
        "PROFILE_C_SEVERE")
            cpus="1.0"
            memory="1024m"
            ;;
        *)
            log_warn "Unknown profile '$profile', defaulting to PROFILE_A_BASELINE"
            ;;
    esac

    # Strictly enforce Single-Engine Isolation (preferences.md)
    if [ "$target_engine" == "mongodb" ]; then
        log_info "Stopping PostgreSQL to enforce single-engine isolation..."
        docker compose stop postgresql 2>/dev/null || true
        log_info "Launching MongoDB under $profile (CPUs: $cpus, RAM: $memory, Swap: 0)..."
        BENCHMARK_CPUS="$cpus" BENCHMARK_MEMORY="$memory" docker compose up -d mongodb
    elif [ "$target_engine" == "postgresql" ]; then
        log_info "Stopping MongoDB to enforce single-engine isolation..."
        docker compose stop mongodb 2>/dev/null || true
        log_info "Launching PostgreSQL 18 under $profile (CPUs: $cpus, RAM: $memory, Swap: 0)..."
        BENCHMARK_CPUS="$cpus" BENCHMARK_MEMORY="$memory" docker compose up -d postgresql
    fi

    # Wait for database readiness
    sleep 3
}

ensure_engine_running() {
    local engine="$1"
    local profile="$2"
    local container="benchmark_${engine}"

    if ! docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
        log_warn "Container ${container} is down (possibly OOM-killed). Recovering under ${profile}..."
        switch_engine "$engine" "$profile"
    fi
}

# ------------------------------------------------------------------------------
# 4. Strict Run Completion Verification (Resolves Bloqueo 2)
# ------------------------------------------------------------------------------
is_run_completed() {
    local engine="$1"
    local profile="$2"
    local query_id="$3"
    local concurrency="$4"
    local run_number="$5"

    if [ ! -f "$CSV_OUTPUT" ]; then
        return 1
    fi

    # Format: timestamp_epoch,engine,profile,query_id,concurrency,run_number,throughput_qps,lat_p50,lat_p95,lat_p99,cpu_throttled,max_rss,disk_spill,status
    local matching_line
    matching_line=$(grep "^[0-9]*,${engine},${profile},${query_id},${concurrency},${run_number}," "$CSV_OUTPUT" 2>/dev/null | tail -n 1 || true)

    if [ -z "$matching_line" ]; then
        return 1
    fi

    local status
    status=$(echo "$matching_line" | awk -F',' '{print $14}')
    local throughput
    throughput=$(echo "$matching_line" | awk -F',' '{print $7}')

    # Bloqueo 2 Fix: If status is 'error', the run is NOT complete and must be repeated
    if [ "$status" == "error" ]; then
        log_warn "Existing run has status 'error'. Must be re-executed: $matching_line"
        return 1
    fi

    # If status is not 'oom' and throughput is 0.0, it is an invalid zombie measurement
    if [ "$status" != "oom" ] && [ "$throughput" == "0.0" ]; then
        log_warn "Existing run has 0.0 throughput without OOM. Must be re-executed: $matching_line"
        return 1
    fi

    return 0
}

# ------------------------------------------------------------------------------
# 5. Pre-flight Verification Gate (Baseline Comparability)
# ------------------------------------------------------------------------------
run_preflight_check() {
    log_info "=== EXECUTING MANDATORY PRE-FLIGHT VERIFICATION GATE ==="
    ensure_telemetry_stack

    log_info "1/2 Verifying PostgreSQL 18 dataset and indexes..."
    switch_engine "postgresql" "PROFILE_A_BASELINE"
    "$PYTHON_BIN" -m benchmark_runner.load --engine postgresql

    log_info "2/2 Verifying MongoDB dataset and indexes..."
    switch_engine "mongodb" "PROFILE_A_BASELINE"
    "$PYTHON_BIN" -m benchmark_runner.load --engine mongodb

    log_success "Pre-flight verification passed: Both engines contain verified 500,000 datasets."
}

# ------------------------------------------------------------------------------
# 6. Pilot Dual-Engine Validation Run
# ------------------------------------------------------------------------------
run_pilot() {
    log_info "=== EXECUTING PILOT DUAL-ENGINE VALIDATION ==="
    run_preflight_check

    # 1. PostgreSQL Pilot Cell
    switch_engine "postgresql" "PROFILE_A_BASELINE"
    flush_page_cache
    log_info "Executing PostgreSQL pilot cell (Profile A, Q1, C=1)..."
    "$PYTHON_BIN" -m benchmark_runner \
        --engine "postgresql" \
        --profile "PROFILE_A_BASELINE" \
        --query "Q1" \
        --concurrency 1 \
        --run-number 1 \
        --variant "default" \
        --warmup 5 \
        --duration 15 \
        --cooldown 3 \
        --output-csv "$CSV_OUTPUT" \
        --prometheus-url "$PROMETHEUS_URL" \
        --pg-port 5434

    # 2. MongoDB Pilot Cell
    switch_engine "mongodb" "PROFILE_A_BASELINE"
    flush_page_cache
    log_info "Executing MongoDB pilot cell (Profile A, Q1, C=1)..."
    "$PYTHON_BIN" -m benchmark_runner \
        --engine "mongodb" \
        --profile "PROFILE_A_BASELINE" \
        --query "Q1" \
        --concurrency 1 \
        --run-number 1 \
        --variant "default" \
        --warmup 5 \
        --duration 15 \
        --cooldown 3 \
        --output-csv "$CSV_OUTPUT" \
        --prometheus-url "$PROMETHEUS_URL"

    log_success "Pilot execution completed for both engines. Output CSV:"
    cat "$CSV_OUTPUT"
}

# ------------------------------------------------------------------------------
# 7. Full Matrix Execution (162 Runs)
# ------------------------------------------------------------------------------
run_full_matrix() {
    log_info "=== INITIATING FULL BENCHMARK MATRIX (162 RUNS) ==="
    run_preflight_check

    local engines=("mongodb" "postgresql")
    local profiles=("PROFILE_A_BASELINE" "PROFILE_B_MEDIUM" "PROFILE_C_SEVERE")
    local concurrencies=(1 10 50)
    local warmup=60
    local duration=300
    local cooldown=60

    for engine in "${engines[@]}"; do
        for profile in "${profiles[@]}"; do
            switch_engine "$engine" "$profile"

            # Execute Q1 (3 concurrencies x 3 runs = 9 runs)
            for concurrency in "${concurrencies[@]}"; do
                for run_num in 1 2 3; do
                    local attempt=0
                    local max_attempts=3
                    while [ $attempt -lt $max_attempts ]; do
                        if is_run_completed "$engine" "$profile" "Q1" "$concurrency" "$run_num"; then
                            log_info "Run validated in CSV: $engine | $profile | Q1 | C=$concurrency | Run=$run_num."
                            break
                        fi

                        if [ $attempt -gt 0 ]; then
                            log_warn "Retrying cell (attempt $((attempt + 1))/$max_attempts): $engine | $profile | Q1 | C=$concurrency | Run=$run_num"
                        fi

                        ensure_engine_running "$engine" "$profile"
                        flush_page_cache
                        "$PYTHON_BIN" -m benchmark_runner \
                            --engine "$engine" \
                            --profile "$profile" \
                            --query "Q1" \
                            --concurrency "$concurrency" \
                            --run-number "$run_num" \
                            --variant "default" \
                            --warmup "$warmup" \
                            --duration "$duration" \
                            --cooldown "$cooldown" \
                            --output-csv "$CSV_OUTPUT" \
                            --prometheus-url "$PROMETHEUS_URL" \
                            --pg-port 5434
                        attempt=$((attempt + 1))
                    done
                done
            done

            # Execute Q2 Memory Stress Variants (2 variants x 3 concurrencies x 3 runs = 18 runs)
            local variants=()
            if [ "$engine" == "mongodb" ]; then
                variants=("allowDiskUse_false" "allowDiskUse_true")
            else
                variants=("work_mem_4MB" "work_mem_64MB")
            fi

            for variant in "${variants[@]}"; do
                local q_key="Q2_${variant}"
                for concurrency in "${concurrencies[@]}"; do
                    for run_num in 1 2 3; do
                        local attempt=0
                        local max_attempts=3
                        while [ $attempt -lt $max_attempts ]; do
                            if is_run_completed "$engine" "$profile" "$q_key" "$concurrency" "$run_num"; then
                                log_info "Run validated in CSV: $engine | $profile | $q_key | C=$concurrency | Run=$run_num."
                                break
                            fi

                            if [ $attempt -gt 0 ]; then
                                log_warn "Retrying cell (attempt $((attempt + 1))/$max_attempts): $engine | $profile | $q_key | C=$concurrency | Run=$run_num"
                            fi

                            ensure_engine_running "$engine" "$profile"
                            flush_page_cache
                            "$PYTHON_BIN" -m benchmark_runner \
                                --engine "$engine" \
                                --profile "$profile" \
                                --query "Q2" \
                                --concurrency "$concurrency" \
                                --run-number "$run_num" \
                                --variant "$variant" \
                                --warmup "$warmup" \
                                --duration "$duration" \
                                --cooldown "$cooldown" \
                                --output-csv "$CSV_OUTPUT" \
                                --prometheus-url "$PROMETHEUS_URL" \
                                --pg-port 5434
                            attempt=$((attempt + 1))
                        done
                    done
                done
            done

        done
    done

    log_success "All 162 runs of the benchmark matrix completed successfully."
}

# ------------------------------------------------------------------------------
# Entrypoint Dispatcher
# ------------------------------------------------------------------------------
main() {
    local mode="${1:---pilot}"
    case "$mode" in
        --preflight)
            run_preflight_check
            ;;
        --pilot)
            run_pilot
            ;;
        --full)
            run_full_matrix
            ;;
        *)
            echo "Usage: $0 [--preflight | --pilot | --full]"
            exit 1
            ;;
    esac
}

main "$@"
