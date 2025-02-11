#!/usr/bin/env python

############################################################################
#                                                                          #
# Copyright (c) 2017 eBay Inc.                                             #
# Modifications copyright (c) 2018-2021 Carl Drougge                       #
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

# Verify general operation and a few corner cases.

from __future__ import division, print_function, unicode_literals

from datetime import datetime, date, time
from sys import version_info
from itertools import compress
from os import unlink
from functools import partial

from accelerator import _dsutil

TMP_FN = "_tmp_test.gz"
COMPRESSION = "gzip"

inf, ninf = float("inf"), float("-inf")

if version_info[0] > 2:
	l = lambda i: i
else:
	l = long

# The Bits types don't accept floats, the others Int types do.
# This wasn't really intentional, but the right thing.

dttm0 = datetime(1789, 7, 14, 12, 42, 1, 82933)
dttm1 = datetime(2500, 12, 31, 23, 59, 59, 999999)
dttm2 = datetime(2015, 1, 1, 0, 0, 0, 0)
dt0 = date(1985, 7, 10)
tm0 = time(0, 0, 0, 0)
tm1 = time(2, 42, 0, 3)
tm2 = time(23, 59, 59, 999999)
if version_info > (3, 6, 0):
	dttm1 = dttm1.replace(fold=1)
	tm1 = tm1.replace(fold=1)

def forstrings(name):
	return name in ("Bytes", "Ascii", "Unicode")
def can_minmax(name):
	return 'Complex' not in name and not forstrings(name)

