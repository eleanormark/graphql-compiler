# Copyright 2018-present Kensho Technologies, LLC.
from decimal import Decimal

import six

from ... import graphql_to_match, graphql_to_sql
from ...compiler.emit_sql import print_mssql_query
from sqlalchemy.dialects import mssql


def sort_db_results(results):
    """Deterministically sort DB results.

    Args:
        results: List[Dict], results from a DB.

    Returns:
        List[Dict], sorted DB results.
    """
    sort_order = []
    if len(results) > 0:
        sort_order = sorted(six.iterkeys(results[0]))

    def sort_key(result):
        """Convert None/Not None to avoid comparisons of None to a non-None type."""
        return tuple((result[col] is not None, result[col]) for col in sort_order)

    return sorted(results, key=sort_key)


def try_convert_decimal_to_string(value):
    """Return Decimals as string if value is a Decimal, return value otherwise."""
    if isinstance(value, list):
        return [try_convert_decimal_to_string(subvalue) for subvalue in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def compile_and_run_match_query(schema, graphql_query, parameters, graph_client):
    """Compiles and runs a MATCH query against the supplied graph client."""
    # MATCH code emitted by the compiler expects Decimals to be passed in as strings
    converted_parameters = {
        name: try_convert_decimal_to_string(value)
        for name, value in six.iteritems(parameters)
    }
    compilation_result = graphql_to_match(schema, graphql_query, converted_parameters)

    query = compilation_result.query
    results = [row.oRecordData for row in graph_client.command(query)]
    return results


def compile_and_run_sql_query(schema, graphql_query, parameters, engine, sql_metadata):
    """Compiles and runs a SQL query against the supplied SQL backend."""
    dialect_name = engine.dialect.name
    compilation_result = graphql_to_sql(schema, graphql_query, parameters, sql_metadata, None)
    query = compilation_result.query
    results_with_query_string = []
    results_with_sqlalchemy_clause = []
    connection = engine.connect()
    query_string = print_mssql_query(query.params(parameters))
    with connection.begin() as trans:
        for result in connection.execute(query_string):
            results_with_query_string.append(dict(result))
        for result in connection.execute(query, parameters):
            results_with_sqlalchemy_clause.append(dict(result))
        trans.rollback()
    if results_with_query_string != results_with_sqlalchemy_clause:
        raise AssertionError('Expected results of query executed a string and as a ClauseElement '
                             'to be the same.')
    return results_with_query_string

