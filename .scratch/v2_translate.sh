#!/usr/bin/env bash
# Chunked translate with health-check + retry, reusing the same pattern
# from the previous session.
set -u
DOC=d71027f0-6537-58d1-8e47-42ef2834fca4
ROOT=/Users/smy/project/book-agent/.scratch
PROGRESS="$ROOT/v2_progress.log"
IDS_FILE="$ROOT/v2_packet_ids.json"
CHUNK=${CHUNK:-8}

> "$PROGRESS"

health_check() {
    local db_ok api_ok
    db_ok=$(uv run python -c "
from sqlalchemy import create_engine
try:
    create_engine('postgresql+psycopg://postgres:postgres@localhost:55432/book_agent', pool_pre_ping=True).connect().close()
    print('ok')
except Exception as e:
    print('FAIL:', e)
" 2>&1 | tail -1)
    api_ok=$(curl -sS -o /dev/null -w "%{http_code}" -m 5 http://localhost:8999/v1/providers/active 2>/dev/null)
    if [ "$db_ok" != "ok" ] || [ "$api_ok" != "200" ]; then
        echo "[$(date +%H:%M:%S)] HEALTH FAIL db=$db_ok api=$api_ok — aborting" | tee -a "$PROGRESS"
        return 1
    fi
    return 0
}

COUNT=$(python3 -c "import json; print(len(json.load(open('$IDS_FILE'))))")
echo "[$(date +%H:%M:%S)] starting v2: $COUNT packets, chunk=$CHUNK" | tee -a "$PROGRESS"
health_check || exit 1

TOTAL_TRANS=0
TOTAL_FAIL=0
START_TIME=$(date +%s)

for ((i=0; i<COUNT; i+=CHUNK)); do
    BATCH=$(python3 -c "
import json
ids=json.load(open('$IDS_FILE'))
print(json.dumps({'packet_ids': ids[$i:$i+$CHUNK]}))
")
    BATCH_SIZE=$(echo "$BATCH" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['packet_ids']))")
    BATCH_NUM=$((i/CHUNK+1))

    ATTEMPT=0
    SUCCESS=0
    while [ $ATTEMPT -lt 3 ]; do
        ATTEMPT=$((ATTEMPT+1))
        RESP_FILE=$(mktemp)
        STATUS=$(curl -sS -X POST "http://localhost:8999/v1/documents/$DOC/translate" \
            -H "Content-Type: application/json" \
            -d "$BATCH" \
            -o "$RESP_FILE" \
            -w "%{http_code}_%{time_total}" --max-time 600 2>&1)
        HTTP=$(echo "$STATUS" | cut -d_ -f1)
        TIME=$(echo "$STATUS" | cut -d_ -f2)
        if [ "$HTTP" = "200" ]; then
            TRANSLATED=$(python3 -c "import json; print(json.load(open('$RESP_FILE')).get('translated_packet_count',0))" 2>/dev/null || echo 0)
            echo "[$(date +%H:%M:%S)] batch $BATCH_NUM/$((COUNT/CHUNK+1)): $TRANSLATED/$BATCH_SIZE ok in ${TIME}s (try $ATTEMPT)" | tee -a "$PROGRESS"
            TOTAL_TRANS=$((TOTAL_TRANS+TRANSLATED))
            SUCCESS=1
            rm -f "$RESP_FILE"
            break
        else
            ERRMSG=$(head -c 200 "$RESP_FILE" 2>/dev/null)
            echo "[$(date +%H:%M:%S)] batch $BATCH_NUM try $ATTEMPT FAILED http=$HTTP time=${TIME}s err=$ERRMSG" | tee -a "$PROGRESS"
            rm -f "$RESP_FILE"
            sleep 5
            health_check || exit 2
        fi
    done
    if [ $SUCCESS -eq 0 ]; then
        TOTAL_FAIL=$((TOTAL_FAIL+BATCH_SIZE))
        echo "[$(date +%H:%M:%S)] batch $BATCH_NUM GAVE UP" | tee -a "$PROGRESS"
    fi
done

ELAPSED=$(($(date +%s)-START_TIME))
echo "[$(date +%H:%M:%S)] DONE: $TOTAL_TRANS translated, $TOTAL_FAIL failed, ${ELAPSED}s total" | tee -a "$PROGRESS"
