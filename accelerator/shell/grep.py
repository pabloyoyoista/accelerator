############################################################################
#                                                                          #
# Copyright (c) 2017 eBay Inc.                                             #
# Modifications copyright (c) 2019-2022 Carl Drougge                       #
# Modifications copyright (c) 2020 Anders Berkeman                         #
#                                                                          #
# Licensed under the Apache License, Version 2.0 (the "License");          #
# you may not use this file except in compliance with the License.         #
# You may obtain a copy of the License at                                  #
#                                                                          #
#  http://www.apache.org/licenses/LICENSE-2.0                              #
#                                                                          #
# Unless required by applicable law or agreed to in writing, software      #
# distributed under the License is distributed on an "AS IS" BASIS,        #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. #
# See the License for the specific language governing permissions and      #
# limitations under the License.                                           #
#                                                                          #
############################################################################

# grep in a dataset(chain)

from __future__ import division, print_function

import sys
import re
import os
from itertools import chain, repeat, cycle
from collections import deque, defaultdict
from argparse import RawTextHelpFormatter, Action, SUPPRESS, ArgumentError
from multiprocessing import Lock
import json
import datetime
import operator
import signal

from accelerator.compat import unicode, izip, PY2
from accelerator.compat import monotonic
from accelerator.compat import QueueEmpty
from accelerator.colourwrapper import colour
from .parser import name2ds, ArgumentParser
from accelerator.error import NoSuchWhateverError
from accelerator.extras import DotDict
from accelerator import g
from accelerator import mp


# os.write can't be trusted to write everything
def write(fd, data):
	while data:
		data = data[os.write(fd, data):]


