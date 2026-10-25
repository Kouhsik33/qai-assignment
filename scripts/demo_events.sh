#!/usr/bin/env bash
# Level 3 demo: reliable event delivery to the pack's flaky receiver (D25).
# Applied changes create outbox events in the same transaction; a worker delivers them
# with retries. The receiver fails 30% of requests and loses 30% of acknowledgements.
set -u
cd "$(dirname "$0")/.."
sc() { python3 -u -m safecounter.cli "$@"; }
step() { printf '\n\033[1;35m== %s ==\033[0m\n' "$*"; }

step "0. Fresh data, then five applied changes (each writes one outbox event with its audit row)"
docker compose run --rm -T migrate python -m safecounter.admin reset 2>/dev/null | tail -1
for v in needs_cleaning damaged good parts_missing good; do
  sc edit r_m5004 rental.return_condition "$v" --as amira --reason "demo $v" | tail -1
done

step "1-3. Start the flaky receiver, deliver until every event is acknowledged, show what it received"
docker compose run --rm -T tools sh -c '
  python3 mock_ai/flaky_receiver.py --port 8098 --fail-rate 0.3 --lost-ack-rate 0.3 --slow-rate 0 --seed 7 2>/dev/null &
  sleep 1
  python -m safecounter.outbox --url http://127.0.0.1:8098/events --until-empty --backoff-base 0.2
  python3 -c "
import json, urllib.request
r = json.loads(urllib.request.urlopen(\"http://127.0.0.1:8098/events\").read())
print(f\"receiver: {r[\"received\"]} deliveries, {r[\"unique_keys\"]} unique events, duplicates by key: {r[\"duplicate_keys\"]}\")
"' 2>/dev/null
echo "  outbox (database view):"
docker compose exec -T db psql -U sc_owner -d safecounter -c \
  "SELECT event_id, attempts, last_error, delivered_at IS NOT NULL AS delivered FROM outbox ORDER BY id"
