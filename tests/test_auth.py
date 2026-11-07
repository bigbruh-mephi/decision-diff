from fastapi import Request, Response

from decision_diff import auth


def _request(scheme, cookie=""):
    return Request({"type": "http", "scheme": scheme, "method": "GET", "path": "/", "query_string": b"",
                    "server": ("x", 80), "headers": [(b"cookie", cookie.encode())]})


def test_plain_http_cookie_is_not_secure_so_browsers_keep_it_on_any_host():
    response = Response()
    name, secure = auth.session_cookie(_request("http"))
    response.set_cookie(name, "t", secure=secure)
    header = response.headers["set-cookie"].lower()
    assert header.startswith("dd_session=") and "secure" not in header


def test_https_keeps_the_host_prefixed_secure_cookie():
    assert auth.session_cookie(_request("https")) == (auth.COOKIE, True)


def test_session_token_reads_only_the_cookie_of_the_request_scheme():
    both = f"dd_session=plain; {auth.COOKIE}=host"
    assert auth.session_token(_request("http", "dd_session=plain")) == "plain"
    assert auth.session_token(_request("http", both)) == "plain"
    assert auth.session_token(_request("http", f"{auth.COOKIE}=host")) is None
    assert auth.session_token(_request("https", both)) == "host"
    assert auth.session_token(_request("https", "dd_session=plain")) is None
