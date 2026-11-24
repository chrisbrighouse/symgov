class VisualMatchInputError(ValueError):
    """The input image is unusable. Maps to a 4xx at the API, never to a catalog no-match."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class VisualMatchUnavailable(RuntimeError):
    """The index cannot answer yet (too little of the Catalog is indexed). Maps to a 503, never to a no-match."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
