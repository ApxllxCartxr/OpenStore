# OpenStore Runbook

Four incident response procedures for production operations.

---

## Incident 1: Duplicate Charge Suspected

**Detection:**
- Customer reports being charged twice for same order
- `reconciliation_drift_total` metric > 0
- Two `Order` rows with same `checkout_id` or `idempotency_key`
- Razorpay dashboard shows two payment links for same `reference_id`

**Diagnosis:**
```bash
# Check for duplicate orders
sqlite3 openstore.db "SELECT checkout_id, COUNT(*) FROM orders GROUP BY checkout_id HAVING COUNT(*) > 1;"

# Check idempotency records
sqlite3 openstore.db "SELECT client_id, idempotency_key, state, COUNT(*) FROM idempotency_records GROUP BY client_id, idempotency_key HAVING COUNT(*) > 1;"

# Check PSP intents
sqlite3 openstore.db "SELECT reference_id, state, COUNT(*) FROM psp_intents GROUP BY reference_id HAVING COUNT(*) > 1;"
```

**Resolution:**
1. Identify the duplicate order (later `created_at`)
2. Refund the duplicate via Razorpay dashboard or API
3. Update local state: `Order.status = 'refunded'`, add `REFUND` ledger entry
4. If idempotency bug: deploy fix, add regression test

**Rollback:**
- If refund fails: contact Razorpay support with `payment_id`
- Document in incident log with timeline

---

## Incident 2: Webhooks Stopped Arriving

**Detection:**
- `reconciliation_drift_total` increases over time
- Orders stuck in `PENDING`/`CREATED` state > 10 minutes
- Razorpay dashboard shows `payment.captured` but local state is `PENDING`
- No webhook logs in application logs

**Diagnosis:**
```bash
# Check pending orders
sqlite3 openstore.db "SELECT * FROM psp_intents WHERE state IN ('PENDING','CREATED') AND created_at < $(date -d '10 minutes ago' +%s);"

# Check webhook endpoint accessibility
curl -X POST https://your-domain.com/webhooks/razorpay -H "Content-Type: application/json" -d '{"event":"test"}'

# Check Razorpay webhook delivery logs in Razorpay dashboard
```

**Resolution:**
1. Run reconciliation sweeper manually:
   ```bash
   python -c "
   import asyncio
   from openstore.runtime import MerchantRuntime
   from openstore.webhooks import run_reconciliation
   rt = MerchantRuntime(...)
   asyncio.run(run_reconciliation(rt.ledger, rt.gateway))
   "
   ```
2. If webhook endpoint down: check DNS, TLS, firewall, application logs
3. If Razorpay webhook disabled: re-enable in Razorpay dashboard
4. Deploy fix for any webhook handler bug

**Rollback:**
- If reconciliation causes issues: revert state changes from backup
- Manual state correction for affected orders

---

## Incident 3: Policy Must Be Revoked Immediately

**Detection:**
- Human reports lost/stolen authenticator
- Compromised agent detected (spike in `policy.tag_violation` from one `client_id`)
- Security team requests immediate revocation

**Diagnosis:**
```bash
# Find active policy for user
sqlite3 openstore.db "SELECT * FROM intent_policies WHERE user_id = '...' AND active = 1;"

# Check recent rejections from this client
sqlite3 openstore.db "SELECT * FROM rejection_records WHERE client_id = '...' ORDER BY created_at DESC LIMIT 20;"
```

**Resolution:**
1. Revoke policy immediately:
   ```bash
   sqlite3 openstore.db "UPDATE intent_policies SET active = 0 WHERE user_id = '...';"
   ```
2. Revoke all active tokens for this client:
   ```bash
   # If using OAuth, revoke tokens
   python -c "
   from openstore.oauth import OAuthServer
   oauth = OAuthServer(...)
   # Revoke all tokens for client_id
   "
   ```
3. Freeze agent sessions:
   ```bash
   sqlite3 openstore.db "UPDATE agent_sessions SET frozen = 1 WHERE client_id = '...';"
   ```
4. Alert security team with list of affected orders

**Rollback:**
- If false alarm: re-activate policy, unfreeze sessions
- Human must re-register authenticator

---

## Incident 4: PSP (Razorpay) Is Down

**Detection:**
- `checkout_confirm` returns `psp.timeout` or `psp.unavailable` errors
- Razorpay status page shows incident
- Payment links not being created
- High `psp.*` error rate in metrics

**Diagnosis:**
```bash
# Check recent PSP errors
sqlite3 openstore.db "SELECT * FROM audit_log WHERE tool = 'checkout_confirm' AND result_summary LIKE '%psp%' ORDER BY created_at DESC LIMIT 10;"

# Test Razorpay API directly
curl -u "$RAZORPAY_KEY_ID:$RAZORPAY_KEY_SECRET" https://api.razorpay.com/v1/payment_links
```

**Resolution:**
1. Enable "PSP degraded mode" - queue orders for retry:
   ```bash
   # Set environment variable or config flag
   export OPENSTORE_PSP_DEGRADED=1
   ```
2. In degraded mode:
   - `checkout_confirm` returns `psp.unavailable` with `retriable: true`
   - Orders queued in `PspIntent` with state `PENDING`
   - Background worker retries every 5 minutes
3. Monitor Razorpay status page
4. When PSP recovers:
   - Disable degraded mode
   - Run reconciliation sweeper to catch any missed payments
   - Process queued `PspIntent` records

**Rollback:**
- If degraded mode causes issues: disable immediately
- Manual processing of queued orders

---

## Common Commands

```bash
# View recent orders
sqlite3 openstore.db "SELECT order_id, status, created_at FROM orders ORDER BY created_at DESC LIMIT 20;"

# View ledger entries for a client
sqlite3 openstore.db "SELECT * FROM ledger_entries WHERE client_id = '...' ORDER BY created_at DESC;"

# View reconciliation drift
sqlite3 openstore.db "SELECT * FROM reconciliation_log ORDER BY created_at DESC LIMIT 10;"

# Manual reconciliation run
python -m openstore.reconcile

# Replay webhook
python -m openstore.replay <event_id>

# Check rate limit status
sqlite3 openstore.db "SELECT * FROM rate_limit_buckets WHERE client_id = '...';"
```

---

## Contacts

- **Razorpay Support**: support@razorpay.com / +91-80-6756-5000
- **Security Team**: security@openstore.local
- **On-call Engineer**: Check PagerDuty / OpsGenie

---

## Post-Incident

After any incident:
1. Create incident report with timeline
2. Add regression test if bug was root cause
3. Update runbook if new failure mode discovered
4. Review metrics for 24h post-incident