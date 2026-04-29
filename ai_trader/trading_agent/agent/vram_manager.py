"""
Dynamic VRAM Manager - Swap models based on trading schedule.

THREE VRAM MODES:

1. TRADING MODE (6:35 AM - 1:00 PM PST) - Requires Perplexity:
   - 0x workers (Perplexity does enrichment, backtesting is pure Python)
   - NO analysis model loaded (not needed during trading)
   - VRAM: ~2GB (minimal - 22GB free!)
   - Goal: Perplexity enrichment + pure Python backtesting, minimal VRAM

2. ANALYSIS MODE (1:00 PM - Market Close):
   - 6x qwen2.5:7b workers (news summarization)
   - 1x qwen3:14b (quick EOD analysis, error handling)
   - VRAM: ~20GB (4GB headroom)
   - Goal: Balance enrichment + analysis quality

3. DEEP ANALYSIS MODE (Grok API Fallback):
   - 0x workers (not needed)
   - 1x qwen3:30b-a3b (EOD reflections, lessons learned)
   - VRAM: ~18GB (6GB headroom)
   - Goal: Local fallback when Grok API unavailable (rare)
   - Normal: Uses Grok-4-1-fast-reasoning API (same price, no VRAM)

Usage:
    manager = VRAMManager(scheduler)
    manager.switch_to_trading_mode()       # 6:35 AM
    manager.switch_to_analysis_mode()      # 1:00 PM
    manager.switch_to_deep_analysis_mode() # After market close
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VRAMManager:
    """
    Manages dynamic model loading/unloading based on trading schedule.
    """

    def __init__(self, scheduler):
        """
        Initialize VRAM manager.

        Args:
            scheduler: AutomatedScheduler instance with access to:
                      - self.agent.ollama_provider (analysis model)
                      - self.worker_pool (worker pool manager)
                      - self.worker_pool_config (config dict)
        """
        self.scheduler = scheduler
        self.current_mode = None  # 'trading', 'analysis', or 'deep_analysis'

    def switch_to_trading_mode(self) -> bool:
        """
        Switch to trading mode: unload all models (Perplexity does enrichment).

        Trading mode requires Perplexity for enrichment. Backtesting is pure Python
        (no LLM). This minimizes VRAM usage (~2GB) leaving 22GB free on 24GB GPU.

        Returns:
            True if switch successful, False otherwise
        """
        if self.current_mode == 'trading':
            logger.debug("Already in trading mode")
            return True

        logger.info("=" * 70)
        logger.info("VRAM: Switching to TRADING MODE")
        logger.info("=" * 70)

        try:
            # 1. Check if Perplexity is available (REQUIRED for trading mode)
            perplexity_available = (
                getattr(self.scheduler, 'perplexity_provider', None) is not None
                and self.scheduler.perplexity_provider.is_available()
            )

            if not perplexity_available:
                logger.warning(
                    "VRAM: Perplexity not available - cannot use trading mode. "
                    "Trading mode requires Perplexity for enrichment. Staying in analysis mode."
                )
                return False

            # 2. Unload analysis model (if loaded)
            ollama = getattr(self.scheduler.agent, 'ollama_provider', None)
            if ollama:
                logger.info("Unloading analysis model to free VRAM...")
                ollama.unload()

            # 3. Shutdown worker pool (not needed - Perplexity does enrichment)
            if self.scheduler.worker_pool:
                logger.info("Shutting down worker pool (Perplexity handles enrichment)...")
                self.scheduler.worker_pool.shutdown()
                self.scheduler.worker_pool = None

            logger.info("VRAM: Trading mode active (Perplexity enrichment, ~22GB VRAM free)")
            self.current_mode = 'trading'
            return True

        except Exception as e:
            logger.error(f"VRAM: Failed to switch to trading mode: {e}")
            return False

    def switch_to_analysis_mode(self) -> bool:
        """
        Switch to analysis mode: load 14b, reduce workers.

        Returns:
            True if switch successful, False otherwise
        """
        if self.current_mode == 'analysis':
            logger.debug("Already in analysis mode")
            return True

        logger.info("=" * 70)
        logger.info("VRAM: Switching to ANALYSIS MODE")
        logger.info("=" * 70)

        try:
            # 1. Shutdown current worker pool
            if self.scheduler.worker_pool:
                logger.info("Shutting down trading-mode worker pool...")
                self.scheduler.worker_pool.shutdown()
                self.scheduler.worker_pool = None

            # 2. Load 14b model
            ollama = getattr(self.scheduler.agent, 'ollama_provider', None)
            if ollama:
                logger.info("Loading 14b model for EOD analysis...")
                if not ollama.warmup(keep_alive_seconds=28800):  # 8h keepalive
                    logger.warning("14b model warmup failed (non-fatal)")

            # 3. Start smaller worker pool (if needed)
            pool_config = self.scheduler.worker_pool_config
            analysis_workers = pool_config.get('analysis_mode_workers', 6)

            logger.info(f"Starting worker pool with {analysis_workers} workers (analysis mode)...")
            from agent.ollama_worker_pool import OllamaWorkerPool

            self.scheduler.worker_pool = OllamaWorkerPool(
                analysis_model=pool_config.get('analysis_model', 'qwen3:14b'),
                worker_model=pool_config.get('worker_model', 'qwen2.5:7b'),
                num_workers=analysis_workers,
                base_url=pool_config.get('base_url', 'http://localhost:11434'),
                timeout_seconds=pool_config.get('timeout_seconds', 60),
                keep_alive_hours=2  # Shorter keepalive for analysis tasks
            )

            if self.scheduler.worker_pool.warmup():
                logger.info(f"VRAM: Analysis mode active (14b + {analysis_workers} workers)")
                self.current_mode = 'analysis'
                return True
            else:
                logger.warning("Worker pool warmup failed (non-fatal)")
                self.scheduler.worker_pool = None
                self.current_mode = 'analysis'
                return True  # 14b loaded successfully, that's what matters

        except Exception as e:
            logger.error(f"VRAM: Failed to switch to analysis mode: {e}")
            return False

    def switch_to_deep_analysis_mode(self) -> bool:
        """
        Switch to deep analysis mode: unload all workers, load 30b model.

        This is a FALLBACK mode for when Grok API is unavailable (rare).
        Normally, EOD analysis uses Grok-4-1-fast-reasoning API (same price, no VRAM).
        Only manually invoke this if Grok API is down and you need local analysis.

        Returns:
            True if switch successful, False otherwise
        """
        if self.current_mode == 'deep_analysis':
            logger.debug("Already in deep analysis mode")
            return True

        logger.info("=" * 70)
        logger.info("VRAM: Switching to DEEP ANALYSIS MODE")
        logger.info("=" * 70)

        try:
            # 1. Shutdown worker pool (not needed after market close)
            if self.scheduler.worker_pool:
                logger.info("Shutting down worker pool (not needed for EOD analysis)...")
                self.scheduler.worker_pool.shutdown()
                self.scheduler.worker_pool = None

            # 2. Get deep analysis model from config
            pool_config = self.scheduler.worker_pool_config
            deep_model = pool_config.get('deep_analysis_model', 'qwen3:30b-a3b')

            # 3. Load the big model
            logger.info(f"Loading {deep_model} for EOD deep analysis...")

            # Replace the agent's ollama_provider with the 30b model
            from agent.ollama_provider import OllamaProvider

            deep_ollama = OllamaProvider(
                model_name=deep_model,
                base_url=pool_config.get('base_url', 'http://localhost:11434'),
                timeout_seconds=pool_config.get('timeout_seconds', 60) * 2  # 2x timeout for bigger model
            )

            if deep_ollama.warmup(keep_alive_seconds=28800):  # 8h keepalive
                # Store old provider for restoration later
                self.scheduler._saved_ollama_provider = getattr(self.scheduler.agent, 'ollama_provider', None)
                self.scheduler.agent.ollama_provider = deep_ollama

                logger.info(f"VRAM: Deep analysis mode active ({deep_model} loaded, 0 workers)")
                self.current_mode = 'deep_analysis'
                return True
            else:
                logger.error(f"Failed to load {deep_model}")
                return False

        except Exception as e:
            logger.error(f"VRAM: Failed to switch to deep analysis mode: {e}")
            return False

    def get_current_mode(self) -> Optional[str]:
        """Get current VRAM mode ('trading', 'analysis', 'deep_analysis', or None)."""
        return self.current_mode

    def get_status(self) -> dict:
        """
        Get current VRAM allocation status.

        Returns:
            Dict with mode, worker count, models loaded, etc.
        """
        ollama = getattr(self.scheduler.agent, 'ollama_provider', None)
        ollama_loaded = ollama and ollama.is_available() if ollama else False

        # Get current model name
        ollama_model = None
        if ollama and ollama_loaded:
            ollama_model = getattr(ollama, 'model_name', None)

        worker_count = 0
        if self.scheduler.worker_pool and self.scheduler.worker_pool._is_warmed_up:
            worker_count = self.scheduler.worker_pool.num_workers

        return {
            'mode': self.current_mode,
            'ollama_loaded': ollama_loaded,
            'ollama_model': ollama_model,
            'worker_count': worker_count,
            'perplexity_available': (
                getattr(self.scheduler, 'perplexity_provider', None) is not None
                and self.scheduler.perplexity_provider.is_available()
            )
        }
