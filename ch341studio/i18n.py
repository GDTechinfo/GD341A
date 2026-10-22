"""Tradução simples: tr("texto em português") -> texto no idioma escolhido.

O português é o idioma de origem. O inglês fica em EN (chave = texto em português).
Textos sem tradução aparecem em português.
"""

LANG = "pt"
LANGS = {"pt": "Português", "en": "English"}


def set_lang(lang: str):
    global LANG
    LANG = lang if lang in LANGS else "pt"


def tr(text: str, **kw) -> str:
    out = EN.get(text, text) if LANG == "en" else text
    return out.format(**kw) if kw else out


EN: dict[str, str] = {}

from .i18n_en import EN as _EN  # noqa: E402

EN.update(_EN)
