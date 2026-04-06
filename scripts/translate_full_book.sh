#!/usr/bin/env bash
# Translate all remaining chapters of a single book until fully done.
set -uo pipefail

VENV="/Users/smy/project/book-agent/.venv/bin/python"
SCRIPT="scripts/run_pdf_chapter_smoke.py"
SOURCE_PATH="$1"
BOOK_ROOT="$2"
PACKET_LIMIT="${3:-50}"
MAX_ROUNDS="${4:-200}"

DB_URL="sqlite+pysqlite:///${BOOK_ROOT}/book-agent.db"
EXPORT_ROOT="${BOOK_ROOT}/exports"

echo "=== FULL BOOK TRANSLATION ==="
echo "Source: $(basename "$SOURCE_PATH")"
echo "Packet limit per round: $PACKET_LIMIT"
echo "Max rounds: $MAX_ROUNDS"
echo "Start: $(date -Iseconds)"
echo ""

ROUND=0
while [ "$ROUND" -lt "$MAX_ROUNDS" ]; do
    ROUND=$((ROUND + 1))
    TS=$(date '+%Y%m%d_%H%M%S')
    REPORT="${BOOK_ROOT}/report-full-r${ROUND}-${TS}.json"

    echo "--- Round $ROUND @ $(date '+%H:%M:%S') ---"

    OUTPUT=$("$VENV" "$SCRIPT" \
        --source-path "$SOURCE_PATH" \
        --database-url "$DB_URL" \
        --export-root "$EXPORT_ROOT" \
        --report-path "$REPORT" \
        --chapter-ordinal auto \
        --packet-limit "$PACKET_LIMIT" \
        --auto-lock-unlocked-concepts \
        2>&1) || true

    # Parse last JSON line for status
    LAST_JSON=$(echo "$OUTPUT" | grep '^{' | tail -1)
    NO_WORK=$(echo "$LAST_JSON" | "$VENV" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('no_work_remaining', False))" 2>/dev/null || echo "False")
    FULLY=$(echo "$LAST_JSON" | "$VENV" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('fully_translated', False))" 2>/dev/null || echo "False")
    CH_ORD=$(echo "$LAST_JSON" | "$VENV" -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('chapter_ordinal', '?'))" 2>/dev/null || echo "?")

    echo "  ch=$CH_ORD fully=$FULLY no_work=$NO_WORK"

    # Check overall progress
    PROGRESS=$("$VENV" -c "
import sqlite3
conn = sqlite3.connect('${BOOK_ROOT}/book-agent.db')
rows = conn.execute('''
    SELECT
        SUM(CASE WHEN tp.status = 'translated' THEN 1 ELSE 0 END) as translated,
        SUM(CASE WHEN tp.status = 'built' THEN 1 ELSE 0 END) as built,
        COUNT(tp.id) as total
    FROM translation_packets tp
''').fetchone()
print(f'{rows[0]},{rows[1]},{rows[2]}')
" 2>/dev/null)

    IFS=',' read -r TRANS BUILT TOTAL <<< "$PROGRESS"
    PCT=$("$VENV" -c "print(f'{${TRANS}/${TOTAL}*100:.1f}' if ${TOTAL}>0 else '0')" 2>/dev/null)
    echo "  Progress: ${TRANS}/${TOTAL} (${PCT}%) translated, ${BUILT} built remaining"

    if [ "$NO_WORK" = "True" ]; then
        echo ""
        echo "=== BOOK FULLY TRANSLATED ==="
        break
    fi
done

echo ""
echo "=== TRANSLATION COMPLETE ==="
echo "Total rounds: $ROUND"
echo "End: $(date -Iseconds)"

# Final per-chapter summary
"$VENV" -c "
import sqlite3
conn = sqlite3.connect('${BOOK_ROOT}/book-agent.db')
conn.row_factory = sqlite3.Row
rows = conn.execute('''
    SELECT c.ordinal, c.title_src,
           COUNT(tp.id) as total,
           SUM(CASE WHEN tp.status = 'translated' THEN 1 ELSE 0 END) as trans,
           SUM(CASE WHEN tp.status = 'built' THEN 1 ELSE 0 END) as built
    FROM chapters c
    LEFT JOIN translation_packets tp ON tp.chapter_id = c.id
    GROUP BY c.id ORDER BY c.ordinal
''').fetchall()
print()
print('=== PER-CHAPTER STATUS ===')
for r in rows:
    pct = (r['trans']/r['total']*100) if r['total']>0 else 0
    mark = 'DONE' if pct>=99.9 else f'{pct:.0f}%'
    print(f'ch.{r[\"ordinal\"]:2d} [{mark:>5s}] {r[\"trans\"]:3d}/{r[\"total\"]:3d} | {(r[\"title_src\"] or \"?\")[:45]}')
" 2>/dev/null
