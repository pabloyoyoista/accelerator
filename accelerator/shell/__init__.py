############################################################################
#                                                                          #
# Copyright (c) 2019-2022 Carl Drougge                                     #
# Modifications copyright (c) 2020-2021 Anders Berkeman                    #
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

from __future__ import print_function
from __future__ import division
from __future__ import unicode_literals

import sys
import errno
import os
from os.path import dirname, basename, realpath, join
import locale
from glob import glob
import re
import shlex
import signal
from argparse import RawDescriptionHelpFormatter

from accelerator.colourwrapper import colour
from accelerator.error import UserError
from accelerator.shell.parser import ArgumentParser

cfg = None

def find_cfgs(basedir='.', wildcard=False):
	"""Find all accelerator.conf (or accelerator*.conf if wildcard=True)
	starting at basedir and continuing all the way to /, yielding them
	from the deepest directory first, starting with accelerator.conf (if
	present) and then the rest in sorted order."""

	cfgname = 'accelerator.conf'
	if wildcard:
		pattern = 'accelerator*.conf'
	else:
		pattern = cfgname
	orgdir = os.getcwd()
	basedir = realpath(basedir)
	while basedir != '/':
		try:
			os.chdir(basedir)
			fns = sorted(glob(pattern))
		finally:
			os.chdir(orgdir)
		if cfgname in fns:
			fns.remove(cfgname)
			fns.insert(0, cfgname)
		for fn in fns:
			yield join(basedir, fn)
		basedir = dirname(basedir)

def load_some_cfg(basedir='.', all=False):
	global cfg

	basedir = realpath(basedir)
	cfgs = find_cfgs(basedir, wildcard=all)
	if all:
		found_any = False
		# Start at the root, so closer cfgs override those further away.
		for fn in reversed(list(cfgs)):
			try:
				load_cfg(fn)
				found_any = True
			except Exception:
				# As long as we find at least one we're happy.
				pass
		if not found_any:
			raise UserError("Could not find 'accelerator*.conf' in %r or any of its parents." % (basedir,))
		cfg.config_filename = None
	else:
		try:
			fn = next(cfgs)
		except StopIteration:
			raise UserError("Could not find 'accelerator.conf' in %r or any of its parents." % (basedir,))
		load_cfg(fn)

def load_cfg(fn):
	global cfg

	from accelerator.configfile import load_config
	from accelerator.job import WORKDIRS

	cfg = load_config(fn)
	for k, v in cfg.workdirs.items():
		if WORKDIRS.get(k, v) != v:
			print("WARNING: %s overrides workdir %s" % (fn, k,), file=sys.stderr)
		WORKDIRS[k] = v
	return cfg

def unpath(path):
	while path in sys.path:
		sys.path.pop(sys.path.index(path))

def setup(config_fn=None, debug_cmd=False):
	try:
		locale.resetlocale()
	except locale.Error:
		print("WARNING: Broken locale", file=sys.stderr)
	# Make sure the accelerator dir in not in sys.path
	# (as it might be if running without installing.)
	unpath(dirname(__file__))
	if config_fn is False:
		return
	user_cwd = os.getcwd()
	if config_fn:
		load_cfg(config_fn)
	else:
		load_some_cfg(all=debug_cmd)
	cfg.user_cwd = user_cwd
	if not debug_cmd:
		# We want the project directory to be first in sys.path.
		unpath(cfg['project_directory'])
		sys.path.insert(0, cfg['project_directory'])
		# For consistency we also always want the project dir
		# as working directory.
		os.chdir(cfg['project_directory'])

def cmd_grep(argv):
	from accelerator.shell.grep import main
	return main(argv, cfg)
cmd_grep.help = '''search for a pattern in one or more datasets'''
cmd_grep.is_debug = True

def cmd_ds(argv):
	from accelerator.shell.ds import main
	return main(argv, cfg)
cmd_ds.help = '''display information about datasets'''
cmd_ds.is_debug = True

def cmd_run(argv):
	from accelerator.build import main
	return main(argv, cfg)
cmd_run.help = '''run a build script'''

def cmd_abort(argv):
	parser = ArgumentParser(prog=argv.pop(0))
	parser.add_argument('-q', '--quiet', action='store_true', negation='not', help="no output")
	args = parser.parse_intermixed_args(argv)
	from accelerator.build import Automata
	a = Automata(cfg.url)
	res = a.abort()
	if not args.quiet:
		print("Killed %d running job%s." % (res.killed, '' if res.killed == 1 else 's'))