for name, data, bad_cnt, res_data in (
	("Float64"       , ["0", float, 0   , 4.2, -0.01, 1e42, inf, ninf, None], 2, [0.0, 4.2, -0.01, 1e42, inf, ninf, None]),
	("Float32"       , ["0", float, l(0), 4.2, -0.01, 1e42, inf, ninf, None], 2, [0.0, 4.199999809265137, -0.009999999776482582, inf , inf, ninf, None]),
	("Int64"         , ["0", int, 0x8000000000000000, -0x8000000000000000, 0, 0x7fffffffffffffff, l(-5), None], 4, [0, 0x7fffffffffffffff, -5, None]),
	("Bits64"        , ["0", int, None, l(-5), -5, 0.1, 0x8000000000000000, 0x7fffffffffffffff, l(0x8000000000000000)], 6, [0x8000000000000000, 0x7fffffffffffffff, 0x8000000000000000]),
	("Int32"         , ["0", int, 0x80000000, -0x80000000, 0, 0x7fffffff, l(-5), None], 4, [0, 0x7fffffff, -5, None]),
	("Bits32"        , ["0", int, None, l(-5), -5, 0.1, 0x80000000, 0x7fffffff, l(0x80000000)], 6, [0x80000000, 0x7fffffff, 0x80000000]),
	("Number"        , ["0", int, 1 << 1007, -(1 << 1007), 1, l(0), -1, 0.5, 0x8000000000000000, -0x800000000000000, 1 << 340, (1 << 1007) - 1, -(1 << 1007) + 1, None], 4, [1, 0, -1, 0.5, 0x8000000000000000, -0x800000000000000, 1 << 340, (1 << 1007) - 1, -(1 << 1007) + 1, None]),
	("Complex64"     , ["0", float, 0   , 4.2+1e42j, inf, ninf, complex(inf, ninf), None], 2, [0+0j, 4.2+1e42j, inf, ninf, complex(inf, ninf), None]),
	("Complex32"     , ["0", float, l(0), 4.2+1e42j, inf, ninf, complex(inf, ninf), None], 2, [0+0j, complex(4.199999809265137, inf), inf, ninf, complex(inf, ninf), None]),
	("Bool"          , ["0", bool, True, False, 0, l(1), None], 2, [True, False, False, True, None]),
	("Bytes"         , [42, str, u"a", b"\n", b"\0", b"", None, b"long" * 1000, b"a\r", b"a\r\n", b"a\nb\0c"], 3, [b"\n", b"\0", b"", None, b"long" * 1000, b"a\r", b"a\r\n", b"a\nb\0c"]),
	("Ascii"         , [42, str, u"foo\xe4", u"a", b"\n", b"\0", b"", None, b"long" * 1000, b"a\r", b"a\r\n", u"a\nb\0c"], 3, [str("a"), str("\n"), str("\0"), str(""), None, str("long" * 1000), str("a\r"), str("a\r\n"), str("a\nb\0c")]),
	("Unicode"         , [42, str, b"a", u"foo\xe4", u"\n", u"\0", u"", None, u"long" * 1000, u"a\r", "a\r\n", "a\nb\0c"], 3, [u"foo\xe4", u"\n", u"\0", u"", None, u"long" * 1000, u"a\r", u"a\r\n", u"a\nb\0c"]),
	("DateTime"      , [42, "now", tm0, dttm0, dttm1, dttm2, None], 3, [dttm0, dttm1, dttm2, None]),
	("Date"          , [42, "now", tm0, dttm0, dttm1, dttm2, dt0, None], 3, [dttm0.date(), dttm1.date(), dttm2.date(), dt0, None]),
	("Time"          , [42, "now", dttm0, tm0, tm1, tm2, None], 3, [tm0, tm1, tm2, None]),
	("ParsedFloat64" , [float, "1 thing", "", "0", " 4.2", -0.01, "1e42 ", " inf", "-inf ", None], 3, [0.0, 4.2, -0.01, 1e42, inf, ninf, None]),
	("ParsedFloat32" , [float, "1 thing", "", "0", " 4.2", -0.01, "1e42 ", " inf", "-inf ", None], 3, [0.0, 4.199999809265137, -0.009999999776482582, inf , inf, ninf, None]),
	("ParsedNumber"  , [int, "", str(1 << 1007), str(-(1 << 1007)), "0.0", 1, 0.0, "-1", "9223372036854775809", -0x800000000000000, str(1 << 340), str((1 << 1007) - 1), str(-(1 << 1007) + 1), None, "1e25"], 4, [0.0, 1, 0, -1, 0x8000000000000001, -0x800000000000000, 1 << 340, (1 << 1007) - 1, -(1 << 1007) + 1, None, 1e25]),
	("ParsedInt64"   , [int, "", "9223372036854775808", -0x8000000000000000, "0.1", 1, 0.1, "9223372036854775807", " -5 ", None], 5, [1, 0, 0x7fffffffffffffff, -5, None]),
	("ParsedBits64"  , [int, "", None, l(-5), "-5", 0.1, " 9223372036854775808", "9223372036854775807 ", "0", 1], 5, [0, 0x8000000000000000, 0x7fffffffffffffff, 0, 1]),
	("ParsedInt32"   , [int, "", 0x80000000, -0x80000000, "0.1", 0.1, "-7", "-0", "2147483647", " -5 ", None, 1], 5, [0, -7, 0, 0x7fffffff, -5, None, 1]),
	("ParsedBits32"  , [int, "", None, l(-5), -5, 0.1, "2147483648", "2147483647", l(0x80000000), 1], 5, [0, 0x80000000, 0x7fffffff, 0x80000000, 1]),
):
	print(name)
	r_name = "Read" + name[6:] if name.startswith("Parsed") else "Read" + name
	r_typ = getattr(_dsutil, r_name)
	w_typ = getattr(_dsutil, "Write" + name)
	r_mk = partial(r_typ, compression=COMPRESSION)
	w_mk = partial(w_typ, compression=COMPRESSION)
	none_support = ("Bits" not in name)
	# verify that failures in init are handled reasonably.
	for typ in (r_typ, w_typ,):
		try:
			with typ("DOES/NOT/EXIST", compression=COMPRESSION) as fh:
				if typ is w_typ:
					# File is not created until written.
					fh.flush()
			raise Exception("%r does not give IOError for DOES/NOT/EXIST" % (typ,))
		except IOError:
			pass
		try:
			typ(TMP_FN, nonexistent_keyword="test", compression=COMPRESSION)
			assert False
		except TypeError:
			pass
		except Exception:
			raise Exception("%r does not give TypeError for bad keyword argument" % (typ,))
	# test that the right data fails to write
	for test_none_support in (False, True):
		if test_none_support and not none_support:
			continue
		with w_mk(TMP_FN, none_support=test_none_support) as fh:
			assert fh.compression == COMPRESSION
			count = 0
			for ix, value in enumerate(data):
				try:
					fh.write(value)
					count += 1
					assert ix >= bad_cnt, repr(value)
					if value is None and not test_none_support:
						raise Exception("Allowed None without none_support")
				except (ValueError, TypeError, OverflowError):
					assert ix < bad_cnt or (value is None and not test_none_support), repr(value)
			assert fh.count == count, "%s: %d lines written, claims %d" % (name, count, fh.count,)
			if can_minmax(name):
				want_min = min(filter(lambda x: x is not None, res_data))
				want_max = max(filter(lambda x: x is not None, res_data))
				assert fh.min == want_min, "%s: claims min %r, not %r" % (name, fh.min, want_min,)
				assert fh.max == want_max, "%s: claims max %r, not %r" % (name, fh.max, want_max,)
	# Okay, errors look good
	with r_mk(TMP_FN) as fh:
		res = list(fh)
		assert res == res_data, res
		if version_info > (3, 6, 0) and 'Time' in name:
			# Python compares times without .fold, but we want to verify it matches.
			res = [v.fold if v else None for v in res]
			assert [v.fold if v else None for v in res_data] == res
	# Data comes back as expected.
	if forstrings(name):
		continue # no default support
	for ix, default in enumerate(data):
		# Verify that defaults are accepted where expected
		try:
			with w_mk(TMP_FN, default=default, none_support=none_support) as fh:
				pass
			assert ix >= bad_cnt, repr(default)
		except AssertionError:
			raise
		except Exception:
			assert ix < bad_cnt, repr(default)
		if ix >= bad_cnt:
			with w_mk(TMP_FN, default=default, none_support=none_support) as fh:
				count = 0
				for value in data:
					try:
						fh.write(value)
						count += 1
					except (ValueError, TypeError, OverflowError):
						assert 0, "No default: %r" % (value,)
				assert fh.count == count, "%s: %d lines written, claims %d" % (name, count, fh.count,)
			# No errors when there is a default
			with r_mk(TMP_FN) as fh:
				res = list(fh)
				assert res == [res_data[ix - bad_cnt]] * bad_cnt + res_data, res
			# Great, all default values came out right in the file!
	# Verify hashing and slicing
	def slice_test(slices, spread_None):
		res = []
		sliced_res = []
		total_count = 0
		for sliceno in range(slices):
			with w_mk(TMP_FN, hashfilter=(sliceno, slices, spread_None), none_support=none_support) as fh:
				count = 0
				for ix, value in enumerate(data):
					try:
						hc = fh.hashcheck(value)
						wrote = fh.write(value)
						count += wrote
						assert ix >= bad_cnt, repr(value)
						assert hc == wrote, "Hashcheck disagrees with write"
					except (ValueError, TypeError, OverflowError):
						assert ix < bad_cnt, repr(value)
				assert fh.count == count, "%s (%d, %d): %d lines written, claims %d" % (name, sliceno, slices, count, fh.count,)
				if not forstrings(name):
					got_min, got_max = fh.min, fh.max
				fh.flush() # we overwrite the same file, so make sure we write.
			total_count += count
			with r_mk(TMP_FN) as fh:
				tmp = list(fh)
			assert len(tmp) == count, "%s (%d, %d): %d lines written, claims %d" % (name, sliceno, slices, len(tmp), count,)
			for v in tmp:
				assert (spread_None and v is None) or w_typ.hash(v) % slices == sliceno, "Bad hash for %r" % (v,)
				if "Bits" not in name or v < 0x8000000000000000:
					assert w_typ.hash(v) == _dsutil.hash(v), "Inconsistent hash for %r" % (v,)
			res.extend(tmp)
			sliced_res.append(tmp)
			if can_minmax(name):
				tmp = list(filter(lambda x: x is not None, tmp))
				if tmp:
					want_min = min(tmp)
					want_max = max(tmp)
					assert got_min == want_min, "%s (%d, %d): claims min %r, not %r" % (name, sliceno, slices, got_min, want_min,)
					assert got_max == want_max, "%s (%d, %d): claims max %r, not %r" % (name, sliceno, slices, got_max, want_max,)
				else:
					assert got_min is None and got_max is None
		assert len(res) == total_count, "%s (%d): %d lines written, claims %d" % (name, slices, len(res), total_count,)
		assert len(res) == len(res_data), "%s (%d): %d lines written, should be %d" % (name, slices, len(res), len(res_data),)
		assert set(res) == set(res_data), "%s (%d): Wrong data: %r != %r" % (name, slices, res, res_data,)
		# verify reading back with hashfilter gives the same as writing with it
		with w_mk(TMP_FN, none_support=none_support) as fh:
			for value in data[bad_cnt:]:
				fh.write(value)
		for sliceno in range(slices):
			with r_mk(TMP_FN, hashfilter=(sliceno, slices, spread_None)) as fh:
				slice_values = list(compress(res_data, fh))
			assert slice_values == sliced_res[sliceno], "Bad reader hashfilter: slice %d of %d gave %r instead of %r" % (sliceno, slices, slice_values, sliced_res[sliceno],)
	for slices in range(1, 24):
		slice_test(slices, False)
		slice_test(slices, True)
		# and a simple check to verify that None actually gets spread too
		if "Bits" not in name:
			kw = dict(hashfilter=(slices - 1, slices, True), none_support=none_support)
			value = None
			for _ in range(2):
				# first lap verifies with normal writing,
				# second lap with invalid values writing the default.
				with w_mk(TMP_FN, **kw) as fh:
					for _ in range(slices * 3):
						fh.write(value)
				with r_mk(TMP_FN) as fh:
					tmp = list(fh)
					assert tmp == [None, None, None], "Bad spread_None %sfor %d slices" % ("from default " if "default" in kw else "", slices,)
				kw["default"] = None
				value = object

