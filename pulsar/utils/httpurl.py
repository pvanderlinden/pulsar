'''This is a substantial module which imports several classes and functions
from the standard library in a python 2.6 to python 3.3 compatible fashion.
On top of that, it implements the :class:`HttpClient` for handling synchronous
and asynchronous HTTP requests in a pythonic way.

It is a thin layer on top of urllib2 in python2 / urllib in Python 3.
Several opensource efforts have been used as source of snippets:

* http-parser_
* request_
* urllib3_
* werkzeug_

This is a long stand-alone module which can be dropped in any library and used
as it is.


.. _http-parser: https://github.com/benoitc/http-parser
.. _urllib3: https://github.com/shazow/urllib3
.. _request: https://github.com/kennethreitz/requests
.. _werkzeug: https://github.com/mitsuhiko/werkzeug
'''
import os
import sys
import re
import io
import string
import time
import mimetypes
import platform
import codecs
import socket
import logging
from functools import reduce
from hashlib import sha1, md5
from uuid import uuid4
from datetime import datetime, timedelta
from email.utils import formatdate
from io import BytesIO
import zlib
from collections import deque
from copy import copy

from .structures import mapping_iterator
from .pep import ispy3k, ispy26

create_connection = socket.create_connection
LOGGER = logging.getLogger('httpurl')

try:
    from select import poll, POLLIN
except ImportError: #pragma    nocover
    poll = False
    try:
        from select import select
    except ImportError: #pragma    nocover
        select = False
        
try:    # Compiled with SSL?
    BaseSSLError = None
    ssl = None
    import ssl
    BaseSSLError = ssl.SSLError
except (ImportError, AttributeError):   # pragma : no cover 
    pass

if ispy3k: # Python 3
    from urllib import request as urllibr
    from http import client as httpclient
    from urllib.parse import quote, unquote, urlencode, urlparse, urlsplit,\
                             parse_qs, parse_qsl, splitport, urlunparse,\
                             urljoin
    from http.client import responses
    from http.cookiejar import CookieJar, Cookie
    from http.cookies import SimpleCookie, BaseCookie, Morsel, CookieError

    string_type = str
    getproxies_environment = urllibr.getproxies_environment
    ascii_letters = string.ascii_letters
    zip = zip
    map = map
    range = range
    chr = chr
    iteritems = lambda d : d.items()
    itervalues = lambda d : d.values()
    is_string = lambda s: isinstance(s, str)
    is_string_or_native_string = is_string

    def to_bytes(s, encoding=None, errors=None):
        errors = errors or 'strict'
        encoding = encoding or 'utf-8'
        if isinstance(s, bytes):
            if encoding != 'utf-8':
                return s.decode('utf-8', errors).encode(encoding, errors)
            else:
                return s
        else:
            return ('%s'%s).encode(encoding, errors)

    def to_string(s, encoding=None, errors='strict'):
        """Inverse of to_bytes"""
        if isinstance(s, bytes):
            return s.decode(encoding or 'utf-8', errors)
        else:
            return str(s)

    def native_str(s, encoding=None):
        if isinstance(s, bytes):
            return s.decode(encoding or 'utf-8')
        else:
            return s

    def force_native_str(s, encoding=None):
        if isinstance(s, bytes):
            return s.decode(encoding or 'utf-8')
        elif not isinstance(s, str):
            return str(s)
        else:
            return s

else:   # pragma : no cover
    import urllib2 as urllibr
    import httplib as httpclient
    from urllib import quote, unquote, urlencode, getproxies_environment,\
                        splitport
    from urlparse import urlparse, urlsplit, parse_qs, urlunparse, urljoin,\
                         parse_qsl
    from httplib import responses
    from cookielib import CookieJar, Cookie
    from Cookie import SimpleCookie, BaseCookie, Morsel, CookieError
    from itertools import izip as zip, imap as map

    string_type = unicode
    ascii_letters = string.letters
    range = xrange
    chr = unichr
    iteritems = lambda d : d.iteritems()
    itervalues = lambda d : d.itervalues()
    is_string = lambda s: isinstance(s, unicode)
    is_string_or_native_string = lambda s: isinstance(s, basestring)

    if sys.version_info < (2, 7):
        #
        def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                              source_address=None):
            """Form Python 2.7"""
            host, port = address
            err = None
            for res in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM):
                af, socktype, proto, canonname, sa = res
                sock = None
                try:
                    sock = socket.socket(af, socktype, proto)
                    if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                        sock.settimeout(timeout)
                    if source_address:
                        sock.bind(source_address)
                    sock.connect(sa)
                    return sock
                except Exception as _:
                    err = _
                    if sock is not None:
                        sock.close()
            if err is not None:
                raise err
            else:
                raise error("getaddrinfo returns an empty list")
    
    def to_bytes(s, encoding=None, errors='strict'):
        encoding = encoding or 'utf-8'
        if isinstance(s, bytes):
            if encoding != 'utf-8':
                return s.decode('utf-8', errors).encode(encoding, errors)
            else:
                return s
        else:
            return unicode(s).encode(encoding, errors)

    def to_string(s, encoding=None, errors='strict'):
        """Inverse of to_bytes"""
        if isinstance(s, bytes):
            return s.decode(encoding or 'utf-8', errors)
        else:
            return unicode(s)

    def native_str(s, encoding=None):
        if isinstance(s, unicode):
            return s.encode(encoding or 'utf-8')
        else:
            return s

    def force_native_str(s, encoding=None):
        if isinstance(s, unicode):
            return s.encode(encoding or 'utf-8')
        elif not isinstance(s, str):
            return str(s)
        else:
            return s

