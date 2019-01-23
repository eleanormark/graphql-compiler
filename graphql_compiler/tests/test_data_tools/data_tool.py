# Copyright 2018-present Kensho Technologies, LLC.
from glob import glob
from os import path

from funcy import retry
import six
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine, text

from ..integration_tests.integration_backend_config import (
    EXPLICIT_DB_BACKENDS, SQL_BACKEND_TO_CONNECTION_STRING, SqlTestBackend
)


def generate_orient_snapshot_data(client):
    """Create OrientDB test DB from the SQL commands file for snapshot testing."""
    project_root = path.dirname(path.abspath(__file__))
    sql_files = glob(path.join(project_root, 'snapshot_data/*.sql'))
    _load_sql_files_to_orient_client(client, sql_files)


def generate_orient_integration_data(client):
    """Create OrientDB test DB from the SQL commands file for snapshot testing."""
    project_root = path.dirname(path.abspath(__file__))
    sql_files = glob(path.join(project_root, 'integration_data/*.sql'))
    _load_sql_files_to_orient_client(client, sql_files)


def _load_sql_files_to_orient_client(client, sql_files):
    """Load list of supplied SQL files into the supplied OrientDB client."""
    for filepath in sql_files:
        with open(filepath) as f:
            for command in f.readlines():
                sanitized_command = command.strip()
                if len(sanitized_command) == 0 or sanitized_command[0] == '#':
                    # comment or empty line, ignore
                    continue

                client.command(sanitized_command)


@retry(tries=20, timeout=1)  # pylint: disable=no-value-for-parameter
def init_sql_integration_test_backends():
    """Connect to and open transaction on each SQL DB under test."""
    sql_test_backends = {}
    for backend_name, base_connection_string in six.iteritems(SQL_BACKEND_TO_CONNECTION_STRING):
        engine = create_engine(base_connection_string)
        # safely create the test DATABASE for all SQL backends except sqlite
        # sqlite's in-memory database does not need to be explicitly created/dropped.
        if backend_name in EXPLICIT_DB_BACKENDS:
            # safely drop the test DB, outside of a transaction (autocommit)
            drop_database_command = text('DROP DATABASE IF EXISTS animals;')
            engine.execution_options(isolation_level="AUTOCOMMIT").execute(drop_database_command)
            # create the test DB, outside of a transaction (autocommit)
            create_database_command = text('CREATE DATABASE animals;')
            engine.execution_options(isolation_level="AUTOCOMMIT").execute(create_database_command)
            # update the connection string and engine to connect to this new DB specifically
            connection_string = base_connection_string + u'/animals'
            engine = create_engine(connection_string)
        sql_test_backend = SqlTestBackend(engine, base_connection_string)
        sql_test_backends[backend_name] = sql_test_backend
    return sql_test_backends


def tear_down_integration_test_backends(sql_test_backends):
    """Rollback backends' transactions to wipe test data and to close the active connections."""
    for backend_name, sql_test_backend in six.iteritems(sql_test_backends):
        if backend_name not in EXPLICIT_DB_BACKENDS:
            continue
        # explicitly release engine resources, specifically to disconnect from active DB
        # some backends including Postgres do no not allow an in use DB to be dropped
        sql_test_backend.engine.dispose()
        # connect to base server, not explicit DB, so DB can be dropped
        engine = create_engine(sql_test_backend.base_connection_string)
        # set execution options to AUTOCOMMIT so that the DB drop is not performed in a transaction
        # as this is not allowed on some SQL backends
        drop_database_command = text('DROP DATABASE IF EXISTS animals;')
        engine.execution_options(isolation_level="AUTOCOMMIT").execute(drop_database_command)


def generate_sql_integration_data(sql_test_backends):
    """Populate test data for SQL backends for integration testing."""
    metadata = MetaData()
    animal_table = Table(
        'animal',
        metadata,
        Column('animal_id', Integer, primary_key=True),
        Column('name', String(length=12), nullable=False),
        Column('net_worth', Float, nullable=False),
    )
    animal_rows = (
        (1, 'Animal 1', 100.0),
        (2, 'Animal 2', 200.0),
        (3, 'Animal 3', 300.0),
        (4, 'Animal 4', 400.0),
    )
    table_values = [
        (animal_table, animal_rows),
    ]
    for sql_test_backend in six.itervalues(sql_test_backends):
        metadata.drop_all(sql_test_backend.engine)
        metadata.create_all(sql_test_backend.engine)
        for table, insert_values in table_values:
            for insert_value in insert_values:
                sql_test_backend.engine.execute(table.insert(insert_value))

    return metadata
