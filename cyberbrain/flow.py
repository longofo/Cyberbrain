"""Execution flow that represents a program's execution."""

import ast
import itertools
from dataclasses import dataclass
from typing import Any, Dict, Set, Tuple, List, Iterable, Union

import astor

from .basis import ID, FrameID
from . import utils


@dataclass()
class VarAppearance:
    """Variable appears in current frame."""

    id: ID
    value: Any


@dataclass()
class VarModification:
    """Variable value modified in current frame."""

    id: ID
    old_value: Any
    new_value: Any


@dataclass()
class VarSwitch:
    """Variable switches at callsite."""

    arg_id: ID
    param_id: ID
    value: Any


class TrackingMetadata:
    """Class that stores metadata during tracing."""

    def __init__(
        self,
        data: Dict[ID, Any],
        code_str: str = None,
        code_ast: ast.AST = None,
        param_to_arg: Dict[ID, ID] = None,
        data_before_return=None,
    ):
        if not any([code_str, code_ast]):
            raise ValueError("Should provide code_str or code_ast.")
        self.code_str = code_str or astor.to_source(code_ast)
        self.code_ast = code_ast or utils.parse_code_str(code_str)
        self.param_to_arg = param_to_arg
        if param_to_arg:
            self.arg_to_param = {}
            for param, args in param_to_arg.items():
                for arg in args:
                    self.arg_to_param[arg] = param

        # It seems that tracking and data should all be flattened, aka they should
        # simply be a mapping of ID -> value. When backtracing, we don't really care
        # about where an identifer is defined in, we only care about whether its value
        # has changed during execution.
        self.tracking: Set[ID] = set()

        self.var_appearances: Set[VarAppearance] = []
        self.var_modifications: Set[VarModification] = []

        # var_switches are set on call node. When some id is switched, it is not counted
        # again in var_appearances.
        self.var_switches: Set[VarSwitch] = []
        self.data = data
        self.data_before_return = data_before_return

    def __repr__(self):
        return ", ".join(
            [
                f"tracking: {self.tracking}",
                f"var_appearances: {self.var_appearances}",
                f"var_modifications: {self.var_modifications}",
                f"var_switches: {self.var_switches}",
            ]
        )

    def get_args(self) -> Set[ID]:
        return set(itertools.chain.from_iterable(self.param_to_arg.values()))

    def add_var_appearances(self, *var_appearances: VarAppearance):
        self.var_appearances.extend(var_appearances)

    def add_var_modifications(self, *var_modifications: VarModification):
        self.var_modifications.extend(var_modifications)

    def add_var_switches(self, *var_switches: VarSwitch):
        self.var_switches.extend(var_switches)

    def sync_tracking_with(self, other: "Node"):
        self.add_tracking(*other.tracking)

    def add_tracking(self, *new_ids: ID):
        """Updates identifiers being tracked.

        Identifiers being tracked must exist in data because we can't track something
        that don't exist in previous nodes.
        """
        for new_id in new_ids:
            if new_id in self.data:
                self.tracking.add(new_id)


class Node:
    """Basic unit of an execution flow."""

    _name_gen = itertools.count()  # Incremental name used in building graphviz.

    def __init__(self, frame_id: Union[FrameID, Tuple[int, ...]], **kwargs):
        if isinstance(frame_id, FrameID):
            self.frame_id = frame_id
        elif isinstance(frame_id, tuple):
            self.frame_id = FrameID(frame_id)
        self.prev = None
        self.next = None
        self.step_into = None
        self.returned_from = None
        self.metadata = TrackingMetadata(**kwargs)
        self.name = str(next(self._name_gen))
        # TODO: record function name for call node

    def __getattr__(self, name):
        """Redirects attributes and calls to metadata.

        __getattr__ is only called when name is not in node's __dict__.
        """
        return getattr(self.metadata, name)

    def __repr__(self):
        return str(self.metadata)

    def is_callsite(self):
        return self.step_into is not None

    def build_relation(self, **relation_dict: Dict[str, "Node"]):
        """A convenient function to add relations at once.

        Usage:
            node.build_relation(prev=node_x, next=node_y)
        """
        for relation_name, node in relation_dict.items():
            if relation_name not in {"prev", "next", "step_into", "returned_from"}:
                raise Exception("wrong relation_name: " + relation_name)
            setattr(self, relation_name, node)

    def get_and_update_var_changes(
        self, other: "Node"
    ) -> Iterable[Union[VarModification, VarAppearance]]:
        """Gets variable changes and stores them to current node.

        current and next are guaranteed to be in the same frame.
        """
        assert self.frame_id == other.frame_id
        for var_id in other.tracking:
            old_value = self.data.get(var_id, _dummy)
            new_value = other.data[var_id]
            if old_value is _dummy:
                var_appearance = VarAppearance(id=var_id, value=new_value)
                self.add_var_appearances(var_appearance)
                yield var_appearance
            elif utils.has_diff(new_value, old_value):
                var_modification = VarModification(var_id, old_value, new_value)
                self.add_var_modifications(var_modification)
                yield var_modification

    def update_var_changes_before_return(self):
        """Compares data with data_before_return, records changes."""
        if self.data_before_return is None:
            pass
        for var_id in self.tracking:
            old_value = self.data.get(var_id, _dummy)
            new_value = self.data_before_return[var_id]
            if old_value is _dummy:
                var_appearance = VarAppearance(id=var_id, value=new_value)
                self.add_var_appearances(var_appearance)
            elif utils.has_diff(new_value, old_value):
                var_modification = VarModification(var_id, old_value, new_value)
                self.add_var_modifications(var_modification)


_dummy = object()


class Flow:
    """Class that represents program's execution.

    A flow consists of multiple Calls and Nodes.
    """

    ROOT = object()

    def __init__(self, start: Node, target: Node):
        self.start = start
        self.start.prev = self.ROOT
        self.target = target
        self._update_target_id()

    def _update_target_id(self) -> ID:
        """Gets ID('x') out of cyberbrain.register(x)."""
        register_call_ast = ast.parse(self.target.code_str.strip())
        assert register_call_ast.body[0].value.func.value.id == "cyberbrain"

        # Finds the target identifier by checking argument passed to register().
        # Assuming argument is a single identifier.
        self.target.add_tracking(
            ID(register_call_ast.body[0].value.args[0].id, self.target.frame_id)
        )
