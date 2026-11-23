class VisualMatchInputError(ValueError):
    """The input image is unusable. Maps to a 4xx at the API, never to a catalog no-match."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
