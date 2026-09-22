"""Spelling, and why grammar is not here.

The spell checker is created directly: the one from LinguServiceManager
resolves in pyuno to the XSpellChecker1 overload, which wants a numeric
language id and rejects a Locale. A word is judged against the language of
the run it sits in, so text marked with the wrong language reads as
misspelled even when it is correct.
"""

from typing import Any, Dict, List
import logging
from uno_values import (AddressError, DEFAULT_SPELLING_RESULTS, 
    MAX_SPELLING_RESULTS, WORD, _locale_name, refusal)

logger = logging.getLogger(__name__)


class SpellingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def check_spelling(self, address: Any = None,
                       max_results: int = DEFAULT_SPELLING_RESULTS,
                       number: bool = False,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Report misspelled words with an address for each

        Every hit's address resolves back to the word, so it can be handed to
        replace_range. Words are judged against the language of the text
        portion they sit in, not the paragraph's or the document's, so an
        English term inside a Russian sentence is checked as English — the
        distinction that makes the report worth reading at all.

        A language with no dictionary installed is skipped and named rather
        than having all of its words called misspellings.

        **A scoped check does not walk the document.** It used to: the address
        was turned into a paragraph *number* — one walk of the body — and then
        every paragraph in the document was enumerated to find the one with
        that number, another walk. Measured on the 519-page guide, checking a
        single paragraph cost 4.0s by number and 5.4–6.2s by anchor, for an
        answer that says `checked_paragraphs: 1`; reading the same paragraph's
        runs through the same anchor was 0.05s. The range an address resolves
        to names its own paragraphs, so that is where they come from now, and
        a hit is addressed by its paragraph's **anchor**. A number is reported
        beside it only when it is free — the address named one — or when
        `number` asks for the walk that works it out.
        """
        doc, error = self._writer_document(doc, "Spell checking")
        if error:
            return error

        try:
            speller = self._spell_checker()
        except Exception as e:
            logger.error(f"No spell checker available: {e}")
            return {"success": False, "code": "UNSUPPORTED", "error": f"No spell checker available: {e}"}

        try:
            paragraphs, scope, first = self._paragraphs_over(doc, address)
            if paragraphs is not None and first is None and number:
                first = self._paragraph_index_of(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if paragraphs is None:
            # The whole document: the walk numbers every paragraph as it goes,
            # so the numbers cost nothing here.
            walk = self._body_paragraphs(doc)
        else:
            walk = ((paragraph, None if first is None else first + step)
                    for step, paragraph in enumerate(paragraphs))

        limit = max(1, min(int(max_results), MAX_SPELLING_RESULTS))
        misspelled = []
        total = 0
        checked_paragraphs = 0
        skipped = []
        held = {}

        for paragraph, position in walk:
            checked_paragraphs += 1
            for word, offset, locale in self._words_of(paragraph, speller,
                                                       skipped):
                if speller.isValid(word, locale, ()):
                    continue
                total += 1
                if len(misspelled) >= limit:
                    continue
                spot = {"offset": offset, "length": len(word)}
                if position is not None:
                    spot["paragraph"] = position
                if id(paragraph) not in held:
                    # One anchor per paragraph, and only for the paragraphs a
                    # hit is actually reported in.
                    held[id(paragraph)] = self._anchor_handle(
                        self._hold_paragraph_anchor(doc, paragraph, position),
                        "paragraph")
                if held[id(paragraph)]:
                    spot["anchor"] = held[id(paragraph)]
                misspelled.append({
                    "word": word,
                    "address": spot,
                    "suggestions": self._suggestions(speller, word, locale),
                    "language": _locale_name(locale)
                })

        logger.info(f"Spell checked {checked_paragraphs} paragraphs, "
                    f"{total} misspellings")
        return {
            "success": True,
            "misspelled": misspelled,
            "total_misspelled": total,
            "truncated": total > len(misspelled),
            "checked_paragraphs": checked_paragraphs,
            "scope": scope,
            "skipped_languages": skipped
        }

    def _spell_checker(self) -> Any:
        """The spell checker, created once per bridge

        The service is created directly rather than through
        LinguServiceManager: the manager's checker resolves in pyuno to the
        XSpellChecker1 overload, which wants a numeric language id and rejects
        every Locale with "Type 17 is not supported".
        """
        speller = getattr(self, "_speller", None)
        if speller is None:
            speller = self.smgr.createInstanceWithContext(
                "com.sun.star.linguistic2.SpellChecker", self.ctx)
            self._speller = speller
        return speller

    def _words_of(self, paragraph: Any, speller: Any, skipped: List[str]):
        """
        Yield (word, offset in paragraph, locale) for a paragraph

        Offsets accumulate across text portions, so the address of a word in
        the third run still points at the right characters.
        """
        offset = 0
        try:
            portions = paragraph.createEnumeration()
        except Exception as e:
            logger.info(f"Could not read the portions of a paragraph: {e}")
            return

        while portions.hasMoreElements():
            portion = portions.nextElement()
            try:
                text = portion.getString()
                locale = portion.CharLocale
            except Exception as e:
                logger.info(f"Skipping an unreadable portion: {e}")
                continue

            name = _locale_name(locale)
            if not name:
                offset += len(text)
                continue

            try:
                known = speller.hasLocale(locale)
            except Exception as e:
                logger.info(f"Could not ask about {name}: {e}")
                known = False

            if not known:
                if name not in skipped:
                    skipped.append(name)
                offset += len(text)
                continue

            for match in WORD.finditer(text):
                yield match.group(), offset + match.start(), locale
            offset += len(text)

    def _suggestions(self, speller: Any, word: str, locale: Any) -> List[str]:
        """What the dictionary offers instead of a word"""
        try:
            alternatives = speller.spell(word, locale, ())
            if alternatives is None:
                return []
            return list(alternatives.getAlternatives())
        except Exception as e:
            logger.info(f"No suggestions for {word!r}: {e}")
            return []
