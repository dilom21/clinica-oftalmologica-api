from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import os
import sys

import pytest

from scripts.saas.provision_tenant import (
    ACTIVE,
    COMPLETED,
    CLEAN_CATALOG_TABLES,
    CLEAN_EMPTY_TABLES,
    CLEAN_EXPECTED_CODE,
    CLEAN_EXPECTED_DATABASE,
    PENDING_BATCH_CODES,
    PENDING_BATCH_TARGETS,
    CleanVerification,
    ERROR,
    EXCLUDED_SCHEMAS,
    EXPECTED_CODE,
    EXPECTED_DATABASE,
    IN_PROGRESS,
    PENDING,
    PROVISIONING,
    Preflight,
    ProvisioningError,
    PostgresConnection,
    RecoveryAudit,
    RecoverySession,
    SchemaSnapshot,
    SOURCE_TABLES,
    SqlAlchemyControlPlaneReader,
    SubprocessPostgresBackend,
    TenantMapping,
    ToolPaths,
    Verification,
    _DEPENDENCY_AUDIT_SQL,
    _ensure_repository_root_on_sys_path,
    dry_run,
    execute_provision,
    execute_provision_clean,
    execute_provision_pending_batch,
    execute_recovery,
    main,
    reconcile_existing,
    _normalize_check_definition,
    _sql_literal,
    _validate_batch_source_preflight,
    validate_database_identifier,
)


@dataclass
class FakeReader:
    mapping: TenantMapping
    requested_codes: list[str] = field(default_factory=list)

    def read_mapping(self, code: str) -> TenantMapping:
        self.requested_codes.append(code)
        return self.mapping


class FakeTools:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def available(self, name: str) -> bool:
        return name not in self.missing


@dataclass
class FakeWriter:
    events: list[str] = field(default_factory=list)
    tenant_state: str = PENDING
    provisioning_state: str = PENDING
    intentos: int = 7

    def start(self, mapping):
        self.events.append("start")
        self.tenant_state = PROVISIONING
        self.provisioning_state = IN_PROGRESS

    def fail(self, mapping, message):
        self.events.append("fail:" + message)
        self.tenant_state = ERROR
        self.provisioning_state = ERROR

    def complete(self, mapping, version="v1"):
        self.events.append("complete")
        self.tenant_state = ACTIVE
        self.provisioning_state = COMPLETED

    def reset_after_recovery(self, mapping):
        self.events.append("reset")
        self.tenant_state = PENDING
        self.provisioning_state = PENDING


class FakeBackend:
    def __init__(self, *, exists=False, preflight=None, verification=None, health=True, failure=None,
                 failure_stage=None):
        self.exists = exists
        self.preflight_result = preflight or Preflight(source_counts={"usuario": 1})
        self.verification = verification or Verification()
        self.health = health
        self.failure = failure
        self.failure_stage = failure_stage
        self.events = []
        self.recovery_exists = True
        self.recovery_sessions = []
        self.recovery_safe_to_terminate = False
        self.recovery_residual_only = False
        self.recovery_drop_failure = False
        self.recovery_absence_failure = False

    def preflight(self, mapping):
        self.events.append("preflight")
        return self.preflight_result

    def database_exists(self, database_name):
        self.events.append("exists")
        if self.recovery_absence_failure:
            return True
        return self.exists

    def create_database(self, database_name):
        self.events.append("create")
        if self.failure:
            raise self.failure

    def dump_public(self, source, destination, counts):
        self.events.append(("dump", source, destination, counts))
        if self.failure_stage == "dump":
            raise self.failure or RuntimeError("dump failure")

    def restore_public(self, target, dump_path):
        self.events.append(("restore", target, dump_path))
        if self.failure_stage == "restore":
            raise self.failure or RuntimeError("restore failure")

    def verify(self, counts, target, source_structure=None):
        self.events.append(("verify", counts, target))
        if self.failure_stage == "verify":
            raise self.failure or RuntimeError("verify failure")
        return self.verification

    def verify_existing(self, counts, target, source_structure):
        self.events.append(("verify_existing", counts, target))
        return self.verification

    def health_check(self, mapping):
        self.events.append("health")
        if self.failure_stage == "health":
            raise self.failure or RuntimeError("health failure")
        return self.health

    def cleanup_dump(self, dump_path):
        self.events.append(("cleanup", dump_path))
        if self.failure_stage == "cleanup":
            raise self.failure or RuntimeError("cleanup failure")
        dump_path.unlink(missing_ok=True)

    def recovery_audit(self, database_name):
        self.events.append(("audit", database_name))
        return RecoveryAudit(
            self.recovery_exists, tuple(self.recovery_sessions),
            "Control Plane mapping", database_name,
            safe_to_terminate=self.recovery_safe_to_terminate,
            residual_only=self.recovery_residual_only,
        )

    def dispose_tenant_engine(self, tenant_database_id):
        self.events.append(("dispose", tenant_database_id))

    def terminate_residual_sessions(self, database_name, sessions):
        self.events.append(("sessions", database_name))
        assert all(session.residual_only for session in sessions)
        return len(sessions)

    def drop_database(self, database_name):
        self.events.append(("drop", database_name))
        if self.recovery_drop_failure:
            raise RuntimeError("drop failure")
        self.recovery_exists = False


def valid_mapping(**changes):
    values = dict(
        code=EXPECTED_CODE, database_name=EXPECTED_DATABASE,
        tenant_state=PENDING, provisioning_state=PENDING,
        empresa_id=1, tenant_database_id=2, provisioning_id=3,
    )
    values.update(changes)
    return TenantMapping(**values)


def run_execute(tmp_path, **kwargs):
    reader = FakeReader(valid_mapping(**kwargs.pop("mapping", {})))
    writer = FakeWriter()
    backend = FakeBackend(**kwargs.pop("backend", {}))
    output = execute_provision(
        reader, writer, backend, FakeTools(), EXPECTED_DATABASE,
        dump_directory=tmp_path,
    )
    return output, reader, writer, backend


class FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class FakeSession:
    def __init__(self, row):
        self.row = row
        self.sql = None
        self.parameters = None
        self.closed = False

    def execute(self, sql, parameters):
        self.sql = str(sql)
        self.parameters = parameters
        return FakeResult(self.row)

    def close(self):
        self.closed = True


def test_sql_reader_uses_explicit_state_keys_and_dry_run_builds_plan():
    session = FakeSession({
        "empresa_id": 1, "codigo": EXPECTED_CODE,
        "tenant_database_id": 2, "database_name": EXPECTED_DATABASE,
        "tenant_state": PENDING, "provisioning_id": 3,
        "provisioning_state": PENDING,
    })
    output = dry_run(SqlAlchemyControlPlaneReader(lambda: session), FakeTools())
    assert "Planned database: tenant_vision_clara." in output
    assert "td.estado AS tenant_state" in session.sql
    assert "pt.estado AS provisioning_state" in session.sql
    assert session.closed


def test_dry_run_does_not_mutate():
    reader = FakeReader(valid_mapping())
    output = dry_run(reader, FakeTools())
    assert reader.requested_codes == [EXPECTED_CODE]
    assert "DRY-RUN" in output and "state change" in output
    assert "CREATE DATABASE" not in output


@pytest.mark.parametrize("value", [
    "admin@oftalmonorte.demo",
    "nombre.apellido+test@dominio.com",
    "o'hara@example.com",
    "texto con espacios",
    "texto-con-guion",
])
def test_sql_literal_quotes_arbitrary_text_without_identifier_validation(value):
    literal = _sql_literal(value)
    assert literal == "'" + value.replace("'", "''") + "'"
    assert literal.startswith("'") and literal.endswith("'")


def test_sql_literal_never_allows_sql_injection_to_escape_literal():
    value = "x'); DROP TABLE usuario; --"
    assert _sql_literal(value) == "'x''); DROP TABLE usuario; --'"


@pytest.mark.parametrize("value", ["Bad-Database", "tenant;drop", "tenant database", "' OR '1'='1"])
def test_dynamic_database_identifiers_remain_strict(value):
    with pytest.raises(ProvisioningError):
        validate_database_identifier(value)


def test_execute_requires_explicit_confirmation():
    with pytest.raises(ProvisioningError):
        execute_provision(FakeReader(valid_mapping()), FakeWriter(), FakeBackend(), FakeTools(), "")


def test_wrong_confirmation_rejected_before_backend():
    backend = FakeBackend()
    with pytest.raises(ProvisioningError, match="confirmation"):
        execute_provision(FakeReader(valid_mapping()), FakeWriter(), backend, FakeTools(), "wrong")
    assert backend.events == []


def test_existing_database_rejected_before_state():
    writer = FakeWriter()
    backend = FakeBackend(exists=True)
    with pytest.raises(ProvisioningError, match="already exists"):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE)
    assert writer.events == []


@pytest.mark.parametrize("field", ["tenant_state", "provisioning_state"])
def test_non_pending_tenant_rejected(field):
    with pytest.raises(ProvisioningError, match="PENDIENTE"):
        execute_provision(FakeReader(valid_mapping(**{field: ACTIVE})), FakeWriter(), FakeBackend(), FakeTools(), EXPECTED_DATABASE)


def test_prechecks_happen_before_state_transition():
    writer = FakeWriter()
    backend = FakeBackend(preflight=Preflight(role_createdb=False))
    with pytest.raises(ProvisioningError, match="prechecks"):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE)
    assert writer.events == []


def test_dependency_audit_targets_external_prohibited_references_only():
    assert "refnamespace = 'public'" not in _DEPENDENCY_AUDIT_SQL
    assert "pg_describe_object" in _DEPENDENCY_AUDIT_SQL
    assert "saas_control" in _DEPENDENCY_AUDIT_SQL
    assert "d.deptype IN ('n','a')" in _DEPENDENCY_AUDIT_SQL


def test_success_marks_provisioning_and_cleans_dump(tmp_path):
    output, _, writer, backend = run_execute(tmp_path)
    assert output.endswith("ACTIVA/COMPLETADO.")
    assert writer.events == ["start", "complete"]
    assert writer.tenant_state == ACTIVE
    assert writer.provisioning_state == COMPLETED
    assert backend.events[0:3] == ["preflight", "exists", "create"]
    assert backend.events[3][0] == "dump"
    assert any(event[0] == "cleanup" for event in backend.events if isinstance(event, tuple))
    assert not list(tmp_path.iterdir())


def test_failure_marks_error_without_drop(tmp_path):
    writer = FakeWriter()
    backend = FakeBackend(failure=RuntimeError("safe failure"))
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE, dump_directory=tmp_path)
    assert writer.events[0] == "start"
    assert writer.events[1].startswith("fail:")
    assert writer.tenant_state == ERROR
    assert writer.provisioning_state == ERROR
    assert not any(event == "drop" for event in backend.events)


@pytest.mark.parametrize("failure_stage", ["dump", "restore", "verify", "health"])
def test_dump_is_cleaned_when_any_post_creation_step_fails(tmp_path, failure_stage):
    writer = FakeWriter()
    backend = FakeBackend(failure_stage=failure_stage)
    with pytest.raises(ProvisioningError):
        execute_provision(
            FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE,
            dump_directory=tmp_path,
        )
    assert any(event[0] == "cleanup" for event in backend.events if isinstance(event, tuple))
    assert not list(tmp_path.iterdir())
    assert "complete" not in writer.events


def test_count_mismatch_blocks_activation(tmp_path):
    writer = FakeWriter()
    backend = FakeBackend(verification=Verification(counts_ok=False))
    with pytest.raises(ProvisioningError):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE, dump_directory=tmp_path)
    assert writer.events[-1].startswith("fail:")
    assert "complete" not in writer.events


