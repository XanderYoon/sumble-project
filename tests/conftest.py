"""Shared fixtures isolate PostgreSQL tests in disposable schemas."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from sumble_api import database


@pytest.fixture
def postgres_schema(monkeypatch):
    if os.environ.get("SUMBLE_RUN_INTEGRATION") != "1":
        pytest.skip("Set SUMBLE_RUN_INTEGRATION=1 and PG variables for real PostgreSQL tests")
    schema = "sumble_test_" + uuid4().hex
    real_connect = psycopg.connect
    connections = []
    backend_pids = []
    with real_connect(connect_timeout=3, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            admin.execute("""
                CREATE TABLE job_posts (
                    id bigint, organization_id bigint, country_id bigint,
                    datetime_pulled timestamptz
                );
                CREATE TABLE detail_rows (id bigint, job_title text, location text);
                CREATE VIEW job_posts_details AS SELECT * FROM detail_rows;
                CREATE TABLE organizations (id bigint, name text);
                CREATE TABLE tech (id bigint, name text);
                CREATE TABLE job_functions (id bigint, name text);
                CREATE TABLE job_posts_tech (job_post_id bigint, tech_id bigint);
                CREATE TABLE job_posts_job_functions (job_post_id bigint, job_function_id bigint);
                INSERT INTO organizations VALUES (1, 'Apple'), (2, 'Apple Services'), (3, NULL);
                INSERT INTO job_posts VALUES
                    (1, 1, 10, '2024-01-01Z'), (2, 1, NULL, '2024-01-01Z'),
                    (3, 2, 10, '2024-01-02Z'), (4, NULL, NULL, NULL),
                    (5, 3, NULL, NULL), (6, 999, 20, '2024-01-02Z'),
                    (7, 1, 20, '2024-01-03Z'), (8, 2, 20, '2024-01-03Z');
                INSERT INTO detail_rows VALUES
                    (1, 'Alpha', 'London'), (2, 'Alpha', NULL), (3, 'Zulu', 'Paris'),
                    (5, NULL, NULL), (6, 'Beta', 'Rome'), (7, 'Beta', NULL), (8, 'Zulu', NULL);
                INSERT INTO tech VALUES
                    (1, 'Python'), (2, 'sql'), (3, '.net'), (4, 'psql'), (5, NULL),
                    (6, 'x''); DROP TABLE job_posts; --'), (7, 'pythonista'),
                    (8, '%'), (9, 'a_b');
                INSERT INTO job_functions VALUES (1, 'Engineering'), (2, 'Sales'), (3, NULL);
                INSERT INTO job_posts_tech VALUES
                    (1, 1), (1, 1), (1, 2), (1, 3), (2, 1), (2, 4),
                    (3, 2), (4, NULL), (5, 5), (6, 6), (7, 7), (8, 8), (8, 9), (999, 1);
                INSERT INTO job_posts_job_functions VALUES
                    (1, 1), (1, 1), (2, 2), (3, 1), (4, NULL), (5, 3), (6, 2);
            """)

            def isolated_connect(*args, **kwargs):
                kwargs["options"] = f"-c search_path={schema}"
                connection = real_connect(*args, **kwargs)
                connections.append(connection)
                backend_pids.append(connection.info.backend_pid)
                return connection

            monkeypatch.setattr(database.psycopg, "connect", isolated_connect)
            yield admin, connections, backend_pids
        finally:
            for connection in connections:
                connection.close()
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
