import sys
sys.path.insert(0,'backend')
from reconciliation_engine.orchestrator import ReconciliationOrchestrator
orch = ReconciliationOrchestrator()

settled = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE status='captured' AND settlement_id IS NOT NULL AND settlement_id NOT LIKE 'setl_missing%' AND order_id LIKE 'order_d2c_%' LIMIT 2")
print('SETTLED D2C:', [(r['order_id'], r['payment_id']) for r in settled])

b2b = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE status='captured' AND settlement_id IS NOT NULL AND order_id LIKE 'order_b2b_%' LIMIT 2")
print('SETTLED B2B:', [(r['order_id'], r['payment_id']) for r in b2b])

failed = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE status='failed' LIMIT 2")
print('FAILED:', [(r['order_id'], r['payment_id']) for r in failed])

refunded = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE status='refunded' LIMIT 2")
print('REFUNDED:', [(r['order_id'], r['payment_id']) for r in refunded])

broken = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE settlement_id LIKE 'setl_missing%' LIMIT 2")
print('BROKEN FK:', [(r['order_id'], r['payment_id']) for r in broken])

pending = orch.db.query("SELECT order_id, payment_id FROM gateway_transactions WHERE status='captured' AND settlement_id IS NULL LIMIT 2")
print('NO SETL:', [(r['order_id'], r['payment_id']) for r in pending])