def main(argv, cfg):
	# -C overrides -A and -B (which in turn override -C)
	class ContextAction(Action):
		def __call__(self, parser, namespace, values, option_string=None):
			namespace.before_context = namespace.after_context = values

	# Default values for tab_length
	tab_length = DotDict(tab_len=8, field_len=16, min_len=2)

	class TabLengthAction(Action):
		def __call__(self, parser, namespace, values, option_string=None):
			names = {}
			for suffix in ('_length', 'length', '_len', 'len', ''):
				for name in ('tab', 'field', 'min'):
					names[name + suffix] = name + '_len'
			min_value = dict(tab_len=1, field_len=0, min_len=1)
			order = ['tab_len', 'field_len', 'min_len']
			unnamed = cycle(order)
			values = re.split(r'[/, ]', values)
			if len(values) == 1 and values[0] and '=' not in values[0]:
				# only a single number provided: skip defaults and be traditional
				tab_length.field_len = 0
				tab_length.min_len = 1
			for value in values:
				if not value:
					continue # so you can do -T/ or similar to just activate it
				if '=' in value:
					name, value = value.rsplit('=', 1)
					name = name.lower()
				else:
					name = next(unnamed)
				if name not in names:
					raise ArgumentError(self, 'unknown field %r' % (name,))
				name = names[name]
				try:
					value = int(value)
				except ValueError:
					raise ArgumentError(self, 'invalid int value for %s: %r' % (name, value,))
				if value < min_value[name] or value > 9999:
					raise ArgumentError(self, 'invalid value for %s: %d' % (name, value,))
				tab_length[name] = value
			# -T overrides -t
			namespace.separator = None
			namespace.tab_length = True

	class SeparatorAction(Action):
		def __call__(self, parser, namespace, values, option_string=None):
			# -t overrides -T
			namespace.tab_length = False
			namespace.separator = values

	parser = ArgumentParser(
		usage="%(prog)s [options] [-e] pattern [...] [-d] ds [...] [[-n] column [...]]",
		description="""positional arguments:
  pattern               (-e, --regexp)
  dataset               (-d, --dataset) can be specified as for "ax ds"
  columns               (-n, --column)""",
		prog=argv.pop(0),
		formatter_class=RawTextHelpFormatter,
	)
	parser.add_argument('-c', '--chain',        action='store_true', negation='dont', help="follow dataset chains", )
	parser.add_argument(      '--chain-length', '--cl', metavar='LENGTH', action='append', type=int, help="follow chains at most this many datasets")
	parser.add_argument(      '--stop-ds',      action='append', metavar='DATASET',   help="follow chains at most to this dataset\nthese options work like in ds.chain() and you can\nspecify them several times (for several datasets)")
	parser.add_argument(      '--colour', '--color', nargs='?', const='always', choices=['auto', 'never', 'always'], type=str.lower, help="colour matched text. can be auto, never or always", metavar='WHEN', )
	parser.add_argument(      '--no-colour', '--no-color', action='store_const', const='never', dest='colour', help=SUPPRESS)
	parser.add_argument(      '--lined',        action='store_true', negation='not',  help="alternate line colour", )
	parser.add_argument('-i', '--ignore-case',  action='store_true', negation='dont', help="case insensitive pattern", )
	parser.add_argument('-v', '--invert-match', action='store_true', negation='dont', help="select non-matching lines", )
	parser.add_argument('-o', '--only-matching',action='store_true', negation='not',  help="only print matching part (or columns with -l)", )
	parser.add_argument('-l', '--list-matching',action='store_true', negation='dont', help="only print matching datasets (or slices with -S)\nwhen used with -o, only print matching columns", )
	parser.add_argument('-H', '--headers',      action='store_true', negation='no',   help="print column names before output (and on each change)", )
	parser.add_argument('-O', '--ordered',      action='store_true', negation='not',  help="output in order (one slice at a time)", )
	parser.add_argument('-M', '--allow-missing-columns', action='store_true', negation='dont', help="datasets are allowed to not have (some) columns", )
	parser.add_argument('-g', '--grep',         action='append',                      help="grep this column only, can be specified multiple times", metavar='COLUMN')
	parser.add_argument('-s', '--slice',        action='append',                      help="grep this slice only, can be specified multiple times",  type=int)
	parser.add_argument('-D', '--show-dataset', action='store_true', negation='dont', help="show dataset on matching lines", )
	parser.add_argument('-S', '--show-sliceno', action='store_true', negation='dont', help="show sliceno on matching lines", )
	parser.add_argument('-L', '--show-lineno',  action='store_true', negation='dont', help="show lineno (per slice) on matching lines", )
	supported_formats = ('csv', 'raw', 'json',)
	parser.add_argument('-f', '--format', default='csv', choices=supported_formats, help="output format, csv (default) / " + ' / '.join(supported_formats[1:]), metavar='FORMAT', )
	parser.add_argument('-t', '--separator', action=SeparatorAction, help="field separator, default tab / tab-like spaces", )
	parser.add_argument('-T', '--tab-length', metavar='LENGTH', action=TabLengthAction,
		help="field alignment, always uses spaces as separator\n" +
		     "specify as many as you like of TABLEN/FIELDLEN/MINLEN\n" +
		     "or as NAME=VALUE (e.g. \"min=3/field=24\")\n" +
		     "TABLEN works like normal tabs\n" +
		     "FIELDLEN sets a longer minimum between fields\n" +
		     "MINLEN sets a minimum len for all separators\n" +
		     "use \"-T/\" to just activate it (sets %d/%d/%d)" % (tab_length.tab_len, tab_length.field_len, tab_length.min_len,)
	)
	parser.add_argument('-B', '--before-context', type=int, default=0, metavar='NUM', help="print NUM lines of leading context", )
	parser.add_argument('-A', '--after-context',  type=int, default=0, metavar='NUM', help="print NUM lines of trailing context", )
	parser.add_argument('-C', '--context',        type=int, default=0, metavar='NUM', action=ContextAction,
		help="print NUM lines of context\n" +
		     "context is only taken from the same slice of the same\n" +
		     "dataset, and may intermix with output from other\n" +
		     "slices. Use -O to avoid that, or -S -L to see it.",
	)
	parser.add_argument('-e', '--regexp',  default=[], action='append', dest='patterns', help=SUPPRESS)
	parser.add_argument('-d', '--dataset', default=[], action='append', dest='datasets', help=SUPPRESS)
	parser.add_argument('-n', '--column',  default=[], action='append', dest='columns', help=SUPPRESS)
	parser.add_argument('words', nargs='*', help=SUPPRESS)
	args = parser.parse_intermixed_args(argv)

	if args.before_context < 0 or args.after_context < 0:
		print('Context must be >= 0', file=sys.stderr)
		return 1

	columns = args.columns

	try:
		args.datasets = [name2ds(cfg, ds) for ds in args.datasets]
	except NoSuchWhateverError as e:
		print(e, file=sys.stderr)
		return 1

	for word in args.words:
		if not args.patterns:
			args.patterns.append(word)
		elif columns and args.datasets:
			columns.append(word)
		else:
			try:
				args.datasets.append(name2ds(cfg, word))
			except NoSuchWhateverError as e:
				if not args.datasets:
					print(e, file=sys.stderr)
					return 1
				columns.append(word)

	if not args.patterns or not args.datasets:
		parser.print_help(file=sys.stderr)
		return 1

	datasets = args.datasets
	patterns = []
	re_flags = re.DOTALL
	if args.ignore_case:
		re_flags |= re.IGNORECASE
	for pattern in args.patterns:
		try:
			patterns.append(re.compile(pattern, re_flags))
		except re.error as e:
			print("Bad pattern %r:\n%s" % (pattern, e,), file=sys.stderr)
			return 1

	grep_columns = set(args.grep or ())
	if grep_columns == set(columns):
		grep_columns = set()

	if args.slice:
		want_slices = []
		for s in args.slice:
			assert 0 <= s < g.slices, "Slice %d not available" % (s,)
			if s not in want_slices:
				want_slices.append(s)
	else:
		want_slices = list(range(g.slices))

	if len(want_slices) == 1:
		# it will be automatically ordered, so let's not work for it.
		args.ordered = False

	if args.only_matching:
		if args.list_matching:
			args.list_matching = False
			only_matching = 'columns'
		else:
			only_matching = 'part'
	else:
		only_matching = False

	if args.chain:
		datasets = list(chain.from_iterable(
			ds.chain(length=length, stop_ds=stop_ds)
			for ds, length, stop_ds in zip(
				datasets,
				cycle(args.chain_length or [-1]),
				cycle(args.stop_ds or [None]),
			)
		))

	def columns_for_ds(ds, columns=columns):
		if columns:
			return [n for n in columns if n in ds.columns]
		else:
			return sorted(ds.columns)

	if columns or grep_columns:
		if args.allow_missing_columns:
			keep_datasets = []
			for ds in datasets:
				if not columns_for_ds(ds):
					continue
				if grep_columns and not columns_for_ds(ds, grep_columns):
					continue
				keep_datasets.append(ds)
			if not keep_datasets:
				return 0
			datasets = keep_datasets
		else:
			bad = False
			need_cols = set(columns)
			if grep_columns:
				need_cols.update(grep_columns)
			for ds in datasets:
				missing = need_cols - set(ds.columns)
				if missing:
					print('ERROR: %s does not have columns %r' % (ds, missing,), file=sys.stderr)
					bad = True
			if bad:
				return 1

	# For the status reporting, this gives how many lines have been processed
	# when reaching each ds ix, per slice. Ends with an extra fictional ds,
	# i.e. the total number of lines for that slice. And then the same again,
	# to simplify the code in the status shower.
	total_lines_per_slice_at_ds = [[0] * g.slices]
	for ds in datasets:
		total_lines_per_slice_at_ds.append([a + b for a, b in zip(total_lines_per_slice_at_ds[-1], ds.lines)])
	total_lines_per_slice_at_ds.append(total_lines_per_slice_at_ds[-1])
	status_interval = {
		# twice per percent, but not too often or too seldom
		sliceno: min(max(total_lines_per_slice_at_ds[-1][sliceno] // 200, 10), 5000)
		for sliceno in want_slices
	}

	# never and always override env settings, auto (default) sets from env/tty
	if args.colour == 'never':
		colour.disable()
		highlight_matches = False
	elif args.colour == 'always':
		colour.enable()
		highlight_matches = True
	else:
		args.colour = 'auto'
		highlight_matches = colour.enabled

	# Don't highlight everything when just trying to cat
	if args.patterns == ['']:
		highlight_matches = False
	# Don't highlight anything with -l
	if args.list_matching:
		highlight_matches = False

	if args.format == 'json':
		# headers was just a mistake, ignore it
		args.headers = False

	separator = args.separator
	if args.tab_length:
		separator = None
	elif separator is None and not sys.stdout.isatty():
		separator = '\t'

	if separator is None:
		# special case where we try to be like a tab, but with spaces.
		# this is useful because terminals typically don't style tabs.
		# and also so you can use smarter tabs and change the length.
		def separate(items, lens, tab_len=tab_length.tab_len, field_len=tab_length.field_len, min_len=tab_length.min_len):
			things = []
			current_pos = min_pos = 0
			for item, item_len in zip(items, lens):
				things.append(item)
				current_pos += item_len
				min_pos += field_len
				spaces = max(min_pos - current_pos, min_len)
				align = (current_pos + spaces) % tab_len
				if align:
					spaces += tab_len - align
				things.append(colour(' ' * spaces, 'grep/separator'))
				current_pos += spaces
			return ''.join(things[:-1])
		separator = '\t'
	else:
		separator_coloured = colour(separator, 'grep/separator')
		def separate(items, lens):
			return separator_coloured.join(items)

	def json_default(obj):
		if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
			return str(obj)
		elif isinstance(obj, complex):
			return [obj.real, obj.imag]
		else:
			return repr(obj)

	if args.format == 'csv':
		def escape_item(item):
			if item and (separator in item or item[0] in '\'"' or item[-1] in '\'"'):
				return '"' + item.replace('\n', '\\n').replace('"', '""') + '"'
			else:
				return item.replace('\n', '\\n')
		errors = 'surrogatepass'
	else:
		if args.format == 'raw' and args.lined:
			# this will be reversed inside the liner process
			def escape_item(item):
				return item.replace('\\', '\\\\').replace('\n', '\\n')
		else:
			escape_item = None
		errors = 'replace' if PY2 else 'surrogateescape'

	# This is for the ^T handling. Each slice sends an update when finishing
	# a dataset, and every status_interval[sliceno] lines while iterating.
	# To minimise the data sent the only information sent over the queue
	# is (sliceno, finished_dataset).
	# Status printing is triggered by ^T (or SIGINFO if that is available)
	# or by SIGUSR1.
	# Pressing it again within two seconds prints stats per slice too.
	q_status = mp.LockFreeQueue()
	def status_collector():
		q_status.make_reader()
		status = {sliceno: [0, 0] for sliceno in want_slices}
		#            [ds_ix, done_lines]
		total_lines = sum(total_lines_per_slice_at_ds[-1])
		previous = [0]
		# base colour conf in if stderr is a tty, not stdout.
		if args.colour == 'auto':
			colour.configure_from_environ(stdout=sys.stderr)
		def show(sig, frame):
			t = monotonic()
			verbose = (previous[0] + 2 > t) # within 2 seconds of previous
			previous[0] = t
			ds_ixes = []
			progress_lines = []
			progress_fraction = []
			for sliceno in want_slices:
				ds_ix, done_lines = status[sliceno]
				ds_ixes.append(ds_ix)
				max_possible = min(done_lines + status_interval[sliceno], total_lines_per_slice_at_ds[ds_ix + 1][sliceno])
				done_lines = (done_lines + max_possible) / 2 # middle of the possibilities
				progress_lines.append(done_lines)
				total = total_lines_per_slice_at_ds[-1][sliceno]
				if total == 0:
					progress_fraction.append(1)
				else:
					progress_fraction.append(done_lines / total)
			progress_total = sum(progress_lines) / (total_lines or 1)
			bad_cutoff = progress_total - 0.1
			if verbose:
				show_ds = (len(datasets) > 1 and min(ds_ixes) != max(ds_ixes))
				for sliceno, ds_ix, p in zip(want_slices, ds_ixes, progress_fraction):
					if ds_ix == len(datasets):
						msg = 'DONE'
					else:
						msg = '{0:d}% of {1:n} lines'.format(round(p * 100), total_lines_per_slice_at_ds[-1][sliceno])
						if show_ds:
							msg = '%s (in %s)' % (msg, datasets[ds_ix].quoted,)
					msg = '%9d: %s' % (sliceno, msg,)
					if p < bad_cutoff:
						msg = colour(msg, 'grep/infohighlight')
					else:
						msg = colour(msg, 'grep/info')
					write(2, msg.encode('utf-8') + b'\n')
			msg = '{0:d}% of {1:n} lines'.format(round(progress_total * 100), total_lines)
			if len(datasets) > 1:
				min_ds = min(ds_ixes)
				max_ds = max(ds_ixes)
				if min_ds < len(datasets):
					ds_name = datasets[min_ds].quoted
					extra = '' if min_ds == max_ds else ' ++'
					msg = '%s (in %s%s)' % (msg, ds_name, extra,)
			worst = min(progress_fraction)
			if worst < bad_cutoff:
				msg = '%s, worst %d%%' % (msg, round(worst * 100),)
			msg = colour('  SUMMARY: %s' % (msg,), 'grep/info')
			write(2, msg.encode('utf-8') + b'\n')
		for signame in ('SIGINFO', 'SIGUSR1'):
			if hasattr(signal, signame):
				sig = getattr(signal, signame)
				signal.signal(sig, show)
				if hasattr(signal, 'pthread_sigmask'):
					signal.pthread_sigmask(signal.SIG_UNBLOCK, {sig})
		tc_original = None
		using_stdin = False
		if not hasattr(signal, 'SIGINFO') and sys.stdin.isatty():
			# ^T wont work automatically on this OS, so we need to handle it as terminal input
			import termios
			from accelerator.compat import selectors
			sel = selectors.DefaultSelector()
			sel.register(0, selectors.EVENT_READ)
			sel.register(q_status.r, selectors.EVENT_READ)
			try:
				tc_original = termios.tcgetattr(0)
				tc_changed = list(tc_original)
				tc_changed[3] &= ~(termios.ICANON | termios.IEXTEN)
				termios.tcsetattr(0, termios.TCSADRAIN, tc_changed)
				using_stdin = True
			except Exception:
				pass
			# we can't set stdin nonblocking, because it's probably the same
			# file description as stdout, so work around that with alarms.
			def got_alarm(sig, frame):
				raise IOError()
			signal.signal(signal.SIGALRM, got_alarm)
		try:
			while True:
				if using_stdin:
					do_q = False
					for key, _ in sel.select():
						if key.fd == 0:
							try:
								signal.alarm(1) # in case something else read it we block for max 1 second
								try:
									pressed = ord(os.read(0, 1))
								finally:
									signal.alarm(0)
								if pressed == 20:
									write(2, b'\n') # "^T" shows in the terminal
									os.kill(os.getpid(), signal.SIGUSR1)
							except Exception:
								pass
						elif key.fd == q_status.r:
							do_q = True
					if not do_q:
						continue
				try:
					sliceno, finished_dataset = q_status.get()
				except QueueEmpty:
					return
				if finished_dataset:
					ds_ix = status[sliceno][0] + 1
					status[sliceno] = [ds_ix, total_lines_per_slice_at_ds[ds_ix][sliceno]]
				else:
					status[sliceno][1] += status_interval[sliceno]
		finally:
			if tc_original is not None:
				try:
					termios.tcsetattr(0, termios.TCSADRAIN, tc_original)
				except Exception:
					pass
	status_process = mp.SimplifiedProcess(target=status_collector, name='ax grep status')
	children = [status_process]
	# everything else will write, so make it a writer right away
	q_status.make_writer()

	# Output is only allowed while holding this lock, so that long lines
	# do not get intermixed. (Or when alone in producing output.)
	io_lock = Lock()

	# This contains some extra stuff to be a better base for the other
	# outputters.
	# When used directly it enforces no ordering, but merges smaller writes
	# to keep the number of syscalls down.

	class Outputter:
		def __init__(self, q_in, q_out):
			self.q_in = q_in
			self.q_out = q_out
			self.buffer = []
			self.merge_buffer = b''

		def put(self, data):
			self.merge_buffer += data
			if len(self.merge_buffer) >= 1024:
				self.move_merge()

		def move_merge(self):
			if self.merge_buffer:
				with io_lock:
					write(1, self.merge_buffer)
				self.merge_buffer = b''

		def start(self, ds):
			pass

		def end(self, ds):
			self.move_merge()

		def finish(self):
			pass

		def full(self):
			return len(self.buffer) > 5000

		def excite(self):
			self.move_merge()
			if self.buffer:
				self.pump(False)

	# Partially ordered output, each header change acts as a fence.
	# This is used in all slices except the first.
	#
	# The queue gets True when the previous slice is ready for the next
	# header change, and None when the header is printed (and it's ok
	# to resume output).

	class HeaderWaitOutputter(Outputter):
		def start(self, ds):
			expect_ds, show = next(show_headers_here)
			assert ds == expect_ds
			if show:
				self.add_wait()
			else:
				self.excite()

		def add_wait(self):
			# Each sync point is separated by None in the buffer
			self.buffer.append(None)
			self.buffer.append(b'') # Avoid need for special case in .drain
			self.pump()

		def move_merge(self):
			data = self.merge_buffer
			self.merge_buffer = b''
			if self.buffer:
				self.pump()
				if self.buffer:
					self.buffer.append(data)
					return
			with io_lock:
				write(1, data)

		def pump(self, wait=None):
			if wait is None:
				wait = self.full()
			try:
				got = self.q_in.get(wait)
			except QueueEmpty:
				if wait:
					# previous slice has exited without sending all messages
					raise
				return
			if got is True:
				# since pump is only called when we have outputted all
				# currently allowed output or when the next message is an
				# unblock for such output we can just unconditionally send
				# the True on to the next slice here.
				self.q_out.put(True)
				self.pump(wait)
				return
			else:
				self.q_out.put(None)
				self.drain()

		def drain(self):
			assert self.buffer[0] is None, 'The buffer must always stop at a sync point (or empty)'
			with io_lock:
				for pos, data in enumerate(self.buffer[1:], 1):
					if data is None:
						break
					elif data:
						write(1, data)
				else:
					# We did not reach the next fence, so last item is real data
					# and needs to be removed. (The buffer will then be empty and
					# output will continue directly until reaching the sync point.)
					pos += 1
			self.buffer[:pos] = ()

		def finish(self):
			while self.buffer:
				self.pump(True)

	# Partially ordered output, each header change acts as a fence.
	# This is used only in the first slice, and outputs the headers.
	#
	# When it is ready to output headers it sends True in the queue.
	# When the True has travelled around the queue ring all slices are
	# ready, the headers are printed, and None is sent to let the other
	# slices resume output.
	# (When the None returns it is ignored, because output is resumed
	# as soon as the headers are printed.)

	class HeaderOutputter(HeaderWaitOutputter):
		def add_wait(self):
			if not self.buffer:
				self.q_out.put(True)
			self.buffer.append(None)
			self.buffer.append(b'') # Avoid need for special case in .drain/.put
			self.pump()

		def drain(self):
			assert self.buffer[0] is None, 'The buffer must always stop at a sync point (or empty)'
			with io_lock:
				for pos, data in enumerate(self.buffer[1:], 1):
					if data is None:
						self.q_out.put(True)
						break
					elif data:
						write(1, data)
				else:
					pos += 1
			self.buffer[:pos] = ()

		def pump(self, wait=None):
			if wait is None:
				wait = self.full()
			try:
				got = self.q_in.get(wait)
			except QueueEmpty:
				if wait:
					# previous slice has exited without sending all messages
					raise
				return
			if got is True:
				# The True we put in when reaching the fence has travelled
				# all the way around the queue ring, it's time to print the
				# new headers
				write(1, next(headers_iter))
				# and then unblock the other slices
				self.q_out.put(None)
				self.drain()
				# No else, when the None comes back we just drop it.
			if not wait:
				self.pump(False)

	# Fully ordered output, each slice waits for the previous slice.
	# For each ds, waits for None (anything really) before starting,
	# sends None when done.

	class OrderedOutputter(Outputter):
		def start(self, ds):
			# Each ds is separated by None in the buffer
			self.buffer.append(None)
			self.buffer.append(b'') # Avoid need for special case in .drain
			self.pump()

		def end(self, ds):
			self.move_merge()
			if not self.buffer:
				# We are done with this ds, so let next slice continue
				self.q_out.put(None)

		def pump(self, wait=None):
			if wait is None:
				wait = self.full()
			try:
				self.q_in.get(wait)
			except QueueEmpty:
				if wait:
					# previous slice has exited without sending all messages
					raise
				return
			self.drain()

		def move_merge(self):
			data = self.merge_buffer
			self.merge_buffer = b''
			if self.buffer:
				self.pump()
				if self.buffer:
					self.buffer.append(data)
					return
			# No need for a lock, the other slices aren't writing concurrently.
			write(1, data)

		def drain(self):
			assert self.buffer[0] is None
			for pos, data in enumerate(self.buffer[1:], 1):
				if data is None:
					# We are done with this ds, so let next slice continue
					self.q_out.put(None)
					break
				elif data:
					write(1, data)
			else:
				# We did not reach the next ds, so last item is real data and
				# needs to be removed. (The buffer will then be empty and
				# output will continue directly until reaching the next ds.)
				pos += 1
			self.buffer[:pos] = ()

		def finish(self):
			not_finished = bool(self.buffer)
			while self.buffer:
				self.pump(True)
			if not_finished:
				self.q_out.put(None)

	# Same as above but for the first slice so it prints headers when needed.

	class OrderedHeaderOutputter(OrderedOutputter):
		def start(self, ds):
			# Each ds is separated by None in the buffer
			self.buffer.append(None)
			if headers:
				expect_ds, show = next(show_headers_here)
				assert ds == expect_ds
			else:
				show = False
			if show:
				# Headers changed, start with those.
				self.buffer.append(next(headers_iter))
			else:
				self.buffer.append(b'') # Avoid need for special case in .drain
			self.pump()

	# Choose the right outputter for the kind of sync we need.
	def outputter(q_in, q_out, first_slice=False):
		if args.list_matching:
			cls = Outputter
		elif args.ordered:
			if first_slice:
				cls = OrderedHeaderOutputter
			else:
				cls = OrderedOutputter
		elif headers:
			if first_slice:
				cls = HeaderOutputter
			else:
				cls = HeaderWaitOutputter
		else:
			cls = Outputter
		return cls(q_in, q_out)

	# Make printer for the selected output options
	def make_show(prefix, used_columns):
		def matching_ranges(item):
			ranges = []
			for p in patterns:
				ranges.extend(m.span() for m in p.finditer(item))
			if not ranges:
				return
			# merge overlapping/adjacent ranges
			ranges.sort()
			ranges = iter(ranges)
			start, stop = next(ranges)
			for a, b in ranges:
				if a <= stop:
					stop = max(stop, b)
				else:
					yield start, stop
					start, stop = a, b
			yield start, stop
		def filter_item(item):
			return ''.join(item[a:b] for a, b in matching_ranges(item))
		if args.format == 'json':
			dumps = json.JSONEncoder(ensure_ascii=False, default=json_default).encode
			def show(lineno, items):
				if only_matching == 'part':
					items = [filter_item(unicode(item)) for item in items]
				if only_matching == 'columns':
					d = {k: v for k, v in zip(used_columns, items) if filter_item(unicode(v))}
				else:
					d = dict(zip(used_columns, items))
				if args.show_lineno:
					prefix['lineno'] = lineno
				if prefix:
					prefix['data'] = d
					d = prefix
				return dumps(d).encode('utf-8', 'surrogatepass') + b'\n'
		else:
			def colour_item(item):
				pos = 0
				parts = []
				for a, b in matching_ranges(item):
					parts.extend((item[pos:a], colour(item[a:b], 'grep/highlight')))
					pos = b
				parts.append(item[pos:])
				return ''.join(parts)
			def show(lineno, items):
				data = list(prefix)
				if args.show_lineno:
					data.append(unicode(lineno))
				show_items = map(unicode, items)
				if only_matching:
					if only_matching == 'columns':
						show_items = (item if filter_item(item) else '' for item in show_items)
					else:
						show_items = map(filter_item, show_items)
				show_items = list(show_items)
				lens = (len(item) for item in data + show_items)
				if highlight_matches:
					show_items = list(map(colour_item, show_items))
				data.extend(show_items)
				if escape_item:
					lens_unesc = (len(item) for item in data)
					data = list(map(escape_item, data))
					lens_esc = (len(item) for item in data)
					lens = (l + esc - unesc for l, unesc, esc in zip(lens, lens_unesc, lens_esc))
				return separate(data, lens).encode('utf-8', errors) + b'\n'
		return show

	# This is called for each slice in each dataset.
	# Each slice has a separate process (the same for all datasets).
	# The first slice runs in the main process (unless -l), everything
	# else runs from one_slice.

	def grep(ds, sliceno, out):
		out.start(ds)
		if len(patterns) == 1:
			chk = patterns[0].search
		else:
			def chk(s):
				return any(p.search(s) for p in patterns)
		first = [True]
		def mk_iter(col):
			kw = {}
			if first[0]:
				first[0] = False
				lines = ds.lines[sliceno]
				if lines > status_interval[sliceno]:
					def cb(n):
						q_status.put((sliceno, False))
						out.excite()
					kw['callback'] = cb
					kw['callback_interval'] = status_interval[sliceno]
			if ds.columns[col].type == 'ascii':
				kw['_type'] = 'unicode'
			it = ds._column_iterator(sliceno, col, **kw)
			if ds.columns[col].type == 'bytes':
				errors = 'replace' if PY2 else 'surrogateescape'
				if ds.columns[col].none_support:
					it = (None if v is None else v.decode('utf-8', errors) for v in it)
				else:
					it = (v.decode('utf-8', errors) for v in it)
			return it
		used_columns = columns_for_ds(ds)
		used_grep_columns = grep_columns and columns_for_ds(ds, grep_columns)
		if grep_columns and set(used_grep_columns) != set(used_columns):
			grep_iter = izip(*(mk_iter(col) for col in used_grep_columns))
		else:
			grep_iter = repeat(None)
		lines_iter = izip(*(mk_iter(col) for col in used_columns))
		if args.before_context:
			before = deque((), args.before_context)
		else:
			before = None
		if args.format == 'json':
			prefix = {}
			if args.show_dataset:
				prefix['dataset'] = ds
			if args.show_sliceno:
				prefix['sliceno'] = sliceno
			show = make_show(prefix, used_columns)
		else:
			prefix = []
			if args.show_dataset:
				prefix.append(ds)
			if args.show_sliceno:
				prefix.append(str(sliceno))
			prefix = tuple(prefix)
			show = make_show(prefix, used_columns)
		if args.invert_match:
			maybe_invert = operator.not_
		else:
			maybe_invert = bool
		to_show = 0
		for lineno, (grep_items, items) in enumerate(izip(grep_iter, lines_iter)):
			if maybe_invert(any(chk(unicode(item)) for item in grep_items or items)):
				if q_list:
					q_list.put((ds, sliceno))
					return
				while before:
					out.put(show(*before.popleft()))
				to_show = 1 + args.after_context
			if to_show:
				out.put(show(lineno, items))
				to_show -= 1
			elif before is not None:
				before.append((lineno, items))
		out.end(ds)

	# This runs in a separate process for each slice except the first
	# one (unless -l), which is handled specially in the main process.

	def one_slice(sliceno, q_in, q_out, q_to_close):
		if q_to_close:
			q_to_close.close()
		if q_in:
			q_in.make_reader()
		if q_out:
			q_out.make_writer()
		if q_list:
			q_list.make_writer()
		try:
			out = outputter(q_in, q_out)
			for ds in datasets:
				if seen_list is None or ds not in seen_list:
					grep(ds, sliceno, out)
				q_status.put((sliceno, True))
			out.finish()
		except QueueEmpty:
			# some other process died, no need to print an error here
			sys.exit(1)

	headers_prefix = []
	if args.show_dataset:
		headers_prefix.append('[DATASET]')
	if args.show_sliceno:
		headers_prefix.append('[SLICE]')
	if args.show_lineno:
		headers_prefix.append('[LINE]')

	if args.lined:
		from .lined import enable_lines
		liner_process = enable_lines('grep', q_status.close, args.format == 'raw')
		if liner_process:
			children.append(liner_process)
		else:
			args.lined = False
			if args.format == 'raw':
				escape_item = None

	# [headers] for each ds where headers change (not including the first).
	# this is every ds where sync between slices has to happen when not --ordered.
	# which ds this is is stored in show_headers_here
	headers = []
	if args.headers:
		current_headers = None
		# [(ds, show_headers?)] for each ds in datasets.
		# records ds as well to uncover bugs (it's not really needed)
		show_headers_here = []
		for ds in datasets:
			candidate_headers = columns_for_ds(ds)
			if candidate_headers != current_headers:
				current_headers = candidate_headers
				headers.append(current_headers)
				show_headers_here.append((ds, True))
			else:
				show_headers_here.append((ds, False))
		def gen_headers(headers):
			show_items = headers_prefix + headers
			if escape_item:
				show_items = list(map(escape_item, show_items))
			coloured = (colour(item, 'grep/header') for item in show_items)
			txt = separate(coloured, map(len, show_items))
			return txt.encode('utf-8', 'surrogatepass') + b'\n'
		# remove the starting ds, so no header changes means no special handling.
		current_headers = headers.pop(0)
		if not args.list_matching:
			write(1, gen_headers(current_headers))
		headers_iter = iter(map(gen_headers, headers))
		show_headers_here[0] = (datasets[0], False) # first one is already printed
		show_headers_here = iter(show_headers_here)

	q_in = q_out = first_q_out = q_to_close = q_list = None
	seen_list = None
	if args.list_matching:
		# in this case all slices get their own process
		# and the main process just prints the maching slices
		q_list = mp.LockFreeQueue()
		separate_process_slices = want_slices
		if not args.show_sliceno:
			seen_list = mp.MpSet()
	else:
		separate_process_slices = want_slices[1:]
		if args.ordered or headers:
			# needs to sync in some way
			q_in = first_q_out = mp.LockFreeQueue()
	for sliceno in separate_process_slices:
		if q_in:
			q_out = mp.LockFreeQueue()
		p = mp.SimplifiedProcess(
			target=one_slice,
			args=(sliceno, q_in, q_out, q_to_close,),
			name='slice-%d' % (sliceno,),
		)
		children.append(p)
		if q_in and q_in is not first_q_out:
			q_in.close()
		q_to_close = first_q_out
		q_in = q_out
	if q_in:
		if q_in is first_q_out:
			# Special case: only one slice, but HeaderOutputter still needs queues.
			class LocalQueue:
				def __init__(self):
					self._lst = []
					self.put = self._lst.append
				def get(self, wait=None):
					if self._lst:
						return self._lst.pop(0)
					raise QueueEmpty()
			q_in = q_out = LocalQueue()
		else:
			q_out = first_q_out
			q_in.make_reader()
			q_out.make_writer()
		if args.ordered:
			q_in.put_local(None)
	del q_to_close
	del first_q_out

	try:
		if args.list_matching:
			if args.headers:
				headers_prefix = ['[DATASET]']
				if seen_list is None:
					headers_prefix.append('[SLICE]')
				write(1, gen_headers([]))
			ordered_res = defaultdict(set)
			q_list.make_reader()
			if seen_list is None:
				used_columns = ['dataset', 'sliceno']
			else:
				used_columns = ['dataset']
			inner_show = make_show({} if args.format == 'json' else [], used_columns)
			def show(ds, sliceno=None):
				if sliceno is None:
					items = [ds]
				else:
					items = [ds, sliceno]
				write(1, inner_show(None, items))
			while True:
				try:
					ds, sliceno = q_list.get()
				except QueueEmpty:
					break
				if seen_list is None:
					if args.ordered:
						ordered_res[ds].add(sliceno)
					else:
						show(ds, sliceno)
				elif ds not in seen_list:
					seen_list.add(ds)
					if not args.ordered:
						show(ds)
			if args.ordered:
				for ds in datasets:
					if seen_list is None:
						for sliceno in sorted(ordered_res[ds]):
							show(ds, sliceno)
					else:
						if ds in seen_list:
							show(ds)
		else:
			out = outputter(q_in, q_out, first_slice=True)
			sliceno = want_slices[0]
			for ds in datasets:
				grep(ds, sliceno, out)
				q_status.put((sliceno, True))
			out.finish()
	except QueueEmpty:
		# don't print an error, probably a subprocess died from EPIPE before
		# the main process. (or the subprocess already printed an error.)
		return 1

	q_status.close()
	os.close(1) # so the liner process will see EOF before we actually exit
	for c in children:
		c.join()
		if c.exitcode:
			return 1