cmd_abort.help = '''abort running job(s)'''

def cmd_alias(argv):
	parser = ArgumentParser(
		prog=argv.pop(0),
		description='''shows all aliases with no arguments, or the expansion of the specified aliases.''',
	)
	parser.add_argument('alias', nargs='*', help='')
	args = parser.parse_intermixed_args(argv)
	if args.alias:
		from accelerator.compat import shell_quote
		for alias in args.alias:
			a, b = expand_aliases([], [alias])
			print(' '.join(map(shell_quote, a + b)))
	else:
		for item in sorted(aliases.items()):
			print('%s = %s' % item)
cmd_alias.help = '''show defined aliases'''

def cmd_server(argv):
	from accelerator.server import main
	from accelerator.methods import MethodLoadException
	try:
		main(argv, cfg)
	except MethodLoadException as e:
		print(e)
cmd_server.help = '''run the main server'''

def cmd_script(argv):
	from accelerator.shell.script import main
	return main(argv, cfg)
cmd_script.help = '''information about build scripts'''

def cmd_init(argv):
	from accelerator.shell.init import main
	main(argv)
cmd_init.help = '''create a project directory'''

def cmd_urd(argv):
	from accelerator.shell.urd import main
	return main(argv, cfg)
cmd_urd.help = '''inspect urd contents'''

def cmd_urd_server(argv):
	from accelerator.urd import main
	main(argv, cfg)
cmd_urd_server.help = '''run the urd server'''

def cmd_method(argv):
	from accelerator.shell.method import main
	main(argv, cfg)
cmd_method.help = '''information about methods'''

def cmd_workdir(argv):
	from accelerator.shell.workdir import main
	main(argv, cfg)
cmd_workdir.help = '''information about workdirs'''
cmd_workdir.is_debug = True

def cmd_job(argv):
	from accelerator.shell.job import main
	return main(argv, cfg)
cmd_job.help = '''information about a job'''
cmd_job.is_debug = True

def cmd_board_server(argv):
	from accelerator.board import main
	main(argv, cfg)
cmd_board_server.help = '''runs a webserver for displaying results'''

def cmd_intro(argv):
	parser = ArgumentParser(prog=argv.pop(0))
	parser.parse_intermixed_args(argv)
	from accelerator import __version__ as ax_version
	def cmd(txt, *a):
		print('  ' + colour(txt, 'intro/highlight', *a))
	def msg(txt='', c='intro/info'):
		if txt:
			print(colour(txt, c))
		else:
			print()
	msg('Welcome to exax ' + ax_version, 'intro/header')
	msg()
	msg('Run')
	cmd('ax init --examples /tmp/axtest')
	cmd('cd /tmp/axtest')
	msg('to setup a project including example files.')
	msg()
	msg('To see example build scripts, run')
	cmd('ax script')
	msg()
	msg('After starting the server:')
	cmd('ax server')
	msg('try for example the first tutorial script:')
	cmd('ax run tutorial01')
	msg('(The "build_"-prefix is not required.)')
	msg()
	msg('All example code should be in the "examples" directory.')
	msg('All example build scripts will print where they are located.')
	msg()
	msg('To see available methods, run')
	cmd('ax method')
	msg()
	msg('For a longer intro, see')
	cmd('https://exax.org/documentation/2019/10/30/initialise.html', 'intro/info')
cmd_intro.help = '''show introduction text'''

def cmd_version(argv, as_command=True):
	from accelerator import __version__ as ax_version
	if as_command:
		parser = ArgumentParser(prog=argv.pop(0))
		parser.parse_intermixed_args(argv)
	print(ax_version)
	if as_command:
		py_version = ''
		suffix = sys.version.strip()
		try:
			# sys.implementation does not exist in python 2.
			py_version = sys.implementation.name
			suffix = ' (%s)' % (suffix.split('\n')[0].strip(),)
			impl_version = '.'.join(map(str, sys.implementation.version))
			py_version = '%s %s' % (py_version, impl_version,)
		except Exception:
			pass
		print('Running on ' + py_version + suffix)
cmd_version.help = '''show installed accelerator version'''

COMMANDS = {
	'abort': cmd_abort,
	'alias': cmd_alias,
	'board-server': cmd_board_server,
	'ds': cmd_ds,
	'grep': cmd_grep,
	'init': cmd_init,
	'intro': cmd_intro,
	'job': cmd_job,
	'method': cmd_method,
	'run': cmd_run,
	'server': cmd_server,
	'script': cmd_script,
	'urd': cmd_urd,
	'urd-server': cmd_urd_server,
	'version': cmd_version,
	'workdir': cmd_workdir,
}

