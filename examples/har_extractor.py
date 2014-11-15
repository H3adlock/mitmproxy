"""
    This inline script utilizes harparser.HAR from https://github.com/JustusW/harparser
    to generate a HAR log object.
"""
from harparser import HAR
from datetime import datetime, timedelta, tzinfo


class UTC(tzinfo):
    def utcoffset(self, dt):
        return timedelta(0)

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "Z"


class _HARLog(HAR.log):
    def __init__(self):
        HAR.log.__init__(self, {"version": "1.2",
                                "creator": {"name": "MITMPROXY HARExtractor",
                                            "version": "0.1",
                                            "comment": ""},
                                "pages": [],
                                "entries": []})

    def reset(self):
        self.__init__()

    def add(self, obj):
        if isinstance(obj, HAR.pages):
            self['pages'].append(obj)
        if isinstance(obj, HAR.entries):
            self['entries'].append(obj)


def start(context, argv):
    """
        On start we reset the HAR, it's not really necessary since it will have been
        instantiated earlier during initial parsing of this file. You will have to
        adapt this to suit your actual needs of HAR generation.
    """
    HARLog.reset()
    context.seen_server_connect = set()
    context.seen_server_ssl = set()


def response(context, flow):
    """
       Called when a server response has been received. At the time of this message both
       a request and a response are present and completely done.
    """
    connect_time = -1
    if flow.server_conn not in context.seen_server_connect:
        # Calculate the connect_time for this server_conn. Afterwards add it to seen list, in
        # order to avoid the connect_time being present in entries that use an existing connection.
        connect_time = flow.server_conn.timestamp_tcp_setup - flow.server_conn.timestamp_start
        context.seen_server_connect.add(flow.server_conn)

    ssl_time = -1
    if flow.server_conn not in context.seen_server_connect \
            and flow.server_conn.timestamp_ssl_setup is not None:
        # Get the ssl_time for this server_conn as the difference between the start of the successful
        # tcp setup and the successful ssl setup. Afterwards add it to seen list, in order to avoid
        # the ssl_time being present in entries that use an existing connection. If  no ssl setup has
        # been made initiate it is also left as -1 since it doesn't apply to this connection.
        ssl_time = flow.server_conn.timestamp_ssl_setup - flow.server_conn.timestamp_tcp_setup
        context.seen_server_ssl.add(flow.server_conn)

    # Calculate the raw timings from the different timestamps present in the request and response object.
    # For lack of a way to measure it dns timings can not be calculated. The same goes for HAR blocked:
    # MITMProxy will open a server connection as soon as it receives the host and port from the client
    # connection. So the time spent waiting is actually spent waiting between request.timestamp_end and
    # response.timestamp_start thus it correlates to HAR wait instead.
    timings_raw = {'send': flow.request.timestamp_end - flow.request.timestamp_start,
                   'wait': flow.response.timestamp_start - flow.request.timestamp_end,
                   'receive': flow.response.timestamp_end - flow.response.timestamp_start,
                   'connect': connect_time,
                   'ssl': ssl_time}

    # HAR timings are integers in ms, so we have to re-encode the raw timings to that format.
    timings = dict([(key, int(1000 * value)) for key, value in timings_raw.iteritems()])

    # The full_time is the sum of all timings. Timings set to -1 will be ignored as per spec.
    full_time = 0
    for item in timings.values():
        if item > -1:
            full_time += item

    started_date_time = datetime.fromtimestamp(flow.request.timestamp_start, tz=UTC()).isoformat()

    request_query_string = [{"name": k, "value": v} for k, v in flow.request.get_query()]
    request_http_version = ".".join([str(v) for v in flow.request.httpversion])
    # Cookies are shaped as tuples by MITMProxy.
    request_cookies = [{"name": k.strip(), "value": v[0]} for k, v in (flow.request.get_cookies() or {}).iteritems()]
    request_headers = [{"name": k, "value": v} for k, v in flow.request.headers]
    request_headers_size = len(str(flow.request.headers))
    request_body_size = len(flow.request.content)

    response_http_version = ".".join([str(v) for v in flow.response.httpversion])
    # Cookies are shaped as tuples by MITMProxy.
    response_cookies = [{"name": k.strip(), "value": v[0]} for k, v in (flow.response.get_cookies() or {}).iteritems()]
    response_headers = [{"name": k, "value": v} for k, v in flow.response.headers]
    response_headers_size = len(str(flow.response.headers))
    response_body_size = len(flow.response.content)
    response_body_decoded_size = len(flow.response.content)
    response_body_compression = response_body_decoded_size - response_body_size
    response_mime_type = flow.response.headers.get('Content-Type', [''])[0]
    response_redirect_url = flow.response.headers.get('Location', [''])[0]

    entry = HAR.entries({"startedDateTime": started_date_time,
                         "time": full_time,
                         "request": {"method": flow.request.method,
                                     "url": flow.request.url,
                                     "httpVersion": request_http_version,
                                     "cookies": request_cookies,
                                     "headers": request_headers,
                                     "queryString": request_query_string,
                                     "headersSize": request_headers_size,
                                     "bodySize": request_body_size, },
                         "response": {"status": flow.response.code,
                                      "statusText": flow.response.msg,
                                      "httpVersion": response_http_version,
                                      "cookies": response_cookies,
                                      "headers": response_headers,
                                      "content": {"size": response_body_size,
                                                  "compression": response_body_compression,
                                                  "mimeType": response_mime_type},
                                      "redirectURL": response_redirect_url,
                                      "headersSize": response_headers_size,
                                      "bodySize": response_body_size, },
                         "cache": {},
                         "timings": timings, })

    # If the current url is in HARPAGE_LIST or does not have a referer we add it as a new pages object.
    if flow.request.url in HARPAGE_LIST or flow.request.headers.get('Referer', None) is None:
        PAGE_COUNT[1] += 1
        page_id = "_".join([str(v) for v in PAGE_COUNT])
        HARLog.add(HAR.pages({"startedDateTime": entry['startedDateTime'],
                              "id": page_id,
                              "title": flow.request.url, }))
        PAGE_REF[flow.request.url] = page_id
        entry['pageref'] = page_id

    # Lookup the referer in our PAGE_REF dict to point this entries pageref attribute to the right pages object.
    elif flow.request.headers.get('Referer', (None, ))[0] in PAGE_REF.keys():
        entry['pageref'] = PAGE_REF[flow.request.headers['Referer'][0]]
        PAGE_REF[flow.request.url] = entry['pageref']

    HARLog.add(entry)


def done(context):
    """
        Called once on script shutdown, after any other events.
    """
    from pprint import pprint
    import json

    json_dump = HARLog.json()
    compressed_json_dump = HARLog.compress()

    print "=" * 100
    pprint(json.loads(json_dump))
    print "=" * 100
    print "HAR log finished with %s bytes (%s bytes compressed)" % (len(json_dump), len(compressed_json_dump))
    print "Compression rate is %s%%" % str(100. * len(compressed_json_dump) / len(json_dump))
    print "=" * 100


def print_attributes(obj, filter_string=None, hide_privates=False):
    """
        Useful helper method to quickly get all attributes of an object and its values.
    """
    for attr in dir(obj):
        if hide_privates and "__" in attr:
            continue
        if filter_string is not None and filter_string not in attr:
            continue
        value = getattr(obj, attr)
        print "%s.%s" % ('obj', attr), value, type(value)


# Some initializations. Add any page you want to have its own pages object to HARPAGE_LIST
HARPAGE_LIST = ['https://github.com/']
HARLog = _HARLog()

CONNECT_TIMES = {}
SSL_TIMES = {}
PAGE_REF = {}
PAGE_COUNT = ['autopage', 0]
