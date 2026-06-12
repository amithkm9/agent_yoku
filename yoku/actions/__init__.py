"""Write-back actions (docs/yoku_agent.md Phase 6) — yoku acts, gated.

Everything side-effecting in yoku flows through `executor`: two-phase
(propose → approve/execute), fully audited, never autonomous.
"""
