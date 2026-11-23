# Vendored from huggingface.co/spaces/oddadmix/tiny-voca-arabic @64e3e2a (Apache-2.0).
# Lura edits: none.
"""
CATT: Character-based Arabic Tashkeel Transformer package.
"""

from .models import CATTEncoderDecoder, CATTEncoderOnly
from .utils import remove_non_arabic

__version__ = "1.0.2"
__author__ = "Faris Alasmary"
__email__ = "farisalasmary@gmail.com"