def test_prohibited_schema_blocks_activation(tmp_path):
    writer = FakeWriter()
    backend = FakeBackend(verification=Verification(schemas_ok=False))
    with pytest.raises(ProvisioningError):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE, dump_directory=tmp_path)
    assert "complete" not in writer.events


@pytest.mark.parametrize("field", ["structure_ok", "columns_ok", "constraints_ok", "indexes_ok"])
def test_structural_mismatch_blocks_activation(tmp_path, field):
    writer = FakeWriter()
    backend = FakeBackend(verification=Verification(**{field: False}))
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision(
            FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE,
            dump_directory=tmp_path,
        )
    assert "complete" not in writer.events


def test_health_check_failure_blocks_activation(tmp_path):
    writer = FakeWriter()
    backend = FakeBackend(health=False)
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision(FakeReader(valid_mapping()), writer, backend, FakeTools(), EXPECTED_DATABASE, dump_directory=tmp_path)
    assert "complete" not in writer.events


def test_public_dump_restore_arguments_and_secret_boundary(tmp_path):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, env):
            self.calls.append((argv, env.copy()))
            return "1"

    runner = Runner()
    from scripts.saas.provision_tenant import ToolPaths
    tools = ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe"))
    backend = SubprocessPostgresBackend(tools, PostgresConnection("host", 5432, "user", "SECRET"), runner)
    dump = tmp_path / "public.dump"
    backend.dump_public("postgres", dump, None)
    backend.restore_public(EXPECTED_DATABASE, dump)
    dump_argv, dump_env = runner.calls[0]
    restore_argv, restore_env = runner.calls[1]
    assert "--schema=public" in dump_argv
    assert "-Fc" in dump_argv
    assert "--no-owner" in dump_argv and "--no-privileges" in dump_argv
    assert "postgres" in dump_argv
    assert EXPECTED_DATABASE in restore_argv
    assert restore_argv.count(EXPECTED_DATABASE) == 1
    assert "SECRET" not in dump_argv and dump_env["PGPASSWORD"] == "SECRET"
    assert "SECRET" not in restore_argv and restore_env["PGPASSWORD"] == "SECRET"
    assert "TEMPLATE postgres" not in " ".join(dump_argv + restore_argv).upper()


def test_recovery_audit_reports_activity_fields_and_classifies_only_idle_supavisor():
    class Runner:
        def __init__(self):
            self.sql = []

        def run(self, argv, env):
            if "-tAc" in argv:
                query = argv[-1]
                self.sql.append(query)
                if "pg_database" in query:
                    return "1"
                return "1|11|pooler|supavisor|client backend|idle|<NULL>|2026-01-01 00:00:00+00"
            return ""

    runner = Runner()
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), runner,
    )
    audit = backend.recovery_audit(EXPECTED_DATABASE)
    assert audit.session_count == 1
    assert audit.residual_only is True
    session = audit.sessions[0]
    assert session.usename == "pooler"
    assert session.application_name == "supavisor"
    assert session.state == "idle"
    assert session.xact_start is None
    assert "client_addr" not in runner.sql[-1]


def test_drop_uses_postgres_force_and_target_only():
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, env):
            self.calls.append(argv)
            if "-tAc" in argv:
                return "t" if "current_database()" in argv[-1] else "f"
            return ""

    runner = Runner()
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), runner,
    )
    backend.drop_database(EXPECTED_DATABASE)
    command = " ".join(runner.calls[-1])
    assert "-d postgres" in command
    assert 'DROP DATABASE "tenant_vision_clara" WITH (FORCE);' in command


@pytest.mark.parametrize("target", ["postgres", "template0", "template1", "other_database"])
def test_drop_rejects_reserved_or_other_targets(target):
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"),
    )
    with pytest.raises(ProvisioningError):
        backend.drop_database(target)


def test_drop_blocks_when_current_database_is_target():
    class Runner:
        def run(self, argv, env):
            if "-tAc" in argv:
                return "f" if "current_database()" in argv[-1] else "f"
            pytest.fail("DROP command must be blocked")

    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), Runner(),
    )
    with pytest.raises(ProvisioningError, match="other than the target"):
        backend.drop_database(EXPECTED_DATABASE)


def test_pg_bin_resolution_override_does_not_touch_path(tmp_path, monkeypatch):
    for name in ("pg_dump", "pg_restore", "psql"):
        (tmp_path / f"{name}.exe").write_text("fake", encoding="utf-8")
    original = os.environ.get("PATH")
    from scripts.saas.provision_tenant import PathToolRunner
    runner = PathToolRunner(tmp_path)
    assert all(runner.available(name) for name in ("pg_dump", "pg_restore", "psql"))
    assert os.environ.get("PATH") == original


def test_cli_execute_requires_confirmation():
    with pytest.raises(SystemExit) as error:
        main(["--execute", "--empresa", EXPECTED_CODE])
    assert error.value.code == 2


def test_cli_requires_exact_empresa_for_all_modes(monkeypatch):
    monkeypatch.setattr(
        "scripts.saas.provision_tenant.build_reader",
        lambda: FakeReader(valid_mapping()),
    )
    assert main(["--dry-run", "--empresa", EXPECTED_CODE]) == 0
    for mode in ("--dry-run", "--execute", "--recover-error"):
        with pytest.raises(SystemExit) as error:
            main([mode, "--empresa", "OTHER-COMPANY"])
        assert error.value.code == 2


def test_cli_requires_empresa():
    with pytest.raises(SystemExit) as error:
        main(["--dry-run"])
    assert error.value.code == 2


def test_cli_requires_a_mode():
    with pytest.raises(SystemExit) as error:
        main(["--empresa", EXPECTED_CODE])
    assert error.value.code == 2


def test_direct_entrypoint_adds_repository_root(monkeypatch):
    root = str(Path(__file__).parents[1].resolve())
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != root])
    _ensure_repository_root_on_sys_path()
    assert sys.path[0] == root


