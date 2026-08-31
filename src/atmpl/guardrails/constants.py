"""Numbered safety invariants used by code, tests, UI, and documentation."""

G1_RISK_TIERS = "G1"
G2_HIGH_REQUIRES_SIGNED_HUMAN = "G2"
G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER = "G3"
G4_ATTRIBUTED_APPROVAL = "G4"
G5_HASH_CHAINED_APPEND_ONLY_AUDIT = "G5"
G6_KILL_SWITCH = "G6"
G7_EXPLICIT_LLM_ADAPTER = "G7"

INVARIANTS = {
    G1_RISK_TIERS: "LOW may auto-complete; MEDIUM needs human approval; HIGH is human-decided.",
    G2_HIGH_REQUIRES_SIGNED_HUMAN: "HIGH transitions accept only a signed human decision.",
    G3_DETERMINISTIC_POLICY_BEFORE_AND_AFTER: (
        "Pure rules inspect input before AI and output after AI."
    ),
    G4_ATTRIBUTED_APPROVAL: "Every approval records actor, role, time, decision, and reason.",
    G5_HASH_CHAINED_APPEND_ONLY_AUDIT: "Audit JSONL is append-only and SHA-256 hash chained.",
    G6_KILL_SWITCH: "An audited org-level switch halts AI while human gates remain available.",
    G7_EXPLICIT_LLM_ADAPTER: "Offline deterministic mock is default; real adapters are explicit.",
}