print("Empty and None values in stringlike types")
for name, value in (
	("Bytes", b""), ("Ascii", ""), ("Unicode", ""),
):
	with getattr(_dsutil, "Write" + name)(TMP_FN, compression=COMPRESSION) as fh:
		fh.write(value)
		fh.write(value)
	with getattr(_dsutil, "Read" + name)(TMP_FN, compression=COMPRESSION) as fh:
		assert list(fh) == [value, value], name + " fails with just empty strings"
	with getattr(_dsutil, "Write" + name)(TMP_FN, none_support=True, compression=COMPRESSION) as fh:
		fh.write(None)
		fh.write(None)
	with getattr(_dsutil, "Read" + name)(TMP_FN, compression=COMPRESSION) as fh:
		assert list(fh) == [None, None], name + " fails with just Nones"

print("Hash testing, false things")
for v in (None, "", b"", 0, 0.0, False,):
	assert _dsutil.hash(v) == 0, "%r doesn't hash to 0" % (v,)
print("Hash testing, strings")
for v in ("", "a", "0", "foo", "a slightly longer string", "\0", "a\0b",):
	l_u = _dsutil.WriteUnicode.hash(v)
	l_a = _dsutil.WriteAscii.hash(v)
	l_b = _dsutil.WriteBytes.hash(v.encode("utf-8"))
	u = _dsutil.WriteUnicode.hash(v)
	a = _dsutil.WriteAscii.hash(v)
	b = _dsutil.WriteBytes.hash(v.encode("utf-8"))
	assert u == l_u == a == l_a == b == l_b, "%r doesn't hash the same" % (v,)