def test_static_artifacts_remain_scoped_and_no_drop_or_template_postgres():
    root = Path(__file__).parents[1]
    for path in [root / "scripts/saas/provision_tenant.py"]:
        text = path.read_text(encoding="utf-8").upper()
        assert "DROP DATABASE" in text
        assert "TEMPLATE POSTGRES" not in text
    assert "saas_control" in EXCLUDED_SCHEMAS
    assert IN_PROGRESS == "EN_PROCESO"
    assert PROVISIONING == "PROVISIONANDO"
    assert COMPLETED == "COMPLETADO"
    assert ERROR == "ERROR"


def run_recovery(tmp_path, **changes):
    reader = FakeReader(valid_mapping(
        tenant_state=ERROR, provisioning_state=ERROR,
        **changes.pop("mapping", {}),
    ))
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    backend = FakeBackend()
    for name, value in changes.pop("backend", {}).items():
        setattr(backend, {"drop_failure": "recovery_drop_failure"}.get(name, name), value)
    output = execute_recovery(reader, writer, backend, EXPECTED_DATABASE)
    return output, reader, writer, backend


def test_recovery_requires_error_mapping_and_exact_confirmation():
    with pytest.raises(ProvisioningError, match="ERROR"):
        execute_recovery(FakeReader(valid_mapping()), FakeWriter(), FakeBackend(), EXPECTED_DATABASE)
    with pytest.raises(ProvisioningError, match="confirmation"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), FakeWriter(), FakeBackend(), "wrong")


@pytest.mark.parametrize("field", ["tenant_state", "provisioning_state"])
def test_recovery_rejects_active_and_pending(field):
    value = ACTIVE if field == "tenant_state" else PENDING
    changes = {"tenant_state": ERROR, "provisioning_state": ERROR, field: value}
    mapping = valid_mapping(**changes)
    backend = FakeBackend()
    with pytest.raises(ProvisioningError, match="ERROR"):
        execute_recovery(FakeReader(mapping), FakeWriter(), backend, EXPECTED_DATABASE)
    assert not any(isinstance(event, tuple) and event[0] == "drop" for event in backend.events)


def test_recovery_never_uses_postgres_or_arbitrary_database():
    backend = FakeBackend()
    with pytest.raises(ProvisioningError):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR, database_name="postgres")), FakeWriter(), backend, EXPECTED_DATABASE)
    assert backend.events == []
    with pytest.raises(ProvisioningError):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR, database_name="arbitrary_db")), FakeWriter(), backend, EXPECTED_DATABASE)
    assert backend.events == []


def test_recovery_drops_before_reset_preserves_attempts():
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    backend = FakeBackend()
    execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), writer, backend, EXPECTED_DATABASE)
    assert ("drop", EXPECTED_DATABASE) in backend.events
    assert backend.events.index(("drop", EXPECTED_DATABASE)) < backend.events.index("exists")
    assert writer.events == ["reset"]
    assert writer.intentos == 7


def test_recovery_drop_failure_does_not_reset():
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    backend = FakeBackend()
    backend.recovery_drop_failure = True
    with pytest.raises(ProvisioningError, match="Recovery DROP failed"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), writer, backend, EXPECTED_DATABASE)
    assert "reset" not in writer.events


def test_recovery_absence_verification_failure_does_not_reset():
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    backend = FakeBackend()
    backend.recovery_absence_failure = True
    with pytest.raises(ProvisioningError, match="still exists"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), writer, backend, EXPECTED_DATABASE)
    assert "reset" not in writer.events


def test_recovery_unknown_supavisor_idle_session_fails_closed_before_mutation():
    backend = FakeBackend()
    backend.recovery_sessions = [RecoverySession(11, "pooler", "supavisor", "client backend", "idle", "2026-01-01", "2026-01-01")]
    with pytest.raises(ProvisioningError, match="exactly one residual"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), FakeWriter(), backend, EXPECTED_DATABASE)
    assert not any(event[0] in {"dispose", "sessions", "drop"} for event in backend.events if isinstance(event, tuple))


def test_recovery_terminates_only_explicit_residual_sessions():
    backend = FakeBackend()
    backend.recovery_sessions = [RecoverySession(11, "tenant-provisioner", "tenant-provisioner", "client backend", "idle", None, "2026-01-01", True)]
    backend.recovery_safe_to_terminate = True
    backend.recovery_residual_only = True
    execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), FakeWriter(), backend, EXPECTED_DATABASE)
    assert ("sessions", EXPECTED_DATABASE) not in backend.events
    assert ("sessions", "postgres") not in backend.events


@pytest.mark.parametrize("state", ["active", "idle in transaction"])
def test_recovery_blocks_supavisor_active_or_transaction_session(state):
    backend = FakeBackend()
    backend.recovery_sessions = [RecoverySession(11, "pooler", "supavisor", "client backend", state, None, "2026-01-01")]
    with pytest.raises(ProvisioningError, match="exactly one residual"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), FakeWriter(), backend, EXPECTED_DATABASE)


def test_recovery_blocks_unknown_application_even_when_idle():
    backend = FakeBackend()
    backend.recovery_sessions = [RecoverySession(11, "pooler", "unknown-app", "client backend", "idle", None, "2026-01-01")]
    with pytest.raises(ProvisioningError, match="exactly one residual"):
        execute_recovery(FakeReader(valid_mapping(tenant_state=ERROR, provisioning_state=ERROR)), FakeWriter(), backend, EXPECTED_DATABASE)


def test_configurable_timeout_and_restore_waits_for_normal_completion(tmp_path):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, argv, env, timeout_seconds):
            self.calls.append((argv, timeout_seconds))
            return "1"

    runner = Runner()
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), runner,
        command_timeout_seconds=17,
    )
    backend.restore_public(EXPECTED_DATABASE, tmp_path / "dump")
    assert runner.calls[0][1] == 17
    assert runner.calls[0][0].count(EXPECTED_DATABASE) == 1


