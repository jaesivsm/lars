# vim: set et sw=4 sts=4 fileencoding=utf-8:
#
# Copyright (c) 2013 Dave Hughes
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""
Provides source and target wrappers for SQL-based databases.

This module provides wrappers which permit easy reading or writing of www-log
records from/to a SQL-based database. The :class:`SQLSource` class treats an
SQL query as the source of its log records, and provides an iterable which
yields rows in namedtuples. The :class:`SQLTarget` class accepts namedtuple
objects in its write method and automatically generates the required SQL
``INSERT`` or ``MERGE`` statements to append or merge records (respectively)
into the specified target table.

The implementation has been tested with SQLite3 (built into Python), and
PostgreSQL, but should work with any PEP-249 (Python DB API 2.0) compatible
database cursor.

Reference
=========
"""

from __future__ import (
    unicode_literals,
    absolute_import,
    print_function,
    division,
    )

import logging
import warnings
from datetime import date, time, datetime

from www2csv import datatypes


# XXX Make Py2 str same as Py3
str = type('')


__all__ = ['SQLWarning', 'SQLSource', 'SQLTarget']


class SQLError(StandardError):
    """
    Base class for all fatal errors generated by classes in the sql module.
    """


class SQLWarning(Warning):
    """
    Raised when an error is encountered inserting a log row.
    """


class SQLSource(object):
    # TODO Code SQLSource
    pass


class SQLTarget(object):
    def __init__(
            self, db_module, connection, table, commit=1000,
            create_table=False, drop_table=False, ignore_drop_errors=True,
            str_type='VARCHAR(1000)', int_type='INTEGER', fixed_type='DOUBLE',
            date_type='DATE', time_type='TIME', datetime_type='TIMESTAMP',
            ip_type='VARCHAR(53)', hostname_type='VARCHAR(255)',
            filename_type='VARCHAR(260)'):
        if not hasattr(db_module, 'paramstyle'):
            raise NameError('The database module has no "paramstyle" global')
        if not hasattr(db_module, 'Error'):
            raise NameError('The database module has no "Error" class')
        self.db_module = db_module
        self.connection = connection
        self.table = table
        if commit < 1:
            raise ValueError('commit must be 1 or more')
        self.commit = commit
        self.create_table = create_table
        self.drop_table = drop_table
        self.ignore_drop_errors = ignore_drop_errors
        self.type_map = {
            str:                   str_type,
            int:                   int_type,
            float:                 fixed_type,
            date:                  date_type,
            time:                  time_type,
            datetime:              datetime_type,
            datatypes.Url:         str_type,
            datatypes.IPv4Address: ip_type,
            datatypes.IPv6Address: ip_type,
            datatypes.IPv4Port:    ip_type,
            datatypes.IPv6Port:    ip_type,
            datatypes.Hostname:    hostname_type,
            datatypes.Filename:    filename_type,
            }
        self._first_row = None
        self._row_casts = None
        self._cursor = None
        self._insert = None
        self._counter = 0

    def __enter__(self):
        logging.debug('Entering SQL context')
        logging.debug('Constructing cursor')
        self._cursor = self.connection.cursor()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        logging.debug('Exiting SQL context')
        logging.debug('Closing cursor')
        self._cursor.close()
        self._cursor = None
        self._first_row = None
        self._row_casts = None
        self._insert = None
        self._counter = 0
        logging.debug('COMMIT')
        self.connection.commit()

    def _create_table(self, row):
        logging.debug('Creating table %s' % self.table)
        sql = 'CREATE TABLE %(table)s (%(fields)s)' % {
            'table':  self.table,
            'fields': ', '.join([
                '%(name)s %(type)s' % {
                    'name': name,
                    'type': self.type_map[type(value)],
                    }
                for (name, value) in zip(
                    row._fields if hasattr(row, '_fields') else
                    ['field%d' % (i + 1) for i in range(len(row))],
                    row)
                ]),
            }
        logging.debug(sql)
        self._cursor.execute(sql)
        logging.debug('COMMIT')
        self.connection.commit()

    def _drop_table(self):
        logging.debug('Dropping table %s' % self.table)
        sql = 'DROP TABLE %s' % self.table
        logging.debug(sql)
        self._cursor.execute(sql)
        logging.debug('COMMIT')
        self.connection.commit()

    def _generate_statement(self, row):
        # Technically we ought to quote the table substitution below in the
        # case that self.table contains a keyword, or "unsafe" characters
        # in SQL. However, that means getting into what constitutes a
        # keyword in various engines, not to mention the myriad quoting
        # systems ([MS SQL], `MySQL`, "standard") that exist in SQL
        # implementations. Instead, we simply assume if the user wants
        # quoting, they can supply it themselves in the table parameter...
        #
        # The parameter bindings are constructed according to the provided
        # paramstyle, so here's the obligatory whinge about Python's crap
        # DB-API. Why do we have *FIVE* different paramstyles?! What's
        # wrong with the absolutely standard qmark (?) paramstyle which
        # *EVERY* database (yes, even MySQL!) supports?! Why do I have to
        # write cryptic garbage like this to construct SQL in Python?! Why
        # for that matter do I have to get the user to pass in paramstyle
        # to the constructor - why isn't it at least an attribute on the
        # connection object?! Eurgh - PEP-249 is garbage...
        logging.debug('Constructing INSERT statement')
        self._insert = 'INSERT INTO %(table)s VALUES (%(values)s)' % {
            'table':  self.table,
            'values': ', '.join([{
                'qmark':    '?',
                'numeric':  ':%d' % i,
                'named':    ':%s' % name,
                'format':   '%s',
                'pyformat': '%%(%s)s' % name,
                }[self.db_module.paramstyle]
                for (i, name) in enumerate(
                    row._fields if hasattr(row, '_fields') else
                    ['field%d' % (j + 1) for j in range(len(row))]
                    )
                ]),
            }
        logging.debug(self._insert)
        logging.debug('Constructing row casts')
        # Bit of a dirty hack, but it seems the most user-friendly way of
        # dealing with IP addresses depending on the type selected for the
        # target table
        ip_bases = (datatypes.IPv4Address, datatypes.IPv6Address)
        if self.type_map[datatypes.IPv4Address].upper().startswith('INT'):
            ip_cast = int
        else:
            ip_cast = str
        self._row_casts = [
            ip_cast if isinstance(value, ip_bases) else
            str if isinstance(value, datatypes.Url) else
            None
            for value in row
            ]

    def write(self, row):
        if self._first_row:
            if len(row) != len(self._first_row):
                raise TypeError('Rows must have the same number of elements')
        else:
            logging.debug('First row')
            self._first_row = row
            self._generate_statement(row)
            if self.drop_table:
                try:
                    self._drop_table()
                except self.db_module.Error as exc:
                    if not self.ignore_drop_errors:
                        raise
                    logging.debug('While dropping table %s occurred', str(exc))
            if self.create_table:
                self._create_table(row)
        # XXX What about paramstyles pyformat and named? Eurgh...
        cast_to_str = (datatypes.IPv4Address, datatypes.IPv6Address)
        params = [
            cast(value) if cast else value
            for (cast, value) in zip(self._row_casts, row)
            ]
        try:
            self._cursor.execute(self._insert, params)
        except self.db_module.Error as exc:
            warnings.warn(
                '%s while inserting row %s' % (str(exc), str(row)), SQLWarning)
        self._counter += 1
        if self._counter % self.commit == 0:
            logging.debug('COMMIT')
            self.connection.commit()
