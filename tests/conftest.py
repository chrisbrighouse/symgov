import os


# Production fails closed without deployment-provided security settings.
# Tests explicitly opt into the repository's non-production local contract.
os.environ.setdefault("SYMGOV_ENVIRONMENT", "test")