HTTPError = urllibr.HTTPError
URLError = urllibr.URLError
request_host = urllibr.request_host
parse_http_list = urllibr.parse_http_list

class SSLError(HTTPError):
    "Raised when SSL certificate fails in an HTTPS connection."
    pass

####################################################    URI & IRI SUFF
#
# The reserved URI characters (RFC 3986 - section 2.2)
#Default is charset is "iso-8859-1" (latin-1) from section 3.7.1
#http://www.ietf.org/rfc/rfc2616.txt
DEFAULT_CHARSET = 'iso-8859-1'
URI_GEN_DELIMS = frozenset(':/?#[]@')
URI_SUB_DELIMS = frozenset("!$&'()*+,;=")
URI_RESERVED_SET = URI_GEN_DELIMS.union(URI_SUB_DELIMS)
URI_RESERVED_CHARS = ''.join(URI_RESERVED_SET)
# The unreserved URI characters (RFC 3986 - section 2.3)
URI_UNRESERVED_SET = frozenset(ascii_letters + string.digits + '-._~')
URI_SAFE_CHARS = URI_RESERVED_CHARS + '%~'
HEADER_TOKEN_CHARS = frozenset("!#$%&'*+-.0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                               '^_`abcdefghijklmnopqrstuvwxyz|~')

escape = lambda s: quote(s, safe='~')
urlquote = lambda iri: quote(iri, safe=URI_RESERVED_CHARS)

def _gen_unquote(uri):
    unreserved_set = URI_UNRESERVED_SET
    for n, part in enumerate(force_native_str(uri).split('%')):
        if not n:
            yield part
        else:
            h = part[0:2]
            if len(h) == 2:
                c = chr(int(h, 16))
                if c in unreserved_set:
                    yield c + part[2:]
                else:
                    yield '%' + part
            else:
                yield '%' + part
                
def unquote_unreserved(uri):
    """Un-escape any percent-escape sequences in a URI that are unreserved
characters. This leaves all reserved, illegal and non-ASCII bytes encoded."""
    return ''.join(_gen_unquote(uri))

def requote_uri(uri):
    """Re-quote the given URI.

    This function passes the given URI through an unquote/quote cycle to
    ensure that it is fully and consistently quoted.
    """
    # Unquote only the unreserved characters
    # Then quote only illegal characters (do not quote reserved, unreserved,
    # or '%')
    return quote(unquote_unreserved(uri), safe=URI_SAFE_CHARS)

def iri_to_uri(iri, kwargs=None):
    '''Convert an Internationalized Resource Identifier (IRI) portion to a URI
portion that is suitable for inclusion in a URL.
This is the algorithm from section 3.1 of RFC 3987.
Returns an ASCII native string containing the encoded result.'''
    if iri is None:
        return iri
    iri = force_native_str(iri)
    if kwargs:
        iri = '%s?%s'%(iri,'&'.join(('%s=%s' % kv for kv in iteritems(kwargs))))
    return urlquote(unquote_unreserved(iri))

def host_and_port(host):
    host, port = splitport(host)
    return host, int(port) if port else None

def default_port(scheme):
    if scheme == "http":
        return '80'
    elif scheme == "https":
        return '443'
    
def host_and_port_default(scheme, host):
    host, port = splitport(host)
    if not port:
        port = default_port(scheme)
    return host, port

def get_hostport(scheme, full_host):
    host, port = host_and_port(full_host)
    if port is None:
        i = host.rfind(':')
        j = host.rfind(']')         # ipv6 addresses have [...]
        if i > j:
            try:
                port = int(host[i+1:])
            except ValueError:
                if host[i+1:] == "": # http://foo.com:/ == http://foo.com/
                    port = default_port(scheme)
                else:
                    raise httpclient.InvalidURL("nonnumeric port: '%s'"
                                                 % host[i+1:])
            host = host[:i]
        else:
            port = default_port(scheme)
        if host and host[0] == '[' and host[-1] == ']':
            host = host[1:-1]
    return host, port

def remove_double_slash(route):
    if '//' in route:
        route = re.sub('/+', '/', route)
    return route

def is_closed(sock):    #pragma nocover
    """Check if socket is connected."""
    if not sock:
        return False
    try:
        if not poll:
            if not select:
                return False
            try:
                return bool(select([sock], [], [], 0.0)[0])
            except socket.error:
                return True
        # This version is better on platforms that support it.
        p = poll()
        p.register(sock, POLLIN)
        for (fno, ev) in p.poll(0.0):
            if fno == sock.fileno():
                # Either data is buffered (bad), or the connection is dropped.
                return True
    except Exception:
        return True

####################################################    CONTENT TYPES
JSON_CONTENT_TYPES = frozenset(('application/json',
                                'application/javascript',
                                'text/json',
                                'text/x-json')) 

####################################################    REQUEST METHODS
ENCODE_URL_METHODS = frozenset(['DELETE', 'GET', 'HEAD', 'OPTIONS'])
ENCODE_BODY_METHODS = frozenset(['PATCH', 'POST', 'PUT', 'TRACE'])
REDIRECT_CODES = (301, 302, 303, 307)

def has_empty_content(status, method=None):
    '''204, 304 and 1xx codes have no content'''
    if status == httpclient.NO_CONTENT or\
            status == httpclient.NOT_MODIFIED or\
            100 <= status < 200 or\
            method == "HEAD":
        return True
    else:
        return False

def is_succesful(status):
    '''2xx status is succesful'''
    return status >= 200 and status < 300

####################################################    HTTP HEADERS
WEBSOCKET_VERSION = (8, 13)
HEADER_FIELDS = {'general': frozenset(('Cache-Control', 'Connection', 'Date',
                                       'Pragma', 'Trailer','Transfer-Encoding',
                                       'Upgrade', 'Sec-WebSocket-Extensions',
                                       'Sec-WebSocket-Protocol',
                                       'Via','Warning')),
                 # The request-header fields allow the client to pass additional
                 # information about the request, and about the client itself,
                 # to the server.
                 'request': frozenset(('Accept', 'Accept-Charset',
                                       'Accept-Encoding', 'Accept-Language',
                                       'Authorization',
                                       'Cookie', 'Expect', 'From',
                                       'Host', 'If-Match', 'If-Modified-Since',
                                       'If-None-Match', 'If-Range',
                                       'If-Unmodified-Since', 'Max-Forwards',
                                       'Proxy-Authorization', 'Range',
                                       'Referer',
                                       'Sec-WebSocket-Key',
                                       'Sec-WebSocket-Version',
                                       'TE',
                                       'User-Agent',
                                       'X-Requested-With')),
                 # The response-header fields allow the server to pass
                 # additional information about the response which cannot be
                 # placed in the Status- Line.
                 'response': frozenset(('Accept-Ranges',
                                        'Age',
                                        'ETag',
                                        'Location',
                                        'Proxy-Authenticate',
                                        'Retry-After',
                                        'Sec-WebSocket-Accept',
                                        'Server',
                                        'Set-Cookie',
                                        'Set-Cookie2',
                                        'Vary',
                                        'WWW-Authenticate',
                                        'X-Frame-Options')),
                 'entity': frozenset(('Allow', 'Content-Encoding',
                                      'Content-Language', 'Content-Length',
                                      'Content-Location', 'Content-MD5',
                                      'Content-Range', 'Content-Type',
                                      'Expires', 'Last-Modified'))}

CLIENT_HEADER_FIELDS = HEADER_FIELDS['general'].union(HEADER_FIELDS['entity'],
                                                      HEADER_FIELDS['request'])
SERVER_HEADER_FIELDS = HEADER_FIELDS['general'].union(HEADER_FIELDS['entity'],
                                                      HEADER_FIELDS['response'])
ALL_HEADER_FIELDS = CLIENT_HEADER_FIELDS.union(SERVER_HEADER_FIELDS)
ALL_HEADER_FIELDS_DICT = dict(((k.lower(),k) for k in ALL_HEADER_FIELDS))

TYPE_HEADER_FIELDS = {'client': CLIENT_HEADER_FIELDS,
                      'server': SERVER_HEADER_FIELDS,
                      'both': ALL_HEADER_FIELDS}

header_type = {0: 'client', 1: 'server', 2: 'both'}
header_type_to_int = dict(((v,k) for k,v in header_type.items()))

def capfirst(x):
    x = x.strip()
    if x:
        return x[0].upper() + x[1:].lower()
    else:
        return x
    
def capheader(name):
    return '-'.join((b for b in (capfirst(n) for n in name.split('-')) if b))

def header_field(name, HEADERS_SET=None, strict=False):
    name = name.lower()
    if name.startswith('x-'):
        return capheader(name)
    else:
        header = ALL_HEADER_FIELDS_DICT.get(name)
        if header and HEADERS_SET:
            return header if header in HEADERS_SET else None
        elif header:
            return header
        elif not strict:
            return capheader(name)

def quote_header_value(value, extra_chars='', allow_token=True):
    """Quote a header value if necessary.

:param value: the value to quote.
:param extra_chars: a list of extra characters to skip quoting.
:param allow_token: if this is enabled token values are returned
                    unchanged."""
    value = force_native_str(value)
    if allow_token:
        token_chars = HEADER_TOKEN_CHARS | set(extra_chars)
        if set(value).issubset(token_chars):
            return value
    return '"%s"' % value.replace('\\', '\\\\').replace('"', '\\"')

def unquote_header_value(value, is_filename=False):
    """Unquotes a header value. Reversal of :func:`quote_header_value`.
This does not use the real unquoting but what browsers are actually
using for quoting.
:param value: the header value to unquote."""
    if value and value[0] == value[-1] == '"':
        # this is not the real unquoting, but fixing this so that the
        # RFC is met will result in bugs with internet explorer and
        # probably some other browsers as well.  IE for example is
        # uploading files with "C:\foo\bar.txt" as filename
        value = value[1:-1]
        # if this is a filename and the starting characters look like
        # a UNC path, then just return the value without quotes.  Using the
        # replace sequence below on a UNC path has the effect of turning
        # the leading double slash into a single slash and then
        # _fix_ie_filename() doesn't work correctly.  See #458.
        if not is_filename or value[:2] != '\\\\':
            return value.replace('\\\\', '\\').replace('\\"', '"')
    return value

def parse_dict_header(value):
    """Parse lists of key, value pairs as described by RFC 2068 Section 2 and
    convert them into a python dict:

    >>> d = parse_dict_header('foo="is a fish", bar="as well"')
    >>> type(d) is dict
    True
    >>> sorted(d.items())
    [('bar', 'as well'), ('foo', 'is a fish')]

    If there is no value for a key it will be `None`:

    >>> parse_dict_header('key_without_value')
    {'key_without_value': None}

    To create a header from the :class:`dict` again, use the
    :func:`dump_header` function.

    :param value: a string with a dict header.
    :return: :class:`dict`
    """
    result = {}
    for item in parse_http_list(value):
        if '=' not in item:
            result[item] = None
            continue
        name, value = item.split('=', 1)
        if value[:1] == value[-1:] == '"':
            value = unquote_header_value(value[1:-1])
        result[name] = value
    return result

class Headers(object):
    '''Utility for managing HTTP headers for both clients and servers.
It has a dictionary like interface
with few extra functions to facilitate the insertion of multiple values.

From http://www.w3.org/Protocols/rfc2616/rfc2616.html

Section 4.2
The order in which header fields with differing field names are received is not
significant. However, it is "good practice" to send general-header fields first,
followed by request-header or response-header fields, and ending with
the entity-header fields.'''
    def __init__(self, headers=None, kind='server', strict=False):
        if isinstance(kind, int):
            kind = header_type.get(kind, 'both')
        else:
            kind = str(kind).lower()
        self.kind = kind
        self.strict = strict
        self.all_headers = TYPE_HEADER_FIELDS.get(self.kind)
        if not self.all_headers:
            self.kind = 'both'
            self.all_headers = TYPE_HEADER_FIELDS[self.kind]
        self._headers = {}
        if headers is not None:
            self.update(headers)

    def __repr__(self):
        return '%s %s' % (self.kind, self._headers.__repr__())

    def __str__(self):
        return '\r\n'.join(self._ordered())

    def __bytes__(self):
        return str(self).encode('latin-1')

    def __iter__(self):
        headers = self._headers
        for k, values in iteritems(headers):
            for value in values:
                yield k, value

    def __len__(self):
        return reduce(lambda x, y: x + len(y), itervalues(self._headers), 0)

    def as_dict(self):
        '''Convert this :class:`Headers` into a dictionary.'''
        return dict(((k, ', '.join(v)) for k, v in iteritems(self._headers)))

    @property
    def kind_number(self):
        return header_type_to_int.get(self.kind)

    def update(self, iterable):
        """Extend the headers with a dictionary or an iterable yielding keys
and values."""
        for key, value in mapping_iterator(iterable):
            self[key] = value

    def __contains__(self, key):
        return header_field(key) in self._headers

    def __getitem__(self, key):
        return ', '.join(self._headers[header_field(key)])

    def __delitem__(self, key):
        self._headers.__delitem__(header_field(key))

    def __setitem__(self, key, value):
        key = header_field(key, self.all_headers, self.strict)
        if key and value is not None:
            if not isinstance(value, list):
                value = [value]
            self._headers[key] = value

    def get(self, key, default=None):
        if key in self:
            return self.__getitem__(key)
        else:
            return default

    def get_all(self, key, default=None):
        return self._headers.get(header_field(key), default)

    def pop(self, key, *args):
        return self._headers.pop(header_field(key), *args)

    def copy(self):
        return self.__class__(self, kind=self.kind)

    def getheaders(self, key):
        '''Required by cookielib. If the key is not available,
it returns an empty list.'''
        return self._headers.get(header_field(key), [])

    def headers(self):
        return list(self)

    def add_header(self, key, value, **params):
        '''Add *value* to *key* header. If the header is already available,
append the value to the list.'''
        key = header_field(key, self.all_headers, self.strict)
        if key and value:
            values = self._headers.get(key, [])
            if value not in values:
                values.append(value)
                self._headers[key] = values

    def flat(self, version, status):
    	'''Full headers bytes representation'''
    	vs = version + (status, self)
    	return ('HTTP/%s.%s %s\r\n%s' % vs).encode(DEFAULT_CHARSET)

    def _ordered(self):
        hf = HEADER_FIELDS
        order = (('general',[]), ('request',[]), ('response',[]), ('entity',[]))
        headers = self._headers
        for key in headers:
            for name, group in order:
                if key in hf[name]:
                    group.append(key)
                    break
        for _, group in order:
            for k in group:
                yield "%s: %s" % (k, ', '.join(headers[k]))
        yield ''
        yield ''


###############################################################################
##    HTTP PARSER
###############################################################################
METHOD_RE = re.compile("[A-Z0-9$-_.]{3,20}")
VERSION_RE = re.compile("HTTP/(\d+).(\d+)")
STATUS_RE = re.compile("(\d{3})\s*(\w*)")
HEADER_RE = re.compile("[\x00-\x1F\x7F()<>@,;:\[\]={} \t\\\\\"]")

# errors
BAD_FIRST_LINE = 0
INVALID_HEADER = 1
INVALID_CHUNK = 2

class InvalidRequestLine(Exception):
    """ error raised when first line is invalid """


class InvalidHeader(Exception):
    """ error raised on invalid header """


class InvalidChunkSize(Exception):
    """ error raised when we parse an invalid chunk size """


class HttpParser(object):
    '''A python HTTP parser.

Original code from https://github.com/benoitc/http-parser

2011 (c) Benoit Chesneau <benoitc@e-engura.org>

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation
files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use,
copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.'''
    def __init__(self, kind=2, decompress=False):
        self.decompress = decompress
        # errors vars
        self.errno = None
        self.errstr = ""
        # protected variables
        self._buf = []
        self._version = None
        self._method = None
        self._status_code = None
        self._status = None
        self._reason = None
        self._url = None
        self._path = None
        self._query_string = None
        self._fragment= None
        self._headers = Headers(kind=kind)
        self._chunked = False
        self._body = []
        self._trailers = None
        self._partial_body = False
        self._clen = None
        self._clen_rest = None
        # private events
        self.__on_firstline = False
        self.__on_headers_complete = False
        self.__on_message_begin = False
        self.__on_message_complete = False
        self.__decompress_obj = None

    @property
    def kind(self):
        return self._headers.kind_number

    def get_version(self):
        return self._version

    def get_method(self):
        return self._method

    def get_status_code(self):
        return self._status_code

    def get_url(self):
        return self._url

    def get_path(self):
        return self._path

    def get_query_string(self):
        return self._query_string

    def get_fragment(self):
        return self._fragment

    def get_headers(self):
        return self._headers

    def recv_body(self):
        """ return last chunk of the parsed body"""
        body = b''.join(self._body)
        self._body = []
        self._partial_body = False
        return body

    def is_headers_complete(self):
        """ return True if all headers have been parsed. """
        return self.__on_headers_complete

    def is_partial_body(self):
        """ return True if a chunk of body have been parsed """
        return self._partial_body

    def is_message_begin(self):
        """ return True if the parsing start """
        return self.__on_message_begin

    def is_message_complete(self):
        """ return True if the parsing is done (we get EOF) """
        return self.__on_message_complete

    def is_chunked(self):
        """ return True if Transfer-Encoding header value is chunked"""
        return self._chunked

    def execute(self, data, length):
        # end of body can be passed manually by putting a length of 0
        if length == 0:
            return self.close(length)
        data = bytes(data)
        # start to parse
        nb_parsed = 0
        while True:
            if not self.__on_firstline:
                idx = data.find(b'\r\n')
                if idx < 0:
                    self._buf.append(data)
                    return len(data)
                else:
                    self.__on_firstline = True
                    self._buf.append(data[:idx])
                    first_line = native_str(b''.join(self._buf),DEFAULT_CHARSET)
                    rest = data[idx+2:]
                    data = b''
                    if self._parse_firstline(first_line):
                        nb_parsed = nb_parsed + idx + 2
                        self._buf = [rest]
                    else:
                        return nb_parsed
            elif not self.__on_headers_complete:
                if data:
                    self._buf.append(data)
                    data = b''
                try:
                    to_parse = b''.join(self._buf)
                    ret = self._parse_headers(to_parse)
                    if ret is False:
                        if to_parse == b'\r\n':
                            self._buf = []
                            return self.close(length)
                        return length
                    nb_parsed = nb_parsed + (len(to_parse) - ret)
                except InvalidHeader as e:
                    self.errno = INVALID_HEADER
                    self.errstr = str(e)
                    return nb_parsed
            elif not self.__on_message_complete:
                self.__on_message_begin = True
                if data:
                    self._buf.append(data)
                    data = b''
                ret = self._parse_body()
                if ret is None:
                    return length
                elif ret < 0:
                    return ret
                elif ret == 0:
                    self.__on_message_complete = True
                    return length
                else:
                    nb_parsed = max(length, ret)
            else:
                return 0

    def close(self, length):
        self.__on_message_begin = True
        self.__on_message_complete = True
        if not self._buf and self.__on_firstline:
            self.__on_headers_complete = True
            return length
        else:
            return length+len(self._buf)
        
    def _parse_firstline(self, line):
        try:
            if self.kind == 2: # auto detect
                try:
                    self._parse_request_line(line)
                except InvalidRequestLine:
                    self._parse_response_line(line)
            elif self.kind == 1:
                self._parse_response_line(line)
            elif self.kind == 0:
                self._parse_request_line(line)
        except InvalidRequestLine as e:
            self.errno = BAD_FIRST_LINE
            self.errstr = str(e)
            return False
        return True

    def _parse_response_line(self, line):
        bits = line.split(None, 1)
        if len(bits) != 2:
            raise InvalidRequestLine(line)

        # version
        matchv = VERSION_RE.match(bits[0])
        if matchv is None:
            raise InvalidRequestLine("Invalid HTTP version: %s" % bits[0])
        self._version = (int(matchv.group(1)), int(matchv.group(2)))

        # status
        matchs = STATUS_RE.match(bits[1])
        if matchs is None:
            raise InvalidRequestLine("Invalid status %" % bits[1])

        self._status = bits[1]
        self._status_code = int(matchs.group(1))
        self._reason = matchs.group(2)

    def _parse_request_line(self, line):
        bits = line.split(None, 2)
        if len(bits) != 3:
            raise InvalidRequestLine(line)
        # Method
        if not METHOD_RE.match(bits[0]):
            raise InvalidRequestLine("invalid Method: %s" % bits[0])
        self._method = bits[0].upper()
        # URI
        self._url = bits[1]
        parts = urlsplit(bits[1])
        self._path = parts.path or ""
        self._query_string = parts.query or ""
        self._fragment = parts.fragment or ""
        # Version
        match = VERSION_RE.match(bits[2])
        if match is None:
            raise InvalidRequestLine("Invalid HTTP version: %s" % bits[2])
        self._version = (int(match.group(1)), int(match.group(2)))

    def _parse_headers(self, data):
        idx = data.find(b'\r\n\r\n')
        if idx < 0: # we don't have all headers
            return False
        chunk = native_str(data[:idx], DEFAULT_CHARSET)
        # Split lines on \r\n keeping the \r\n on each line
        lines = deque(('%s\r\n' % line for line in chunk.split('\r\n')))
        # Parse headers into key/value pairs paying attention
        # to continuation lines.
        while len(lines):
            # Parse initial header name : value pair.
            curr = lines.popleft()
            if curr.find(":") < 0:
                continue
            name, value = curr.split(":", 1)
            name = name.rstrip(" \t").upper()
            if HEADER_RE.search(name):
                raise InvalidHeader("invalid header name %s" % name)
            name, value = name.strip(), [value.lstrip()]
            # Consume value continuation lines
            while len(lines) and lines[0].startswith((" ", "\t")):
                value.append(lines.popleft())
            value = ''.join(value).rstrip()
            # multiple headers
            if name in self._headers:
                value = "%s, %s" % (self._headers[name], value)
            # store new header value
            self._headers[name] = value
        # detect now if body is sent by chunks.
        clen = self._headers.get('content-length')
        te = self._headers.get('transfer-encoding', '').lower()
        self._chunked = (te == 'chunked')
        if clen and not self._chunked:
            try:
                clen = int(clen)
            except ValueError:
                clen = None
            else:
                if clen < 0:  # ignore nonsensical negative lengths
                    clen = None
        else:
            clen = None
        status = self._status_code
        if status and (status == httpclient.NO_CONTENT or
            status == httpclient.NOT_MODIFIED or
            100 <= status < 200 or      # 1xx codes
            self._method == "HEAD"):
            clen = 0
        self._clen_rest = self._clen = clen
        # detect encoding and set decompress object
        if self.decompress:
            encoding = self._headers.get('content-encoding')
            if encoding == "gzip":
                self.__decompress_obj = zlib.decompressobj(16+zlib.MAX_WBITS)
            elif encoding == "deflate":
                self.__decompress_obj = zlib.decompressobj()
        rest = data[idx+4:]
        self._buf = [rest]
        self.__on_headers_complete = True
        return len(rest)

    def _parse_body(self):
        data = b''.join(self._buf)
        if not self._chunked:
            if self._clen_rest is not None:
                self._clen_rest -= len(data)
            # maybe decompress
            if self.__decompress_obj is not None:
                data = self.__decompress_obj.decompress(data)
            self._partial_body = True
            if data:
                self._body.append(data)
            self._buf = []
            if self._clen_rest is None or self._clen_rest <= 0:
                self.__on_message_complete = True
        else:
            try:
                size, rest = self._parse_chunk_size(data)
            except InvalidChunkSize as e:
                self.errno = INVALID_CHUNK
                self.errstr = "invalid chunk size [%s]" % str(e)
                return -1
            if size == 0:
                return size
            if size is None or len(rest) < size:
                return None
            body_part, rest = rest[:size], rest[size:]
            if len(rest) < 2:
                self.errno = INVALID_CHUNK
                self.errstr = "chunk missing terminator [%s]" % data
                return -1
            # maybe decompress
            if self.__decompress_obj is not None:
                body_part = self.__decompress_obj.decompress(body_part)
            self._partial_body = True
            self._body.append(body_part)
            self._buf = [rest[2:]]
            return len(rest)

    def _parse_chunk_size(self, data):
        idx = data.find(b'\r\n')
        if idx < 0:
            return None, None
        line, rest_chunk = data[:idx], data[idx+2:]
        chunk_size = line.split(b';', 1)[0].strip()
        try:
            chunk_size = int(chunk_size, 16)
        except ValueError:
            raise InvalidChunkSize(chunk_size)
        if chunk_size == 0:
            self._parse_trailers(rest_chunk)
            return 0, None
        return chunk_size, rest_chunk

    def _parse_trailers(self, data):
        idx = data.find(b'\r\n\r\n')
        if data[:2] == b'\r\n':
            self._trailers = self._parse_headers(data[:idx])


################################################################################
##    HTTP CLIENT
################################################################################

class WWWAuthenticate(object):
    _require_quoting = frozenset(['domain', 'nonce', 'opaque', 'realm'])
    
    def __init__(self, type, **options):
        self.type = type
        self.options = options
    
    @classmethod
    def basic(cls, realm='authentication required'):
        """Clear the auth info and enable basic auth."""
        return cls('basic', realm=realm)
    
    @classmethod    
    def digest(cls, realm, nonce, qop=('auth',), opaque=None,
               algorithm=None, stale=None):
        options = {}
        if stale:
            options['stale'] = 'TRUE'
        if opaque is not None:
            options['opaque'] = opaque
        if algorithm is not None:
            options['algorithm'] = algorithm
        return cls('digest', realm=realm, nonce=nonce,
                   qop=', '.join(qop), **options)
    
    def __str__(self):
        """Convert the stored values into a WWW-Authenticate header."""
        return '%s %s' % (self.type.title(), ', '.join((
            '%s=%s' % (key, quote_header_value(value,
                                allow_token=key not in self._require_quoting))
            for key, value in iteritems(self.options)
        )))
    __repr__ = __str__
        
###############################################    UTILITIES, ENCODERS, PARSERS
def get_environ_proxies():
    """Return a dict of environment proxies. From requests_."""

    proxy_keys = [
        'all',
        'http',
        'https',
        'ftp',
        'socks',
        'no'
    ]

    get_proxy = lambda k: os.environ.get(k) or os.environ.get(k.upper())
    proxies = [(key, get_proxy(key + '_proxy')) for key in proxy_keys]
    return dict([(key, val) for (key, val) in proxies if val])

def addslash(url):
    '''Add a slash at the beginning of *url*.'''
    if not url.startswith('/'):
        url = '/%s' % url
    return url

def appendslash(url):
    '''Append a slash to *url* if it does not have one.'''
    if not url.endswith('/'):
        url = '%s/' % url
    return url

def choose_boundary():
    """Our embarassingly-simple replacement for mimetools.choose_boundary."""
    return uuid4().hex

def get_content_type(filename):
    return mimetypes.guess_type(filename)[0] or 'application/octet-stream'

def encode_multipart_formdata(fields, boundary=None, charset=None):
    """
    Encode a dictionary of ``fields`` using the multipart/form-data mime format.

    :param fields:
        Dictionary of fields or list of (key, value) field tuples.  The key is
        treated as the field name, and the value as the body of the form-data
        bytes. If the value is a tuple of two elements, then the first element
        is treated as the filename of the form-data section.

        Field names and filenames must be unicode.

    :param boundary:
        If not specified, then a random boundary will be generated using
        :func:`mimetools.choose_boundary`.
    """
    charset = charset or 'utf-8'
    body = BytesIO()
    if boundary is None:
        boundary = choose_boundary()
    for fieldname, value in mapping_iterator(fields):
        body.write(('--%s\r\n' % boundary).encode(charset))
        if isinstance(value, tuple):
            filename, data = value
            body.write(('Content-Disposition: form-data; name="%s"; '
                        'filename="%s"\r\n' % (fieldname, filename))\
                       .encode(charset))
            body.write(('Content-Type: %s\r\n\r\n' %
                       (get_content_type(filename))).encode(charset))
        else:
            data = value
            body.write(('Content-Disposition: form-data; name="%s"\r\n'
                        % (fieldname)).encode(charset))
            body.write(b'Content-Type: text/plain\r\n\r\n')
        data = body.write(to_bytes(data))
        body.write(b'\r\n')
    body.write(('--%s--\r\n' % (boundary)).encode(charset))
    content_type = 'multipart/form-data; boundary=%s' % boundary
    return body.getvalue(), content_type

def hexmd5(x):
    return md5(to_bytes(x)).hexdigest()

def hexsha1(x):
    return sha1(to_bytes(x)).hexdigest()

def http_date(epoch_seconds=None):
    """
    Formats the time to match the RFC1123 date format as specified by HTTP
    RFC2616 section 3.3.1.

    Accepts a floating point number expressed in seconds since the epoch, in
    UTC - such as that outputted by time.time(). If set to None, defaults to
    the current time.

    Outputs a string in the format 'Wdy, DD Mon YYYY HH:MM:SS GMT'.
    """
    return formatdate(epoch_seconds, usegmt=True)

#################################################################### COOKIE
def create_cookie(name, value, **kwargs):
    """Make a cookie from underspecified parameters.

    By default, the pair of `name` and `value` will be set for the domain ''
    and sent on every request (this is sometimes called a "supercookie").
    """
    result = dict(
        version=0,
        name=name,
        value=value,
        port=None,
        domain='',
        path='/',
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={'HttpOnly': None},
        rfc2109=False,)
    badargs = set(kwargs) - set(result)
    if badargs:
        err = 'create_cookie() got unexpected keyword arguments: %s'
        raise TypeError(err % list(badargs))
    result.update(kwargs)
    result['port_specified'] = bool(result['port'])
    result['domain_specified'] = bool(result['domain'])
    result['domain_initial_dot'] = result['domain'].startswith('.')
    result['path_specified'] = bool(result['path'])
    return Cookie(**result)

def cookiejar_from_dict(cookie_dict, cookiejar=None):
    """Returns a CookieJar from a key/value dictionary.

    :param cookie_dict: Dict of key/values to insert into CookieJar.
    """
    if cookiejar is None:
        cookiejar = CookieJar()
    if cookie_dict is not None:
        for name in cookie_dict:
            cookiejar.set_cookie(create_cookie(name, cookie_dict[name]))
    return cookiejar

def parse_cookie(cookie):
    if cookie == '':
        return {}
    if not isinstance(cookie, BaseCookie):
        try:
            c = SimpleCookie()
            c.load(cookie)
        except CookieError:
            # Invalid cookie
            return {}
    else:
        c = cookie
    cookiedict = {}
    for key in c.keys():
        cookiedict[key] = c.get(key).value
    return cookiedict


cc_delim_re = re.compile(r'\s*,\s*')


class accept_content_type(object):

    def __init__(self, values=None):
        self._all = {}
        self.update(values)

    def update(self, values):
        if values:
            all = self._all
            accept_headers = cc_delim_re.split(values)
            for h in accept_headers:
                v = h.split('/')
                if len(v) == 2:
                    a, b = v
                    if a in all:
                        all[a].append(b)
                    else:
                        all[a] = [b]

    def __contains__(self, content_type):
        all = self._all
        if not all:
            # If no Accept header field is present, then it is assumed that the
            # client accepts all media types.
            return True
        a, b = content_type.split('/')
        if a in all:
            all = all[a]
            if '*' in all:
                return True
            else:
                return b in all
        elif '*' in all:
            return True
        else:
            return False


def patch_vary_headers(response, newheaders):
    """\
Adds (or updates) the "Vary" header in the given HttpResponse object.
newheaders is a list of header names that should be in "Vary". Existing
headers in "Vary" aren't removed.

For information on the Vary header, see:

    http://www.w3.org/Protocols/rfc2616/rfc2616-sec14.html#sec14.44
    """
    # Note that we need to keep the original order intact, because cache
    # implementations may rely on the order of the Vary contents in, say,
    # computing an MD5 hash.
    if 'Vary' in response:
        vary_headers = cc_delim_re.split(response['Vary'])
    else:
        vary_headers = []
    # Use .lower() here so we treat headers as case-insensitive.
    existing_headers = set([header.lower() for header in vary_headers])
    additional_headers = [newheader for newheader in newheaders
                          if newheader.lower() not in existing_headers]
    response['Vary'] = ', '.join(vary_headers + additional_headers)


def has_vary_header(response, header_query):
    """
    Checks to see if the response has a given header name in its Vary header.
    """
    if not response.has_header('Vary'):
        return False
    vary_headers = cc_delim_re.split(response['Vary'])
    existing_headers = set([header.lower() for header in vary_headers])
    return header_query.lower() in existing_headers


class CacheControl(object):
    '''
    http://www.mnot.net/cache_docs/

.. attribute:: maxage

    Specifies the maximum amount of time that a representation will be
    considered fresh.
    '''
    def __init__(self, maxage=None, private=False,
                 must_revalidate=False, proxy_revalidate=False,
                 nostore=False):
        self.maxage = maxage
        self.private = private
        self.must_revalidate = must_revalidate
        self.proxy_revalidate = proxy_revalidate
        self.nostore = nostore

    def __call__(self, headers):
        if self.nostore:
            headers['cache-control'] = 'no-store'
        elif self.maxage:
            headers['cache-control'] = 'max-age=%s' % self.maxage
            if self.private:
                headers.add_header('cache-control', 'private')
            else:
                headers.add_header('cache-control', 'public')
            if self.must_revalidate:
                headers.add_header('cache-control', 'must-revalidate')
            elif self.proxy_revalidate:
                headers.add_header('cache-control', 'proxy-revalidate')
        else:
            headers['cache-control'] = 'no-cache'
