from loguru import logger
from rich.highlighter import ReprHighlighter
from rich.pretty import Pretty, is_expandable

from flexget import options, plugin
from flexget.event import event
from flexget.terminal import TerminalTable, console

logger = logger.bind(name='dump')


def dump(entries, debug=False, eval_lazy=False, trace=False, title_only=False):
    """
    Dump *entries* to stdout

    :param list entries: Entries to be dumped.
    :param bool debug: Print non printable fields as well.
    :param bool eval_lazy: Evaluate lazy fields.
    :param bool trace: Display trace information.
    :param bool title_only: Display only title field
    """

    def sort_key(field):
        # Sort certain fields above the rest
        if field == 'title':
            return (0,)
        if field == 'url':
            return (1,)
        if field == 'original_url':
            return (2,)
        return 3, field

    highlighter = ReprHighlighter()

    for entry in entries:
        entry_table = TerminalTable(
            'field', ':', 'value',
            show_header=False,
            show_edge=False,
            pad_edge=False,
            collapse_padding=True,
            box=None,
            padding=0,
        )
        for field in sorted(entry, key=sort_key):
            if field.startswith('_') and not debug:
                continue
            if title_only and field != 'title':
                continue
            if entry.is_lazy(field) and not eval_lazy:
                value = '<LazyField - value will be determined when it is accessed>'
            else:
                try:
                    value = entry[field]
                except KeyError:
                    value = '<LazyField - lazy lookup failed>'
            if field.rsplit('_', maxsplit=1)[-1] == 'url':
                renderable = f'[link={value}][repr.url]{value}[/repr.url][/link]'
            elif isinstance(value, str):
                renderable = value.replace('\r', '').replace('\n', '')
            elif is_expandable(value):
                renderable = Pretty(value)
            else:
                try:
                    renderable = highlighter(str(value))
                except Exception:
                    renderable = f'[[i]not printable[/i]] ({repr(value)})'
            entry_table.add_row(f'{field}', ': ', renderable)
        console(entry_table)
        if trace:
            console('── Processing trace:', style='italic')
            trace_table = TerminalTable(
                'Plugin', 'Operation', 'Message',
                show_edge=False,
                pad_edge=False,
            )
            for item in entry.traces:
                trace_table.add_row(item[0], '' if item[1] is None else item[1], item[2])
            console(trace_table)
        if not title_only:
            console('')


class OutputDump:
    """
    Outputs all entries to console
    """

    schema = {'type': 'boolean'}

    @plugin.priority(0)
    def on_task_output(self, task, config):
        if not config and task.options.dump_entries is None:
            return

        eval_lazy = 'eval' in task.options.dump_entries
        trace = 'trace' in task.options.dump_entries
        title = 'title' in task.options.dump_entries
        states = ['accepted', 'rejected', 'failed', 'undecided']
        dumpstates = [s for s in states if s in task.options.dump_entries]
        specificstates = dumpstates
        if not dumpstates:
            dumpstates = states
        undecided = [entry for entry in task.all_entries if entry.undecided]
        if 'undecided' in dumpstates:
            console.rule('Undecided', style='gray')
            if undecided:
                dump(undecided, task.options.debug, eval_lazy, trace, title)
            elif specificstates:
                console('No undecided entries', style='italic')
        if 'accepted' in dumpstates:
            console.rule('Accepted', style='green')
            if task.accepted:
                dump(task.accepted, task.options.debug, eval_lazy, trace, title)
            elif specificstates:
                console('No accepted entries', style='italic')
        if 'rejected' in dumpstates:
            console.rule('Rejected', style='red')
            if task.rejected:
                dump(task.rejected, task.options.debug, eval_lazy, trace, title)
            elif specificstates:
                console('No rejected entries', style='italic')
        if 'failed' in dumpstates:
            console.rule('Failed', style='yellow')
            if task.failed:
                dump(task.failed, task.options.debug, eval_lazy, trace, title)
            elif specificstates:
                console('No failed entries', style='italic')


@event('plugin.register')
def register_plugin():
    plugin.register(OutputDump, 'dump', builtin=True, api_ver=2)


@event('options.register')
def register_parser_arguments():
    options.get_parser('execute').add_argument(
        '--dump',
        nargs='*',
        choices=['eval', 'trace', 'accepted', 'rejected', 'undecided', 'failed', 'title'],
        dest='dump_entries',
        help=(
            'display all entries in task with fields they contain, '
            'use `--dump eval` to evaluate all lazy fields. Specify an entry '
            'state/states to only dump matching entries.'
        ),
    )
