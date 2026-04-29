"""
Pattern Extractor - Phase 2 of Backtest Validation System

Extracts testable patterns from Ollama's end-of-day reflections.

The goal is to convert natural language insights like:
  "High conviction news catalysts in uptrends work well"

Into structured patterns that can be backtested:
  {
    'type': 'entry_filter',
    'conditions': {
      'conviction': '>=9',
      'catalyst_type': 'news',
      'daily_trend': 'uptrend'
    },
    'hypothesis': 'High conviction news catalysts in uptrends are profitable'
  }

Usage:
    extractor = PatternExtractor()
    patterns = extractor.extract_testable_patterns(
        reflection_text="Today's winning trade...",
        trades=todays_trades
    )
"""

import re
import logging
from typing import List, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PatternExtractor:
    """
    Extracts testable trading patterns from natural language reflections.
    
    This parser looks for common pattern indicators in Ollama's EOD reflections
    and converts them into structured rules that can be backtested.
    """
    
    def __init__(self):
        """Initialize the pattern extractor with recognition patterns."""
        
        # Keywords that indicate entry conditions
        self.entry_keywords = {
            'conviction': ['conviction', 'confidence level', 'confidence'],
            'catalyst': ['catalyst', 'news', 'earnings', 'breakout', 'momentum', 'gap'],
            'trend': ['uptrend', 'downtrend', 'trending', 'trend'],
            'timing': ['morning', 'afternoon', 'open', 'close', 'mid-day'],
            'volume': ['volume surge', 'high volume', 'volume spike'],
            'technical': ['rsi', 'macd', 'support', 'resistance', 'vwap']
        }
        
        # Pattern types we can extract
        self.pattern_types = {
            'entry_filter': ['entry', 'enter', 'buy', 'purchase', 'took'],
            'exit_rule': ['exit', 'sell', 'close', 'stop', 'target'],
            'timing': ['timing', 'time', 'when', 'hour', 'minute'],
            'risk_management': ['risk', 'position size', 'stop loss', 'capital']
        }
        
        # Numeric pattern matchers
        self.numeric_patterns = {
            'conviction': r'(?:conviction|confidence).*?(\d+)/10',
            'percent_move': r'([+\-]?\d+(?:\.\d+)?)\s*%',
            'days': r'(\d+)\s*(?:day|d)(?:s)?',
            'price_level': r'\$?(\d+(?:\.\d+)?)'
        }
    
    def extract_testable_patterns(self, reflection_text: str, 
                                  trades: List[Dict] = None) -> List[Dict]:
        """
        Extract testable patterns from reflection text.
        
        Args:
            reflection_text: Natural language reflection from Ollama
            trades: List of trade dictionaries from today (optional, for context)
        
        Returns:
            List of pattern dictionaries that can be backtested
        """
        if not reflection_text:
            logger.warning("Empty reflection text provided")
            return []
        
        patterns = []
        
        # Split reflection into sentences for analysis
        sentences = self._split_into_sentences(reflection_text)
        
        # Extract patterns from each sentence
        for sentence in sentences:
            # Skip short or non-informative sentences
            if len(sentence) < 20:
                continue
            
            # Look for winning trade patterns
            if self._indicates_success(sentence):
                pattern = self._extract_success_pattern(sentence, trades)
                if pattern:
                    patterns.append(pattern)
            
            # Look for losing trade patterns (things to avoid)
            elif self._indicates_failure(sentence):
                pattern = self._extract_failure_pattern(sentence, trades)
                if pattern:
                    patterns.append(pattern)
            
            # Look for general insights
            elif self._indicates_insight(sentence):
                pattern = self._extract_insight_pattern(sentence, trades)
                if pattern:
                    patterns.append(pattern)
        
        # Deduplicate similar patterns
        patterns = self._deduplicate_patterns(patterns)
        
        logger.info(f"Extracted {len(patterns)} testable patterns from reflection")
        for p in patterns:
            logger.debug(f"  Pattern: {p.get('hypothesis', 'N/A')}")
        
        return patterns
    
    def _split_into_sentences(self, text: str) -> List[str]:
        """Split text into sentences for analysis."""
        # Simple sentence splitter (can be improved with NLTK if needed)
        sentences = re.split(r'[.!?]+', text)
        return [s.strip() for s in sentences if s.strip()]
    
    def _indicates_success(self, sentence: str) -> bool:
        """Check if sentence describes a successful pattern."""
        success_indicators = [
            'win', 'winning', 'profit', 'successful', 'worked',
            'good', 'favorable', 'positive', 'beat', 'strong'
        ]
        sentence_lower = sentence.lower()
        return any(indicator in sentence_lower for indicator in success_indicators)
    
    def _indicates_failure(self, sentence: str) -> bool:
        """Check if sentence describes a failed pattern."""
        failure_indicators = [
            'loss', 'losing', 'failed', 'avoid', 'mistake',
            'bad', 'negative', 'weak', 'faded', 'reversed'
        ]
        sentence_lower = sentence.lower()
        return any(indicator in sentence_lower for indicator in failure_indicators)
    
    def _indicates_insight(self, sentence: str) -> bool:
        """Check if sentence contains a general insight."""
        insight_indicators = [
            'insight:', 'lesson:', 'learned', 'pattern', 'notice',
            'observe', 'tend', 'typically', 'usually', 'often'
        ]
        sentence_lower = sentence.lower()
        return any(indicator in sentence_lower for indicator in insight_indicators)
    
    def _extract_success_pattern(self, sentence: str, trades: List[Dict] = None) -> Optional[Dict]:
        """Extract a pattern from a successful trade description."""
        conditions = {}
        sentence_lower = sentence.lower()
        
        # Extract conviction level
        conviction_match = re.search(self.numeric_patterns['conviction'], sentence_lower)
        if conviction_match:
            conviction = int(conviction_match.group(1))
            conditions['conviction'] = f'>={conviction}'
        
        # Extract catalyst type
        catalyst = self._extract_catalyst_type(sentence_lower)
        if catalyst:
            conditions['catalyst_type'] = catalyst
        
        # Extract trend information
        if 'uptrend' in sentence_lower or 'trending up' in sentence_lower:
            conditions['daily_trend'] = 'uptrend'
        elif 'downtrend' in sentence_lower or 'trending down' in sentence_lower:
            conditions['daily_trend'] = 'downtrend'
        
        # Extract percentage moves
        percent_matches = re.findall(self.numeric_patterns['percent_move'], sentence)
        if percent_matches:
            # Look for multi-day returns
            if 'day' in sentence_lower or 'd' in sentence_lower:
                for match in percent_matches:
                    try:
                        pct = float(match)
                        if abs(pct) > 5:  # Significant move
                            if pct > 0:
                                conditions['daily_return_10d'] = f'>{int(pct)}'
                            break
                    except ValueError:
                        pass
        
        # Extract volume information
        if 'volume surge' in sentence_lower or 'high volume' in sentence_lower:
            conditions['volume'] = 'above_average'
        
        # Skip if no meaningful conditions extracted
        if len(conditions) < 1:
            return None
        
        # Create hypothesis from sentence
        hypothesis = self._create_hypothesis(sentence, conditions, is_success=True)
        
        # Determine pattern type
        pattern_type = self._determine_pattern_type(sentence_lower)
        
        return {
            'type': pattern_type,
            'conditions': conditions,
            'hypothesis': hypothesis,
            'extracted_from': f"EOD reflection {datetime.now().strftime('%Y-%m-%d')}",
            'raw_sentence': sentence
        }
    
    def _extract_failure_pattern(self, sentence: str, trades: List[Dict] = None) -> Optional[Dict]:
        """Extract a pattern from a failed trade description."""
        conditions = {}
        sentence_lower = sentence.lower()
        
        # Extract what went wrong
        if 'chased' in sentence_lower:
            conditions['entry_type'] = 'chased'
        
        if 'late' in sentence_lower:
            conditions['timing'] = 'late'
        
        if 'fade' in sentence_lower or 'faded' in sentence_lower:
            conditions['pattern'] = 'gap_fade'
        
        # Extract gap information
        if 'gap' in sentence_lower:
            percent_matches = re.findall(self.numeric_patterns['percent_move'], sentence)
            if percent_matches:
                for match in percent_matches:
                    try:
                        pct = float(match)
                        if abs(pct) > 5:
                            conditions['gap_size'] = f'>{int(abs(pct))}'
                            break
                    except ValueError:
                        pass
        
        # Skip if no meaningful conditions
        if len(conditions) < 1:
            return None
        
        # Create hypothesis (negative pattern)
        hypothesis = self._create_hypothesis(sentence, conditions, is_success=False)
        
        return {
            'type': 'avoid_pattern',
            'conditions': conditions,
            'hypothesis': hypothesis,
            'extracted_from': f"EOD reflection {datetime.now().strftime('%Y-%m-%d')}",
            'raw_sentence': sentence,
            'is_negative': True
        }
    
    def _extract_insight_pattern(self, sentence: str, trades: List[Dict] = None) -> Optional[Dict]:
        """Extract a general insight pattern."""
        conditions = {}
        sentence_lower = sentence.lower()
        
        # Look for timing insights
        if 'morning' in sentence_lower:
            conditions['time_of_day'] = 'morning'
        elif 'afternoon' in sentence_lower:
            conditions['time_of_day'] = 'afternoon'
        
        # Look for market regime insights
        if 'bull' in sentence_lower or 'bullish' in sentence_lower:
            conditions['market_regime'] = 'bullish'
        elif 'bear' in sentence_lower or 'bearish' in sentence_lower:
            conditions['market_regime'] = 'bearish'
        elif 'choppy' in sentence_lower or 'sideways' in sentence_lower:
            conditions['market_regime'] = 'choppy'
        
        # Skip if too vague
        if len(conditions) < 1:
            return None
        
        hypothesis = sentence.strip()
        
        return {
            'type': 'timing' if 'time_of_day' in conditions else 'entry_filter',
            'conditions': conditions,
            'hypothesis': hypothesis,
            'extracted_from': f"EOD reflection {datetime.now().strftime('%Y-%m-%d')}",
            'raw_sentence': sentence
        }
    
    def _extract_catalyst_type(self, text: str) -> Optional[str]:
        """Extract the type of catalyst from text."""
        if 'news' in text or 'headline' in text:
            return 'news'
        elif 'earnings' in text or 'eps' in text:
            return 'earnings'
        elif 'fda' in text or 'approval' in text:
            return 'fda'
        elif 'breakout' in text:
            return 'technical_breakout'
        elif 'momentum' in text:
            return 'momentum'
        elif 'gap' in text:
            return 'gap'
        return None
    
    def _determine_pattern_type(self, text: str) -> str:
        """Determine the type of pattern from text."""
        # Check each pattern type
        for ptype, keywords in self.pattern_types.items():
            if any(keyword in text for keyword in keywords):
                return ptype
        
        # Default to entry_filter if uncertain
        return 'entry_filter'
    
    def _create_hypothesis(self, sentence: str, conditions: Dict, is_success: bool) -> str:
        """Create a human-readable hypothesis from conditions."""
        # Try to create a concise hypothesis
        parts = []
        
        if 'conviction' in conditions:
            parts.append(f"High conviction ({conditions['conviction']})")
        
        if 'catalyst_type' in conditions:
            parts.append(f"{conditions['catalyst_type']} catalyst")
        
        if 'daily_trend' in conditions:
            parts.append(f"in {conditions['daily_trend']}")
        
        if 'daily_return_10d' in conditions:
            parts.append(f"with strong momentum ({conditions['daily_return_10d']}% 10d)")
        
        if parts:
            hypothesis = " ".join(parts)
            if is_success:
                hypothesis += " - profitable pattern"
            else:
                hypothesis += " - avoid this pattern"
        else:
            # Fall back to cleaned sentence
            hypothesis = sentence.strip()[:100]
        
        return hypothesis
    
    def _deduplicate_patterns(self, patterns: List[Dict]) -> List[Dict]:
        """Remove duplicate or very similar patterns."""
        if not patterns:
            return []
        
        unique_patterns = []
        seen_hypotheses = set()
        
        for pattern in patterns:
            # Create a simple hash of the hypothesis
            hypothesis = pattern.get('hypothesis', '')
            hypothesis_key = hypothesis.lower()[:50]  # First 50 chars
            
            if hypothesis_key not in seen_hypotheses:
                seen_hypotheses.add(hypothesis_key)
                unique_patterns.append(pattern)
        
        return unique_patterns
    
    def extract_from_winning_trade(self, trade_symbol: str, entry_price: float,
                                   exit_price: float, conviction: int,
                                   catalyst: str = None, 
                                   context: str = None) -> Dict:
        """
        Extract a pattern directly from trade data (without reflection text).
        
        This is a fallback method for when we have trade data but limited
        reflection text.
        
        Args:
            trade_symbol: Stock symbol
            entry_price: Entry price
            exit_price: Exit price
            conviction: Conviction level (1-10)
            catalyst: Type of catalyst
            context: Additional context string
        
        Returns:
            Pattern dictionary
        """
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        
        conditions = {
            'conviction': f'>={conviction}',
        }
        
        if catalyst:
            conditions['catalyst_type'] = catalyst
        
        hypothesis = f"Trades with {conviction}/10 conviction"
        if catalyst:
            hypothesis += f" on {catalyst} catalyst"
        hypothesis += f" - won {pnl_pct:.1f}%"
        
        return {
            'type': 'entry_filter',
            'conditions': conditions,
            'hypothesis': hypothesis,
            'extracted_from': f"Direct trade analysis {datetime.now().strftime('%Y-%m-%d')}",
            'symbol': trade_symbol,
            'sample_pnl': pnl_pct
        }


# Convenience function for quick extraction
def extract_patterns_from_reflection(reflection_text: str, 
                                     trades: List[Dict] = None) -> List[Dict]:
    """
    Quick function to extract patterns from a reflection.
    
    Usage:
        patterns = extract_patterns_from_reflection(
            "Today's winning trade: ROIV with 9/10 conviction on positive trial data news.
             Stock was in daily uptrend (+13.6% over 10 days) and showed intraday momentum.
             Insight: High conviction news catalysts combined with strong trends work well."
        )
    """
    extractor = PatternExtractor()
    return extractor.extract_testable_patterns(reflection_text, trades)