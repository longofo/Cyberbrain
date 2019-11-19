"""Formats trace result to user friendly output."""

import html
import itertools
from os.path import abspath, basename, expanduser, join
from typing import List

from graphviz import Digraph

from . import utils
from .flow import Flow, Node

DESKTOP = abspath(join(expanduser("~"), "Desktop"))

# g = Digraph(
#     name="Cyberbrain Output", graph_attr=[("forcelabels", "true")], format="canon"
# )
g = Digraph(name="Cyberbrain Output")

# Color comes from https://paletton.com/#uid=35d0u0kkuDpafTcfVLxoCwpucs+.
g.attr("edge", color="#E975B0", penwidth="2")


class NodeView:
    """Class that wraps a node and deal with visualization."""

    _portname_cache = {}  # Maps node address to their node to avoid duplicates.
    _incrementor = itertools.count()  # Generates 1, 2, 3, ...

    def __init__(self, node: Node):
        self._node = node
        self._portname = self._generate_portname()

    def __getattr__(self, name):
        """Redirects attribute access to stored node."""
        return getattr(self._node, name)

    def _generate_portname(self):
        node_addr = id(self._node)
        if node_addr not in self._portname_cache:
            self._portname_cache[node_addr] = str(next(self._incrementor))
        return self._portname_cache[node_addr]

    @property
    def portname(self):
        return self._portname

    @property
    def tracking(self):
        """Tracking does not necessarily need to be displayed."""
        return str(self._node.tracking)

    @property
    def var_changes(self):
        """Formats var changes."""
        output = ""
        for ap in self.var_appearances:
            output += f"{ap.id} = {ap.value}\n"

        for mod in self.var_modifications:
            output += f"{mod.id} {mod.old_value} → {mod.new_value}\n"

        if self.is_target:
            output += "🐸🐸🐸🐸🐸🐸"

        if self.is_relevant_return:
            output += f"return {self.return_value}"

        return output

    @property
    def next(self):
        return NodeView(self._node.next) if self._node.next else None

    @property
    def step_into(self):
        return NodeView(self._node.step_into) if self._node.step_into else None

    @property
    def returned_from(self):
        return NodeView(self._node.returned_from) if self._node.returned_from else None


def generate_subgraph(frame_start: NodeView):
    current = frame_start
    name = str(frame_start.frame_id) + "_code"
    lines: List[str] = []
    while current is not None:
        if not current.shown_in_output:
            current = current.next
            continue
        # Syntax hilight is very hard in Graphviz, because modern highlighters don't use
        # the deprecated <font color=...> way but class and css, which is not supported
        # in Graphviz.
        lines.append(
            utils.dedent(
                f"""<tr><td align='left' port='{current.portname}'>
                            <font face='Consolas'>
                                {html.escape(current.code_str)}
                            </font>
                        </td>"
                        <td align='left' sides='b' border='1' color='grey' bgcolor='#F9FE80'>
                            ◤&nbsp;{html.escape(current.var_changes)}
                        </td>
                    </tr>
                """
            )
        )
        if current.is_callsite:
            g.edge(
                f"{name}:{current.portname}",
                generate_subgraph(current.step_into),
                # TODO: Ideally this should be param → argument expression,
                # e.g. x → [1, 2, 3], so that users can see the passed value.
                label="\n".join(
                    f" {','.join(args)} → {param}, {current.step_into.vars[param]}"
                    for param, args in current.param_to_arg.items()
                ),
            )
        current = current.next
    rows = (
        [
            utils.dedent(
                f"""<tr><td sides='b' border='1' align='left' colspan='2' color='grey'>
                            <font color='#0AB127'>
                                {html.escape(
                                    basename(frame_start.source_location.filepath))}
                                : {html.escape(frame_start.frame_id.co_name)}
                            </font>
                        </td>
                    </tr>
                """
            )
        ]
        + lines
    )
    g.node(
        name,
        label="<<table cellspacing='0' cellborder='0'>%s</table>>" % "".join(rows),
        shape="plaintext",
    )
    return name


def generate_output(flow: Flow, filename=None):
    generate_subgraph(NodeView(flow.start))
    # print(g.pipe().decode("utf-8"))
    g.render(join(DESKTOP, filename or "output"), view=True)