def split_args(argv):
	prev = None
	for ix, arg in enumerate(argv):
		if not arg.startswith('-') and prev != '--config':
			return argv[:ix], argv[ix:]
		prev = arg
	return argv, []

_unesc_re = re.compile(r'\\([abefnrtv\\]|x[0-9a-f]{2})', re.IGNORECASE)
_unesc_v = {
	'a': '\a',
	'b': '\b',
	'e': '\x1b',
	'f': '\f',
	'n': '\n',
	'r': '\r',
	't': '\t',
	'v': '\v',
	'\\': '\\',
}
def _unesc(m):
	v = m.group(1)
	if len(v) > 1:
		return chr(int(v[1:], 16))
	else:
		return _unesc_v.get(v.lower(), v)

def parse_user_config(alias_d, colour_d):
	from accelerator.compat import open
	from os import environ
	cfgdir = environ.get('XDG_CONFIG_HOME')
	if not cfgdir:
		home = environ.get('HOME')
		if not home:
			return None
		cfgdir = join(home, '.config')
	fn = join(cfgdir, 'accelerator', 'config')
	try:
		fh = open(fn, 'r', encoding='utf-8')
	except IOError:
		return None
	with fh:
		from configparser import ConfigParser
		cfg = ConfigParser()
		cfg.optionxform = str # case sensitive (don't downcase aliases)
		cfg.read_file(fh)
		if 'alias' in cfg:
			alias_d.update(cfg['alias'])
		if 'colour' in cfg:
			colour_d.update({k: [_unesc_re.sub(_unesc, e) for e in v.split()] for k, v in cfg['colour'].items()})

def printdesc(items, columns, colour_prefix, full=False):
	ddot = ' ...'
	def chopline(description, max_len):
		if len(description) > max_len:
			max_len -= len(ddot)
			parts = description.split()
			description = ''
			for part in parts:
				if len(description) + len(part) + 1 > max_len:
					break
				if description:
					description = '%s %s' % (description, part,)
				else:
					description = part
			description += colour.faint(ddot)
		return description
	items = [(name, description.strip('\n').split('\n')) for name, description in items]
	if not full:
		# make names the same length, within same-ish length groups
		lens = set(len(name) for name, _ in items)
		len2len = {}
		group_size = 14
		spaces = ' ' * group_size
		while lens:
			this = min(lens)
			here = {l for l in lens if l < this + group_size}
			m = max(here)
			len2len.update({l: m for l in here})
			lens -= here
		items = [
			(
				(name + spaces)[:len2len[len(name)]],
				[description[0]],
			)
			for name, description in items
		]
	items = [(name, description if description[0] else None) for name, description in items]
	for name, description in items:
		max_len = columns - len(ddot) - len(name)
		preamble = colour('  ' + name, colour_prefix + '/highlight')
		if description and max_len > 10:
			if full:
				print(preamble)
				for line in description:
					print('    ' + line)
			else:
				print(preamble + '  ' + chopline(description[0], max_len))
		else:
			print(preamble)

def expand_env(words, alias):
	from os import environ
	for word in words:
		if word.startswith('${') and word.endswith('}'):
			k = word[2:-1]
			if k in environ:
				try:
					expanded = shlex.split(environ[k])
				except ValueError as e:
					raise ValueError('Failed to expand alias %s (%s -> %r): %s' % (alias, word, environ[k], e,))
				for word in expanded:
					yield word
		else:
			yield word

def expand_aliases(main_argv, argv):
	used_aliases = []
	while argv and argv[0] in aliases:
		alias = argv[0]
		if alias == 'noalias': # save the user from itself
			break
		try:
			expanded = shlex.split(aliases[alias])
		except ValueError as e:
			raise ValueError('Failed to expand alias %s (%r): %s' % (argv[0], aliases[argv[0]], e,))
		expanded = list(expand_env(expanded, alias))
		more_main_argv, argv = split_args(expanded + argv[1:])
		main_argv.extend(more_main_argv)
		if expanded and alias == expanded[0]:
			break
		used_aliases.append(alias)
		if alias in used_aliases[:-1]:
			raise ValueError('Alias loop: %r' % (used_aliases,))

	while argv and argv[0] == 'noalias':
		argv.pop(0)
	return main_argv, argv

