#!/usr/bin/env python3
"""Capture actual D2 persisted native input hashes in a verified archive process."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive-process", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.archive_process:
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        environment.pop("PYTHONPATH", None)
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:], "--archive-process"],
            env=environment,
            check=True,
        )
        return
    # Verification does not import application code. Import paths are installed
    # only afterwards, in this new interpreter, before the first package import.
    from scientific_context_baseline import verify_archive

    archive = args.archive.resolve(strict=True)
    provenance = verify_archive(args.repository.resolve(strict=True), archive, args.source_commit)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(archive / "backend/src"), str(archive / "backend/tests")]
    package = importlib.import_module("polymath")
    if not Path(package.__file__).resolve().is_relative_to(archive / "backend/src"):
        raise RuntimeError("Historical application import escaped verified archive")
    import httpx
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import Session

    def forbid_http(*args, **kwargs):
        raise RuntimeError("Historical persisted input capture forbids HTTP/model calls")

    httpx.Client.send = forbid_http
    httpx.AsyncClient.send = forbid_http
    from polymath.research.meta import MetaModel

    MetaModel.__init__ = forbid_http
    from native_input_compatibility import capture_inputs, seed_inputs

    from polymath.budget_models import BudgetAccount, BudgetReservation
    from polymath.meta_research_models import NativeResearchRunBinding

    url = make_url(os.environ["TEST_DATABASE_URL"])
    if url.host not in ("localhost", "127.0.0.1") or url.drivername != "postgresql+psycopg":
        raise RuntimeError("Historical input capture requires isolated local PostgreSQL")
    schema = "polymath_d2_input_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    # D2 Alembic env.py reads Settings directly and does not consume a supplied
    # Config connection; pin its URL explicitly inside this isolated child.
    os.environ["DATABASE_URL"] = scoped.render_as_string(hide_password=False)
    engine = create_engine(scoped, hide_parameters=True)
    try:
        config = Config(str(archive / "backend/alembic.ini"))
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        with engine.connect() as connection:
            if (
                connection.scalar(text("SELECT current_schema()")) != schema
                or connection.scalar(text("SELECT version_num FROM alembic_version"))
                != "0023_public_research"
            ):
                raise RuntimeError("Archive migrations escaped their isolated D2 schema")
        cases = seed_inputs(engine)
        hashes = capture_inputs(engine, cases)
        with Session(engine) as session:
            if any(
                session.scalar(select(func.count()).select_from(model))
                for model in (BudgetAccount, BudgetReservation, NativeResearchRunBinding)
            ):
                raise RuntimeError("Input fixtures must not manufacture financial/native authority")
        imports = {}
        for name, module in tuple(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if filename and (
                name == "polymath" or name.startswith("polymath.") or name.startswith("test_")
            ):
                resolved = Path(filename).resolve()
                if not resolved.is_relative_to(archive):
                    raise RuntimeError("Historical import escaped verified archive: " + name)
                imports[name] = resolved.relative_to(archive).as_posix()
        helper = Path(__file__).with_name("native_input_compatibility.py")
        payload = {
            "format": "polymath-d2-persisted-native-input-v1",
            "provenance": provenance,
            "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
            "verified_imports": imports,
            "fixture_scope": (
                "Actual D2 native_run_input_sha256 on stable persisted input rows; "
                "no native approval, model/provider call or financial authority"
            ),
            "cases": {
                name: {"run_id": cases[name], "native_input_sha256": digest}
                for name, digest in hashes.items()
            },
            "checkpoint_fingerprints": None,
            "omissions": [
                "Actual worker checkpoint fingerprints and full controller/native authority "
                "journeys are not claimed by these metadata fixtures"
            ],
        }
        with args.output.open("x") as output:
            output.write(json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "cases": len(cases),
                    "source_commit": args.source_commit,
                    "output": str(args.output),
                }
            )
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