def test_progress_reporter_has_ten_steps_without_secrets(tmp_path):
    reports = []
    reader = FakeReader(valid_mapping())
    writer = FakeWriter()
    backend = FakeBackend()
    execute_provision(reader, writer, backend, FakeTools(), EXPECTED_DATABASE,
                      dump_directory=tmp_path, reporter=lambda step, label: reports.append((step, label)))
    assert [step for step, _ in reports] == list(range(1, 11))
    text = " ".join(label for _, label in reports)
    assert "PGPASSWORD" not in text and "DATABASE_URL" not in text and "SECRET" not in text


def snapshot(*, columns=(), constraints=(), indexes=()):
    return SchemaSnapshot(tuple(columns), tuple(constraints), tuple(indexes), ("tables=27", "sequences=27"))


def source_counts(**changes):
    counts = {table: 0 for table in SOURCE_TABLES}
    counts.update(changes)
    return counts


def test_semantic_constraints_ignore_names_and_not_null_oid_constraints():
    source = snapshot(constraints=(
        ("p", "public", "usuario", "id"),
        ("u", "public", "usuario", "correo"),
        ("f", "public", "cita", "paciente_id", "public", "paciente", "id", "SIMPLE", "NO ACTION", "CASCADE", "False", "False"),
    ))
    target = snapshot(constraints=(
        ("p", "public", "usuario", "id"),
        ("u", "public", "usuario", "correo"),
        ("f", "public", "cita", "paciente_id", "public", "paciente", "id", "SIMPLE", "NO ACTION", "CASCADE", "False", "False"),
    ))
    assert source.constraints == target.constraints
    assert "not_null" not in repr(source.constraints)


class SnapshotRunner:
    def __init__(self, column_rows):
        self.column_rows = column_rows

    def run(self, argv, env):
        query = argv[-1]
        if "information_schema.columns" in query:
            return "\n".join(self.column_rows)
        if "FROM pg_constraint" in query:
            return (
                "f\tpublic\trol_funcion\trol_id,funcion_id\tpublic\trol\t"
                "id\tSIMPLE\tNO ACTION\tCASCADE\tFalse\tFalse\t"
                "FOREIGN KEY (rol_id, funcion_id) REFERENCES public.rol(id)"
            )
        if "FROM pg_indexes" in query:
            return "idx_table\tidx_name\tCREATE INDEX idx_name ON public.idx_table (id)"
        if "'tables='" in query:
            return "tables=27\nsequences=27\nviews=0\nmaterialized_views=0"
        raise AssertionError(f"Unexpected snapshot query: {query}")


def fixture_column_rows(count=182):
    return [
        f"table_{index // 10}\tcolumn_{index}\tinteger\tpg_catalog\tint4\tYES\t{index + 1}"
        for index in range(count)
    ]


def test_schema_snapshot_keeps_all_columns_when_processing_constraints():
    rows = fixture_column_rows()
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), SnapshotRunner(rows),
    )

    snapshot_result = backend.snapshot_target("postgres")

    assert snapshot_result.columns == tuple(rows)
    assert len(snapshot_result.columns) == 182


def test_schema_snapshot_constraint_columns_do_not_overwrite_column_rows():
    backend = SubprocessPostgresBackend(
        ToolPaths(Path("pg_dump.exe"), Path("pg_restore.exe"), Path("psql.exe")),
        PostgresConnection("host", 5432, "user", "SECRET"), SnapshotRunner(fixture_column_rows()),
    )

    snapshot_result = backend.snapshot_target("postgres")

    assert snapshot_result.constraints == (
        (
            "f", "public", "rol_funcion", "rol_id,funcion_id", "public", "rol", "id",
            "SIMPLE", "NO ACTION", "CASCADE", "False", "False",
        ),
    )
    assert len(snapshot_result.columns) == 182


def test_batch_source_preflight_accepts_the_182_column_fixture():
    _validate_batch_source_preflight(Preflight(
        source_counts=source_counts(),
        source_structure=SchemaSnapshot(
            tuple(fixture_column_rows()), (), tuple(str(index) for index in range(67)),
            ("tables=27", "sequences=27", "views=0", "materialized_views=0"),
        ),
    ))


def test_batch_source_preflight_rejects_a_missing_column():
    with pytest.raises(ProvisioningError, match="182 columns"):
        _validate_batch_source_preflight(Preflight(
            source_counts=source_counts(),
            source_structure=SchemaSnapshot(
                tuple(fixture_column_rows(181)), (), tuple(str(index) for index in range(67)),
                ("tables=27", "sequences=27"),
            ),
        ))


@pytest.mark.parametrize(("object_counts", "indexes", "message"), [
    (("tables=26", "sequences=27"), 67, "27 tables"),
    (("tables=27", "sequences=26"), 67, "27 tables and 27 sequences"),
    (("tables=27", "sequences=27"), 66, "67 indexes"),
])
def test_batch_source_preflight_keeps_structural_guards(object_counts, indexes, message):
    with pytest.raises(ProvisioningError, match=message):
        _validate_batch_source_preflight(Preflight(
            source_counts=source_counts(),
            source_structure=SchemaSnapshot(
                tuple(fixture_column_rows()), (), tuple(str(index) for index in range(indexes)),
                object_counts,
            ),
        ))


def test_batch_source_preflight_requires_schema_v1_snapshot():
    with pytest.raises(ProvisioningError, match="schema v1"):
        _validate_batch_source_preflight(Preflight(source_counts=source_counts()))


@pytest.mark.parametrize("left,right", [
    (("p", "public", "t", "id"), ("p", "public", "t", "other_id")),
    (("u", "public", "t", "email"), ("u", "public", "t", "phone")),
    (("f", "public", "t", "x", "public", "a", "id", "SIMPLE", "NO ACTION", "CASCADE", "False", "False"),
     ("f", "public", "t", "x", "public", "b", "id", "SIMPLE", "NO ACTION", "CASCADE", "False", "False")),
    (("f", "public", "t", "x", "public", "a", "id", "SIMPLE", "NO ACTION", "CASCADE", "False", "False"),
     ("f", "public", "t", "x", "public", "a", "id", "SIMPLE", "NO ACTION", "RESTRICT", "False", "False")),
 ])
