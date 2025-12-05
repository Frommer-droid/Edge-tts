"""Custom dictionary for word replacements in TTS text preprocessing.

Supports two types of replacements:
1. Exact match: "Конечно=Конешно" (matches only "Конечно")
2. Wildcard: "Конечн*=Конечън*" (matches "Конечная", "Конечной", etc.)

Priority: Exact matches are applied first, then wildcard patterns.
"""

import re
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)


class CustomDictionary:
    """Manages custom word replacements for TTS pronunciation fixes."""
    
    def __init__(self, dictionary_path: Optional[Path] = None):
        """
        Initialize the custom dictionary.
        
        Args:
            dictionary_path: Path to the dictionary file. If None, uses default location.
        """
        self.dictionary_path = dictionary_path
        
        # Exact matches (no wildcards)
        self.exact_replacements: Dict[str, str] = {}
        self._exact_patterns: Dict[str, re.Pattern] = {}
        
        # Wildcard matches (contain *)
        self.wildcard_replacements: List[Tuple[str, str, re.Pattern]] = []
        
        if self.dictionary_path and self.dictionary_path.exists():
            self.load()
    
    @property
    def replacements(self) -> Dict[str, str]:
        """Combined replacements for backwards compatibility."""
        result = self.exact_replacements.copy()
        for source, target, _ in self.wildcard_replacements:
            result[source] = target
        return result
    
    def load(self) -> None:
        """Load dictionary from file."""
        if not self.dictionary_path or not self.dictionary_path.exists():
            logger.warning(f"Dictionary file not found: {self.dictionary_path}")
            return
        
        try:
            with open(self.dictionary_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            self.exact_replacements.clear()
            self._exact_patterns.clear()
            self.wildcard_replacements.clear()
            
            for line_num, line in enumerate(lines, start=1):
                line = line.strip()
                if not line or line.startswith('#'):
                    # Skip empty lines and comments
                    continue
                
                if '=' not in line:
                    logger.warning(f"Invalid format at line {line_num}: {line}")
                    continue
                
                parts = line.split('=', 1)
                if len(parts) != 2:
                    logger.warning(f"Invalid format at line {line_num}: {line}")
                    continue
                
                source_word = parts[0].strip()
                target_word = parts[1].strip()
                
                if not source_word or not target_word:
                    logger.warning(f"Empty word at line {line_num}: {line}")
                    continue
                
                # Check if this is a wildcard pattern
                if '*' in source_word:
                    self._add_wildcard_replacement(source_word, target_word)
                else:
                    self._add_exact_replacement(source_word, target_word)
            
            total = len(self.exact_replacements) + len(self.wildcard_replacements)
            logger.info(f"Loaded {total} replacements ({len(self.exact_replacements)} exact, {len(self.wildcard_replacements)} wildcard) from {self.dictionary_path}")
        
        except Exception as e:
            logger.error(f"Error loading dictionary: {e}")
    
    def _add_exact_replacement(self, source_word: str, target_word: str) -> None:
        """Add an exact match replacement."""
        self.exact_replacements[source_word] = target_word
        
        # Compile regex pattern for case-insensitive whole-word matching
        pattern = re.compile(
            r'\b' + re.escape(source_word) + r'\b',
            flags=re.IGNORECASE
        )
        self._exact_patterns[source_word] = pattern
    
    def _add_wildcard_replacement(self, source_word: str, target_word: str) -> None:
        """Add a wildcard replacement."""
        # Convert wildcard to regex
        # "Конечн*" -> r"\b(Конечн.*?)\b"
        # We need to capture the matched part to preserve case
        
        # Escape everything except *
        escaped = re.escape(source_word).replace(r'\*', '(.*?)')
        pattern_str = r'\b' + escaped + r'\b'
        
        pattern = re.compile(pattern_str, flags=re.IGNORECASE)
        self.wildcard_replacements.append((source_word, target_word, pattern))
    
    def apply_replacements(self, text: str) -> str:
        """
        Apply dictionary replacements to text.
        
        Priority:
        1. Exact matches are applied first
        2. Then wildcard patterns
        
        Replacements are case-insensitive but preserve the case of the original text.
        Only whole words are replaced (using word boundaries).
        
        Args:
            text: Input text
        
        Returns:
            Text with replacements applied
        """
        if not self.exact_replacements and not self.wildcard_replacements:
            return text
        
        result = text
        
        # 1. Apply exact matches first (higher priority)
        for source_word, target_word in self.exact_replacements.items():
            pattern = self._exact_patterns.get(source_word)
            if not pattern:
                continue
            
            # Find all matches
            matches = list(pattern.finditer(result))
            
            # Replace in reverse order to preserve indices
            for match in reversed(matches):
                matched_text = match.group(0)
                
                # Preserve case of the matched text
                replacement = self._preserve_case(matched_text, target_word)
                
                # Replace the matched text
                result = result[:match.start()] + replacement + result[match.end():]
        
        # 2. Apply wildcard patterns
        for source_pattern, target_pattern, compiled_pattern in self.wildcard_replacements:
            matches = list(compiled_pattern.finditer(result))
            
            # Replace in reverse order to preserve indices
            for match in reversed(matches):
                matched_text = match.group(0)
                
                # Get the wildcard part (captured group)
                wildcard_part = match.group(1) if match.lastindex and match.lastindex >= 1 else ''
                
                # Replace * in target with the captured part
                target_replacement = target_pattern.replace('*', wildcard_part)
                
                # Preserve case
                replacement = self._preserve_case(matched_text, target_replacement)
                
                # Replace the matched text
                result = result[:match.start()] + replacement + result[match.end():]
        
        return result
    
    @staticmethod
    def _preserve_case(original: str, replacement: str) -> str:
        """
        Preserve the case pattern of the original text in the replacement.
        
        Examples:
            original="СЛОВО", replacement="словъ" -> "СЛОВЪ"
            original="Слово", replacement="словъ" -> "Словъ"
            original="слово", replacement="СЛОВЪ" -> "словъ"
        
        Args:
            original: Original matched text
            replacement: Replacement text
        
        Returns:
            Replacement with case preserved from original
        """
        if not original:
            return replacement
            
        if original.isupper():
            # ALL CAPS
            return replacement.upper()
        elif original[0].isupper() and (len(original) == 1 or original[1:].islower()):
            # Title Case
            return replacement.capitalize()
        else:
            # lowercase or mixed case - use replacement as-is (lowercase)
            return replacement.lower()
    
    def save(self) -> None:
        """Save dictionary to file with alphabetical sorting."""
        if not self.dictionary_path:
            logger.warning("No dictionary path set, cannot save")
            return
        
        try:
            # Combine exact and wildcard replacements
            all_replacements = list(self.exact_replacements.items())
            for source, target, _ in self.wildcard_replacements:
                all_replacements.append((source, target))
            
            # Sort alphabetically by source word (ignoring case)
            sorted_items = sorted(all_replacements, key=lambda x: x[0].lower())
            
            with open(self.dictionary_path, 'w', encoding='utf-8') as f:
                for source_word, target_word in sorted_items:
                    f.write(f"{source_word}={target_word}\n")
            
            logger.info(f"Saved {len(all_replacements)} replacements to {self.dictionary_path}")
        
        except Exception as e:
            logger.error(f"Error saving dictionary: {e}")
    
    def reload(self) -> None:
        """Reload dictionary from file."""
        self.load()
    
    def add_replacement(self, source_word: str, target_word: str) -> None:
        """
        Add a new replacement to the dictionary.
        
        Args:
            source_word: Word to replace (can contain * for wildcards)
            target_word: Replacement word (can contain * for wildcards)
        """
        if not source_word or not target_word:
            return
        
        if '*' in source_word:
            self._add_wildcard_replacement(source_word, target_word)
        else:
            self._add_exact_replacement(source_word, target_word)
    
    def remove_replacement(self, source_word: str) -> None:
        """
        Remove a replacement from the dictionary.
        
        Args:
            source_word: Word to remove
        """
        if source_word in self.exact_replacements:
            del self.exact_replacements[source_word]
            if source_word in self._exact_patterns:
                del self._exact_patterns[source_word]
        
        # Remove from wildcard list
        self.wildcard_replacements = [
            (src, tgt, pat) for src, tgt, pat in self.wildcard_replacements
            if src != source_word
        ]


# Global dictionary instance
_global_dictionary: Optional[CustomDictionary] = None


def get_dictionary() -> Optional[CustomDictionary]:
    """Get the global dictionary instance."""
    return _global_dictionary


def init_dictionary(dictionary_path: Path) -> CustomDictionary:
    """
    Initialize the global dictionary instance.
    
    Args:
        dictionary_path: Path to the dictionary file
    
    Returns:
        Initialized CustomDictionary instance
    """
    global _global_dictionary
    _global_dictionary = CustomDictionary(dictionary_path)
    return _global_dictionary


def apply_custom_dictionary(text: str) -> str:
    """
    Apply custom dictionary replacements to text.
    
    This is a convenience function that uses the global dictionary instance.
    
    Args:
        text: Input text
    
    Returns:
        Text with replacements applied, or original text if no dictionary is loaded
    """
    if _global_dictionary:
        return _global_dictionary.apply_replacements(text)
    return text
