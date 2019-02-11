# Copyright 2019-present Kensho Technologies, LLC.
from collections import namedtuple
from copy import copy
from itertools import chain

from graphql.validation import validate

from ...ast_manipulation import get_ast_field_name, get_human_friendly_ast_field_name
from ...exceptions import GraphQLInvalidMacroError
from ...schema import VERTEX_FIELD_PREFIXES, is_vertex_field_name
from .directives import MacroEdgeDefinitionDirective, MacroEdgeTargetDirective
from .helpers import get_only_selection_from_ast


def _validate_macro_ast_with_macro_directives(schema, ast, macro_directives):
    """Raise errors if the macro uses the macro directives incorrectly or is otherwise invalid."""
    if ast.directives is not None:
        directive_names = [directive.name.value for directive in ast.directives]
        raise GraphQLInvalidMacroError(
            u'Unexpectedly found directives at the top level of the GraphQL input. '
            u'This is not supported. Directives: {}'.format(directive_names))

    if ast.variable_definitions is not None:
        raise GraphQLInvalidMacroError(
            u'Unexpectedly found variable definitions at the top level of the GraphQL input. '
            u'This is not supported. Variable definitions: {}'.format(ast.variable_definitions))

    required_macro_directives = (MacroEdgeDefinitionDirective, MacroEdgeTargetDirective)

    # pylint: disable=protected-access
    schema_with_macro_directives = copy(schema)
    schema_with_macro_directives._directives = list(chain(
        schema_with_macro_directives._directives, required_macro_directives))
    # pylint: enable=protected-access

    validation_errors = validate(schema, ast)
    if validation_errors:
        raise GraphQLInvalidMacroError(
            u'Macro edge failed validation: {}'.format(validation_errors))

    for directive_definition in required_macro_directives:
        macro_data = macro_directives.get(directive_definition.name, None)
        if not macro_data:
            raise GraphQLInvalidMacroError(
                u'Required macro edge directive "@{}" was not found anywhere within the supplied '
                u'macro edge definition GraphQL.'.format(directive_definition.name))

        if len(macro_data) > 1:
            raise GraphQLInvalidMacroError(
                u'Required macro edge directive "@{}" was unexpectedly present more than once in '
                u'the supplied macro edge definition GraphQL. It was found {} times.'
                .format(directive_definition.name, len(macro_data)))


def _validate_class_selection_ast(ast, macro_defn_ast):
    """Ensure that the macro's top-level selection AST adheres to our expectations."""
    directive_names = [
        directive.name.value
        for directive in ast.directives
    ]
    unexpected_directives = [
        directive_name
        for directive_name in directive_names
        if directive_name != MacroEdgeDefinitionDirective.name
    ]
    if unexpected_directives:
        raise GraphQLInvalidMacroError(
            u'Found unexpected directives at the top level of the macro definition GraphQL: '
            u'{}'.format(unexpected_directives))

    if ast is not macro_defn_ast:
        raise GraphQLInvalidMacroError(
            u'Expected to find the "@{}" directive at the top level of the macro definition '
            u'GraphQL (on the "{}" field), but instead found it on the "{}" field. This is '
            u'not allowed.'.format(MacroEdgeDefinitionDirective.name,
                                   get_human_friendly_ast_field_name(ast),
                                   get_human_friendly_ast_field_name(macro_defn_ast)))


def _validate_macro_edge_name_for_class_name(schema, class_name, macro_edge_name):
    """Ensure that the provided macro edge name is valid for the given class name."""
    # The macro edge must be a valid edge name.
    if not is_vertex_field_name(macro_edge_name):
        raise GraphQLInvalidMacroError(
            u'The provided macro edge name "{}" is not valid, since it does not start with '
            u'the expected prefixes for vertex fields: {}'
            .format(macro_edge_name, list(VERTEX_FIELD_PREFIXES)))

    # The macro edge must not have the same name as an existing edge on the class where it exists.
    class_object = schema.get_type(class_name)
    if macro_edge_name in class_object.fields:
        raise GraphQLInvalidMacroError(
            u'The provided macro edge name "{}" has the same name as an existing field on the '
            u'"{}" GraphQL type or interface. This is not allowed, please choose a different name.'
            .format(macro_edge_name, class_name))


# ############
# Public API #
# ############


MacroEdgeDescriptor = namedtuple(
    'MacroEdgeDescriptor', (
        'expansion_ast',  # GraphQL AST object defining how the macro edge should be expanded
        'macro_args',     # Dict[str, Any] containing any arguments that the macro requires
    )
)


def get_and_validate_macro_edge_info(schema, ast, macro_directives, macro_edge_args,
                                     type_equivalence_hints=None):
    """Return a tuple with the three parts of information that uniquely describe a macro edge.

    Args:
        schema: GraphQL schema object, created using the GraphQL library
        ast: GraphQL library AST OperationDefinition object, describing the GraphQL that is defining
             the macro edge.
        macro_directives: Dict[str, List[Tuple[AST object, Directive]]], mapping the name of an
                          encountered directive to a list of its appearances, each described by
                          a tuple containing the AST with that directive and the directive object
                          itself.
        macro_edge_args: dict mapping strings to any type, containing any arguments the macro edge
                         requires in order to function.
        type_equivalence_hints: optional dict of GraphQL interface or type -> GraphQL union.
                                Used as a workaround for GraphQL's lack of support for
                                inheritance across "types" (i.e. non-interfaces), as well as a
                                workaround for Gremlin's total lack of inheritance-awareness.
                                The key-value pairs in the dict specify that the "key" type
                                is equivalent to the "value" type, i.e. that the GraphQL type or
                                interface in the key is the most-derived common supertype
                                of every GraphQL type in the "value" GraphQL union.
                                Recursive expansion of type equivalence hints is not performed,
                                and only type-level correctness of this argument is enforced.
                                See README.md for more details on everything this parameter does.
                                *****
                                Be very careful with this option, as bad input here will
                                lead to incorrect output queries being generated.
                                *****

    Returns:
        tuple (class name for macro, name of macro edge, MacroEdgeDescriptor),
        where the first two values are strings and the last one is a MacroEdgeDescriptor object
    """
    _validate_macro_ast_with_macro_directives(schema, ast, macro_directives)

    macro_defn_ast, macro_defn_directive = macro_directives[MacroEdgeDefinitionDirective.name][0]
    # macro_target_ast, _ = macro_directives[MacroEdgeTargetDirective.name][0]

    # TODO(predrag): Required further validation:
    # - the macro definition directive AST contains only @filter/@fold directives together with
    #   the target directive;
    # - after adding an output, the macro compiles successfully, the macro args and necessary and
    #   sufficient for the macro, and the macro args' types match the inferred types of the
    #   runtime parameters in the macro.

    class_ast = get_only_selection_from_ast(ast)
    class_name = get_ast_field_name(class_ast)

    _validate_class_selection_ast(class_ast, macro_defn_ast)

    macro_edge_name = macro_defn_directive.arguments['name'].value

    _validate_macro_edge_name_for_class_name(schema, class_name, macro_edge_name)

    _make_macro_edge_descriptor()

    return class_name, macro_edge_name


def _make_macro_edge_descriptor():
    """Not implemented yet."""
    raise NotImplementedError()