def test_semantic_constraint_changes_are_not_equal(left, right):
    assert snapshot(constraints=(left,)).constraints != snapshot(constraints=(right,)).constraints


def test_check_normalization_only_accepts_safe_unbounded_text_aliases():
    assert _normalize_check_definition("CHECK ((tipo)::varchar = 'X'::text)") == "CHECK ((tipo)::text = 'X'::text)"
    assert _normalize_check_definition("CHECK ((tipo)::varchar[] = ARRAY['X']::text[])") == "CHECK ((tipo)::text[] = ARRAY['X']::text[])"
    assert _normalize_check_definition("CHECK (estado = 'A')") != _normalize_check_definition("CHECK (estado <> 'A')")
    assert _normalize_check_definition("CHECK ((valor)::varchar(10) = 'X'::text)") != "CHECK ((valor)::text = 'X'::text)"


def test_check_normalization_collapses_redundant_identical_text_casts_only():
    source = "CHECK ((estado)::text = 'A'::text)"
    target = "CHECK ((estado)::text::text::text = 'A'::text::text)"

    assert _normalize_check_definition(target) == _normalize_check_definition(source)
    assert _normalize_check_definition("CHECK ((estado)::text::integer = 'A'::text)") != _normalize_check_definition(source)
    assert _normalize_check_definition("CHECK ((estado)::text::varchar(10) = 'A'::text)") != _normalize_check_definition(source)


def test_check_normalization_equates_redundant_text_array_casts():
    casted = "CHECK (estado = ANY ((ARRAY['A'::varchar, 'B'::varchar]::varchar[])::text[]))"
    typed_elements = "CHECK (estado = ANY (ARRAY['A'::text, 'B'::text]))"
    assert _normalize_check_definition(casted) == _normalize_check_definition(typed_elements)


def test_check_normalization_keeps_array_operator_and_values_distinct():
    expected = "CHECK (estado = ANY (ARRAY['A'::text, 'B'::text]))"
    different_operator = "CHECK (estado <> ANY (ARRAY['A'::text, 'B'::text]))"
    different_value = "CHECK (estado = ANY (ARRAY['A'::text, 'C'::text]))"
    assert _normalize_check_definition(expected) != _normalize_check_definition(different_operator)
    assert _normalize_check_definition(expected) != _normalize_check_definition(different_value)


@pytest.mark.parametrize("source,target", [
    (snapshot(columns=("t\na\tinteger\tpg_catalog\tint4\tNO\t1",)),
     snapshot(columns=("t\na\tinteger\tpg_catalog\tint4\tYES\t1",))),
    (snapshot(constraints=(("c", "public", "t", "CHECK ((estado)::text = 'A'::text)"),)),
     snapshot(constraints=())),
    (snapshot(constraints=(("c", "public", "t", "CHECK ((estado)::text = 'A'::text)"),)),
     snapshot(constraints=(("c", "public", "t", "CHECK ((estado)::text <> 'A'::text)"),))),
    (snapshot(indexes=("t\tidx_a\tCREATE INDEX idx_a ON public.t USING btree (a)",)),
     snapshot(indexes=())),
    (snapshot(indexes=("t\tidx_a\tCREATE INDEX idx_a ON public.t USING btree (a)",)),
     snapshot(indexes=("t\tidx_b\tCREATE INDEX idx_b ON public.t USING btree (b)",))),
])
def test_snapshot_detects_nullable_check_and_index_differences(source, target):
    assert source != target


def run_reconcile(**changes):
    mapping_changes = changes.pop("mapping", {})
    backend_changes = changes.pop("backend", {})
    reader = FakeReader(clean_mapping(tenant_state=ERROR, provisioning_state=ERROR, **mapping_changes))
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    source = snapshot(columns=("usuario\tid\tinteger\tpg_catalog\tint4\tNO\t1",))
    backend = FakeBackend(exists=True, preflight=Preflight(
        source_counts=source_counts(usuario=1, paciente=1, cita=1),
        source_structure=source,
    ), **backend_changes)
    return reconcile_existing(reader, writer, backend, changes.pop("confirm", CLEAN_EXPECTED_DATABASE)), writer, backend


def test_reconcile_success_is_read_only_and_preserves_attempts():
    reports = []
    reader = FakeReader(clean_mapping(tenant_state=ERROR, provisioning_state=ERROR))
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    writer.intentos = 9
    source = snapshot(columns=("usuario\tid\tinteger\tpg_catalog\tint4\tNO\t1",))
    backend = FakeBackend(exists=True, preflight=Preflight(
        source_counts=source_counts(usuario=1, paciente=1, cita=1), source_structure=source,
    ))
    output = reconcile_existing(reader, writer, backend, CLEAN_EXPECTED_DATABASE,
                                reporter=lambda step, label: reports.append((step, label)))
    assert output.endswith("ACTIVA/COMPLETADO.")
    assert [step for step, _ in reports] == list(range(1, 7))
    assert writer.events == ["complete"]
    assert writer.intentos == 9
    verify_event = next(event for event in backend.events if event[0] == "verify_existing")
    assert set(verify_event[1]) == set(SOURCE_TABLES)
    assert not any(event in {"create", "dump", "restore", "start", "drop"}
                   for event in backend.events if isinstance(event, str))


@pytest.mark.parametrize("mapping", [
    {"tenant_state": ACTIVE, "provisioning_state": ERROR},
    {"tenant_state": ERROR, "provisioning_state": PENDING},
])
def test_reconcile_rejects_non_error_states_without_backend_calls(mapping):
    backend = FakeBackend()
    with pytest.raises(ProvisioningError, match="ERROR"):
        reconcile_existing(FakeReader(clean_mapping(**mapping)), FakeWriter(), backend, CLEAN_EXPECTED_DATABASE)
    assert backend.events == []


