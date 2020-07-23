from __future__ import (
    absolute_import,
    division,
    print_function,
    unicode_literals
)

import re
import uuid


def camelcase_to_snakecase(name):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def gen_random_id():
    """Generate correlationID."""
    return str(uuid.uuid4()).replace('-', '')