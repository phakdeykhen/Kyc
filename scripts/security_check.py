"""Verify live least-privilege, tenant isolation and privileged erasure controls.

  python scripts/security_check.py [--json artifacts/phase17-security-check.json]

Catalog checks include effective grants through PUBLIC and inherited roles, column
ACLs, role memberships, object ownership and executable SECURITY DEFINER functions.
API probes use DATABASE_URL and roll back every transaction.
"""

import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import sqlalchemy as sa  # noqa: E402

from kyc.core.config import get_settings  # noqa: E402
from kyc.db.privileges import (API_ROLE, APPEND_ONLY, COLUMN_UPDATES, SECURITY_DEFINER_FUNCTIONS,
                               TABLE_PRIVILEGES)  # noqa: E402


def _result(check: str, ok: bool, detail=None) -> dict:
    return {"check": check, "result": "PASS" if ok else "FAIL", **({"detail": detail} if detail else {})}


def _policy(expression: str | None) -> str:
    return re.sub(r"[\s()]", "", expression or "").replace("::text", "")


def catalog_checks(db: sa.Connection, role: str = API_ROLE, schema: str = "public") -> list[dict]:
    """Read actual PostgreSQL permissions, including PUBLIC and inheritance."""
    params = {"role": role, "schema": schema}
    checks = []

    def check(name, ok, detail=None):
        checks.append(_result(name, ok, detail))

    attributes = db.execute(sa.text("""SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication
                                        FROM pg_roles WHERE rolname = :role"""), params).mappings().first()
    check("api_role_exists", attributes is not None)
    if attributes is None:
        return checks
    risky = sorted(key for key, value in attributes.items() if value)
    check("api_role_attributes", not risky, risky or None)
    memberships = db.execute(sa.text("""SELECT rolname FROM pg_roles WHERE rolname <> :role
                                         AND pg_has_role(:role, oid, 'MEMBER') ORDER BY rolname"""), params).scalars().all()
    # NOINHERIT membership still permits SET ROLE. Keep the API isolated from any
    # other role instead of relying solely on today's inherited object grants.
    check("api_role_has_no_memberships", not memberships, memberships or None)
    owned = db.execute(sa.text("""SELECT pg_describe_object(classid, objid, objsubid) AS object
        FROM pg_shdepend WHERE refclassid = 'pg_authid'::regclass AND deptype = 'o'
          AND refobjid = (SELECT oid FROM pg_roles WHERE rolname = :role)
          AND dbid IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))
        ORDER BY object"""), params).scalars().all()
    check("api_role_owns_nothing", not owned, owned or None)
    writable_schemas = db.execute(sa.text("""SELECT nspname FROM pg_namespace
        WHERE has_schema_privilege(:role, oid, 'CREATE')
          AND nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema' ORDER BY nspname"""), params).scalars().all()
    check("api_role_cannot_create_in_schemas", not writable_schemas, writable_schemas or None)
    tables = db.execute(sa.text("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
         p.polname, p.polcmd, p.polpermissive, p.polroles,
         pg_get_expr(p.polqual, p.polrelid) AS using_expression,
         pg_get_expr(p.polwithcheck, p.polrelid) AS check_expression
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_policy p ON p.polrelid = c.oid
        WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
          AND c.relname <> 'alembic_version' ORDER BY c.relname"""), params).mappings().all()
    expected_scope = "{column} = NULLIF(current_setting('app.organization_id', true), '')::uuid"
    policies = {}
    for row in tables:
        policies.setdefault(row["relname"], []).append(row)
    weak = sorted(name for name, entries in policies.items() if len(entries) != 1 or any(
        not (row["relrowsecurity"] and row["relforcerowsecurity"] and row["polname"] == "tenant_isolation"
             and row["polcmd"] == "*" and row["polpermissive"] and row["polroles"] == [0]
             and _policy(row["using_expression"]) == _policy(expected_scope.format(
                 column="id" if name == "organizations" else "organization_id"))
             and _policy(row["check_expression"]) == _policy(expected_scope.format(
                 column="id" if name == "organizations" else "organization_id")))
        for row in entries))
    check("forced_rls_on_every_table", not weak, weak or None)
    check("rls_table_count", True, {"tables_with_forced_rls": len(policies) - len(weak)})
    privileges = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"]
    if int(db.execute(sa.text("SHOW server_version_num")).scalar_one()) >= 170000:
        privileges.append("MAINTAIN")
    privileges += [privilege + " WITH GRANT OPTION" for privilege in privileges]
    granted = db.execute(sa.text("""SELECT n.nspname, c.relname, privilege_type FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        CROSS JOIN unnest(CAST(:privileges AS text[])) AS privilege_type
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f')
                   THEN has_table_privilege(:role, c.oid, privilege_type) ELSE false END"""),
                         params | {"privileges": privileges}).all()
    actual = {}
    for namespace, table, privilege in granted:
        actual.setdefault(table if namespace == schema else f"{namespace}.{table}", set()).add(privilege)
    extra = {table: sorted(values - TABLE_PRIVILEGES.get(table, frozenset()))
             for table, values in actual.items() if values - TABLE_PRIVILEGES.get(table, frozenset())}
    missing = {table: sorted(values - actual.get(table, set()))
               for table, values in TABLE_PRIVILEGES.items() if values - actual.get(table, set())}
    check("no_privileges_beyond_matrix", not extra, extra or None)
    check("matrix_privileges_present", not missing, missing or None)
    columns = db.execute(sa.text("""SELECT n.nspname, c.relname, a.attname, privilege_type FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        CROSS JOIN unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES', 'SELECT WITH GRANT OPTION',
            'INSERT WITH GRANT OPTION', 'UPDATE WITH GRANT OPTION', 'REFERENCES WITH GRANT OPTION']) AS privilege_type
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
          AND CASE WHEN c.relkind IN ('r', 'p', 'v', 'm', 'f') AND a.attnum > 0 AND NOT a.attisdropped
                   THEN has_column_privilege(:role, c.oid, a.attnum, privilege_type) ELSE false END"""), params).all()
    columns = [(table if namespace == schema else f"{namespace}.{table}", column, privilege)
               for namespace, table, column, privilege in columns]
    column_extra = sorted(f"{table}.{column}:{privilege}" for table, column, privilege in columns
                          if privilege not in TABLE_PRIVILEGES.get(table, frozenset())
                          and not (privilege == "UPDATE" and column in COLUMN_UPDATES.get(table, ())))
    column_missing = sorted(f"{table}.{column}" for table, allowed in COLUMN_UPDATES.items() for column in allowed
                            if (table, column, "UPDATE") not in columns)
    check("no_column_privileges_beyond_matrix", not column_extra, column_extra or None)
    check("matrix_column_updates_present", not column_missing, column_missing or None)
    sequences = db.execute(sa.text("""SELECT n.nspname || '.' || c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema' AND c.relkind = 'S'
          AND CASE WHEN c.relkind = 'S' THEN has_sequence_privilege(:role, c.oid, 'USAGE, SELECT, UPDATE')
                   ELSE false END ORDER BY 1"""), params).scalars().all()
    check("api_role_has_no_sequence_privileges", not sequences, sequences or None)
    functions = db.execute(sa.text("""SELECT n.nspname, p.proname || '(' || oidvectortypes(p.proargtypes) || ')' AS signature,
         p.proconfig, p.prorettype = 'integer'::regtype AS returns_integer,
         has_function_privilege(:role, p.oid, 'EXECUTE') AS executable,
         has_function_privilege(:role, p.oid, 'EXECUTE WITH GRANT OPTION') AS can_grant,
         EXISTS (SELECT 1 FROM aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl
                  WHERE acl.grantee = 0 AND acl.privilege_type = 'EXECUTE') AS public_execute
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
        ORDER BY n.nspname, signature"""), params).mappings().all()
    allowed = set(SECURITY_DEFINER_FUNCTIONS)
    unexpected = [f"{row['nspname']}.{row['signature']}" for row in functions
                  if row["executable"] and not (row["nspname"] == schema and row["signature"] in allowed)]
    check("no_unexpected_executable_security_definer_functions", not unexpected, unexpected or None)
    safe = {row["signature"]: row for row in functions if row["nspname"] == schema and row["signature"] in allowed}
    unsafe = sorted(signature for signature in allowed if signature not in safe or not (
        safe[signature]["executable"] and not safe[signature]["public_execute"] and not safe[signature]["can_grant"]
        and safe[signature]["returns_integer"]
        and safe[signature]["proconfig"] == [f"search_path=pg_catalog, {schema}, pg_temp"]))
    check("scoped_erasure_function_hardened", not unsafe, unsafe or None)
    encryption = db.execute(sa.text("SHOW password_encryption")).scalar_one()
    check("password_encryption_scram", encryption == "scram-sha-256", encryption)
    return checks


def api_checks(db: sa.Connection) -> list[dict]:
    checks = [_result("probe_runs_as_api_role", db.execute(sa.text("SELECT current_user")).scalar_one() == API_ROLE)]
    tenant = db.execute(sa.text("SELECT current_setting('app.organization_id', true)")).scalar()
    checks.append(_result("api_has_no_default_tenant", tenant in (None, "")))
    tls = db.execute(sa.text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()")).scalar()
    checks.append({"check": "api_connection_tls", "result": "INFO",
                   "detail": "encrypted" if tls else "not encrypted (local socket/loopback; production requires sslmode)"})
    db.rollback()
    with db.begin() as transaction:
        visible = {table: db.execute(sa.text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                   for table in ("kyc_sessions", "identity_documents", "biometric_templates", "audit_logs", "api_keys")}
        transaction.rollback()
    checks.append(_result("default_deny_without_tenant", not any(visible.values()), visible))
    for table in APPEND_ONLY:
        for statement in (f'UPDATE "{table}" SET created_at = created_at', f'DELETE FROM "{table}"'):
            with db.begin() as transaction:
                try:
                    db.execute(sa.text(statement))
                    allowed = True
                except sa.exc.ProgrammingError:
                    allowed = False
                transaction.rollback()
            checks.append(_result(f"append_only_{table}_{statement.split()[0].lower()}_refused", not allowed))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args()
    settings = get_settings()
    if settings.migration_database_url is None:
        raise SystemExit("MIGRATION_DATABASE_URL is required to read the catalogs.")
    admin = sa.create_engine(settings.migration_database_url.get_secret_value())
    try:
        with admin.connect() as db:
            checks = catalog_checks(db)
    finally:
        admin.dispose()
    api = sa.create_engine(settings.database_url.get_secret_value())
    try:
        with api.connect() as db:
            checks += api_checks(db)
    finally:
        api.dispose()
    failed = sum(item["result"] == "FAIL" for item in checks)
    report = {"database_role": API_ROLE, "passed": sum(item["result"] == "PASS" for item in checks),
              "failed": failed, "checks": checks}
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