def main():
	# Several commands use SIGUSR1 which (naturally...) defaults to killing the
	# process, so start by blocking that to minimise the race time.
	if hasattr(signal, 'pthread_sigmask'):
		signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
	else:
		# Or if we can't block it, just ignore it.
		signal.signal(signal.SIGUSR1, signal.SIG_IGN)

	# As of python 3.8 the default start_method is 'spawn' on macOS.
	# This doesn't work for us. 'fork' is fairly unsafe on macOS,
	# but it's better than not working at all. See
	# https://bugs.python.org/issue33725
	# for more information.
	import multiprocessing
	if hasattr(multiprocessing, 'set_start_method'):
		# If possible, make the forkserver (used by database updates) pre-import everthing
		if hasattr(multiprocessing, 'set_forkserver_preload'):
			multiprocessing.set_forkserver_preload(['accelerator', 'accelerator.server'])
		multiprocessing.set_start_method('fork')

	from accelerator import g
	g.running = 'shell'

	from accelerator.autoflush import AutoFlush
	main_argv, argv = split_args(sys.argv[1:])
	sys.stdout = AutoFlush(sys.stdout)
	sys.stderr = AutoFlush(sys.stderr)

	# configuration defaults
	global aliases
	aliases = {
		'cat': 'grep -e ""',
	}
	colour_d = {
		'warning': ('RED',),
		'highlight': ('BOLD',),
		'grep/highlight': ('RED',),
		'info': ('BRIGHTBLUE',),
		'infohighlight': ('BOLD', 'BRIGHTBLUE',),
		'separator': ('CYAN', 'UNDERLINE',),
		'header': ('BRIGHTBLUE', 'BOLD',),
		'evenlines': ('BLACK', 'WHITEBG',),
		'oddlines': ('BLACK', 'BRIGHTWHITEBG',),
	}
	parse_user_config(aliases, colour_d)
	colour._names.update(colour_d)

	main_argv, argv = expand_aliases(main_argv, argv)

	epilog = ['commands:', '']
	cmdlen = max(len(cmd) for cmd in COMMANDS)
	template = '  %%%ds  %%s' % (cmdlen,)
	for cmd, func in sorted(COMMANDS.items()):
		epilog.append(template % (cmd, func.help,))
	epilog.append('')
	epilog.append('aliases:')
	epilog.extend('  %s = %s' % item for item in sorted(aliases.items()))
	epilog.append('')
	epilog.append('use "' + colour('%(prog)s <command> --help', 'help/highlight') + '" for <command> usage')
	epilog.append('try "' + colour('%(prog)s intro', 'help/highlight') + '" for an introduction')
	parser = ArgumentParser(
		usage='%(prog)s [--config CONFIG_FILE] command [args]',
		epilog='\n'.join(epilog),
		formatter_class=RawDescriptionHelpFormatter,
	)
	parser.add_argument('--config', metavar='CONFIG_FILE', help='configuration file')
	parser.add_argument('--version', action='store_true', negation='no', help='alias for the version command')
	args = parser.parse_intermixed_args(main_argv)
	if args.version:
		sys.exit(cmd_version((), False))
	args.command = argv.pop(0) if argv else None
	if args.command not in COMMANDS:
		parser.print_help(file=sys.stderr)
		if args.command is not None:
			print(file=sys.stderr)
			print('Unknown command "%s"' % (args.command,), file=sys.stderr)
		sys.exit(2)
	config_fn = args.config
	if args.command in ('init', 'intro', 'version', 'alias',):
		config_fn = False
	cmd = COMMANDS[args.command]
	debug_cmd = getattr(cmd, 'is_debug', False)
	try:
		setup(config_fn, debug_cmd)
		argv.insert(0, '%s %s' % (basename(sys.argv[0]), args.command,))
		return cmd(argv)
	except UserError as e:
		print(e, file=sys.stderr)
		return 1
	except OSError as e:
		if e.errno == errno.EPIPE:
			return 1
		else:
			raise
	except KeyboardInterrupt:
		# Exiting with KeyboardInterrupt causes python to print a traceback.
		# We don't want that, but we do want to exit from SIGINT (so the
		# calling process can know that happened).
		signal.signal(signal.SIGINT, signal.SIG_DFL)
		os.kill(os.getpid(), signal.SIGINT)
		# If that didn't work let's re-raise the KeyboardInterrupt.
		raise
