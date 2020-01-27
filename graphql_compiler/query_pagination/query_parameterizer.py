# Copyright 2019-present Kensho Technologies, LLC.
from copy import copy
from typing import Any, Dict, List, Set, Tuple, cast

from graphql.language.ast import (
    ArgumentNode,
    DirectiveNode,
    DocumentNode,
    FieldNode,
    InlineFragmentNode,
    ListValueNode,
    NameNode,
    OperationDefinitionNode,
    SelectionSetNode,
    StringValueNode,
)

from ..global_utils import ASTWithParameters
from ..ast_manipulation import get_ast_field_name, get_only_query_definition
from ..compiler.helpers import get_parameter_name
from ..exceptions import GraphQLError
from ..schema.schema_info import QueryPlanningSchemaInfo
from .pagination_planning import VertexPartitionPlan


def _generate_new_name(base_name: str, taken_names: Set[str]) -> str:
    """Return a name based on the provided string that is not already taken.

    This method tries the following names: {base_name}_0, then {base_name}_1, etc.
    and returns the first one that's not in taken_names

    Args:
        base_name: The base for name construction as explained above
        taken_names: The set of names not permitted as output

    Returns:
        a name based on the base_name that is not in taken_names
    """
    index = 0
    while "{}_{}".format(base_name, index) in taken_names:
        index += 1
    return "{}_{}".format(base_name, index)


def _get_binary_filter_node_parameter(filter_directive: DirectiveNode) -> str:
    """Return the parameter name for a binary Filter Directive."""
    filter_arguments = cast(ListValueNode, filter_directive.arguments[1].value).values
    if len(filter_arguments) != 1:
        raise AssertionError("Expected one argument in filter {}".format(filter_directive))

    argument_name = cast(StringValueNode, filter_arguments[0]).value
    parameter_name = get_parameter_name(argument_name)
    return parameter_name


def _get_filter_node_operation(filter_directive: DirectiveNode) -> str:
    """Return the @filter's op_name as a string."""
    return cast(StringValueNode, filter_directive.arguments[0].value).value


def _add_pagination_filter(
    query_ast: DocumentNode,
    query_path: Tuple[str, ...],
    pagination_field: str,
    directive_to_add: DirectiveNode,
) -> Tuple[DocumentNode, List[str]]:
    """Add the filter to the target field, returning a query and the names of removed filters.

    Args:
        query_ast: The query in which we are adding a filter
        query_path: The path to the pagination vertex
        pagination_field: The field on which we are adding a filter
        directive_to_add: The filter directive to add

    Returns:
        new_ast: A query with the filter inserted, and any filters on the same location with
                 the same operation removed.
        removed_parameters: The parameter names used in filters that were removed.
    """
    if not isinstance(query_ast, (FieldNode, InlineFragmentNode, OperationDefinitionNode)):
        raise AssertionError(
            f'Input AST is of type "{type(query_ast).__name__}", which should not be a selection.'
        )

    removed_parameters = []
    new_selections = []
    if len(query_path) == 0:
        # Add the filter to the correct field on this vertex and remove any redundant filters.
        found_field = False
        for selection_ast in query_ast.selection_set.selections:
            new_selection_ast = selection_ast
            field_name = get_ast_field_name(selection_ast)
            if field_name == pagination_field:
                found_field = True
                new_selection_ast = copy(selection_ast)
                new_selection_ast.directives = copy(selection_ast.directives)

                new_directives = []
                for directive in selection_ast.directives:
                    operation = _get_filter_node_operation(directive)
                    if operation == _get_filter_node_operation(directive_to_add):
                        removed_parameters.append(_get_binary_filter_node_parameter(directive))
                    else:
                        new_directives.append(directive)
                new_directives.append(directive_to_add)
                new_selection_ast.directives = new_directives
            new_selections.append(new_selection_ast)
        if not found_field:
            new_selections.insert(
                0, FieldNode(name=NameNode(value=pagination_field), directives=[directive_to_add])
            )
    else:
        # Recurse until the target vertex is reached.
        if query_ast.selection_set is None:
            raise AssertionError()

        found_field = False
        new_selections = []
        for selection_ast in query_ast.selection_set.selections:
            new_selection_ast = selection_ast
            field_name = get_ast_field_name(selection_ast)
            if field_name == query_path[0]:
                found_field = True
                new_selection_ast, sub_removed_parameters = _add_pagination_filter(
                    selection_ast, query_path[1:], pagination_field, directive_to_add
                )
                removed_parameters.extend(sub_removed_parameters)
            new_selections.append(new_selection_ast)

        if not found_field:
            raise AssertionError()

    new_ast = copy(query_ast)
    new_ast.selection_set = SelectionSetNode(selections=new_selections)
    return new_ast, removed_parameters


def _make_binary_filter_directive_node(op_name: str, param_name: str) -> DirectiveNode:
    """Make a binary filter directive node with the given binary op_name and parameter name."""
    return DirectiveNode(
        name=NameNode(value="filter"),
        arguments=[
            ArgumentNode(name=NameNode(value="op_name"), value=StringValueNode(value=op_name),),
            ArgumentNode(
                name=NameNode(value="value"),
                value=ListValueNode(values=[StringValueNode(value="$" + param_name)]),
            ),
        ],
    )


def generate_parameterized_queries(
    schema_info: QueryPlanningSchemaInfo,
    query: ASTWithParameters,
    vertex_partition: VertexPartitionPlan,
) -> Tuple[ASTWithParameters, ASTWithParameters, str]:
    """Generate two parameterized queries that can be used to paginate over a given query.

    Args:
        schema_info: QueryPlanningSchemaInfo
        query: the query to parameterize
        vertex_partition: pagination plan

    Returns:
        # TODO use ASTWithParameters struct to simplify API
        next_page: Ast and params for next page.
        remainder_ast: Ast and params for remainder.
        param_name: The parameter name used in the new filters.
    """
    query_type = get_only_query_definition(query.query_ast, GraphQLError)

    param_name = _generate_new_name("__paged_param", set(query.parameters.keys()))
    next_page_root, next_page_removed_parameters = _add_pagination_filter(
        query_type,
        vertex_partition.query_path,
        vertex_partition.pagination_field,
        _make_binary_filter_directive_node("<", param_name),
    )
    remainder_root, remainder_removed_parameters = _add_pagination_filter(
        query_type,
        vertex_partition.query_path,
        vertex_partition.pagination_field,
        _make_binary_filter_directive_node(">=", param_name),
    )

    page_parameters = {k: v for k, v in query.parameters.items() if k not in next_page_removed_parameters}
    remainder_parameters = {
        k: v for k, v in query.parameters.items() if k not in remainder_removed_parameters
    }

    next_page = ASTWithParameters(DocumentNode(definitions=[next_page_root]), page_parameters)
    remainder = ASTWithParameters(DocumentNode(definitions=[remainder_root]), remainder_parameters)
    return next_page, remainder, param_name