def test_reconcile_requires_exact_confirmation_and_existing_database():
    with pytest.raises(ProvisioningError, match="confirmation"):
        run_reconcile(confirm="TENANT_OFTALMO_NORTE")
    backend = FakeBackend(exists=False)
    with pytest.raises(ProvisioningError, match="does not exist"):
         reconcile_existing(FakeReader(clean_mapping(tenant_state=ERROR, provisioning_state=ERROR)),
                            FakeWriter(tenant_state=ERROR, provisioning_state=ERROR), backend, CLEAN_EXPECTED_DATABASE)
    assert "complete" not in backend.events


@pytest.mark.parametrize("counts", [
    {table: 0 for table in SOURCE_TABLES[:-1]},
    {**source_counts(), "unexpected_table": 1},
])
def test_reconcile_rejects_incomplete_or_unexpected_source_counts(counts):
    source = snapshot(columns=("usuario\tid\tinteger\tpg_catalog\tint4\tNO\t1",))
    backend = FakeBackend(exists=True, preflight=Preflight(
        source_counts=counts, source_structure=source,
    ))
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    with pytest.raises(ProvisioningError, match="source counts"):
        reconcile_existing(
            FakeReader(clean_mapping(tenant_state=ERROR, provisioning_state=ERROR)),
            writer, backend, CLEAN_EXPECTED_DATABASE,
        )
    assert "complete" not in writer.events


@pytest.mark.parametrize("field", [
    "structure_ok", "schemas_ok", "columns_ok", "constraints_ok", "indexes_ok", "counts_ok",
    "catalogs_ok", "admin_ok", "clean_data_ok",
])
def test_reconcile_every_verification_gate_blocks_activation(field):
    source = snapshot(columns=("usuario\tid\tinteger\tpg_catalog\tint4\tNO\t1",))
    backend = FakeBackend(preflight=Preflight(
        source_counts=source_counts(usuario=1, paciente=1, cita=1), source_structure=source,
        ), verification=Verification(**{field: False}))
    writer = FakeWriter(tenant_state=ERROR, provisioning_state=ERROR)
    with pytest.raises(ProvisioningError):
        reconcile_existing(FakeReader(clean_mapping(tenant_state=ERROR, provisioning_state=ERROR)), writer, backend, CLEAN_EXPECTED_DATABASE)
    assert writer.tenant_state == ERROR and writer.provisioning_state == ERROR
    assert "complete" not in writer.events


def test_cli_reconcile_is_explicit_and_does_not_construct_provision_tools(monkeypatch):
    with pytest.raises(SystemExit) as error:
        main(["--reconcile-clean-existing", "--empresa", CLEAN_EXPECTED_CODE])
    assert error.value.code == 2
    monkeypatch.setattr("scripts.saas.provision_tenant.build_reconcile_components", lambda *args: pytest.fail("not reached"))
    with pytest.raises(SystemExit) as error:
        main(["--reconcile-clean-existing", "--empresa", "OTHER-COMPANY", "--confirm", CLEAN_EXPECTED_DATABASE])
    assert error.value.code == 2


class CleanBackend(FakeBackend):
    def __init__(self, verification=None, exists=False):
        super().__init__(exists=exists, preflight=Preflight(
            role_createdb=True, dependencies_ok=True,
            source_structure=snapshot(),
        ))
        self.clean_verification = verification or CleanVerification()
        self.clean_calls = []

    def dump_public(self, source, destination, counts, schema_only=False):
        self.clean_calls.append(("dump", source, schema_only))

    def seed_catalogs(self, source, target, directory):
        self.clean_calls.append(("seed", source, target, tuple(CLEAN_CATALOG_TABLES)))

    def bootstrap_admin(self, target, email, password_hash):
        self.clean_calls.append(("admin", target, email, password_hash))

    def verify_clean(self, target, source_structure, admin_email):
        self.clean_calls.append(("verify_clean", target, admin_email))
        return self.clean_verification


def clean_mapping(**changes):
    values = dict(code=CLEAN_EXPECTED_CODE, database_name="tenant_oftalmo_norte",
                  tenant_state=PENDING, provisioning_state=PENDING,
                  empresa_id=10, tenant_database_id=20, provisioning_id=30)
    values.update(changes)
    return TenantMapping(**values)


def test_clean_provision_requires_dynamic_pending_mapping_and_confirmation():
    backend = CleanBackend()
    with pytest.raises(ProvisioningError, match="confirmation"):
        execute_provision_clean(FakeReader(clean_mapping()), FakeWriter(), backend,
                                FakeTools(), "wrong", "admin@norte.test",
                                password_provider=lambda _: "x")
    assert backend.clean_calls == []


def test_clean_provision_uses_schema_only_catalog_seed_and_hashes_password(tmp_path, monkeypatch):
    backend = CleanBackend()
    hashed = "HASHED-ONLY"
    monkeypatch.setattr("app.core.security.hash_password", lambda value: hashed)
    passwords = iter(("not-in-argv", "not-in-argv"))
    writer = FakeWriter()
    execute_provision_clean(FakeReader(clean_mapping()), writer, backend, FakeTools(),
                            "tenant_oftalmo_norte", "Admin@Norte.Test",
                            password_provider=lambda _: next(passwords), dump_directory=tmp_path)
    assert backend.clean_calls[0] == ("dump", "postgres", True)
    assert backend.clean_calls[1][0] == "seed"
    assert backend.clean_calls[2] == ("admin", "tenant_oftalmo_norte", "admin@norte.test", hashed)
    assert "not-in-argv" not in repr(backend.clean_calls)
    assert writer.events == ["start", "complete"]


def test_clean_provision_fail_closed_before_activation(tmp_path):
    backend = CleanBackend(verification=CleanVerification(empty_ok=False))
    writer = FakeWriter()
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision_clean(FakeReader(clean_mapping()), writer, backend, FakeTools(),
                                "tenant_oftalmo_norte", "admin@norte.test",
                                password_provider=lambda _: "same", dump_directory=tmp_path)
    assert "complete" not in writer.events
    assert writer.tenant_state == ERROR


