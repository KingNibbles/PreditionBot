"""A web client that is only created on first use, so importing the app never needs httpx."""


class LazyHttp:
    _http = None
    timeout = 10.0

    @property
    def http(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=self.timeout)
        return self._http