assert _dsutil.hash(b"\xe4") != _dsutil.hash("\xe4"), "Unicode hash fail"
assert _dsutil.WriteBytes.hash(b"\xe4") != _dsutil.WriteUnicode.hash("\xe4"), "Unicode hash fail"
try:
	_dsutil.WriteAscii.hash(b"\xe4")
	raise Exception("Ascii.hash accepted non-ascii")
except ValueError:
	pass
print("Hash testing, numbers")
for v in (0, 1, 2, 9007199254740991, -42):
	assert _dsutil.WriteInt64.hash(v) == _dsutil.WriteFloat64.hash(float(v)), "%d doesn't hash the same" % (v,)
	assert _dsutil.WriteInt64.hash(v) == _dsutil.WriteNumber.hash(v), "%d doesn't hash the same" % (v,)

print("Number boundary test")
Z = 128 * 1024 # the internal buffer size in _dsutil
with _dsutil.WriteNumber(TMP_FN, compression=COMPRESSION) as fh:
	todo = Z - 100
	while todo > 0:
		fh.write(42)
		todo -= 9
	# v goes over a block boundary.
	v = 0x2e6465726f6220657261206577202c6567617373656d20676e6f6c207974746572702061207369207374696220646e6173756f6874206120796c6c6175746341203f7468676972202c6c6c657720736120746867696d206577202c65726568206567617373656d2074726f68732061206576616820732774656c20796548
	want = [42] * fh.count + [v]
	fh.write(v)