def test_clean_provision_cleanup_failure_marks_error_before_activation(tmp_path):
    backend = CleanBackend()
    backend.failure_stage = "cleanup"
    writer = FakeWriter()
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision_clean(FakeReader(clean_mapping()), writer, backend, FakeTools(),
                                "tenant_oftalmo_norte", "admin@norte.test",
                                password_provider=lambda _: "same", dump_directory=tmp_path)
    assert "complete" not in writer.events
    assert writer.tenant_state == ERROR
    assert writer.provisioning_state == ERROR
    assert sum(event[0] == "cleanup" for event in backend.events if isinstance(event, tuple)) == 1


def test_clean_catalog_scope_is_explicit_and_does_not_include_clinical_tables():
    assert CLEAN_CATALOG_TABLES == ("accion", "modulo", "funcion", "rol", "rol_funcion")
    assert "paciente" not in CLEAN_CATALOG_TABLES


def test_clean_verification_explicitly_checks_unseeded_services_table_as_empty():
    assert "servicios_oftalmologicos" in CLEAN_EMPTY_TABLES
    assert "servicios_oftalmologicos" not in CLEAN_CATALOG_TABLES
    assert "cita" in CLEAN_EMPTY_TABLES


@dataclass
class BatchReader:
    mappings: dict[str, TenantMapping]
    requested_codes: list[str] = field(default_factory=list)

    def read_mapping(self, code):
        self.requested_codes.append(code)
        return self.mappings[code]


def batch_mapping(code, database, index):
    return TenantMapping(
        code=code, database_name=database, tenant_state=PENDING,
        provisioning_state=PENDING, empresa_id=index, tenant_database_id=100 + index,
        provisioning_id=200 + index, empresa_state=ACTIVE, subscription_id=300 + index,
        subscription_state=ACTIVE, subscription_start=date(2020, 1, 1),
        subscription_end=date(2099, 12, 31),
    )


def batch_source_preflight():
    return Preflight(
        role_createdb=True, dependencies_ok=True,
        source_counts={table: 0 for table in SOURCE_TABLES},
        source_structure=SchemaSnapshot(
            tuple(str(index) for index in range(182)), (),
            tuple(str(index) for index in range(67)), ("tables=27", "sequences=27"),
        ),
    )


class BatchBackend(CleanBackend):
    def __init__(self):
        super().__init__()
        self.created_databases = []

    def create_database(self, database_name):
        self.events.append("create")
        self.created_databases.append(database_name)
        if self.failure:
            raise self.failure


def batch_components(failure=None):
    mappings = {
        code: batch_mapping(code, database, index)
        for index, (code, database, _) in enumerate(PENDING_BATCH_TARGETS, start=1)
    }
    reader = BatchReader(mappings)
    writer = FakeWriter()
    backend = BatchBackend()
    backend.preflight_result = batch_source_preflight()
    backend.failure = failure
    return reader, writer, backend


def test_pending_batch_has_closed_order_and_excludes_completed_tenants():
    assert PENDING_BATCH_CODES == (
        "VISUAL-ORIENTAL", "INSTITUTO-VISION", "OFTALMOCARE", "VISTA-SUR", "MEDICO-OCULAR",
    )
    assert not set(PENDING_BATCH_CODES) & {EXPECTED_CODE, CLEAN_EXPECTED_CODE}


def test_pending_batch_global_preflight_stops_before_create(tmp_path):
    reader, writer, backend = batch_components()
    backend.preflight_result = Preflight(
        role_createdb=False, source_structure=SchemaSnapshot((), (), (), ()),
    )
    with pytest.raises(ProvisioningError, match="prechecks"):
        execute_provision_pending_batch(
            reader, writer, backend, FakeTools(), password_provider=lambda _: "unused",
            dump_directory=tmp_path,
        )
    assert not any(event == "create" for event in backend.events)
    assert writer.events == []


def test_pending_batch_is_sequential_prompts_twice_and_keeps_database_isolation(tmp_path):
    reader, writer, backend = batch_components()
    prompts = []
    values = iter([f"password-{index // 2}" for index in range(10)])
    execute_provision_pending_batch(
        reader, writer, backend, FakeTools(),
        password_provider=lambda prompt: (prompts.append(prompt), next(values))[1],
        dump_directory=tmp_path,
    )
    assert backend.created_databases == [database for _, database, _ in PENDING_BATCH_TARGETS]
    assert len(prompts) == 10
    assert writer.events.count("complete") == 5
    assert len(set(backend.created_databases)) == 5
    assert [event[2] for event in backend.clean_calls if event[0] == "admin"] == [
        email for _, _, email in PENDING_BATCH_TARGETS
    ]
    assert reader.requested_codes[:5] == list(PENDING_BATCH_CODES)


def test_pending_batch_stops_at_first_error_and_does_not_touch_following_tenants(tmp_path):
    reader, writer, backend = batch_components(RuntimeError("controlled failure"))
    passwords = iter(["same"] * 2)
    with pytest.raises(ProvisioningError, match="marked ERROR"):
        execute_provision_pending_batch(
            reader, writer, backend, FakeTools(), password_provider=lambda _: next(passwords),
            dump_directory=tmp_path,
        )
    assert backend.created_databases == [PENDING_BATCH_TARGETS[0][1]]
    assert writer.events[0] == "start"
    assert writer.events[1].startswith("fail:")
    assert "complete" not in writer.events
    assert reader.requested_codes == list(PENDING_BATCH_CODES) + [PENDING_BATCH_CODES[0]]


def test_pending_batch_does_not_expose_passwords_or_accept_completed_tenants():
    assert "--password" not in __import__("scripts.saas.provision_tenant", fromlist=["__file__"]).__file__
    with pytest.raises(SystemExit) as error:
        main(["--provision-pending-batch", "--empresa", EXPECTED_CODE])
    assert error.value.code == 2
