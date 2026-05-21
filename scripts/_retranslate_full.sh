#!/usr/bin/env bash
# Full-book re-translation driver (postgres). Translates ch1..ch9
# sequentially (so chapter translation memory builds in order), with
# intra-chapter parallelism. One-off helper for the publication-grade
# remediation re-translate.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export CH1_PARALLEL="${CH1_PARALLEL:-6}"

# ch-key  chapter_id
CHAPTERS=(
  "ch1 b13f7481-d2af-5629-bb8f-52d9c2b9abc9"
  "ch2 732562f6-1d41-5dd6-9520-7fe7068fa760"
  "ch3 e55d9240-670f-54a4-9448-f3e25ce69ee0"
  "ch4 ef60bd3b-f1e6-5a40-9905-5cc783a93c49"
  "ch5 3aba5820-ccb3-5614-b8ee-5b0b2ada3b01"
  "ch6 d38b47bd-236e-5d51-b27d-d1e9fc1d91d3"
  "ch7 cffe6908-541c-520a-898d-8ab596492401"
  "ch8 f0b4c3ba-3bca-5771-b865-6c75171f9cc0"
  "ch9 50593486-936f-5331-b6bb-d6097101e48d"
)

for entry in "${CHAPTERS[@]}"; do
  ck="${entry%% *}"; cid="${entry##* }"
  ids_file="/tmp/${ck}-packet-ids.json"
  "$PY" -c "
import json
from sqlalchemy import create_engine, text
e=create_engine('postgresql+psycopg://postgres:postgres@localhost:55432/book_agent')
with e.connect() as c:
    rows=c.execute(text(\"select id from translation_packets where chapter_id=:c and status='built' order by created_at, id\"), {'c':'$cid'}).fetchall()
json.dump([str(r[0]) for r in rows], open('$ids_file','w'))
print('$ck', len(rows), 'built packets')
"
  echo \"=== translating $ck ===\"
  PACKET_IDS_FILE="$ids_file" EXPORT_DIR="${ck}-export" "$PY" scripts/translate_chapter1_smoke.py 2>&1 \
    | grep -E 'translation_done|FAIL' | tail -6
done
echo "=== ALL CHAPTERS DONE ==="
