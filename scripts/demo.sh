#!/usr/bin/env bash
# Safe Counter live demo. Every line printed comes from real API calls.
# Needs: docker compose up -d (db, mock_ai, api). Runs the CLI with the host's python3.
set -u
cd "$(dirname "$0")/.."
sc() { python3 -u -m safecounter.cli "$@"; }
step() { printf '\n\033[1;33m== %s ==\033[0m\n' "$*"; [ -n "${PAUSE:-}" ] && read -r -p "(enter) " _; }

step "0. Fresh data"
docker compose run --rm -T migrate python -m safecounter.admin reset 2>/dev/null | tail -1

step "1-4. A note goes in; the AI proposes; the server validates, routes and labels sensitivity"
sc note N01

step "5. Counter staff cannot approve a refund (sensitive: manager or area admin)"
sc approve --as amira --pick rental.deposit_refund_pence --reason "I wrote the note"

step "6. The record has NOT changed yet"
sc show r_m5004 --as amira | grep -E '"(returned_at|deposit_refund_pence)"'

step "6. The manager approves; the note's author approves the normal field (R3)"
sc approve --as chloe --pick rental.deposit_refund_pence --reason "checked deposit slip"
sc approve --as amira --pick rental.returned_at --reason "I took the return"

step "7. Now the record has changed"
sc show r_m5004 --as amira | grep -E '"(returned_at|deposit_refund_pence)"'

step "8. Audit rows and hash chain"
sc audit --as chloe
sc verify --as chloe

step "N09: the AI calls a write-off 'normal'; the server says sensitive, area admin only"
sc note N09
sc approve --as chloe --pick item.status --reason "agree"
sc approve --as femi --shop shop_millbrook --pick item.status --reason "write-off agreed"

step "N08: instructions hidden in a pasted email; pre-approval claim has no effect"
sc note N08

step "9. Hostile AI output: scope escape, self-approval, rule changes, downgrades"
sc note N02 --mode hostile_output

step "10. The AI hangs: bounded error, nothing saved, other endpoints still answer"
sc note N03 --mode hang &
sleep 1; curl -s -m 2 localhost:8080/health; echo "  ← /health answered while the AI hangs"
wait
sc proposals --as chloe | grep -c "i_m107" | sed 's/^/  proposals for N03 records stored: /'
sc note N03 --mode garbage
sc note N03 --mode drop

step "Two approvals at once: exactly one wins"
sc edit r_m5005 rental.damage_charge_pence 2000 --as amira --reason "bent brace"
ID=$(sc pending-id rental.damage_charge_pence --as chloe)
sc approve "$ID" --as chloe --reason "chloe ok" &
sc approve "$ID" --as femi --shop shop_millbrook --reason "femi ok" &
wait

step "Rejected stays rejected"
sc note N06
ID=$(sc pending-id customer.phone --as ewa)
sc reject "$ID" --as ewa --reason "could not confirm with customer"
sc approve "$ID" --as femi --shop shop_eastfield --reason "try again"

step "Tamper with an audit row as the database owner, then verify"
docker compose run --rm -T migrate python -m safecounter.admin tamper shop_millbrook 2 2>/dev/null | tail -1
sc verify --as chloe