with _dsutil.ReadNumber(TMP_FN, compression=COMPRESSION) as fh:
	assert want == list(fh)

print("Number want_count large end test")
with _dsutil.WriteNumber(TMP_FN, compression=COMPRESSION) as fh:
	fh.write(2 ** 1000)
	fh.write(7)
with _dsutil.ReadNumber(TMP_FN, want_count=1, compression=COMPRESSION) as fh:
	assert [2 ** 1000] == list(fh)

print("Large ascii strings (with a size between blocks)")
data = ["a" * (128 * 1024 - 6), "b" * (128 * 1024 - 6), "c" * (2090 * 1024), "d"]
with _dsutil.WriteAscii(TMP_FN, compression=COMPRESSION) as fh:
	for v in data:
		fh.write(v)
with _dsutil.ReadAscii(TMP_FN, compression=COMPRESSION) as fh:
	assert data == list(fh)

print("Callback tests")
with _dsutil.WriteNumber(TMP_FN, compression=COMPRESSION) as fh:
	for n in range(1000):
		fh.write(n)
def callback(num_lines):
	global cb_count
	cb_count += 1
	if cb_interval > 1:
		assert num_lines in good_num_lines or num_lines == 1000 + cb_offset
for cb_interval, want_count, expected_cb_count in (
	(300, -1, (3,)),
	(250, 300, (1,)),
	(250, 200, (0,)),
	(1, -1, (999, 1000,)),
	(5, -1, (199, 200,)),
	(5, 12, (2,)),
	(10000, -1, (0,)),
):
	for cb_offset in (0, 50000000, -10000):
		cb_count = 0
		good_num_lines = range(cb_interval + cb_offset, (1000 if want_count == -1 else want_count) + cb_offset, cb_interval)
		with _dsutil.ReadNumber(TMP_FN, want_count=want_count, callback=callback, callback_interval=cb_interval, callback_offset=cb_offset, compression=COMPRESSION) as fh:
			lst = list(fh)
			assert len(lst) == 1000 if want_count == -1 else want_count
		assert cb_count in expected_cb_count
def callback2(num_lines):
	raise StopIteration
with _dsutil.ReadNumber(TMP_FN, callback=callback2, callback_interval=1, compression=COMPRESSION) as fh:
	lst = list(fh)
	assert lst == [0]
def callback3(num_lines):
	1 / 0
with _dsutil.ReadNumber(TMP_FN, callback=callback3, callback_interval=1, compression=COMPRESSION) as fh:
	good = False
	try:
		lst = list(fh)
	except ZeroDivisionError:
		good = True
	assert good

unlink(TMP_FN)
