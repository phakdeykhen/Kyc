"""The API role's database privileges: one declared matrix (Phase 17).

scripts/bootstrap_local.py revokes everything from the role and grants exactly this;
scripts/security_check.py verifies the live database against it. A table missing here
is unreachable for the API, which is the safe default.
"""

API_ROLE = "kyc_app"

# table → privileges. Comments say why a write is needed; anything append-only has no UPDATE/DELETE.
TABLE_PRIVILEGES: dict[str, frozenset[str]] = {name: frozenset(privileges.split()) for name, privileges in {
    "alembic_version": "SELECT",                      # readiness check only
    "organizations": "SELECT",                        # provisioned by scripts/manage_tenants.py
    "kyc_sessions": "SELECT INSERT UPDATE",
    "audit_logs": "SELECT INSERT",                    # append-only
    # Phase 2–3 captures and extraction. DELETE serves recapture replacement, retention and erasure.
    "identity_documents": "SELECT INSERT UPDATE DELETE",
    "document_images": "SELECT INSERT DELETE",
    "document_checks": "SELECT INSERT DELETE",
    "document_fields": "SELECT INSERT DELETE",
    "mrz_results": "SELECT INSERT DELETE",            # Phase 5
    "barcode_results": "SELECT INSERT DELETE",        # Phase 7
    # Phases 8–11: biometric evidence; challenges are marked used (UPDATE).
    "selfie_captures": "SELECT INSERT DELETE",
    "face_quality_checks": "SELECT INSERT DELETE",
    "biometric_templates": "SELECT INSERT DELETE",
    "face_comparisons": "SELECT INSERT DELETE",
    "liveness_challenges": "SELECT INSERT UPDATE DELETE",
    "liveness_checks": "SELECT INSERT DELETE",
    "nfc_challenges": "SELECT INSERT UPDATE DELETE",
    "nfc_results": "SELECT INSERT DELETE",
    "fraud_signals": "SELECT INSERT DELETE",          # Phase 12: replaced on every analysis
    "risk_assessments": "SELECT INSERT",              # Phase 13: append-only decision history
    "reviewers": "SELECT",                            # Phase 14: provisioned by scripts/create_reviewer.py
    "manual_reviews": "SELECT INSERT",                # append-only
    "consents": "SELECT INSERT",                      # + column UPDATE below (revocation, erasure)
    "api_keys": "SELECT INSERT",                      # Phase 15; + column UPDATE below
    "webhook_endpoints": "SELECT INSERT UPDATE",      # Phase 16
    "webhook_deliveries": "SELECT INSERT UPDATE DELETE",
}.items()}

# Column-level UPDATE where the row is otherwise immutable for the API.
COLUMN_UPDATES: dict[str, tuple[str, ...]] = {
    "api_keys": ("last_used_at", "revoked_at"),
    "consents": ("revoked_at", "user_id"),            # Phase 17: revocation and pseudonymised erasure
}

# Records that must never be rewritten or removed by the API role.
APPEND_ONLY = ("audit_logs", "risk_assessments", "manual_reviews")
SECURITY_DEFINER_FUNCTIONS = ("erase_review_notes(uuid)",)


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def grant_statements(role: str = API_ROLE, schema: str = "public") -> list[str]:
    """Revoke-then-grant, so a re-run removes privileges added by hand."""
    target, namespace = _identifier(role), _identifier(schema)
    statements = [f'REVOKE ALL ON ALL TABLES IN SCHEMA {namespace} FROM {target}, PUBLIC',
                  f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA {namespace} FROM {target}, PUBLIC',
                  f'REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {namespace} FROM {target}, PUBLIC',
                  # Table-level REVOKE leaves separately granted column ACLs
                  # intact. Reset those too, including unknown/new tables.
                  f"""DO $kyc_privileges$
                  DECLARE object record;
                  BEGIN
                    FOR object IN
                      SELECT c.relname, string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) AS columns
                      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                      JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                      WHERE n.nspname = {_literal(schema)} AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                      GROUP BY c.relname
                    LOOP
                      EXECUTE format('REVOKE ALL (%s) ON TABLE %I.%I FROM %I, PUBLIC',
                                     object.columns, {_literal(schema)}, object.relname, {_literal(role)});
                    END LOOP;
                  END
                  $kyc_privileges$""",
                  f'REVOKE CREATE ON SCHEMA {namespace} FROM {target}, PUBLIC',
                  f'GRANT USAGE ON SCHEMA {namespace} TO {target}']
    for table, privileges in sorted(TABLE_PRIVILEGES.items()):
        statements.append(f'GRANT {", ".join(sorted(privileges))} ON {namespace}.{_identifier(table)} TO {target}')
    for table, columns in sorted(COLUMN_UPDATES.items()):
        statements.append(f'GRANT UPDATE ({", ".join(map(_identifier, columns))}) '
                          f'ON {namespace}.{_identifier(table)} TO {target}')
    for signature in SECURITY_DEFINER_FUNCTIONS:
        name, arguments = signature.split("(", 1)
        statements.append(f'GRANT EXECUTE ON FUNCTION {namespace}.{_identifier(name)}({arguments} TO {target}')
    return statements
